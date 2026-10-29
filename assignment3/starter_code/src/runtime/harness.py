"""Shared model calls for task-local agent loops.

Subclasses implement their own conversation and tool-execution policy. Agent owns
configuration/client creation and supplies the current task's logger and Sandbox.
Every runtime uses the supplied Sandbox for tools; schemas sent to the model
are selected per call and do not change the underlying sandbox capabilities.
"""

from ..sandbox import Sandbox


class Harness:
    def __init__(self, models: dict, clients: dict, logger, sandbox: Sandbox):
        self.models = models
        self.clients = clients
        self.logger = logger
        self.sandbox = sandbox
        self.iteration = 0  # maintained by the concrete loop

    def _call_model(self, tier: str, messages: list, iteration: int, *, tools=None, **kwargs):
        """Request one non-streaming completion and record its usage for this task.

        tools is an optional list of chat-completions tool schemas, not Python
        Tool objects. None or [] omits tools. Execution of returned tool calls is
        the loop's responsibility. Other request options pass through unchanged.
        """
        if tier not in self.models or tier not in self.clients:
            raise ValueError(f"Model tier {tier!r} is not configured")
        if kwargs.get("stream"):
            raise ValueError("Harness._call_model requires a non-streaming response to log usage")
        model_id = self.models[tier]["model_name"]
        if tools:
            kwargs["tools"] = tools
        response = self.clients[tier].chat.completions.create(
            model=model_id, messages=messages, **kwargs)
        usage = getattr(response, "usage", None)
        self.logger.api_request(
            iteration,
            getattr(usage, "prompt_tokens", None),
            getattr(usage, "completion_tokens", None),
            getattr(usage, "total_tokens", None),
            model_id,
            cached_prompt_tokens=getattr(
                getattr(usage, "prompt_tokens_details", None), "cached_tokens", 0) or 0,
            model_tier=tier,
        )
        return response

    def run(self, task: dict) -> int:
        """
        The whole sync agent loop for a complete task.

        return the number of iterations to solve the task, or -1 if the loop failed to solve it.
        """
        raise NotImplementedError("Implement the agent loop in a Harness subclass")
