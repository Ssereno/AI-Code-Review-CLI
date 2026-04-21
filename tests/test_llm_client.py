"""Tests for the LLM client module."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from src.config import ReviewConfig
from src.llm_client import LLMClient, LLMError, build_user_message, get_pr_comment_prompt, get_scope_guidance, get_system_prompt


class FakeResponse:
    """Minimal HTTP response stub for provider tests."""

    def __init__(self, *, status_code: int = 200, json_data: object | None = None, text: str = "", headers: dict[str, str] | None = None, exc: Exception | None = None) -> None:
        self.status_code = status_code
        self._json_data = json_data if json_data is not None else {}
        self.text = text
        self.headers = headers or {}
        self._exc = exc

    def raise_for_status(self) -> None:
        """Raise the configured exception when requested."""
        if self._exc is not None:
            raise self._exc

    def json(self) -> object:
        """Return the configured JSON payload."""
        return self._json_data


def install_requests(monkeypatch: pytest.MonkeyPatch, response: FakeResponse) -> ModuleType:
    """Install a fake requests module that returns the provided response."""
    module = ModuleType("requests")
    module._calls = []

    def post(url: str, headers: dict | None = None, json: dict | None = None, timeout: int = 0) -> FakeResponse:
        module._calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return response

    module.post = post
    module.exceptions = SimpleNamespace(
        ConnectionError=type("ConnectionError", (Exception,), {}),
        Timeout=type("Timeout", (Exception,), {}),
        RequestException=type("RequestException", (Exception,), {}),
    )
    monkeypatch.setitem(sys.modules, "requests", module)
    return module


def make_llm_config(**changes: object) -> ReviewConfig:
    """Build a valid baseline LLM configuration."""
    config = ReviewConfig(
        llm_provider="openai",
        api_key="secret",
        model="gpt-4o-mini",
        max_tokens=256,
        temperature=0.2,
        review_language="en",
        verbosity="detailed",
    )
    for key, value in changes.items():
        setattr(config, key, value)
    return config


def test_prompt_helpers_select_expected_language_and_scope() -> None:
    """It should select prompts and scope guidance consistently."""
    assert "experienced code reviewer" in get_system_prompt("quick", "en")
    assert "JSON" in get_pr_comment_prompt("pt")
    assert "full_code" in get_scope_guidance("full_code", "en")
    assert "file e line" in get_scope_guidance("diff_only", "pt", structured=True)
    assert "added lines" in get_scope_guidance("diff_only", "en")


def test_build_user_message_includes_files_and_context() -> None:
    """It should compose files, context and diff sections."""
    message = build_user_message(
        diff="+print('x')",
        files_summary=[{"file": "src/app.py", "additions": 1, "deletions": 0}],
        context="Please focus on safety.",
    )

    assert "Changed Files" in message
    assert "src/app.py" in message
    assert "Please focus on safety." in message
    assert "```diff" in message


def test_load_custom_prompt_text_variants(tmp_path: Path, mocker) -> None:
    """It should handle empty, missing and readable prompt files."""
    config = make_llm_config(custom_prompt_file="")
    client = LLMClient(config)
    assert client._load_custom_prompt_text() == ""

    config.custom_prompt_file = str(tmp_path / "missing.md")
    assert client._load_custom_prompt_text() == ""

    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("extra instructions", encoding="utf-8")
    config.custom_prompt_file = str(prompt_file)
    assert client._load_custom_prompt_text() == "extra instructions"

    mocker.patch("builtins.open", side_effect=OSError("boom"))
    assert client._load_custom_prompt_text() == ""


def test_review_dispatches_and_merges_custom_prompt(mocker, tmp_path: Path) -> None:
    """It should route reviews to the configured provider and merge custom context."""
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("Always mention tests", encoding="utf-8")
    config = make_llm_config(custom_prompt_file=str(prompt_file))
    client = LLMClient(config)
    openai = mocker.patch("src.llm_client.LLMClient._call_openai", return_value="review text")

    result = client.review("+code", [{"file": "a.py", "additions": 1, "deletions": 0}], context="Focus on bugs")

    assert result == "review text"
    system_prompt, user_message = openai.call_args.args[:2]
    assert "Custom user instructions" in system_prompt
    assert "Custom context loaded from" in user_message


def test_review_raises_for_unsupported_provider() -> None:
    """It should reject unsupported providers before any HTTP call."""
    client = LLMClient(make_llm_config(llm_provider="unknown"))

    with pytest.raises(LLMError, match="Unsupported provider"):
        client.review("+code", [])


def test_review_pr_structured_dispatches_and_parses(mocker) -> None:
    """It should dispatch structured reviews and normalize JSON comments."""
    client = LLMClient(make_llm_config(llm_provider="copilot"))
    copilot = mocker.patch(
        "src.llm_client.LLMClient._call_copilot",
        return_value='[{"file": "src/app.py", "line": 5, "type": "bug", "severity": "high", "comment": "boom", "suggestion": "fix", "reference": "Docs"}]',
    )

    comments = client.review_pr_structured("+code", [{"file": "a.py", "additions": 1, "deletions": 0}])

    assert copilot.called
    assert comments == [{"file": "src/app.py", "line": 5, "type": "bug", "severity": "high", "comment": "boom", "suggestion": "fix", "reference": "Docs"}]


def test_parse_structured_comments_handles_markdown_single_object_and_invalid_json() -> None:
    """It should parse code fences, single objects and invalid fallback payloads."""
    client = LLMClient(make_llm_config())
    fenced = client._parse_structured_comments(
        "```json\n[{\"file\": \"a.py\", \"line\": 1, \"comment\": \"x\"}]\n```"
    )
    single = client._parse_structured_comments(
        '{"file": "a.py", "line": 2, "type": "style", "severity": "low", "comment": "y"}'
    )
    fallback = client._parse_structured_comments("not json at all")

    assert fenced[0]["file"] == "a.py"
    assert single[0]["line"] == 2
    assert fallback[0]["comment"] == "not json at all"


def test_call_openai_builds_expected_payload_and_validates_configuration(mocker) -> None:
    """It should validate configuration and delegate HTTP calls for OpenAI APIs."""
    config = make_llm_config(model="gpt-4o")
    client = LLMClient(config)
    helper = mocker.patch("src.llm_client.LLMClient._http_openai_compatible", return_value="ok")

    assert client._call_openai("sys", "user") == "ok"
    url, headers, payload = helper.call_args.args
    assert url.endswith("/chat/completions")
    assert headers["Authorization"] == "Bearer secret"
    assert payload["model"] == "gpt-4o"

    azure_client = LLMClient(make_llm_config(api_base_url="https://azure.local/deployment"))
    helper.reset_mock(return_value=True)
    azure_client._call_openai("sys", "user", azure=True)
    azure_url, azure_headers, _ = helper.call_args.args
    assert "api-version=2024-02-01" in azure_url
    assert azure_headers["api-key"] == "secret"

    with pytest.raises(LLMError, match="OpenAI API key not configured"):
        LLMClient(make_llm_config(api_key="", openai_api_key=""))._call_openai("sys", "user")
    with pytest.raises(LLMError, match="Azure OpenAI requires API_BASE_URL"):
        LLMClient(make_llm_config(api_base_url=""))._call_openai("sys", "user", azure=True)


def test_http_openai_compatible_success_and_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """It should handle OpenAI-compatible HTTP success and major failure modes."""
    success = FakeResponse(json_data={"choices": [{"message": {"content": "ok"}}]})
    requests_module = install_requests(monkeypatch, success)
    client = LLMClient(make_llm_config())
    assert client._http_openai_compatible("https://api.local", {}, {}) == "ok"
    assert requests_module._calls[0]["url"] == "https://api.local"

    for status, fragment in [(401, "invalid or expired"), (429, "Rate limit exceeded"), (500, "API error (500)")]:
        install_requests(monkeypatch, FakeResponse(status_code=status, text="boom"))
        with pytest.raises(LLMError, match=re.escape(fragment)):
            client._http_openai_compatible("https://api.local", {}, {})

    install_requests(monkeypatch, FakeResponse(json_data={"no_choices": []}))
    with pytest.raises(LLMError, match="Unexpected API response"):
        client._http_openai_compatible("https://api.local", {}, {})

    requests_module = install_requests(monkeypatch, FakeResponse())
    requests_module.post = lambda *args, **kwargs: (_ for _ in ()).throw(requests_module.exceptions.ConnectionError())
    with pytest.raises(LLMError, match="Could not connect"):
        client._http_openai_compatible("https://api.local", {}, {})

    requests_module.post = lambda *args, **kwargs: (_ for _ in ()).throw(requests_module.exceptions.Timeout())
    with pytest.raises(LLMError, match="timed out"):
        client._http_openai_compatible("https://api.local", {}, {})

    requests_module.post = lambda *args, **kwargs: (_ for _ in ()).throw(requests_module.exceptions.RequestException("boom"))
    with pytest.raises(LLMError, match="HTTP request error"):
        client._http_openai_compatible("https://api.local", {}, {})


def test_gemini_provider_success_and_error_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """It should parse Gemini responses and convert common API failures."""
    response = FakeResponse(json_data={"candidates": [{"content": {"parts": [{"text": "gemini ok"}]}}]})
    install_requests(monkeypatch, response)
    client = LLMClient(make_llm_config(llm_provider="gemini", api_key="", gemini_api_key="gemini-secret", model="gemini-1.5-pro"))
    assert client._call_gemini("sys", "user") == "gemini ok"

    for status, payload, fragment in [
        (400, {"error": {"message": "bad request"}}, "Gemini error (400): bad request"),
        (403, {}, "insufficient permissions"),
        (429, {}, "rate limit exceeded"),
        (500, {}, "Gemini API error (500)"),
    ]:
        install_requests(monkeypatch, FakeResponse(status_code=status, json_data=payload, text="boom"))
        with pytest.raises(LLMError, match=re.escape(fragment)):
            client._call_gemini("sys", "user")

    requests_module = install_requests(monkeypatch, FakeResponse())
    requests_module.post = lambda *args, **kwargs: (_ for _ in ()).throw(requests_module.exceptions.ConnectionError())
    with pytest.raises(LLMError, match="Could not connect to Gemini"):
        client._call_gemini("sys", "user")


def test_claude_provider_success_and_error_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """It should parse Claude responses and convert common API failures."""
    install_requests(monkeypatch, FakeResponse(json_data={"content": [{"type": "text", "text": "claude ok"}]}))
    client = LLMClient(make_llm_config(llm_provider="claude", api_key="", anthropic_api_key="claude-secret", model="claude-3-sonnet"))
    assert client._call_claude("sys", "user") == "claude ok"

    install_requests(monkeypatch, FakeResponse(status_code=401))
    with pytest.raises(LLMError, match="Claude API key invalid"):
        client._call_claude("sys", "user")

    install_requests(monkeypatch, FakeResponse(status_code=429))
    with pytest.raises(LLMError, match="rate limit exceeded"):
        client._call_claude("sys", "user")

    install_requests(monkeypatch, FakeResponse(status_code=500, json_data={"error": {"message": "server blew up"}}, text="boom"))
    with pytest.raises(LLMError, match="server blew up"):
        client._call_claude("sys", "user")


def test_ollama_provider_fallback_and_errors(monkeypatch: pytest.MonkeyPatch, mocker) -> None:
    """It should use the OpenAI-compatible endpoint, native fallback and errors."""
    response = FakeResponse(json_data={"choices": [{"message": {"content": "ollama ok"}}]})
    install_requests(monkeypatch, response)
    client = LLMClient(make_llm_config(llm_provider="ollama", api_key="", api_base_url="http://localhost:11434", model="llama3"))
    assert client._call_ollama("sys", "user") == "ollama ok"

    fallback = mocker.patch("src.llm_client.LLMClient._call_ollama_native", return_value="native ok")
    install_requests(monkeypatch, FakeResponse(status_code=404, text="not found"))
    assert client._call_ollama("sys", "user") == "native ok"
    fallback.assert_called_once()

    install_requests(monkeypatch, FakeResponse(status_code=500, text="boom"))
    with pytest.raises(LLMError, match=re.escape("Ollama error (500)")):
        client._call_ollama("sys", "user")


def test_ollama_native_success_and_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """It should parse native Ollama responses and wrap errors."""
    install_requests(monkeypatch, FakeResponse(json_data={"message": {"content": "native"}}))
    client = LLMClient(make_llm_config(llm_provider="ollama", api_key=""))
    assert client._call_ollama_native("http://localhost:11434", "llama3", "sys", "user") == "native"

    install_requests(monkeypatch, FakeResponse(exc=RuntimeError("boom")))
    with pytest.raises(LLMError, match="Ollama native call"):
        client._call_ollama_native("http://localhost:11434", "llama3", "sys", "user")


def test_copilot_provider_success_and_error_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """It should parse Copilot responses and handle common API errors."""
    install_requests(monkeypatch, FakeResponse(json_data={"choices": [{"message": {"content": "copilot ok"}}]}))
    client = LLMClient(make_llm_config(llm_provider="copilot", api_key="", github_token="gh-token", api_base_url="https://models.github.ai/inference"))
    assert client._call_copilot("sys", "user") == "copilot ok"

    for status, headers, fragment in [
        (401, {}, "invalid or insufficient permissions"),
        (403, {}, "Access denied to GitHub Copilot"),
        (429, {"Retry-After": "90"}, "Wait 90s"),
        (500, {}, "GitHub Copilot API error (500): boom"),
    ]:
        install_requests(monkeypatch, FakeResponse(status_code=status, text="boom", headers=headers, json_data={"error": {"message": "boom"}}))
        with pytest.raises(LLMError, match=re.escape(fragment)):
            client._call_copilot("sys", "user")


def test_bedrock_provider_success_and_error_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """It should create a Bedrock client with configured credentials and parse output."""
    botocore_exceptions = ModuleType("botocore.exceptions")
    class FakeBotoCoreError(Exception):
        """Stub boto core exception."""
    class FakeClientError(Exception):
        """Stub client error exception."""
    botocore_exceptions.BotoCoreError = FakeBotoCoreError
    botocore_exceptions.ClientError = FakeClientError
    monkeypatch.setitem(sys.modules, "botocore.exceptions", botocore_exceptions)

    boto3_module = ModuleType("boto3")

    class FakeBedrockClient:
        """Stub Bedrock runtime client."""
        def converse(self, **kwargs: object) -> dict:
            return {"output": {"message": {"content": [{"text": "bedrock ok"}]}}}

    class FakeSession:
        """Stub boto3 session."""
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs
        def client(self, service_name: str, region_name: str) -> FakeBedrockClient:
            assert service_name == "bedrock-runtime"
            assert region_name == "us-east-1"
            return FakeBedrockClient()

    boto3_module.Session = FakeSession
    monkeypatch.setitem(sys.modules, "boto3", boto3_module)

    client = LLMClient(
        make_llm_config(
            llm_provider="bedrock",
            api_key="",
            model="anthropic.claude-3-5-sonnet",
            bedrock_region="us-east-1",
            bedrock_profile="dev",
            bedrock_access_key_id="access",
            bedrock_secret_access_key="secret",
            bedrock_session_token="token",
        )
    )
    assert client._call_bedrock("sys", "user") == "bedrock ok"

    with pytest.raises(LLMError, match="requires bedrock.region"):
        LLMClient(make_llm_config(llm_provider="bedrock", api_key="", bedrock_region=""))._call_bedrock("sys", "user")

    class BrokenSession(FakeSession):
        """Session that raises provider-side failures."""
        def client(self, service_name: str, region_name: str) -> FakeBedrockClient:
            raise FakeClientError("boom")

    boto3_module.Session = BrokenSession
    with pytest.raises(LLMError, match="Error calling AWS Bedrock"):
        client._call_bedrock("sys", "user")