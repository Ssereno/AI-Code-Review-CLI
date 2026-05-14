"""Tests for PR token and cost usage tracking."""

from __future__ import annotations

import json

from src.usage_tracker import (
    TokenUsage,
    append_usage_record,
    build_pr_usage_record,
    estimate_text_tokens,
    find_model_pricing,
    load_usage_records,
    summarize_usage_by_pr,
)


def test_estimate_text_tokens_uses_simple_character_heuristic() -> None:
    """It should return a stable fallback estimate."""
    assert estimate_text_tokens("abcd") == 1
    assert estimate_text_tokens("abcde") == 2


def test_build_pr_usage_record_aggregates_tokens_and_cost() -> None:
    """It should summarize per-call tokens and configured model pricing."""
    events = [
        TokenUsage(
            provider="openai",
            model="gpt-4o-mini",
            operation="general_review",
            prompt_tokens=1_000,
            completion_tokens=500,
        ),
        TokenUsage(
            provider="openai",
            model="gpt-4o-mini",
            operation="structured_comments",
            prompt_tokens=2_000,
            completion_tokens=250,
        ),
    ]

    record = build_pr_usage_record(
        repository="repo-a",
        pr_id=42,
        provider="openai",
        model="gpt-4o-mini",
        review_scope="diff_only",
        verbosity="detailed",
        dry_run=True,
        comments_generated=3,
        events=events,
        pricing_config={
            "openai": {
                "gpt-4o-mini": {
                    "input_per_1m": 0.15,
                    "output_per_1m": 0.60,
                    "currency": "USD",
                }
            }
        },
    )

    assert record["pull_request_id"] == 42
    assert record["tokens"]["prompt_tokens"] == 3_000
    assert record["tokens"]["completion_tokens"] == 750
    assert record["tokens"]["total_tokens"] == 3_750
    assert record["cost"]["amount"] == 0.0009
    assert record["calls"][0]["operation"] == "general_review"


def test_build_pr_usage_record_reports_missing_pricing() -> None:
    """It should still store tokens when no matching price is configured."""
    event = TokenUsage(
        provider="bedrock",
        model="model-x",
        operation="general_review",
        prompt_tokens=10,
        completion_tokens=5,
    )

    record = build_pr_usage_record(
        repository="repo-a",
        pr_id=7,
        provider="bedrock",
        model="model-x",
        review_scope="diff_only",
        verbosity="quick",
        dry_run=False,
        comments_generated=1,
        events=[event],
        pricing_config={},
    )

    assert record["cost"] is None
    assert record["missing_pricing"] == ["bedrock/model-x"]


def test_find_model_pricing_supports_default_and_case_insensitive_keys() -> None:
    """It should allow provider defaults and flexible config casing."""
    pricing = {
        "OpenAI": {
            "default": {
                "input_per_1m": 1,
                "output_per_1m": 2,
            }
        }
    }

    assert find_model_pricing(pricing, "openai", "unknown")["output_per_1m"] == 2


def test_append_usage_record_writes_jsonl(tmp_path) -> None:
    """It should append one JSON object per line."""
    target = tmp_path / "usage.jsonl"
    record = {"pull_request_id": 1, "tokens": {"total_tokens": 12}}

    resolved = append_usage_record(str(target), record)

    assert resolved == str(target)
    stored = json.loads(target.read_text(encoding="utf-8").strip())
    assert stored["tokens"]["total_tokens"] == 12


def test_load_usage_records_skips_invalid_json_lines(tmp_path) -> None:
    """It should load valid JSONL records and ignore malformed lines."""
    target = tmp_path / "usage.jsonl"
    target.write_text(
        '{"pull_request_id": 1}\nnot-json\n{"pull_request_id": 2}\n',
        encoding="utf-8",
    )

    records = load_usage_records(str(target))

    assert [record["pull_request_id"] for record in records] == [1, 2]
    assert load_usage_records(str(tmp_path / "missing.jsonl")) == []


def test_summarize_usage_by_pr_groups_records() -> None:
    """It should aggregate multiple review runs for the same PR."""
    records = [
        {
            "repository": "repo-a",
            "pull_request_id": 42,
            "timestamp": "2026-05-11T10:00:00+00:00",
            "provider": "openai",
            "model": "gpt-4o-mini",
            "comments_generated": 2,
            "tokens": {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
                "calls": 2,
                "estimated": False,
            },
            "cost": {"amount": 0.01, "currency": "USD", "estimated": False},
        },
        {
            "repository": "repo-a",
            "pull_request_id": 42,
            "timestamp": "2026-05-11T11:00:00+00:00",
            "provider": "openai",
            "model": "gpt-4o-mini",
            "comments_generated": 1,
            "tokens": {
                "prompt_tokens": 40,
                "completion_tokens": 10,
                "total_tokens": 50,
                "calls": 1,
                "estimated": True,
            },
            "cost": None,
            "missing_pricing": ["openai/gpt-4o-mini"],
        },
    ]

    summary = summarize_usage_by_pr(records)[0]

    assert summary["pull_request_id"] == 42
    assert summary["reviews"] == 2
    assert summary["comments_generated"] == 3
    assert summary["latest_timestamp"] == "2026-05-11T11:00:00+00:00"
    assert summary["tokens"]["total_tokens"] == 170
    assert summary["tokens"]["estimated"] is True
    assert summary["cost"]["amount"] == 0.01
    assert summary["missing_pricing"] == ["openai/gpt-4o-mini"]
