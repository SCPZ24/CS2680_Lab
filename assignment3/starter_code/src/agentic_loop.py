"""Your agent. Everything in src/ is yours: replace this starter with your own design.

madsOpt.py (course code) builds the dispatcher client and calls

    Agent(dispatcher).run()

That is all it needs from src/. This starter only shows how to use
what the course provides. It makes no attempt to fix anything, so every task fails until you
write your agent:

- the dispatcher (dispatcher/dispatcher.py) opens tasks and grades patches: Agent.run() and
  Agent.solve() below show every call;
- a Sandbox (src/sandbox.py) is the only way to read, write or run anything in a task's
  repository, which lives in its own task container;
- model-config.yaml defines expert, standard and starter; the runner passes a snapshot with
  proxy URLs, and self.models / self.clients contain only the configured tiers;
- the trace (dispatcher.logger(k)) must get every model call and every tool call: the
  leaderboard computes your cost and turn counts from it.
"""

import sys

from .config import load_model_config
from .sandbox import Sandbox


def _debug(msg: str):
    print(f"[madsOpt] {msg}", file=sys.stderr, flush=True)


def call_model(client, model_id: str, messages: list, logger, iteration: int, *, model_tier=None, **kwargs):
    """One chat-completions request to the selected model, logged in the task's trace.

    `messages` is an OpenAI-style message list; extra keyword arguments (e.g. tools=[...]) are
    passed to the API unchanged. Nothing else is done for you here.
    """
    response = client.chat.completions.create(model=model_id, messages=messages, **kwargs)
    usage = response.usage
    logger.api_request(iteration,
                       getattr(usage, "prompt_tokens", None),
                       getattr(usage, "completion_tokens", None),
                       getattr(usage, "total_tokens", None),
                       model_id,
                       cached_prompt_tokens=getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", 0) or 0,
                       model_tier=model_tier)
    return response


class Agent:
    def __init__(self, dispatcher, models=None):
        from openai import OpenAI                       # installed in the agent container

        self.dispatcher = dispatcher
        self.models = load_model_config() if models is None else models
        self.clients = {
            tier: OpenAI(base_url=model["base_url"], api_key=model["api_key"])
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
        sb = Sandbox(task["sandbox_url"], task["workdir"])  # this task's repository
        # What to fix: task["problem_statement"], task["requirements"], task["interface"].
        iterations = 0
        messages = []  # Task-local conversation; keep it across rework, never across tasks.

        # ---- your agent's work on the task goes here -----------------------------------------
        # Ask a configured tier, e.g. if "standard" in self.models:
        # response = call_model(self.clients["standard"], self.models["standard"]["model_name"],
        #                       messages, logger, iterations, model_tier="standard")
        # Repository:     r = sb.exec("git status --short", timeout_s=60)  # {"output", "exit_code", "timed_out"}
        #                 text = sb.read_text("path/to/file.py");  sb.write_text("path/to/file.py", text)
        # Log tool use:   logger.tool_call(iterations, name, args)
        #                 logger.tool_result(iterations, name, result, is_error)
        # ---------------------------------------------------------------------------------------

        size = self.dispatcher.extract_patch(k)           # `git diff` of the sandbox -> the task's patch folder
        # (or self.dispatcher.submit_patch(k, text) to write a patch yourself)
        if size:                                          # evaluate() refuses an empty patch
            evaluation = self.dispatcher.evaluate(k)      # {"tests_failed", "tests_total", "attempt"}; waits for the grading
            _debug(f"task {k}: {evaluation['tests_failed']}/{evaluation['tests_total']} tests failing")
            # To keep working after reading the evaluation: self.dispatcher.continue_task(k)
            # (your edits stay in the sandbox), then extract_patch(k) and evaluate(k) again.
        self.dispatcher.done(k, "done", iterations)       # final: the last evaluation is the task's result
