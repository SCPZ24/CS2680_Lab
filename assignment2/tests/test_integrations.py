"""Verify OpenRouter request wiring without a network call."""

import json
import os
import unittest
from unittest.mock import patch

import httpx

from src.integrations import LLMClient


class OpenRouterClientTests(unittest.TestCase):
    def test_environment_key_url_model_and_reasoning_on_wire(self):
        captured = []

        def respond(request):
            captured.append(request)
            return httpx.Response(200, json={
                "id": "test", "object": "chat.completion", "created": 0,
                "model": "qwen/qwen3.6-35b-a3b",
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": "OK"}}],
            })

        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "offline-test"}, clear=True):
            client = LLMClient(enable_thinking=False)
        with httpx.Client(transport=httpx.MockTransport(respond)) as transport:
            original = client.client
            client.client = original.with_options(http_client=transport)
            try:
                result = client.invoke([{"role": "user", "content": "hello"}])
            finally:
                client.client.close()
                original.close()
        self.assertEqual(result.choices[0].message.content, "OK")
        self.assertEqual(len(captured), 1)
        request = captured[0]
        self.assertEqual(str(request.url), "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(request.headers["authorization"], "Bearer offline-test")
        body = json.loads(request.content)
        self.assertEqual(body["model"], "qwen/qwen3.6-35b-a3b")
        self.assertEqual(body["reasoning"], {"enabled": False})
        self.assertFalse(body["stream"])
        self.assertNotIn("enable_thinking", body)

    def test_missing_openrouter_key_does_not_use_other_provider_key(self):
        with patch.dict(os.environ, {"SILICONFLOW_API_KEY": "other-provider"}, clear=True):
            with self.assertRaisesRegex(ValueError, "OPENROUTER_API_KEY"):
                LLMClient()
