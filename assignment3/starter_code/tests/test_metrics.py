"""Exercise the real lifecycle and report code offline, with only Docker/grading mocked."""

import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from test_model_config import import_runner
from dispatcher.dispatcher import TaskTraceLogger
from evaluation_scripts.metrics import summarize_usage
from src.agentic_loop import Agent
from src.runtime.harness import Harness
from src.config import load_model_config


MODEL = dict(base_url="https://example.test/v1", api_key="secret-not-for-reports",
             model_name="example/model", input=2, cache=0.5, output=8)


class MetricsTests(unittest.TestCase):
    def test_optional_prices_and_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "models.yaml"
            path.write_text(json.dumps({"starter": MODEL, "extra": {"input": -1}}))
            self.assertEqual(load_model_config(path), {"starter": MODEL})
            for value in (-1, True, "cheap", float("nan"), float("inf")):
                path.write_text(json.dumps({"starter": dict(MODEL, input=value)}))
                with self.assertRaises(ValueError):
                    load_model_config(path)
            path.write_text(json.dumps({"starter": {"input": 2}, "expert": None}))
            self.assertEqual(load_model_config(path), {})

    def test_usage_cache_pricing_and_unknown_cost(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run.jsonl"
            logger = TaskTraceLogger(str(path), True)
            logger.run_start("/app")
            client = Mock()
            client.chat.completions.create.return_value = SimpleNamespace(usage=SimpleNamespace(
                prompt_tokens=1000, completion_tokens=100, total_tokens=1100,
                prompt_tokens_details=SimpleNamespace(cached_tokens=400)))
            harness = Harness({"starter": MODEL}, {"starter": client}, logger, Mock())
            harness._call_model("starter", [], 1)
            logger.run_end("done", 1)
            logger.close()
            result = summarize_usage(path, {"starter": MODEL})
            self.assertEqual(result["total_tokens"], 1100)
            self.assertEqual(result["cached_prompt_tokens"], 400)
            self.assertAlmostEqual(result["cost_usd"], 0.0022)
            self.assertNotIn("model_tier", client.chat.completions.create.call_args.kwargs)
            models = {"starter": {k: v for k, v in MODEL.items() if k != "cache"}}
            self.assertIsNone(summarize_usage(path, models)["cost_usd"])
            events = [json.loads(line) for line in path.read_text().splitlines()]
            request = next(e for e in events if e["event"] == "api_request")
            request["cached_prompt_tokens"] = 0
            path.write_text("\n".join(map(json.dumps, events)))
            self.assertAlmostEqual(summarize_usage(path, models)["cost_usd"], 0.0028)
            request["model_tier"] = None
            path.write_text("\n".join(map(json.dumps, events)))
            self.assertIsNone(summarize_usage(path, {"starter": MODEL, "expert": MODEL})["cost_usd"])
            request["prompt_tokens"] = None
            path.write_text("\n".join(map(json.dumps, events)))
            result = summarize_usage(path, {"starter": MODEL})
            self.assertEqual(result["missing_usage_requests"], 1)
            self.assertIsNone(result["cost_usd"])
            path.write_text('{"event":"run_start"}\n{"event":')
            self.assertIsNone(summarize_usage(path, {"starter": MODEL})["cost_usd"])
            self.assertIsNone(summarize_usage(path.with_name("missing"), {})["cost_usd"])
            self.assertEqual(summarize_usage(path.with_name("missing"), {}, opened=False)["cost_usd"], 0)

    def test_failed_evaluation_rework_serial_guard_and_e2e_report(self):
        runner = import_runner()
        task = runner.Task(0, "task0", {"instance_id": "iid0", "docker_image": "image"})
        unopened = runner.Task(1, "task1", {"instance_id": "iid1", "docker_image": "image"})
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            os.chdir(tmp)
            stack.callback(os.chdir, cwd)
            Path("run_logs").mkdir()
            stack.enter_context(patch.object(runner, "HOSTLOG", tmp))
            stack.enter_context(patch.object(runner, "GRADINGS", tmp))
            stack.enter_context(patch.object(runner, "MODELS", {"starter": MODEL}))
            for name in ("log", "sh", "stop_sandbox", "remove_patch_dir", "remove_patch"):
                stack.enter_context(patch.object(runner, name))
            stack.enter_context(patch.object(runner, "start_sandbox", return_value="/app"))
            stack.enter_context(patch.object(runner, "patch_dir", side_effect=lambda *a, **kw: os.open(tmp, os.O_RDONLY)))
            stack.enter_context(patch.object(runner, "patch_size", return_value=1))
            stack.enter_context(patch.object(runner, "total_tests", return_value=3))
            stack.enter_context(patch.object(runner.time, "monotonic", side_effect=[10.0, 40.0]))
            reply = stack.enter_context(patch.object(runner, "seq_json"))
            verdicts = iter(["0", "1"])

            def grade(t, n_done=None):
                t.grading = Mock(stdout=io.StringIO(next(verdicts)))
                t.grading_attempt = t.attempt
                t.grading_t0 = runner.time.time()

            stack.enter_context(patch.object(runner, "start_grading", side_effect=grade))
            server = runner.Server([task, unopened], 1)
            server.handle(0, {"op": "next_task"})
            self.assertEqual(reply.call_args.args[1]["task"]["task"], 0)
            server.handle(1, {"op": "next_task"})
            self.assertFalse(reply.call_args.args[1]["ok"])
            server.handle(2, {"op": "evaluate", "k": 0})
            runner.finish_grading(task)
            server.handle(3, {"op": "continue", "k": 0})
            server.handle(4, {"op": "continue", "k": 0})  # No new failure: not another rework.
            self.assertEqual(task.rework_count, 1)
            server.handle(5, {"op": "evaluate", "k": 0})
            runner.finish_grading(task)
            server.handle(6, {"op": "done", "k": 0})
            server.handle(7, {"op": "next_task"})
            self.assertIsNone(reply.call_args.args[1]["task"])
            logger = TaskTraceLogger(".tasks/trace/madsOpt_logs/0/run.jsonl", True)
            logger.run_start("/app")
            logger.api_request(0, 1000, 100, 1100, MODEL["model_name"], cached_prompt_tokens=400, model_tier="starter")
            logger.run_end("done", 1)
            logger.close()
            runner.write_results([task, unopened])
            text = Path("run_logs/metrics.json").read_text()
            report = json.loads(text)
            self.assertNotIn(MODEL["api_key"], text)
            self.assertNotIn(MODEL["base_url"], text)
            row = report["tasks"][0]
            self.assertEqual((row["evaluations"], row["failed_evaluations"], row["rework_count"]), (2, 1, 1))
            self.assertEqual(row["e2e_seconds"], 30.0)
            self.assertEqual(report["totals"]["e2e_seconds"], 30.0)
            self.assertAlmostEqual(report["totals"]["cost_usd"], 0.0022)
            self.assertFalse(report["tasks"][1]["opened"])
            self.assertIsNone(report["tasks"][1]["e2e_seconds"])
            self.assertIn("0.00220000", Path("run_all_results.md").read_text())

    def test_agent_completes_solve_before_requesting_next_task(self):
        dispatcher = Mock()
        dispatcher.next_task.side_effect = [{"task": 0}, {"task": 1}, None]
        agent = object.__new__(Agent)
        agent.dispatcher = dispatcher
        agent.solve = Mock()
        calls = Mock()
        calls.attach_mock(dispatcher.next_task, "next_task")
        calls.attach_mock(agent.solve, "solve")
        agent.run()
        self.assertEqual([call[0] for call in calls.mock_calls],
                         ["next_task", "solve", "next_task", "solve", "next_task"])


if __name__ == "__main__":
    unittest.main()
