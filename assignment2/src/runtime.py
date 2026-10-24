from __future__ import annotations

from enum import Enum
import json
import time
from typing import Any, TYPE_CHECKING

from openai import APIConnectionError, APIStatusError

from .integrations import LLMClient
from .prompts import RAW_SYSTEM_PROMPT
from .log_client import LogClient

if TYPE_CHECKING:
    from .tools import Tool


class MSG(Enum):
    Task_Complete = 0


class LoopMsgBox:
    def __init__(self):
        self.msg = []

    def add_message(self, message: MSG):
        self.msg.append(message)

    def emit_messages(self):
        tmp = self.msg
        self.msg = []
        return tmp


class AgentRuntime:
    def __init__(self, llm_client: LLMClient, log_client: LogClient, pwd: str, loop_msg_box: LoopMsgBox, tool_group: list[Tool] | None = None):
        self.llm_client = llm_client
        self.log_client = log_client
        self.msg_box = loop_msg_box
        self.pwd = pwd
        self.tools = tool_group or []
        self.tool_by_name = {tool.name: tool for tool in self.tools}
        self.messages = self._build_context()
        self.iteration = 0


    def _build_context(self) -> list[dict[str, Any]]:
        """Build the initial system message without adding a user message."""
        system_prompt = "\n\n".join(
            (RAW_SYSTEM_PROMPT.strip(), f"Current working directory: {self.pwd}")
        )
        return [{"role": "system", "content": system_prompt}]


    def add_user_message(self, content: str):
        self.messages.append({"role": "user", "content": content})


    def run_loop(self) -> str:
        """Start the main loop of the agent."""
        self.iteration = 0
        self.log_client.write_log(
            event="run_start", model_id=self.llm_client.model_name, workdir=self.pwd
        )
        reason = "error"
        try:
            while True:
                self.iteration += 1
                response = self.invoke_llm()
                message = response.choices[0].message
                self.messages.append(message.model_dump(exclude_none=True))

                tool_calls = message.tool_calls or []
                if not tool_calls:
                    reason = "no_tool_calls"
                    return reason

                self._parse_tools(tool_calls)
                if MSG.Task_Complete in self.msg_box.emit_messages():
                    reason = "done"
                    return reason
        finally:
            self.log_client.write_log(
                event="run_end", reason=reason, num_iterations=self.iteration
            )


    def invoke_llm(self):
        """Invoke the LLM with the current messages and return the response."""
        for attempt in range(3):
            try:
                response = self.llm_client.invoke(
                    self.messages,
                    tools=[tool.definition() for tool in self.tools] or None,
                )
                break
            except (APIConnectionError, APIStatusError) as exc:
                retryable = isinstance(exc, APIConnectionError) or exc.status_code in {408, 409, 429} or exc.status_code >= 500
                if not retryable or attempt == 2:
                    raise
                backoff_s = 2 ** attempt
                self.log_client.write_log(
                    event="api_retry", iteration=self.iteration,
                    error=str(exc), backoff_s=backoff_s,
                )
                time.sleep(backoff_s)

        usage = response.usage
        self.log_client.write_log(
            event="api_request", iteration=self.iteration,
            prompt_tokens=usage.prompt_tokens if usage else None,
            completion_tokens=usage.completion_tokens if usage else None,
            total_tokens=usage.total_tokens if usage else None,
        )

        return response

    def _parse_tools(self, tool_calls: list[Any]) -> None:
        """Execute model-requested tools and append their result messages."""
        for tool_call in tool_calls:
            tool_name = tool_call.function.name
            arguments = tool_call.function.arguments
            try:
                arguments = json.loads(arguments)
            except (ValueError, TypeError):
                # Keep malformed arguments in the trace so the failure is explainable.
                pass
            self.log_client.write_log(
                event="tool_call", iteration=self.iteration,
                tool_name=tool_name, arguments=arguments,
            )
            is_error = False
            try:
                if not isinstance(arguments, dict):
                    raise ValueError("tool arguments must be a JSON object")
                result = self.tool_by_name[tool_name].use(**arguments)
                if tool_name == "bash" and isinstance(result, str):
                    # Bash reports failure/timeout in its text instead of raising.
                    is_error = result.split("\n", 1)[0] != "exit code: 0"
            except Exception as exc:
                result = f"Tool error: {type(exc).__name__}: {exc}"
                is_error = True

            if not isinstance(result, str):
                result = json.dumps(result, ensure_ascii=False, default=str)
            self.log_client.write_log(
                event="tool_result", iteration=self.iteration,
                tool_name=tool_name, result=result, is_error=is_error,
            )
            self.messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result,
                }
            )
