"""From assignment2/: python3 -B -m unittest discover -s tests -v."""

from contextlib import chdir, redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import io
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import httpx
from openai import APIConnectionError, AuthenticationError, RateLimitError
from openai.types.chat import ChatCompletion

from src import integrations, runtime, tools
from src.log_client import RuntimeLogClient
from src.runtime import AgentRuntime, LoopMsgBox


def response(calls=(), usage=True):
    return ChatCompletion(
        id="test", object="chat.completion", created=0, model="test-model",
        choices=[{"index": 0, "finish_reason": "tool_calls" if calls else "stop",
                  "message": {"role": "assistant", "content": "",
                              "tool_calls": [
                                  {"id": f"call_{index}", "type": "function",
                                   "function": {"name": name, "arguments": arguments}}
                                  for index, (name, arguments) in enumerate(calls)
                              ]}}],
        usage={"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12} if usage else None,
    )


class RuntimeLoggingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.logger = RuntimeLogClient(str(self.root / "logs" / "run.jsonl"))
        self.client = Mock(model_name="test-model")
        self.box = LoopMsgBox()
        self.agent = AgentRuntime(
            self.client, self.logger, str(self.root), self.box,
            tool_group=[tools.ReadFile(str(self.root)), tools.Bash(str(self.root)), tools.Done(self.box)],
        )
        self.agent.add_user_message("Test logging")

    def events(self):
        rows = [json.loads(line) for line in self.logger.log_path.read_text().splitlines()]
        expected = {
            "run_start": {"model_id", "workdir"},
            "api_request": {"iteration", "prompt_tokens", "completion_tokens", "total_tokens"},
            "api_retry": {"iteration", "error", "backoff_s"},
            "tool_call": {"iteration", "tool_name", "arguments"},
            "tool_result": {"iteration", "tool_name", "result", "is_error"},
            "run_end": {"reason", "num_iterations"},
        }
        for row in rows:
            self.assertEqual(set(row), expected[row["event"]] | {"event", "timestamp"})
            self.assertIsNotNone(datetime.fromisoformat(row["timestamp"]).tzinfo)
        return rows

    def test_tools_and_truncated_results_match_history(self):
        (self.root / "large.txt").write_text("中文\n" * 10_000, encoding="utf-8")
        self.client.invoke.side_effect = [response([
            ("read_file", '{"path":"large.txt"}'),
            ("read_file", '{"path":"missing.txt"}'),
            ("missing_tool", "{}"),
            ("read_file", "{broken"),
            ("bash", '{"command":"exit 3"}'),
        ]), response([("done", '{"summary":"完成"}')])]
        self.assertEqual(self.agent.run_loop(), "done")
        rows = self.events()
        self.assertEqual([r["event"] for r in rows],
                         ["run_start", "api_request"] + ["tool_call", "tool_result"] * 5
                         + ["api_request", "tool_call", "tool_result", "run_end"])
        results = [r for r in rows if r["event"] == "tool_result"]
        history = [m["content"] for m in self.agent.messages if m["role"] == "tool"]
        self.assertEqual([r["result"] for r in results], history)
        self.assertEqual([r["is_error"] for r in results], [False, True, True, True, True, False])
        self.assertLessEqual(len(results[0]["result"]), 12000)
        self.assertIn("truncated", results[0]["result"])
        self.assertEqual(rows[-1]["num_iterations"], 2)
        self.assertEqual([r["iteration"] for r in rows if r["event"] == "api_request"], [1, 2])
        self.assertEqual(rows[1]["total_tokens"], 12)

    def test_no_calls_and_missing_usage(self):
        self.client.invoke.return_value = response(usage=False)
        self.assertEqual(self.agent.run_loop(), "no_tool_calls")
        rows = self.events()
        self.assertIsNone(rows[1]["prompt_tokens"])
        self.assertEqual(rows[-1]["reason"], "no_tool_calls")

    def test_transient_retry_then_success(self):
        error = APIConnectionError(request=httpx.Request("POST", "https://example.test"))
        self.client.invoke.side_effect = [error, response()]
        with patch.object(runtime.time, "sleep") as sleep:
            self.agent.run_loop()
        sleep.assert_called_once_with(1)
        rows = self.events()
        self.assertEqual([r["event"] for r in rows], ["run_start", "api_retry", "api_request", "run_end"])
        self.assertEqual(rows[1]["iteration"], rows[2]["iteration"])
        self.assertEqual(rows[-1]["num_iterations"], 1)

    def test_exhausted_retries_record_error_end(self):
        self.client.invoke.side_effect = APIConnectionError(request=httpx.Request("POST", "https://example.test"))
        with patch.object(runtime.time, "sleep") as sleep, self.assertRaises(APIConnectionError):
            self.agent.run_loop()
        rows = self.events()
        self.assertEqual([r["backoff_s"] for r in rows if r["event"] == "api_retry"], [1, 2, 4, 8, 16, 32])
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [1, 2, 4, 8, 16, 32])
        self.assertEqual(self.client.invoke.call_count, 7)
        self.assertEqual(rows[-1]["reason"], "error")

    def test_rate_limit_retry_after_and_stderr_progress(self):
        future = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=180), usegmt=True)
        for header, minimum, maximum in [
            ("90", 90, 90), (future, 175, 180), ("invalid", 1, 1),
            ("NaN", 1, 1), ("-10", 1, 1),
        ]:
            with self.subTest(retry_after=header):
                error = RateLimitError(
                    "TPM limit reached", response=httpx.Response(
                        429, headers={"Retry-After": header},
                        request=httpx.Request("POST", "https://example.test")), body=None,
                )
                self.client.invoke.side_effect = [error, response()]
                stdout, stderr = io.StringIO(), io.StringIO()
                with patch.object(runtime.time, "sleep") as sleep, redirect_stdout(stdout), redirect_stderr(stderr):
                    self.agent.run_loop()
                self.assertGreaterEqual(sleep.call_args.args[0], minimum)
                self.assertLessEqual(sleep.call_args.args[0], maximum)
                self.assertEqual(stdout.getvalue(), "")
                self.assertIn("TPM limit reached", stderr.getvalue())
                self.assertIn("attempt 2/7", stderr.getvalue())
                self.assertIn("input_tokens=10", stderr.getvalue())
        self.events()  # The assignment JSONL schema stays unchanged.

    def test_auth_error_not_retried(self):
        self.client.invoke.side_effect = AuthenticationError(
            "Unauthorized", response=httpx.Response(401, request=httpx.Request("POST", "https://example.test")), body=None
        )
        with self.assertRaises(AuthenticationError):
            self.agent.run_loop()
        self.assertEqual(self.client.invoke.call_count, 1)
        self.assertEqual([r["event"] for r in self.events()], ["run_start", "run_end"])

    def test_schema_rejected_and_files_not_reused(self):
        with self.assertRaises(ValueError):
            self.logger.write_log(event="run_end", reason="done")
        with self.assertRaises(ValueError):
            self.logger.write_log(event="run_end", reason="done", num_iterations=1, extra=True)
        with self.assertRaises(FileExistsError):
            RuntimeLogClient(str(self.logger.log_path))

    def test_cli_logs_opt_in_and_legacy_aliases(self):
        entry = Path(__file__).resolve().parents[1] / "madsLoop.py"
        with chdir(self.root), patch.dict(os.environ, {"OPENROUTER_API_KEY": "offline"}), patch.object(
            integrations.LLMClient, "invoke", return_value=response()
        ):
            for arguments, count in [
                (["-p", "hello", str(self.root)], 0),
                (["-p", "hello", "--log", str(self.root)], 1),
                (["-p", "hello", "--logs", "--workdir", str(self.root)], 2),
            ]:
                with patch.object(sys, "argv", [str(entry), *arguments]):
                    runpy.run_path(str(entry), run_name="__main__")
                files = list((self.root / "madsLoop_logs").glob("*.jsonl"))
                self.assertEqual(len(files), count)
            for file in files:
                rows = [json.loads(line) for line in file.read_text().splitlines()]
                self.assertEqual([r["event"] for r in rows], ["run_start", "api_request", "run_end"])


if __name__ == "__main__":
    unittest.main()
