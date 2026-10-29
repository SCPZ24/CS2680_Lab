"""Offline checks for the shared model-call path."""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from src.runtime.harness import Harness
from src.agentic_loop import Agent


class HarnessTests(unittest.TestCase):
    def test_tier_routing_tools_and_task_logger(self):
        models = {tier: {"model_name": tier + "-model"} for tier in ("expert", "starter")}
        clients = {tier: Mock() for tier in models}
        logger = Mock()
        sandbox = Mock()
        harness = Harness(models, clients, logger, sandbox)
        response = SimpleNamespace(usage=SimpleNamespace(
            prompt_tokens=100, completion_tokens=20, total_tokens=120,
            prompt_tokens_details=SimpleNamespace(cached_tokens=30)))
        clients["starter"].chat.completions.create.return_value = response
        messages = [{"role": "user", "content": "Read the file"}]
        tools = [{"type": "function", "function": {
            "name": "read_file", "description": "Read a file",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
                           "required": ["path"]}}}]
        self.assertIs(harness._call_model("starter", messages, 2, tools=tools, tool_choice="auto"), response)
        clients["starter"].chat.completions.create.assert_called_once_with(
            model="starter-model", messages=messages, tools=tools, tool_choice="auto")
        logger.api_request.assert_called_once_with(
            2, 100, 20, 120, "starter-model", cached_prompt_tokens=30, model_tier="starter")
        clients["expert"].chat.completions.create.assert_not_called()

        # Another task can share HTTP clients while logging to its own logger.
        other_logger = Mock()
        other = Harness(models, clients, other_logger, sandbox)
        self.assertIs(harness.sandbox, sandbox)
        self.assertIs(other.sandbox, sandbox)
        clients["expert"].chat.completions.create.return_value = SimpleNamespace(usage=None)
        for schemas in (None, []):
            other._call_model("expert", [], 0, tools=schemas)
            self.assertNotIn("tools", clients["expert"].chat.completions.create.call_args.kwargs)
        other_logger.api_request.assert_called_with(
            0, None, None, None, "expert-model", cached_prompt_tokens=0, model_tier="expert")
        self.assertEqual(logger.api_request.call_count, 1)
        with self.assertRaisesRegex(ValueError, "not configured"):
            harness._call_model("standard", [], 3)
        with self.assertRaisesRegex(ValueError, "non-streaming"):
            harness._call_model("starter", [], 3, stream=True)
        self.assertEqual(clients["starter"].chat.completions.create.call_count, 1)

    def test_solve_injects_sandbox_and_runs_harness(self):
        agent = object.__new__(Agent)
        agent.models, agent.clients, agent.dispatcher = {}, {}, Mock()
        agent.dispatcher.extract_patch.return_value = 0
        task = {"task": 0, "sandbox_url": "http://sandbox", "workdir": "/app"}
        with patch("src.agentic_loop.Sandbox") as sandbox, \
             patch("src.agentic_loop.ReActBase3ToolHarness") as harness:
            harness.return_value.run.return_value = 3
            harness.return_value.iteration = 3
            agent.solve(task)
            harness.assert_called_once_with(
                agent.models, agent.clients, agent.dispatcher.logger.return_value, sandbox.return_value)
            harness.return_value.run.assert_called_once_with(task)
            agent.dispatcher.done.assert_called_once_with(0, "done", 3)
        with self.assertRaises(NotImplementedError):
            Harness({}, {}, Mock(), Mock()).run(task)


if __name__ == "__main__":
    unittest.main()
