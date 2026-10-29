"""A synchronous ReAct loop with three Sandbox-backed tools."""

import json
import os

from .harness import Harness
from .prompts import REACT_RAW_SYSTEM_PROMPT
from ..sandbox import SandboxError, SandboxPathError


class ReActBase3ToolHarness(Harness):
    def __init__(self, models: dict, clients: dict, logger, sandbox):
        super().__init__(models, clients, logger, sandbox)
        self.tools = [
            {"type": "function", "function": {
                "name": "exec", "description": "Run a shell command in the task repository. Returns combined output, exit_code and timed_out.",
                "parameters": {"type": "object", "properties": {
                    "command": {"type": "string", "description": "Shell command to execute."},
                    "timeout_s": {"type": "integer", "minimum": 1, "maximum": 900},
                    "cwd": {"type": "string", "description": "Optional working directory; defaults to the repository root."},
                }, "required": ["command", "timeout_s"], "additionalProperties": False}}},
            {"type": "function", "function": {
                "name": "read_text", "description": "Read an entire text file. Relative paths resolve against the task repository root.",
                "parameters": {"type": "object", "properties": {
                    "path": {"type": "string"},
                }, "required": ["path"], "additionalProperties": False}}},
            {"type": "function", "function": {
                "name": "write_text", "description": "Write the full content of a text file, creating parent directories if needed. Overwrites existing content.",
                "parameters": {"type": "object", "properties": {
                    "path": {"type": "string"}, "content": {"type": "string"},
                }, "required": ["path", "content"], "additionalProperties": False}}},
        ]
        self.max_iterations = int(os.environ.get("MADSOPT_MAX_ITERATIONS") or "100")
        if self.max_iterations < 1:
            raise ValueError("MADSOPT_MAX_ITERATIONS must be positive")
        self.messages = self._build_initial_messages()
        self.final_answer = None
        self.stop_reason = None

    def _build_initial_messages(self) -> list:
        return [{"role": "system", "content": REACT_RAW_SYSTEM_PROMPT}]

    def _execute_tool(self, call, iteration):
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
            else:  # write_text; only the three declared tool names are accepted above.
                result = {"existed": self.sandbox.write_text(**args)}
                is_error = False
        except (ValueError, TypeError, SandboxError, SandboxPathError) as error:
            result, is_error = {"error": str(error)}, True
        content = json.dumps(result, ensure_ascii=False)
        self.logger.tool_result(iteration, name, content, is_error)
        self.messages.append({"role": "tool", "tool_call_id": call.id, "content": content})

    def run(self, task: dict) -> int:
        """Return model-call count on final text, or -1 on an incomplete response/budget stop.

        A final answer means the model stopped working, not that hidden tests passed.
        Each run starts with a fresh conversation, including when this object is reused.
        """
        self.iteration = 0
        self.final_answer = self.stop_reason = None
        self.messages = self._build_initial_messages()
        description = "\n\n".join(f"{key}:\n{task[key]}" for key in
                                    ("problem_statement", "requirements", "interface") if task.get(key))
        self.messages.append({"role": "user", "content": description})
        while self.iteration < self.max_iterations:
            response = self._call_model("expert", self.messages, self.iteration, tools=self.tools)
            self.iteration += 1
            if not response.choices:
                self.stop_reason = "empty_response"
                return -1
            choice = response.choices[0]
            message = choice.message
            self.messages.append(message.model_dump(mode="json", exclude_none=True))
            # A truncated/filtered response is not a final answer or a safe tool call.
            if choice.finish_reason not in ("stop", "tool_calls"):
                self.stop_reason = f"incomplete_response:{choice.finish_reason}"
                return -1
            if message.tool_calls:
                # Usage and tool events use the same zero-based model-call index.
                for call in message.tool_calls:
                    self._execute_tool(call, self.iteration - 1)
                continue
            if choice.finish_reason == "stop" and message.content and message.content.strip():
                self.final_answer = message.content
                self.stop_reason = "final"
                return self.iteration
            self.stop_reason = "empty_response"
            return -1
        self.stop_reason = "max_iterations"
        return -1
