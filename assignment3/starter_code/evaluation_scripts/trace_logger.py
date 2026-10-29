"""JSONL run-trace logger (assignment section 0.3).

Adapted from the course logger for local self-study: api_request additionally records
model_tier and cached_prompt_tokens for YAML-based pricing. The official leaderboard uses
its own copy. Your agent logs through dispatcher.logger(k), a TraceLogger writing
madsOpt_logs/<k>/run.jsonl.

Writes ./madsOpt_logs/run-<utc timestamp>.jsonl in the CURRENT working
directory (not the repo checkout), one JSON object per line. Every line has
`timestamp` (ISO-8601) and `event`, plus event-specific fields. A no-op unless
--log was passed.
"""

import datetime
import json
import os


def _now_iso() -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .isoformat(timespec="milliseconds")
    )


class TraceLogger:
    def __init__(self, enabled: bool):
        self.enabled = enabled
        self._fh = None
        if enabled:
            os.makedirs("madsOpt_logs", exist_ok=True)
            stamp = datetime.datetime.now(datetime.timezone.utc).strftime(
                "%Y%m%dT%H%M%SZ"
            )
            path = os.path.join("madsOpt_logs", f"run-{stamp}.jsonl")
            # line-buffered so the trace survives a crash mid-run
            self._fh = open(path, "w", buffering=1)

    def _emit(self, event: str, fields: dict):
        if not self._fh:
            return
        line = {"timestamp": _now_iso(), "event": event}
        line.update(fields)
        self._fh.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")

    # -- one method per graded event type ---------------------------------

    def event(self, event: str, **fields):
        self._emit(event, fields)

    def run_start(self, workdir: str):
        self._emit("run_start", {"workdir": workdir})

    # model_id: the model this request was sent to; a run may use several (cascades,
    # subagents), and cost is computed per request from it.
    def api_request(self, iteration: int, prompt_tokens, completion_tokens, total_tokens, model_id: str,
                    *, cached_prompt_tokens=0, model_tier=None):
        self._emit(
            "api_request",
            {
                "iteration": iteration,
                "model_id": model_id,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
                "cached_prompt_tokens": cached_prompt_tokens,
                "model_tier": model_tier,
            },
        )

    def api_retry(self, iteration: int, error: str, backoff_s: float):
        self._emit(
            "api_retry",
            {"iteration": iteration, "error": error, "backoff_s": backoff_s},
        )

    def tool_call(self, iteration: int, tool_name: str, arguments):
        self._emit(
            "tool_call",
            {"iteration": iteration, "tool_name": tool_name, "arguments": arguments},
        )

    def tool_result(self, iteration: int, tool_name: str, result: str, is_error: bool):
        self._emit(
            "tool_result",
            {
                "iteration": iteration,
                "tool_name": tool_name,
                "result": result,
                "is_error": is_error,
            },
        )

    def run_end(self, reason: str, num_iterations: int):
        self._emit("run_end", {"reason": reason, "num_iterations": num_iterations})

    def close(self):
        if self._fh:
            self._fh.close()
            self._fh = None
