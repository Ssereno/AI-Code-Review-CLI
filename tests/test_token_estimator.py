"""Tests for prompt token estimation."""

import pytest
from src.token_estimator import TokenEstimator


@pytest.mark.parametrize(
    ("provider", "model", "expected"),
    [
        ("openai", "gpt-4o-mini", "gpt-4o-mini"),
        ("azure_openai", "gpt-4o-mini", "azure/gpt-4o-mini"),
        ("gemini", "gemini-2.0-flash", "gemini/gemini-2.0-flash"),
        ("claude", "claude-3-5-sonnet-latest", "anthropic/claude-3-5-sonnet-latest"),
        ("ollama", "llama3", "ollama/llama3"),
        ("copilot", "gpt-4o", "github/gpt-4o"),
        ("bedrock", "anthropic.claude-3-5-sonnet", "bedrock/anthropic.claude-3-5-sonnet"),
    ],
)
def test_estimate_uses_provider_qualified_model(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    model: str,
    expected: str,
) -> None:
    """It should derive the LiteLLM model from provider and model."""
    captured: list[tuple[str, list[dict[str, str]]]] = []

    def fake_count(model: str, messages: list[dict[str, str]]) -> int:
        captured.append((model, messages))
        return 17

    monkeypatch.setattr(TokenEstimator, "_count", staticmethod(fake_count))
    messages = [{"role": "system", "content": "rules"}]

    result = TokenEstimator().estimate(provider, model, messages)

    assert result.total_prompt_tokens == 17
    assert result.model == expected
    assert captured == [(expected, messages)]


def test_estimate_falls_back_to_unqualified_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It should retry with the raw model when the qualified name fails."""
    attempted: list[str] = []

    def fake_count(model: str, messages: list[dict[str, str]]) -> int:
        attempted.append(model)
        if model == "gemini/custom-model":
            raise ValueError("unknown model")
        return 9

    monkeypatch.setattr(TokenEstimator, "_count", staticmethod(fake_count))

    result = TokenEstimator().estimate("gemini", "custom-model", [])

    assert result.total_prompt_tokens == 9
    assert result.model == "custom-model"
    assert attempted == ["gemini/custom-model", "custom-model"]


def test_estimate_is_unavailable_when_both_models_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It should return an unavailable result instead of raising."""
    monkeypatch.setattr(
        TokenEstimator,
        "_count",
        staticmethod(lambda model, messages: (_ for _ in ()).throw(ValueError("bad model"))),
    )
    monkeypatch.setattr(
        TokenEstimator,
        "_count_locally",
        staticmethod(lambda model, messages: (_ for _ in ()).throw(ImportError("tiktoken missing"))),
    )

    result = TokenEstimator().estimate("claude", "custom-model", [])

    assert result.total_prompt_tokens is None
    assert result.model == "anthropic/custom-model"
    assert "bad model" in result.error


def test_estimate_falls_back_to_local_tiktoken(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It should use local tiktoken when LiteLLM cannot count the model."""
    monkeypatch.setattr(
        TokenEstimator,
        "_count",
        staticmethod(lambda model, messages: (_ for _ in ()).throw(ValueError("bad model"))),
    )
    monkeypatch.setattr(TokenEstimator, "_count_locally", staticmethod(lambda model, messages: 23))

    result = TokenEstimator().estimate("bedrock", "application-profile-arn", [])

    assert result.total_prompt_tokens == 23
    assert result.model == "local/tiktoken"