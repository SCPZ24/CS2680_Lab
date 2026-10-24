"""JSONL event logging for the assignment's runtime trace schema."""

from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4
class LogClient:
    def __init__(self):
        pass

    def write_log(self, **kwargs):
        pass



class EmptyLogClient(LogClient):
    def write_log(self, **kwargs):
        pass



class RuntimeLogClient(LogClient):
    EVENT_FIELDS = {
        "run_start": {"model_id", "workdir"},
        "api_request": {"iteration", "prompt_tokens", "completion_tokens", "total_tokens"},
        "api_retry": {"iteration", "error", "backoff_s"},
        "tool_call": {"iteration", "tool_name", "arguments"},
        "tool_result": {"iteration", "tool_name", "result", "is_error"},
        "run_end": {"reason", "num_iterations"},
    }

    def __init__(self, log_path: str | None = None):
        super().__init__()
        self.log_path = Path(log_path) if log_path else Path("madsLoop_logs") / f"run_{uuid4().hex}.jsonl"
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        # Refuse to mix multiple runs in an existing trace.
        self.log_path.touch(exist_ok=False)


    def write_log(self, *, event: str, **fields):
        expected = self.EVENT_FIELDS.get(event)
        if expected is None or set(fields) != expected:
            raise ValueError(f"Invalid fields for log event {event!r}: {sorted(fields)}")
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **fields,
        }
        line = json.dumps(entry, ensure_ascii=False, allow_nan=False)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
