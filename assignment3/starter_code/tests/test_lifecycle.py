"""Exercise failure/rework/interrupt paths without model calls or Docker."""
import contextlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from test_model_config import import_runner
from src.agentic_loop import Agent
from src.runtime.react_base3tool import ReActBase3ToolHarness
from evaluation_scripts.metrics import summarize_usage


class LifecycleTests(unittest.TestCase):
    def test_rework_keeps_history_and_shares_call_budget(self):
        harness = ReActBase3ToolHarness({}, {}, Mock(), Mock())
        harness.max_iterations = 2
        message = Mock(content="finished", tool_calls=[])
        message.model_dump.return_value = {"role": "assistant", "content": "finished"}
        harness._call_model = Mock(return_value=SimpleNamespace(choices=[
            SimpleNamespace(message=message, finish_reason="stop")]))
        self.assertEqual(harness.run({"problem_statement": "fix bug"}), 1)
        self.assertEqual(harness.resume("test failed"), 2)
        self.assertEqual(harness.messages[1]["content"], "problem_statement:\nfix bug")
        self.assertEqual(harness.messages[3]["content"], "test failed")
        self.assertEqual(harness.resume("still failed"), -1)
        self.assertEqual(harness.stop_reason, "max_iterations")
        self.assertEqual(harness._call_model.call_count, 2)
        harness.run({"problem_statement": "new task"})
        self.assertNotIn("test failed", str(harness.messages))
        self.assertEqual(harness.iteration, 1)

    def test_agent_reworks_failed_grade_and_stops_after_success_or_cap(self):
        for passed, expected, resumes in ((True, "resolved", 1), (False, "max_reworks", 2)):
            with self.subTest(passed=passed), patch("src.agentic_loop.Sandbox"), \
                    patch("src.agentic_loop.ReActBase3ToolHarness") as factory:
                agent = object.__new__(Agent)
                agent.models, agent.clients, agent.dispatcher = {}, {}, Mock()
                agent.max_reworks = 2
                h = factory.return_value
                h.run.return_value = h.resume.return_value = h.iteration = 1
                h.max_iterations = 100
                agent.dispatcher.extract_patch.return_value = 1
                agent.dispatcher.evaluate.side_effect = [
                    dict(resolved=False, tests_failed=1, tests_total=2, attempt=0),
                    dict(resolved=passed, tests_failed=0 if passed else 1, tests_total=2, attempt=1),
                    dict(resolved=False, tests_failed=1, tests_total=2, attempt=2)]
                agent.solve(dict(task=0, sandbox_url="http://sandbox", workdir="/app"))
                self.assertEqual(h.resume.call_count, resumes)
                self.assertEqual(agent.dispatcher.continue_task.call_count, resumes)
                agent.dispatcher.done.assert_called_once_with(0, expected, 1)

    def test_api_failure_closes_task_then_processes_next(self):
        agent = object.__new__(Agent)
        agent.models, agent.clients, agent.dispatcher = {}, {}, Mock()
        task = dict(task=0, sandbox_url="http://sandbox", workdir="/app")
        agent.dispatcher.next_task.side_effect = [task, dict(task, task=1), None]
        agent.dispatcher.extract_patch.return_value = 0
        with patch("src.agentic_loop.Sandbox"), patch("src.agentic_loop.ReActBase3ToolHarness") as factory:
            factory.return_value.run.side_effect = [TimeoutError(), 1]
            factory.return_value.iteration = 1
            agent.run()
        self.assertEqual([c.args[1] for c in agent.dispatcher.done.call_args_list],
                         ["error:TimeoutError", "no_patch"])
        self.assertEqual(factory.call_count, 2)

    def test_lost_or_failed_response_never_counts_as_zero_cost(self):
        for suffix, pending, errors in (([], 1, 0), ([{"event": "api_error"}], 0, 1)):
            with tempfile.TemporaryDirectory() as tmp:
                trace = Path(tmp) / "run.jsonl"
                events = [{"event": "api_start"}] + suffix + [{"event": "run_end"}]
                trace.write_text("\n".join(map(json.dumps, events)))
                result = summarize_usage(trace, {})
                self.assertIsNone(result["cost_usd"])
                self.assertEqual(result["pending_api_requests"], pending)
                self.assertEqual(result["api_errors"], errors)
                self.assertEqual(result["known_cost_usd"], 0)

    def test_trace_truncated_mid_utf8_still_produces_partial_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run.jsonl"
            path.write_bytes(b'{"event":"api_start"}\n{"event":"tool_result","result":"\xe4')
            result = summarize_usage(path, {})
            self.assertEqual(result["trace_errors"], 1)
            self.assertEqual(result["pending_api_requests"], 1)
            self.assertIsNone(result["cost_usd"])

    def test_cleanup_publishes_current_trace_after_agent_is_stopped(self):
        runner = import_runner()
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            os.chdir(tmp)
            stack.callback(os.chdir, cwd)
            Path("run_logs").mkdir()
            Path("madsOpt_logs/old_task").mkdir(parents=True)
            trace = Path(".tasks/trace/madsOpt_logs/0/run.jsonl")
            trace.parent.mkdir(parents=True)
            trace.write_text('{"event":"api_start"}\n')
            stack.enter_context(patch.object(runner, "sh", return_value=SimpleNamespace(stdout="", stderr="")))
            runner.cleanup()
            self.assertEqual(Path("madsOpt_logs/0/run.jsonl").read_text(), trace.read_text())
            self.assertFalse(Path("madsOpt_logs/old_task").exists())

    def test_interrupt_writes_partial_report_with_elapsed_time(self):
        runner = import_runner()
        task = runner.Task(0, "0", {"instance_id": "iid", "docker_image": "image"})
        task.e2e_t0 = 10.0
        task.live = True
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            os.chdir(tmp)
            stack.callback(os.chdir, cwd)
            for name in ("preconditions", "resolve_docker", "snapshot_scripts", "open_task_dirs",
                         "ensure_images", "start_infra", "cleanup", "move_gradings", "log", "sh"):
                stack.enter_context(patch.object(runner, name))
            stack.enter_context(patch.object(runner, "parse_args", return_value=SimpleNamespace(limit=1)))
            stack.enter_context(patch.object(runner, "session_id", return_value="local_test"))
            stack.enter_context(patch.object(runner, "load_model_config", return_value={}))
            stack.enter_context(patch.object(runner, "load_tasks", return_value=([task], 1)))
            stack.enter_context(patch.object(runner, "HOSTLOG", tmp))
            stack.enter_context(patch.object(runner.signal, "signal"))
            stack.enter_context(patch.object(runner.time, "monotonic", return_value=25.0))
            stack.enter_context(patch.object(runner.Server, "serve", side_effect=KeyboardInterrupt))
            self.assertEqual(runner.main(), 130)
            result = json.loads(Path("run_logs/metrics.json").read_text())
            row = result["tasks"][0]
            self.assertFalse(row["finished"])
            self.assertEqual(row["end_reason"], "interrupted")
            self.assertEqual(row["e2e_seconds"], 15.0)
            self.assertIsNone(row["cost_usd"])
            self.assertIn("Interrupted", Path("run_all_results.md").read_text())

    def test_portable_timeout_preserves_exit_status_and_stops_command(self):
        helper = str(Path(__file__).resolve().parents[1] / "evaluation_scripts/run_with_timeout.py")
        for seconds, code, status in (("2", "raise SystemExit(7)", 7),
                                      ("0.05", "import time; time.sleep(60)", 124)):
            result = subprocess.run([sys.executable, helper, seconds, sys.executable, "-c", code], timeout=8)
            self.assertEqual(result.returncode, status)


if __name__ == "__main__":
    unittest.main()
