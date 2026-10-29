"""Shared model calls and Sandbox tool execution for task-local agent loops.

Subclasses implement their own conversation and tool-execution policy. Agent owns
configuration/client creation and supplies the current task's logger and Sandbox.
Every runtime uses the supplied Sandbox for tools; schemas sent to the model
are selected per call and do not change the underlying sandbox capabilities.
"""

import json

from ..sandbox import Sandbox, SandboxError, SandboxPathError
from .messages_api import call_messages


class Harness:
    def __init__(self, models: dict, clients: dict, logger, sandbox: Sandbox):
        self.models = models
        self.clients = clients
        self.logger = logger
        self.sandbox = sandbox
        self.tools = []  # Model-facing schemas supplied by each concrete runtime.
        self.messages = []  # Task-local history; the runtime controls resets.
        self.iteration = 0  # maintained by the concrete loop

    def _call_model(self, tier: str, messages: list, iteration: int, *, tools=None, **kwargs):
        """Request one non-streaming completion and record its usage for this task.

        tools is an optional list of chat-completions tool schemas, not Python
        Tool objects. None or [] omits tools. Execution of returned tool calls is
        the loop's responsibility. api_format=messages translates this conversation
        and normalizes the response; the default keeps Chat Completions unchanged.
        """
        if tier not in self.models or tier not in self.clients:
            raise ValueError(f"Model tier {tier!r} is not configured")
        if kwargs.get("stream"):
            raise ValueError("Harness._call_model requires a non-streaming response to log usage")
        model_id = self.models[tier]["model_name"]
        if tools:
            kwargs["tools"] = tools
        self.logger.event("api_start", iteration=iteration, model_id=model_id, model_tier=tier)
        try:
            if self.models[tier].get("api_format", "chat_completions") == "messages":
                response = call_messages(self.clients[tier], model_id, messages, **kwargs)
            else:
                response = self.clients[tier].chat.completions.create(
                    model=model_id, messages=messages, **kwargs)
        except Exception as error:
            self.logger.event("api_error", iteration=iteration, model_id=model_id,
                              model_tier=tier, error_type=type(error).__name__)
            raise
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

    def _execute_tool(self, call, iteration):
        """Execute a declared Sandbox tool and append its result to self.messages.

        Subclasses define self.tools; only the shared three-tool implementation
        is supported here. Add explicit dispatch when introducing a new tool.
        """
        name, raw_args = call.function.name, call.function.arguments
        self.logger.tool_call(iteration, name, raw_args)
        try:
            schema = next((t["function"]["parameters"] for t in self.tools
                           if t["function"]["name"] == name), None)
            if schema is None:
                raise ValueError(f"Unknown tool: {name}")
            args = json.loads(raw_args)
            if not isinstance(args, dict):
                raise ValueError("Tool arguments must be a JSON object")
            if set(args) - schema["properties"].keys() or set(schema["required"]) - args.keys():
                raise ValueError("Missing required or unexpected tool arguments")
            for key, value in args.items():
                spec = schema["properties"][key]
                expected = int if spec["type"] == "integer" else str
                if type(value) is not expected:
                    raise ValueError(f"Invalid type for {key}: expected {spec['type']}")
                if expected is int and not spec["minimum"] <= value <= spec["maximum"]:
                    raise ValueError(f"{key} must be between {spec['minimum']} and {spec['maximum']}")
            if name == "exec":
                result = self.sandbox.exec(**args)
                is_error = result["exit_code"] != 0 or result["timed_out"]
            elif name == "read_text":
                result = {"content": self.sandbox.read_text(**args)}
                is_error = False
            elif name == "write_text":
                result = {"existed": self.sandbox.write_text(**args)}
                is_error = False
            else:
                raise ValueError(f"Tool has no Sandbox implementation: {name}")
        except (ValueError, TypeError, SandboxError, SandboxPathError) as error:
            result, is_error = {"error": str(error)}, True
        content = json.dumps(result, ensure_ascii=False)
        self.logger.tool_result(iteration, name, content, is_error)
        self.messages.append({"role": "tool", "tool_call_id": call.id, "content": content})

    def run(self, task: dict) -> int:
        """
        The whole sync agent loop for a complete task.

        Return the model-call count on normal completion, or -1 if the loop stops
        incomplete. Completion does not imply passing the dispatcher's evaluation.
        """
        raise NotImplementedError("Implement the agent loop in a Harness subclass")
