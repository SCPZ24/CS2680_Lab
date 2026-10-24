"""External service integrations used by the agent harness."""

from __future__ import annotations

import os
from typing import Any

from openai import OpenAI


class LLMClient:
    """Small wrapper around the course's OpenAI-compatible chat endpoint."""

    def __init__(
        self,
        base_url: str = "https://api.cs2680.com/v1",
        api_key: str | None = None,
        model_name: str = "qwen3.6-35b-a3b",
        enable_thinking: bool | None = None,
    ):
        api_key = api_key or os.environ.get("CS2680_API_KEY")
        if not api_key:
            raise ValueError("CS2680_API_KEY is required")

        self.model_name = model_name
        self.enable_thinking = enable_thinking
        # Retries belong in madsLoop so every retry can be written to the
        # required agent log instead of being hidden inside the SDK.
        self.client = OpenAI(
            base_url=base_url,
            api_key=api_key,
            max_retries=0,
        )

    def invoke(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> Any:
        """Return one complete, non-streaming chat completion response."""
        request: dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "stream": False,
        }
        if tools is not None:
            request["tools"] = tools
        if self.enable_thinking is not None:
            request["extra_body"] = {"enable_thinking": self.enable_thinking}
        return self.client.chat.completions.create(**request)
