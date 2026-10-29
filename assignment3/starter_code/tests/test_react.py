"""Offline ReAct conversations using the real loop and fake SDK/Sandbox."""
import copy
import json
import os
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, call, patch
from src.runtime.react_base3tool import ReActBase3ToolHarness
from src.sandbox import SandboxPathError


def tool(name, args, ident):
    return NS(id=ident, function=NS(name=name, arguments=args))


def reply(*calls, text=None, reason=None):
    payload = {"role": "assistant", "content": text}
    if calls:
        payload["tool_calls"] = [{"id": c.id, "type": "function", "function": {
            "name": c.function.name, "arguments": c.function.arguments}} for c in calls]
    message = Mock(content=text, tool_calls=list(calls))
    message.model_dump.return_value = payload
    return NS(usage=None, choices=[NS(message=message, finish_reason=reason or ("tool_calls" if calls else "stop"))])


class ReActTests(unittest.TestCase):
    def make_harness(self, responses, limit=10):
        client, logger, sandbox = Mock(), Mock(), Mock()
        self.requests = []
        responses = iter(responses)
        def request(**kwargs):
            self.requests.append(copy.deepcopy(kwargs))
            return next(responses)
        client.chat.completions.create.side_effect = request
        with patch.dict(os.environ, {"MADSOPT_MAX_ITERATIONS": str(limit)}):
            harness = ReActBase3ToolHarness({"expert": {"model_name": "test-model"}},
                                          {"expert": client}, logger, sandbox)
        return harness, logger, sandbox

    def test_all_tools_message_protocol_and_final(self):
        harness, logger, sandbox = self.make_harness([
            reply(tool("read_text", '{"path":"a.py"}', "r"),
                  tool("write_text", '{"path":"b.py","content":"fixed"}', "w"),
                  tool("exec", '{"command":"pytest","timeout_s":60}', "e"), text="Inspecting."),
            reply(text="Fixed and tested.")])
        sandbox.read_text.return_value = "old"
        sandbox.write_text.return_value = False
        sandbox.exec.return_value = {"output": "passed", "exit_code": 0, "timed_out": False}
        self.assertEqual(harness.run({"problem_statement": "Fix it", "requirements": "Keep API", "interface": "f()"}), 2)
        self.assertEqual(sandbox.mock_calls, [call.read_text(path="a.py"),
                         call.write_text(path="b.py", content="fixed"), call.exec(command="pytest", timeout_s=60)])
        self.assertEqual([m["role"] for m in self.requests[1]["messages"]],
                         ["system", "user", "assistant", "tool", "tool", "tool"])
        results = self.requests[1]["messages"][-3:]
        self.assertEqual([r["tool_call_id"] for r in results], ["r", "w", "e"])
        self.assertEqual(json.loads(results[1]["content"]), {"existed": False})
        self.assertEqual({t["function"]["name"] for t in self.requests[0]["tools"]}, {"exec", "read_text", "write_text"})
        self.assertEqual([c.args[0] for c in logger.api_request.call_args_list], [0, 1])
        self.assertEqual([c.args[0] for c in logger.tool_result.call_args_list], [0, 0, 0])
        self.assertEqual(harness.final_answer, "Fixed and tested.")
        self.assertEqual(harness.stop_reason, "final")

    def test_tool_errors_are_returned_to_model(self):
        harness, logger, sandbox = self.make_harness([
            reply(tool("read_text", "{", "json"), tool("done", "{}", "unknown"),
                  tool("exec", '{"command":"ls","timeout_s":true}', "type"),
                  tool("read_text", '{"path":"missing"}', "path"),
                  tool("exec", '{"command":"pytest","timeout_s":1}', "exec")),
            reply(text="Unable to complete.")])
        sandbox.read_text.side_effect = SandboxPathError("missing file")
        sandbox.exec.return_value = {"output": "failed", "exit_code": 1, "timed_out": False}
        self.assertEqual(harness.run({"problem_statement": "Fix it"}), 2)
        self.assertTrue(all(c.args[3] for c in logger.tool_result.call_args_list))
        self.assertEqual(sum(m["role"] == "tool" for m in self.requests[1]["messages"]), 5)
        sandbox.exec.assert_called_once_with(command="pytest", timeout_s=1)
        self.assertEqual(harness.stop_reason, "final")

    def test_budget_truncation_and_empty_response_are_not_final(self):
        pending = tool("exec", '{"command":"ls","timeout_s":1}', "e")
        for response, limit, reason in [(reply(pending), 1, "max_iterations"),
                                        (reply(pending, reason="length"), 10, "incomplete_response:length"),
                                        (reply(text=""), 10, "empty_response")]:
            with self.subTest(reason=reason):
                harness, logger, sandbox = self.make_harness([response], limit)
                sandbox.exec.return_value = {"output": "", "exit_code": 0, "timed_out": False}
                self.assertEqual(harness.run({"problem_statement": "Fix it"}), -1)
                self.assertEqual(harness.stop_reason, reason)
                self.assertEqual(harness.iteration, 1)
                self.assertIsNone(harness.final_answer)
                if reason != "max_iterations":
                    sandbox.exec.assert_not_called()

    def test_reusing_harness_resets_task_context(self):
        harness, _, _ = self.make_harness([reply(text="one"), reply(text="two")])
        harness.run({"problem_statement": "FIRST TASK"})
        self.assertEqual(harness.run({"problem_statement": "SECOND TASK"}), 1)
        self.assertNotIn("FIRST TASK", json.dumps(self.requests[1]["messages"]))
        self.assertEqual(len(self.requests[1]["messages"]), 2)


if __name__ == "__main__":
    unittest.main()
