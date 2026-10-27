"""Offline checks: python3 -B -m unittest discover -s tests -v."""

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.config import MODEL_CONFIG_ENV, MODEL_TIERS, load_model_config
from src.agentic_loop import Agent
from dispatcher import egress_proxy


def import_runner():
    spec = importlib.util.spec_from_file_location("a3_runner_test", ROOT / "evaluation_scripts/run_all.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    cwd = os.getcwd()
    try:
        spec.loader.exec_module(module)
    finally:
        os.chdir(cwd)
    return module


class ModelConfigTests(unittest.TestCase):
    def load_text(self, text):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "models.yaml"
            path.write_text(text)
            return load_model_config(path)

    def test_missing_empty_and_extra_tiers_are_silent(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            for text in ("", "{}", "extra: invalid", "expert:\nstandard: {}\nstarter:\n  base_url:\n  model_name:\n  api_key:"):
                self.assertEqual(self.load_text(text), {})
            models = self.load_text("""
expert:
  base_url: https://openrouter.ai/api/v1/
  model_name: vendor/expert
  api_key: test-secret
extra: [ignored, even, when, invalid]
""")
        self.assertEqual(list(models), ["expert"])
        self.assertEqual(models["expert"]["base_url"], "https://openrouter.ai/api/v1")
        self.assertEqual(output.getvalue(), "")

    def test_all_tiers_and_explicit_file_override_environment(self):
        data = {tier: {"base_url": f"https://{tier}.example/api/v1", "model_name": tier,
                       "api_key": f"test-{tier}"} for tier in MODEL_TIERS}
        with patch.dict(os.environ, {MODEL_CONFIG_ENV: "{}", "CS2680_MODEL_STANDARD": "ignored"}):
            models = self.load_text(json.dumps(data))  # JSON is valid YAML.
        self.assertEqual(models, data)

    def test_partial_and_invalid_yaml_errors_do_not_include_secrets(self):
        for text in ("expert: {api_key: private-secret}",
                     "expert: [private-secret", "expert: private-secret",
                     "expert: {base_url: 'https://private-secret@host/v1', model_name: m, api_key: private-secret}"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError) as error:
                    self.load_text(text)
                self.assertNotIn("private-secret", str(error.exception))

    def test_snapshot_needs_no_yaml_and_agent_accepts_missing_standard(self):
        models = {"starter": {"base_url": "http://proxy:3128/v1", "model_name": "small", "api_key": "test"}}
        sdk = Mock()
        with patch.dict(os.environ, {MODEL_CONFIG_ENV: json.dumps(models)}), \
             patch.dict(sys.modules, {"yaml": None, "openai": sdk}):
            agent = Agent(Mock())
            self.assertEqual(agent.models, models)
            self.assertEqual(list(agent.clients), ["starter"])
            sdk.OpenAI.assert_called_once_with(base_url="http://proxy:3128/v1", api_key="test")
        with patch.dict(os.environ, {MODEL_CONFIG_ENV: "{}"}), patch.dict(sys.modules, {"openai": sdk}):
            self.assertEqual(Agent(Mock()).clients, {})

    def test_runner_passes_per_tier_proxy_snapshot_without_keys_in_arguments(self):
        runner = import_runner()
        runner.MODELS = {tier: {"base_url": f"https://{tier}.example/api/v1",
                                "model_name": tier, "api_key": f"secret-{tier}"}
                         for tier in MODEL_TIERS}
        runner.PROXIES = {tier: f"proxy_{tier}" for tier in MODEL_TIERS}
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(runner, "HOSTLOG", tmp), \
             patch.object(runner, "create_internal_network"), \
             patch.object(runner, "log"), \
             patch.object(runner, "SEQ_FD", 0, create=True), \
             patch.object(runner, "exists_at", return_value=True), \
             patch.object(runner, "sh") as shell:
            runner.start_infra()
        starts = [call for call in shell.call_args_list if call.args[:2] == ("docker", "run")]
        self.assertEqual(len(starts), 4)
        runtime = json.loads(starts[-1].kwargs["env"][MODEL_CONFIG_ENV])
        for tier in MODEL_TIERS:
            self.assertEqual(runtime[tier]["base_url"], f"http://proxy_{tier}:3128/v1")
            self.assertEqual(runtime[tier]["api_key"], f"secret-{tier}")
            self.assertEqual(runtime[tier]["model_name"], tier)
            self.assertNotIn(f"secret-{tier}", repr([call.args for call in starts]))
            self.assertIn(runner.MODELS[tier]["base_url"], starts[MODEL_TIERS.index(tier)].args)

    def test_proxy_maps_full_base_path_and_keeps_request_boundaries(self):
        for prefix, expected in (("/api/v1", "/api/v1/chat/completions?x=1"),
                                 ("/v1", "/v1/chat/completions?x=1"),
                                 ("", "/chat/completions?x=1")):
            with patch.object(egress_proxy, "UPSTREAM_PATH", prefix):
                self.assertEqual(egress_proxy._upstream_path("/v1/chat/completions?x=1"), expected)
        for path in ("/admin", "http://example.com/v1/models", "/v1/../admin", "/v1/%2e%2e/admin"):
            self.assertFalse(egress_proxy._allowed_path(path))

    def test_preflight_checks_configured_tiers_and_rejects_bad_gateway(self):
        runner = import_runner()
        runner.MODELS = {"expert": {"base_url": "https://upstream.example/api/v1"}}
        models = {"expert": {"base_url": "http://proxy:3128/v1", "model_name": "large", "api_key": "test"}}

        class APIStatusError(Exception):
            def __init__(self, status_code):
                self.status_code = status_code

        class Connection:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def sendall(self, request): self.request = request
            def recv(self, size):
                code = 405 if self.request.startswith(b"CONNECT") else 403
                return f"HTTP/1.1 {code} Refused\r\n".encode()

        def connect(address, **kwargs):
            if address[0] == "proxy": return Connection()
            raise OSError("isolated")

        for status_code, expected in ((401, 0), (403, 0), (502, 1)):
            sdk = Mock(APIStatusError=APIStatusError, APIConnectionError=ConnectionError)
            client = Mock()
            client.models.list.side_effect = APIStatusError(status_code)
            sdk.OpenAI.return_value = contextlib.nullcontext(client)

            def run_probe(*args, **kwargs):
                output = io.StringIO()
                with contextlib.redirect_stdout(output), \
                     patch.dict(sys.modules, {"openai": sdk}), \
                     patch.dict(os.environ, {MODEL_CONFIG_ENV: json.dumps(models)}), \
                     patch("socket.create_connection", side_effect=connect):
                    exec(compile(kwargs["input"], "<preflight>", "exec"), {})
                return Mock(stdout=output.getvalue(), stderr="", returncode=0)

            with patch.object(runner, "sh", side_effect=run_probe), \
                 patch.object(runner, "log"), patch("builtins.open", unittest.mock.mock_open()):
                self.assertEqual(runner.preflight([]), expected)


if __name__ == "__main__":
    unittest.main()
