"""
Usage tracking for AI Code Review.

Stores per-review token usage and optional cost estimates in JSON Lines format.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any


DEFAULT_USAGE_FILE = ".ai-review-usage.jsonl"
TOKENS_PER_MILLION = 1_000_000


@dataclass
class TokenUsage:
    """Normalized token usage for one LLM API call."""

    provider: str
    model: str
    operation: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated: bool = False

    def __post_init__(self) -> None:
        self.prompt_tokens = max(0, int(self.prompt_tokens or 0))
        self.completion_tokens = max(0, int(self.completion_tokens or 0))
        self.total_tokens = max(0, int(self.total_tokens or 0))
        if self.total_tokens == 0:
            self.total_tokens = self.prompt_tokens + self.completion_tokens

    def to_dict(self) -> dict[str, Any]:
        """Returns a JSON-serializable representation."""
        return asdict(self)


def estimate_text_tokens(*texts: str) -> int:
    """Returns a rough token estimate when a provider omits usage metadata."""
    total_chars = sum(len(text or "") for text in texts)
    if total_chars <= 0:
        return 0
    return math.ceil(total_chars / 4)


def aggregate_usage(events: list[TokenUsage]) -> dict[str, Any]:
    """Aggregates many LLM call usage events."""
    return {
        "prompt_tokens": sum(event.prompt_tokens for event in events),
        "completion_tokens": sum(event.completion_tokens for event in events),
        "total_tokens": sum(event.total_tokens for event in events),
        "estimated": any(event.estimated for event in events),
        "calls": len(events),
    }


def build_pr_usage_record(
    *,
    repository: str,
    pr_id: int,
    provider: str,
    model: str,
    review_scope: str,
    verbosity: str,
    dry_run: bool,
    comments_generated: int,
    events: list[TokenUsage],
    pricing_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Builds the persisted usage record for one PR review."""
    calls = []
    total_cost = 0.0
    cost_count = 0
    missing_pricing: list[str] = []
    currency = "USD"

    for event in events:
        call = event.to_dict()
        pricing = find_model_pricing(pricing_config, event.provider, event.model)
        if pricing:
            amount = calculate_event_cost(event, pricing)
            currency = str(pricing.get("currency") or currency)
            call["cost"] = {
                "amount": round(amount, 8),
                "currency": currency,
                "estimated": event.estimated,
            }
            total_cost += amount
            cost_count += 1
        else:
            call["cost"] = None
            missing_pricing.append(f"{event.provider}/{event.model}")
        calls.append(call)

    record: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "repository": repository,
        "pull_request_id": pr_id,
        "provider": provider,
        "model": model,
        "review_scope": review_scope,
        "verbosity": verbosity,
        "dry_run": dry_run,
        "comments_generated": comments_generated,
        "tokens": aggregate_usage(events),
        "calls": calls,
    }

    if cost_count:
        record["cost"] = {
            "amount": round(total_cost, 8),
            "currency": currency,
            "estimated": any(event.estimated for event in events),
        }
    else:
        record["cost"] = None

    if missing_pricing:
        record["missing_pricing"] = sorted(set(missing_pricing))

    return record


