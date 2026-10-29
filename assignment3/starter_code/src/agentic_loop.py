"""Your agent. Everything in src/ is yours: replace this starter with your own design.

madsOpt.py (course code) builds the dispatcher client and calls

    Agent(dispatcher).run()

That is all it needs from src/. Tasks run serially using the three-tool ReAct harness:

- the dispatcher (dispatcher/dispatcher.py) opens tasks and grades patches: Agent.run() and
  Agent.solve() below show every call;
- a Sandbox (src/sandbox.py) is the only way to read, write or run anything in a task's
  repository, which lives in its own task container;
- model-config.yaml defines expert, standard and starter; the runner passes a snapshot with
  proxy URLs, and self.models / self.clients contain only the configured tiers;
- the trace (dispatcher.logger(k)) must get every model call and every tool call: the
  leaderboard computes your cost and turn counts from it.
"""

import os
import sys

from .config import load_model_config
from .runtime.react_base3tool import ReActBase3ToolHarness
from .sandbox import Sandbox


def _debug(msg: str):
    print(f"[madsOpt] {msg}", file=sys.stderr, flush=True)


class Agent:
    def __init__(self, dispatcher, models=None):
        from openai import OpenAI                       # installed in the agent container

        self.max_reworks = int(os.environ.get("MADSOPT_MAX_REWORKS") or "2")
        if self.max_reworks < 0:
            raise ValueError("MADSOPT_MAX_REWORKS must be non-negative")
        self.dispatcher = dispatcher
        self.models = load_model_config() if models is None else models
        self.clients = {
            tier: OpenAI(base_url=model["base_url"], api_key=model["api_key"], max_retries=0, timeout=120)
            for tier, model in self.models.items()
        }

    def run(self):
        """Work through the tasks. This starter takes them one at a time, in the order given."""
        from dispatcher import DispatcherShutdown
        while True:
            try:
                # Opens the next task (its task container is started for you) and returns its
                # dict, or None when no tasks are left. Raises DispatcherError
                # (from dispatcher import DispatcherError) when a task is already open.
                task = self.dispatcher.next_task()
            except DispatcherShutdown:                   # the dispatcher ended the run (e.g. time limit)
                return
            if task is None:
                return
            try:
                self.solve(task)
            except DispatcherShutdown:
                return

    def solve(self, task: dict):
        k = task["task"]                                  # the task index
        logger = self.dispatcher.logger(k)                # this task's trace: madsOpt_logs/<k>/run.jsonl
        from dispatcher import DispatcherShutdown
        harness = None
        reason = "done"
        try:
            sb = Sandbox(task["sandbox_url"], task["workdir"])
            harness = ReActBase3ToolHarness(self.models, self.clients, logger, sb)
            outcome = harness.run(task)
            for rework in range(getattr(self, "max_reworks", 2) + 1):
                size = self.dispatcher.extract_patch(k)
                if not size:
                    reason = harness.stop_reason if outcome < 0 else "no_patch"
                    break
                evaluation = self.dispatcher.evaluate(k)
                resolved = evaluation.get("resolved", evaluation["tests_total"] > 0 and evaluation["tests_failed"] == 0)
                if resolved:
                    reason = "resolved"
                    break
                if outcome < 0:
                    reason = harness.stop_reason
                    break
                if rework == getattr(self, "max_reworks", 2):
                    reason = "max_reworks"
                    break
                if harness.iteration >= harness.max_iterations:
                    reason = "max_iterations"
                    break
                self.dispatcher.continue_task(k)
                outcome = harness.resume(
                    f"The submitted patch did not resolve the task. Evaluation attempt {evaluation['attempt']}: "
                    f"{evaluation['tests_failed']}/{evaluation['tests_total']} tests failed. "
                    "Your previous edits remain in the repository. Investigate and revise the patch, "
                    "run available local checks, then give a final response.")
        except DispatcherShutdown:
            logger.event("task_exit", reason="dispatcher_shutdown")
            raise
        except Exception as error:
            reason = f"error:{type(error).__name__}"
            logger.event("agent_error", error_type=type(error).__name__)
            _debug(f"task {k}: {reason}")
            if harness is not None:
                try:
                    self.dispatcher.extract_patch(k)  # Preserve any useful edits before closing.
                except DispatcherShutdown:
                    raise
                except Exception as extract_error:
                    logger.event("patch_error", error_type=type(extract_error).__name__)
        self.dispatcher.done(k, reason, harness.iteration if harness is not None else 0)
