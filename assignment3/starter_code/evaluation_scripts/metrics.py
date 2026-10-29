"""Local self-study metrics, computed after the agent stops. Prices are USD / 1M tokens."""

import json
from pathlib import Path

from src.config import PRICE_FIELDS


def summarize_usage(path, models, *, opened=True):
    """Count reported usage only. Unknown prices/usage never silently become free calls.

    prompt_tokens includes cached input; completion_tokens includes reasoning tokens.
    Providers omitting cached_tokens are treated as reporting zero cache hits.
    """
    result = dict(api_requests=0, prompt_tokens=0, cached_prompt_tokens=0,
                  completion_tokens=0, total_tokens=0, missing_usage_requests=0,
                  unpriced_requests=0, trace_errors=0, trace_complete=not opened,
                  api_starts=0, api_errors=0, pending_api_requests=0, known_cost_usd=0.0)
    try:
        trace = Path(path).open(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        trace = ()
    try:
        for line in trace:
            try:
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise ValueError("not an event")
            except ValueError:
                result["trace_errors"] += 1
                continue
            if event.get("event") == "run_end":
                result["trace_complete"] = True
            if event.get("event") == "api_start":
                result["api_starts"] += 1
            if event.get("event") == "api_error":
                result["api_errors"] += 1
            if event.get("event") != "api_request":
                continue
            result["api_requests"] += 1
            tokens = {key: event.get(key, 0 if key == "cached_prompt_tokens" else None)
                      for key in ("prompt_tokens", "cached_prompt_tokens", "completion_tokens")}
            valid = all(type(n) is int and n >= 0 for n in tokens.values())
            valid = valid and tokens["cached_prompt_tokens"] <= tokens["prompt_tokens"]
            if not valid:
                result["missing_usage_requests"] += 1
                continue
            for key, n in tokens.items():
                result[key] += n
            result["total_tokens"] += tokens["prompt_tokens"] + tokens["completion_tokens"]
            tier = event.get("model_tier")
            candidates = [model for name, model in models.items()
                          if model["model_name"] == event.get("model_id")
                          and (tier is None or name == tier)]
            counts = (tokens["prompt_tokens"] - tokens["cached_prompt_tokens"],
                      tokens["cached_prompt_tokens"], tokens["completion_tokens"])
            if len(candidates) != 1 or any(
                    n and candidates[0].get(field) is None
                    for field, n in zip(PRICE_FIELDS, counts)):
                result["unpriced_requests"] += 1
                continue
            result["known_cost_usd"] += sum(
                n * candidates[0].get(field, 0) / 1_000_000
                for field, n in zip(PRICE_FIELDS, counts) if n)
    finally:
        if hasattr(trace, "close"):
            trace.close()
    result["pending_api_requests"] = max(0, result["api_starts"] - result["api_requests"] - result["api_errors"])
    result["usage_complete"] = (result["trace_complete"] and not result["trace_errors"]
                                and not result["missing_usage_requests"] and not result["api_errors"]
                                and not result["pending_api_requests"])
    result["cost_complete"] = result["usage_complete"] and not result["unpriced_requests"]
    result["cost_usd"] = result["known_cost_usd"] if result["cost_complete"] else None
    return result


def collect_metrics(tasks, models, trace_root):
    rows = []
    for task in tasks:
        usage = summarize_usage(Path(trace_root) / str(task.k) / "run.jsonl", models,
                                opened=task.e2e_t0 is not None)
        rows.append(dict(task=task.k, key=task.key, opened=task.e2e_t0 is not None,
                         finished=task.finalized, end_reason=task.end_reason,
                         resolved=bool(task.last and task.last[0]),
                         evaluations=task.evaluations, failed_evaluations=task.failed_evaluations,
                         rework_count=task.rework_count,
                         e2e_seconds=(task.e2e_t1 - task.e2e_t0 if task.e2e_t1 is not None else None),
                         **usage))
    started = [task.e2e_t0 for task in tasks if task.e2e_t0 is not None]
    ended = [task.e2e_t1 for task in tasks if task.e2e_t1 is not None]
    total = {key: sum(row[key] for row in rows) for key in (
        "evaluations", "failed_evaluations", "rework_count", "api_requests", "prompt_tokens",
        "cached_prompt_tokens", "completion_tokens", "total_tokens", "missing_usage_requests",
        "unpriced_requests", "trace_errors", "known_cost_usd", "api_starts", "api_errors", "pending_api_requests")}
    total["e2e_seconds"] = max(ended) - min(started) if started and len(started) == len(ended) else None
    total["usage_complete"] = all(row["usage_complete"] for row in rows)
    total["cost_complete"] = all(row["cost_complete"] for row in rows)
    total["cost_usd"] = total["known_cost_usd"] if total["cost_complete"] else None
    # Never persist base URLs or API keys in the report.
    pricing = {tier: {"model_name": model["model_name"],
                      **{field: model.get(field) for field in PRICE_FIELDS}}
               for tier, model in models.items()}
    return dict(currency="USD", price_unit="per_million_tokens", pricing=pricing, totals=total, tasks=rows)
