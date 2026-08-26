from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class TokenEstimate:
    """Result of estimating the input tokens for an LLM request."""

    total_prompt_tokens: int | None
    model: str
    error: str = ""


class TokenEstimator:
    """Estimates prompt tokens with LiteLLM for configured providers."""

    _PROVIDER_PREFIXES = {
        "azure_openai": "azure",
        "gemini": "gemini",
        "claude": "anthropic",
        "ollama": "ollama",
        "copilot": "github",
        "bedrock": "bedrock",
    }

    def estimate(
        self,
        provider: str,
        model: str,
        messages: list[dict[str, str]],
    ) -> TokenEstimate:
        """Estimates input tokens, falling back to the unqualified model.

        Args:
            provider: Provider configured for the LLM request.
            model: Effective model name, including configured defaults.
            messages: Complete chat payload sent to the provider.

        Returns:
            A token estimate, or an unavailable result when LiteLLM cannot
            resolve either the provider-qualified or raw model name.
        """
        qualified_model = self._qualified_model(provider, model)
        errors: list[str] = []

        for candidate in dict.fromkeys((qualified_model, model)):
            try:
                token_count = self._count(candidate, messages)
                return TokenEstimate(token_count, candidate)
            except Exception as exc:
                errors.append(f"{candidate}: {exc}")

        try:
            token_count = self._count_locally(model, messages)
            return TokenEstimate(token_count, "local/tiktoken")
        except Exception as exc:
            errors.append(f"local/tiktoken: {exc}")

        return TokenEstimate(
            total_prompt_tokens=None,
            model=qualified_model,
            error="; ".join(errors),
        )

    @classmethod
    def _qualified_model(cls, provider: str, model: str) -> str:
        """Builds the LiteLLM model name for a configured provider."""
        normalized_provider = provider.lower().strip()
        prefix = cls._PROVIDER_PREFIXES.get(normalized_provider)
        if not prefix:
            return model
        return f"{prefix}/{model}"

    @staticmethod
    def _count(model: str, messages: list[dict[str, str]]) -> int:
        """Calls LiteLLM lazily so importing the CLI remains lightweight."""
        from litellm import token_counter

        return int(token_counter(model=model, messages=messages))

    @staticmethod
    def _count_locally(model: str, messages: list[dict[str, str]]) -> int:
        """Counts message text locally when LiteLLM has no model support."""
        import tiktoken

        try:
            encoding = tiktoken.encoding_for_model(model)
        except KeyError:
            encoding = tiktoken.get_encoding("cl100k_base")

        serialized_messages = "\n".join(
            f"{message.get('role', '')}: {message.get('content', '')}"
            for message in messages
        )
        return len(encoding.encode(serialized_messages))