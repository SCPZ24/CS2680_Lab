from __future__ import annotations

from enum import Enum
import json
from typing import Any, TYPE_CHECKING

try:
    from integrations import LLMClient
    from prompts import RAW_SYSTEM_PROMPT
except ModuleNotFoundError:  # Allows ``import assignment2.runtime`` as well.
    from .integrations import LLMClient
    from .prompts import RAW_SYSTEM_PROMPT

if TYPE_CHECKING:
    try:
        from tools import Tool
    except ModuleNotFoundError:
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
    def __init__(self, llm_client: LLMClient, pwd: str, loop_msg_box: LoopMsgBox, log_path: str | None = None, tool_group: list[Tool] | None = None):
        self.llm_client = llm_client
        self.log_path = log_path
        self.msg_box = loop_msg_box
        self.pwd = pwd
        self.tools = tool_group or []
        self.tool_by_name = {tool.name: tool for tool in self.tools}
        self.messages = self._build_context()


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
        while True:
            response = self.invoke_llm()
            message = response.choices[0].message
            self.messages.append(message.model_dump(exclude_none=True))

            tool_calls = message.tool_calls or []
            if not tool_calls:
                return "no_tool_calls"

            self._parse_tools(tool_calls)
            if self.log_path is not None:
                pass

            if MSG.Task_Complete in self.msg_box.emit_messages():
                return "done"


    def invoke_llm(self):
        """Invoke the LLM with the current messages and return the response."""
        response = self.llm_client.invoke(
            self.messages,
            tools=[tool.definition() for tool in self.tools] or None,
        )

        return response

    def _parse_tools(self, tool_calls: list[Any]) -> None:
        """Execute model-requested tools and append their result messages."""
        for tool_call in tool_calls:
            tool_name = tool_call.function.name
            try:
                arguments = json.loads(tool_call.function.arguments)
                if not isinstance(arguments, dict):
                    raise ValueError("tool arguments must be a JSON object")
                result = self.tool_by_name[tool_name].use(**arguments)
            except Exception as exc:
                result = f"Tool error: {type(exc).__name__}: {exc}"

            if not isinstance(result, str):
                result = json.dumps(result, ensure_ascii=False, default=str)
            self.messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result,
                }
            )
