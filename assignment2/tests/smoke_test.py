"""From assignment2/: python3 -B -m tests.smoke_test.

Requires OPENROUTER_API_KEY. Uses the actual CLI entry point and a temporary
repository; diagnostics are separate from the assignment's agent-log schema.
"""

import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
from unittest.mock import patch

from src.integrations import LLMClient
from src.runtime import AgentRuntime


def main():
    if not os.environ.get("OPENROUTER_API_KEY"):
        raise SystemExit("Set OPENROUTER_API_KEY before running this live check.")

    destination = Path(tempfile.mkdtemp(prefix="madsloop-smoke-"))
    workspace = destination / "repo"
    workspace.mkdir()
    source = "def total(items):\n    return sum(price for price, quantity in items)\n"
    tests = '''import unittest
from cart import total

class TotalTests(unittest.TestCase):
    def test_quantities(self):
        self.assertEqual(total([(10, 3), (5, 2)]), 40)
    def test_empty(self):
        self.assertEqual(total([]), 0)
    def test_zero_quantity(self):
        self.assertEqual(total([(10, 0)]), 0)
    def test_single_items(self):
        self.assertEqual(total([(7, 1), (2, 1)]), 9)
'''
    (workspace / "cart.py").write_text(source)
    test_file = workspace / "test_cart.py"
    test_file.write_text(tests)
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)

    def verify():
        return subprocess.run(
            [sys.executable, "-B", "-m", "unittest", "-v", "test_cart"],
            cwd=workspace, capture_output=True, text=True, timeout=20,
        )

    baseline = verify()
    (destination / "before.log").write_text(baseline.stdout + baseline.stderr)
    assert baseline.returncode != 0, "Fixture must fail before the agent edits it"
    print(f"Baseline fails as expected. Artifacts: {destination}", flush=True)

    original_invoke = LLMClient.invoke
    original_loop = AgentRuntime.run_loop
    calls = 0
    diagnostics = []
    state = {}

    def invoke(client, messages, tools=None):
        nonlocal calls
        if calls >= 8:
            raise RuntimeError("Smoke test stopped at the 8-request limit")
        calls += 1
        client.client = client.client.with_options(timeout=90)
        print(f"Request {calls}/8", flush=True)
        response = original_invoke(client, messages, tools)
        message = response.choices[0].message
        names = [call.function.name for call in message.tool_calls or []]
        diagnostics.append({
            "request": calls,
            "finish_reason": response.choices[0].finish_reason,
            "usage": response.usage.model_dump() if response.usage else None,
            "tools": names,
        })
        print(f"Returned tools: {names}", flush=True)
        return response

    def run_loop(runtime):
        state["runtime"] = runtime
        state["reason"] = original_loop(runtime)
        return state["reason"]

    prompt = (
        "Fix cart.total(items): each item is a (price, quantity) pair and the "
        "total must include quantity. Inspect the code and run the existing "
        "tests before editing, make a minimal fix, run the tests again, and "
        "call done with a summary. Do not modify test_cart.py. "
        f"Use {sys.executable} -B -m unittest -v test_cart to run tests. "
        "Operate only inside the supplied working directory."
    )
    entry = Path(__file__).resolve().parents[1] / "madsLoop.py"
    try:
        with patch.object(LLMClient, "invoke", invoke), patch.object(AgentRuntime, "run_loop", run_loop), patch.object(
            sys, "argv", [str(entry), "-p", prompt, "--workdir", str(workspace)]
        ):
            runpy.run_path(str(entry), run_name="__main__")
    finally:
        (destination / "requests.json").write_text(json.dumps(diagnostics, indent=2))
        if "runtime" in state:
            (destination / "messages.json").write_text(
                json.dumps(state["runtime"].messages, ensure_ascii=False, indent=2)
            )

    assert test_file.read_text() == tests, "Agent modified the test file"
    result = verify()
    (destination / "after.log").write_text(result.stdout + result.stderr)
    assert result.returncode == 0, result.stdout + result.stderr
    assert state.get("reason") == "done", f"Unexpected stop: {state.get('reason')}"
    print(f"PASS: harness repaired the code and finished via done in {calls} requests.")
    print(f"Artifacts: {destination}")


if __name__ == "__main__":
    main()
