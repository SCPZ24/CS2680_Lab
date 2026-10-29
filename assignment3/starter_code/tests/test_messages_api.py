"""Messages protocol translation, including a multi-tool round trip and usage."""
import json
import unittest
from unittest.mock import Mock

from src.runtime.harness import Harness
from src.config import _model_configs


class MessagesTests(unittest.TestCase):
    def test_tools_system_history_usage_and_native_blocks(self):
        client, logger = Mock(), Mock()
        model = {"model_name": "test", "api_format": "messages"}
        harness = Harness({"expert": model}, {"expert": client}, logger, Mock())
        native = [{"type": "thinking", "thinking": "plan", "signature": "signature"},
                  {"type": "tool_use", "id": "a", "name": "read_text", "input": {"path": "a"}},
                  {"type": "tool_use", "id": "b", "name": "read_text", "input": {"path": "b"}}]
        client.post.return_value = {"type": "message", "id": "r", "model": "test", "content": native,
                                    "stop_reason": "tool_use", "usage": {"input_tokens": 10,
                                    "cache_read_input_tokens": 20, "cache_creation_input_tokens": 5, "output_tokens": 3}}
        tools = [{"type": "function", "function": {"name": "read_text", "parameters": {
            "type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]
        messages = [{"role": "system", "content": "System"}, {"role": "user", "content": "Read both"}]
        response = harness._call_model("expert", messages, 0, tools=tools, tool_choice="required", max_tokens=100)
        self.assertEqual(client.post.call_args.args, ("/messages",))
        body = client.post.call_args.kwargs["body"]
        self.assertEqual(body["system"], [{"type": "text", "text": "System"}])
        self.assertEqual(body["tool_choice"], {"type": "any"})
        self.assertEqual(body["tools"][0]["input_schema"], tools[0]["function"]["parameters"])
        self.assertEqual(body["max_tokens"], 100)
        self.assertEqual(response.choices[0].finish_reason, "tool_calls")
        self.assertEqual(json.loads(response.choices[0].message.tool_calls[1].function.arguments), {"path": "b"})
        logger.api_request.assert_called_once_with(0, 35, 3, 38, "test", cached_prompt_tokens=20, model_tier="expert")
        messages.append(response.choices[0].message.model_dump(mode="json", exclude_none=True))
        messages.extend([{"role": "tool", "tool_call_id": "a", "content": "A"},
                         {"role": "tool", "tool_call_id": "b", "content": "B"}])
        client.post.return_value = {"type": "message", "content": [{"type": "text", "text": "OK"}], "stop_reason": "end_turn"}
        response = harness._call_model("expert", messages, 1)
        turns = client.post.call_args.kwargs["body"]["messages"]
        self.assertEqual(turns[1]["content"], native)
        self.assertEqual([b["tool_use_id"] for b in turns[2]["content"]], ["a", "b"])
        self.assertEqual(len(native), 3)  # Conversion must not mutate prior history.
        self.assertEqual(response.choices[0].message.content, "OK")
        self.assertEqual(response.choices[0].finish_reason, "stop")
        self.assertIsNone(response.usage)
        for reason in ("max_tokens", "pause_turn", None):
            client.post.return_value["stop_reason"] = reason
            self.assertEqual(harness._call_model("expert", messages, 2).choices[0].finish_reason, "length")

    def test_format_snapshot_and_default_compatibility(self):
        entry = {"base_url": "http://host.docker.internal:18765/v1", "api_key": "local-no-auth", "model_name": "test"}
        self.assertNotIn("api_format", _model_configs({"expert": entry})["expert"])
        entry["api_format"] = "messages"
        self.assertEqual(_model_configs({"expert": entry})["expert"]["api_format"], "messages")
        entry["api_format"] = "bad"
        with self.assertRaises(ValueError):
            _model_configs({"expert": entry})