def append_usage_record(file_path: str, record: dict[str, Any]) -> str:
    """Appends one JSON usage record and returns the resolved file path."""
    resolved = resolve_usage_file(file_path)
    directory = os.path.dirname(resolved)
    if directory:
        os.makedirs(directory, exist_ok=True)

    with open(resolved, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
        fh.write("\n")

    return resolved


def load_usage_records(file_path: str) -> list[dict[str, Any]]:
    """Loads usage records from a JSON Lines file."""
    resolved = resolve_usage_file(file_path)
    if not os.path.exists(resolved):
        return []

    records: list[dict[str, Any]] = []
    with open(resolved, "r", encoding="utf-8") as fh:
        for line in fh:
            text = line.strip()
            if not text:
                continue
            try:
                record = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                records.append(record)
    return records


def summarize_usage_by_pr(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregates persisted usage records by repository and pull request."""
    buckets: dict[tuple[str, int], dict[str, Any]] = {}

    for record in records:
        pr_id = _int_value(record.get("pull_request_id"))
        if pr_id <= 0:
            continue

        repository = str(record.get("repository") or "(unknown)")
        key = (repository, pr_id)
        bucket = buckets.setdefault(
            key,
            {
                "repository": repository,
                "pull_request_id": pr_id,
                "reviews": 0,
                "comments_generated": 0,
                "latest_timestamp": "",
                "providers": set(),
                "models": set(),
                "tokens": {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "calls": 0,
                    "estimated": False,
                },
                "_cost_amount": 0.0,
                "_cost_count": 0,
                "_cost_currencies": set(),
                "_cost_estimated": False,
                "_missing_pricing": set(),
            },
        )

        bucket["reviews"] += 1
        bucket["comments_generated"] += _int_value(record.get("comments_generated"))
        timestamp = str(record.get("timestamp") or "")
        if timestamp > bucket["latest_timestamp"]:
            bucket["latest_timestamp"] = timestamp

        if record.get("provider"):
            bucket["providers"].add(str(record["provider"]))
        if record.get("model"):
            bucket["models"].add(str(record["model"]))

        tokens = record.get("tokens") or {}
        if isinstance(tokens, dict):
            bucket["tokens"]["prompt_tokens"] += _int_value(tokens.get("prompt_tokens"))
            bucket["tokens"]["completion_tokens"] += _int_value(tokens.get("completion_tokens"))
            bucket["tokens"]["total_tokens"] += _int_value(tokens.get("total_tokens"))
            bucket["tokens"]["calls"] += _int_value(tokens.get("calls"))
            bucket["tokens"]["estimated"] = (
                bucket["tokens"]["estimated"]
                or bool(tokens.get("estimated"))
            )

        cost = record.get("cost")
        if isinstance(cost, dict):
            bucket["_cost_amount"] += _float_value(cost.get("amount"))
            bucket["_cost_count"] += 1
            if cost.get("currency"):
                bucket["_cost_currencies"].add(str(cost["currency"]))
            bucket["_cost_estimated"] = (
                bucket["_cost_estimated"]
                or bool(cost.get("estimated"))
            )

        missing_pricing = record.get("missing_pricing") or []
        if isinstance(missing_pricing, list):
            bucket["_missing_pricing"].update(str(item) for item in missing_pricing)

    summaries: list[dict[str, Any]] = []
    for bucket in buckets.values():
        currencies = sorted(bucket.pop("_cost_currencies"))
        cost_count = bucket.pop("_cost_count")
        cost_amount = bucket.pop("_cost_amount")
        cost_estimated = bucket.pop("_cost_estimated")
        missing_pricing = sorted(bucket.pop("_missing_pricing"))

        bucket["providers"] = sorted(bucket["providers"])
        bucket["models"] = sorted(bucket["models"])
        bucket["missing_pricing"] = missing_pricing
        if cost_count:
            bucket["cost"] = {
                "amount": round(cost_amount, 8),
                "currency": currencies[0] if len(currencies) == 1 else ("mixed" if currencies else "USD"),
                "estimated": cost_estimated,
            }
        else:
            bucket["cost"] = None
        summaries.append(bucket)

    return sorted(
        summaries,
        key=lambda item: (
            str(item["repository"]).lower(),
            int(item["pull_request_id"]),
        ),
    )


def resolve_usage_file(file_path: str) -> str:
    """Resolves the configured usage file path."""
    configured = file_path or DEFAULT_USAGE_FILE
    expanded = os.path.expandvars(os.path.expanduser(configured))
    if os.path.isabs(expanded):
        return expanded
    return os.path.abspath(expanded)


def find_model_pricing(
    pricing_config: dict[str, Any] | None,
    provider: str,
    model: str,
) -> dict[str, Any] | None:
    """Finds pricing for a provider/model from config."""
    if not isinstance(pricing_config, dict):
        return None

    provider = provider or ""
    model = model or ""

    direct = _lookup_mapping(pricing_config, f"{provider}/{model}")
    if _is_pricing(direct):
        return direct

    provider_pricing = _lookup_mapping(pricing_config, provider)
    if not isinstance(provider_pricing, dict):
        return None

    if _is_pricing(provider_pricing):
        return provider_pricing

    model_pricing = _lookup_mapping(provider_pricing, model)
    if _is_pricing(model_pricing):
        return model_pricing

    default_pricing = _lookup_mapping(provider_pricing, "default")
    if _is_pricing(default_pricing):
        return default_pricing

    return None


def calculate_event_cost(event: TokenUsage, pricing: dict[str, Any]) -> float:
    """Calculates cost for one usage event using per-million token prices."""
    input_price = _price_value(pricing, "input_per_1m", "prompt_per_1m", "input", "prompt")
    output_price = _price_value(
        pricing,
        "output_per_1m",
        "completion_per_1m",
        "output",
        "completion",
    )
    return (
        (event.prompt_tokens / TOKENS_PER_MILLION) * input_price
        + (event.completion_tokens / TOKENS_PER_MILLION) * output_price
    )


def _lookup_mapping(mapping: dict[str, Any], key: str) -> Any:
    if key in mapping:
        return mapping[key]

    lowered = key.lower()
    for existing_key, value in mapping.items():
        if str(existing_key).lower() == lowered:
            return value
    return None


def _is_pricing(value: Any) -> bool:
    return isinstance(value, dict) and (
        any(k in value for k in ("input_per_1m", "prompt_per_1m", "input", "prompt"))
        or any(k in value for k in ("output_per_1m", "completion_per_1m", "output", "completion"))
    )


def _price_value(pricing: dict[str, Any], *keys: str) -> float:
    for key in keys:
        if key in pricing and pricing[key] is not None:
            return float(pricing[key])
    return 0.0


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float_value(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
