"""Translate our text/tool conversation to /v1/messages and back.

Reuse the existing HTTP client and expose the same response shape to all loops.
The original Messages content blocks survive round trips (including thinking).
"""

import json


def call_messages(client, model_id, messages, *, tools=None, **kwargs):
    from openai.types.chat import ChatCompletion

    system, turns = [], []
    for message in messages:
        role = message["role"]
        content = message.get("content")
        if role in ("system", "developer"):
            if content:
                system.append({"type": "text", "text": content})
            continue
        if role == "tool":
            role = "user"
            blocks = [{"type": "tool_result", "tool_use_id": message["tool_call_id"],
                       "content": content or ""}]
        elif role == "assistant" and message.get("messages_content") is not None:
            blocks = message["messages_content"]
        elif role in ("user", "assistant"):
            if content is not None and not isinstance(content, str):
                raise ValueError("Messages adapter currently supports text and tool messages only")
            blocks = [{"type": "text", "text": content}] if content else []
            for call in message.get("tool_calls") or []:
                blocks.append({"type": "tool_use", "id": call["id"],
                               "name": call["function"]["name"],
                               "input": json.loads(call["function"]["arguments"])})
        else:
            raise ValueError(f"Unsupported message role: {role}")
        if turns and turns[-1]["role"] == role:
            turns[-1]["content"].extend(blocks)
        else:
            turns.append({"role": role, "content": list(blocks)})

    options = dict(kwargs)
    options.pop("stream", None)  # Harness accepts non-streaming calls only.
    max_tokens = options.pop("max_completion_tokens", options.pop("max_tokens", 4096))
    choice = options.pop("tool_choice", None)
    body = dict(options, model=model_id, messages=turns, max_tokens=max_tokens, stream=False)
    if system:
        body["system"] = system
    if tools:
        body["tools"] = [{"name": tool["function"]["name"],
                          "description": tool["function"].get("description", ""),
                          "input_schema": tool["function"]["parameters"]} for tool in tools]
    if choice is not None:
        if isinstance(choice, str):
            body["tool_choice"] = {"type": "any" if choice == "required" else choice}
        else:
            body["tool_choice"] = {"type": "tool", "name": choice["function"]["name"]}
    raw = client.post("/messages", cast_to=dict, body=body,
                      options={"headers": {"anthropic-version": "2023-06-01"}})
    if raw.get("type") != "message" or not isinstance(raw.get("content"), list):
        raise ValueError("Invalid Messages API response")
    blocks = raw["content"]
    calls = [{"id": b["id"], "type": "function", "function": {
        "name": b["name"], "arguments": json.dumps(b["input"], ensure_ascii=False)}}
        for b in blocks if b["type"] == "tool_use"]
    text = "".join(b["text"] for b in blocks if b["type"] == "text")
    usage = raw.get("usage") or {}
    normalized_usage = None
    if usage.get("input_tokens") is not None and usage.get("output_tokens") is not None:
        cached = usage.get("cache_read_input_tokens", 0) or 0
        # Messages input_tokens excludes cache reads/writes; our accounting includes them.
        prompt = usage["input_tokens"] + cached + (usage.get("cache_creation_input_tokens", 0) or 0)
        output = usage["output_tokens"]
        normalized_usage = {"prompt_tokens": prompt, "completion_tokens": output,
                            "total_tokens": prompt + output,
                            "prompt_tokens_details": {"cached_tokens": cached}}
    reason = {"end_turn": "stop", "stop_sequence": "stop", "tool_use": "tool_calls",
              "max_tokens": "length", "refusal": "content_filter"}.get(raw.get("stop_reason"), "length")
    return ChatCompletion.model_validate({
        "id": raw.get("id", "messages-response"), "object": "chat.completion", "created": 0,
        "model": raw.get("model", model_id), "usage": normalized_usage,
        "choices": [{"index": 0, "finish_reason": reason, "message": {
            "role": "assistant", "content": text or None, "tool_calls": calls or None,
            "messages_content": blocks}}],
    })
