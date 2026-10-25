"""Offline checks for endpoint overrides, including batch -> Docker forwarding."""

import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class EndpointCliTests(unittest.TestCase):
    def test_agent_endpoint_defaults_and_overrides(self):
        cases = [
            ([], {"OPENROUTER_API_KEY": "default-key"},
             "https://openrouter.ai/api/v1", "default-key", True),
            (["-k", "override-key"], {"OPENROUTER_API_KEY": "default-key"},
             "https://openrouter.ai/api/v1", "override-key", True),
            (["-b", "http://localhost:18082/", "-k", "campus-key"], {},
             "http://localhost:18082/v1", "campus-key", False),
            (["-b", "http://localhost:18082/v1", "-k", "campus-key"], {},
             "http://localhost:18082/v1", "campus-key", False),
        ]
        for flags, env, url, key, openrouter in cases:
            with self.subTest(flags=flags), tempfile.TemporaryDirectory() as workdir:
                with patch.dict(os.environ, env, clear=True), \
                     patch.object(sys, "argv", ["madsLoop.py", "-p", "test", workdir, *flags]), \
                     patch("src.integrations.LLMClient") as client, \
                     patch("src.runtime.AgentRuntime") as runtime:
                    runpy.run_path(str(ROOT / "madsLoop.py"), run_name="__main__")
                client.assert_called_once_with(
                    base_url=url, api_key=key,
                    model_name="qwen/qwen3.6-35b-a3b" if openrouter else "qwen3.6-35b-a3b",
                    enable_thinking=True if openrouter else None,
                )
                runtime.return_value.run_loop.assert_called_once()

    def test_batch_forwards_endpoint_to_task_container(self):
        for custom in (False, True):
            with self.subTest(custom=custom), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                for name in ("run_all.sh", "run_task.sh"):
                    shutil.copyfile(ROOT / "evaluation_scripts" / name, root / name)
                (root / "madsLoop.py").touch()
                (root / "make_patches.py").write_text("pass\n")
                (root / "evaluate.sh").write_text("exit 0\n")
                (root / "agent_task_input.json").write_text(json.dumps({"task": {
                    "instance_id": "task", "docker_image": "test-image",
                    "problem_statement": "test problem",
                }}))
                (root / "pro_eval").mkdir()
                (root / "pro_eval/eval_results.json").write_text('{"task": true}')
                docker = root / "docker"
                docker.write_text(f"#!{sys.executable}\n" +
                    "import json, pathlib, sys\n"
                    "if sys.argv[1] == 'image': print('sha256:test')\n"
                    "if sys.argv[1] == 'run':\n"
                    "    pathlib.Path('docker_args.json').write_text(json.dumps(sys.argv[2:]))\n")
                docker.chmod(0o755)
                env = {k: v for k, v in os.environ.items()
                       if not k.startswith("MADSLOOP_") and k != "OPENROUTER_API_KEY"}
                env["PATH"] = str(root) + os.pathsep + env["PATH"]
                flags = []
                url, key = "https://openrouter.ai/api/v1", "default-key"
                if custom:
                    url, key = "http://localhost:18082/", "key with spaces;$literal"
                    flags = ["-b", url, "-k", key]
                else:
                    env["OPENROUTER_API_KEY"] = key
                result = subprocess.run(
                    ["bash", "run_all.sh", "--workers", "1", *flags],
                    cwd=root, env=env, capture_output=True, text=True, timeout=15,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                args = json.loads((root / "docker_args.json").read_text())
                self.assertIn("MADSLOOP_BASE_URL=" + url, args)
                self.assertIn("MADSLOOP_API_KEY=" + key, args)
                self.assertIn('-b "$MADSLOOP_BASE_URL" -k "$MADSLOOP_API_KEY"', args[-1])
                self.assertNotIn(key, result.stdout + result.stderr)
