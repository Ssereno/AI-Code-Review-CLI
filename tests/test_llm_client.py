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


def install_requests_raw(monkeypatch: pytest.MonkeyPatch, response: FakeResponse) -> ModuleType:
    """Install a fake requests module that captures raw-body (data=) POST calls."""
    module = ModuleType("requests")
    module._calls = []

    def post(url: str, headers: dict | None = None, data: str | None = None, timeout: int = 0) -> FakeResponse:
        module._calls.append({"url": url, "headers": headers, "data": data, "timeout": timeout})
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
    assert "code reviewer" in get_system_prompt("quick", "en").lower()
    assert "JSON" in get_pr_comment_prompt("pt")
    assert "full_code" in get_scope_guidance("full_code", "en")
    assert "file e line" in get_scope_guidance("diff_only", "pt", structured=True)
    assert "added lines" in get_scope_guidance("diff_only", "en")


# ---------------------------------------------------------------------------
# SYSTEM_PROMPTS / get_system_prompt — Phase 1 change validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("language", ["en", "pt"])
def test_get_system_prompt_quick_returns_non_empty_string(language: str) -> None:
    """quick prompts must return a non-empty string for every supported language."""
    prompt = get_system_prompt("quick", language)
    assert isinstance(prompt, str)
    assert len(prompt.strip()) > 0


def test_get_system_prompt_detailed_en_enforces_inline_comment_format() -> None:
    """detailed/en must specify the inline comment output format after Phase 1."""
    prompt = get_system_prompt("detailed", "en")
    # Must instruct the LLM to return only inline comments
    assert "inline comments" in prompt.lower()
    # Must include the exact per-line format marker
    assert "Line <line_number>" in prompt


def test_get_system_prompt_detailed_en_forbids_summary_sections() -> None:
    """detailed/en must explicitly forbid summary sections after Phase 1."""
    prompt = get_system_prompt("detailed", "en")
    assert "Do NOT produce summary sections" in prompt


def test_get_system_prompt_detailed_en_contains_no_focus_on_or_structured_review_language() -> None:
    """detailed/en must NOT contain generic 'Focus on' or 'structured review' language after Phase 1."""
    prompt = get_system_prompt("detailed", "en")
    assert "focus on" not in prompt.lower()
    assert "structured review" not in prompt.lower()


def test_get_system_prompt_detailed_pt_enforces_inline_comment_format() -> None:
    """detailed/pt must specify the inline comment output format in Portuguese after Phase 1."""
    prompt = get_system_prompt("detailed", "pt")
    assert "inline" in prompt.lower()
    # Must include the per-line format marker in Portuguese
    assert "Linha" in prompt


def test_get_system_prompt_detailed_pt_forbids_summary_sections() -> None:
    """detailed/pt must explicitly forbid summary sections in Portuguese after Phase 1."""
    prompt = get_system_prompt("detailed", "pt")
    assert "NÃO produzas" in prompt
    # The forbidden construct must mention summary sections
    assert "sumário" in prompt.lower() or "secções" in prompt.lower()


def test_get_system_prompt_detailed_pt_contains_no_focus_on_or_structured_review_language() -> None:
    """detailed/pt must NOT contain generic 'Focus on' or 'structured review' language after Phase 1."""
    prompt = get_system_prompt("detailed", "pt")
    assert "focus on" not in prompt.lower()
    assert "structured review" not in prompt.lower()


def test_get_system_prompt_security_en_mentions_security_specific_terms() -> None:
    """security/en must include core vulnerability types it is expected to check for."""
    prompt = get_system_prompt("security", "en")
    for term in ("SQL Injection", "XSS", "CSRF"):
        assert term in prompt, f"Security prompt missing expected term: '{term}'"


def test_get_system_prompt_unknown_verbosity_falls_back_to_detailed() -> None:
    """An unrecognised verbosity level must silently fall back to the 'detailed' prompt."""
    fallback = get_system_prompt("unknown_verbosity", "en")
    expected = get_system_prompt("detailed", "en")
    assert fallback == expected


def test_get_system_prompt_unknown_language_falls_back_to_pt() -> None:
    """An unrecognised language code must fall back to the Portuguese ('pt') prompt."""
    fallback = get_system_prompt("detailed", "xx")
    expected = get_system_prompt("detailed", "pt")
    assert fallback == expected


# ---------------------------------------------------------------------------
# get_scope_guidance — diff_only with FULL_FILE_CONTEXT
# ---------------------------------------------------------------------------

def test_get_scope_guidance_diff_only_references_full_file_context_markers() -> None:
    """diff_only guidance must reference FULL_FILE_CONTEXT_START and _END for all language/mode combos."""
    for lang in ("en", "pt"):
        for structured in (False, True):
            guidance = get_scope_guidance("diff_only", lang, structured=structured)
            assert "FULL_FILE_CONTEXT_START" in guidance, f"lang={lang} structured={structured}"
            assert "FULL_FILE_CONTEXT_END" in guidance, f"lang={lang} structured={structured}"


@pytest.mark.parametrize("language", ["en", "pt"])
def test_get_scope_guidance_diff_only_structured_demands_valid_line_number(language: str) -> None:
    """Structured diff_only guidance must instruct the LLM to supply a line number > 0."""
    guidance = get_scope_guidance("diff_only", language, structured=True)
    assert "(>0)" in guidance


def test_get_scope_guidance_diff_only_non_structured_focuses_on_changed_lines() -> None:
    """Non-structured diff_only guidance must tell the LLM to focus on changed/added lines."""
    guidance_en = get_scope_guidance("diff_only", "en", structured=False)
    guidance_pt = get_scope_guidance("diff_only", "pt", structured=False)

    assert "changed lines" in guidance_en.lower() or "added lines" in guidance_en.lower()
    assert "alteradas" in guidance_pt.lower() or "adicionadas" in guidance_pt.lower()


def test_get_scope_guidance_diff_only_structured_does_not_demand_line_in_non_structured() -> None:
    """Non-structured diff_only guidance must NOT mandate a minimum line number."""
    guidance_en = get_scope_guidance("diff_only", "en", structured=False)
    assert "(>0)" not in guidance_en


def test_get_scope_guidance_unknown_scope_falls_back_to_diff_only() -> None:
    """An unrecognised review_scope must fall through and return diff_only guidance."""
    guidance = get_scope_guidance("unknown_scope", "en")

    assert "diff_only" in guidance
    assert "FULL_FILE_CONTEXT_START" in guidance


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


def test_review_pr_dispatches_and_merges_custom_prompt(mocker, tmp_path: Path) -> None:
    """It should route reviews to the configured provider and merge custom context."""
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("Always mention tests", encoding="utf-8")
    config = make_llm_config(custom_prompt_file=str(prompt_file))
    client = LLMClient(config)
    openai = mocker.patch(
        "src.llm_client.LLMClient._call_openai",
        return_value='{"summary": "review text", "comments": []}',
    )

    result = client.review_pr("+code", [{"file": "a.py", "additions": 1, "deletions": 0}], context="Focus on bugs")

    assert result == ("review text", [])
    system_prompt, user_message = openai.call_args.args[:2]
    assert "Custom user instructions" in system_prompt
    assert "Custom context loaded from" in user_message

def test_review_omits_custom_instructions_header_when_filter_empties_prompt(mocker, tmp_path: Path) -> None:
    """If filtering removes all sections, the 'Custom user instructions' header should not appear."""
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text(
        "<!-- lang: java -->\n"
        "## Java Rules\n"
        "- Use streams.\n",
        encoding="utf-8",
    )
    config = make_llm_config(custom_prompt_file=str(prompt_file))
    client = LLMClient(config)
    openai = mocker.patch("src.llm_client.LLMClient._call_openai", return_value="review text")

    client.review("+code", [{"file": "component.ts", "additions": 1, "deletions": 0}])

    system_prompt, user_message = openai.call_args.args[:2]
    assert "Custom user instructions" not in system_prompt
    assert "Custom context loaded from" not in user_message
    
def test_review_filters_custom_prompt_sections_by_changed_extensions(mocker, tmp_path: Path) -> None:
    """Only sections matching the extensions of changed files (plus 'all') should be kept."""
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text(
        "<!-- lang: all -->\n"
        "## General\n"
        "- Always check for secrets.\n"
        "\n"
        "<!-- lang: cs -->\n"
        "## CSharp Rules\n"
        "- Avoid magic strings.\n"
        "\n"
        "<!-- lang: py -->\n"
        "## Python Rules\n"
        "- Use type hints.\n",
        encoding="utf-8",
    )
    config = make_llm_config(custom_prompt_file=str(prompt_file))
    client = LLMClient(config)
    openai = mocker.patch("src.llm_client.LLMClient._call_openai", return_value="review text")

    client.review("+code", [{"file": "app.py", "additions": 1, "deletions": 0}])

    system_prompt, _ = openai.call_args.args[:2]
    assert "General" in system_prompt
    assert "Python Rules" in system_prompt
    assert "CSharp Rules" not in system_prompt


def test_review_keeps_only_all_sections_when_no_matching_extension(mocker, tmp_path: Path) -> None:
    """If no changed file matches a tagged section, only 'all' sections remain."""
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text(
        "<!-- lang: all -->\n"
        "## General\n"
        "- Always check for secrets.\n"
        "\n"
        "<!-- lang: java -->\n"
        "## Java Rules\n"
        "- Use streams.\n",
        encoding="utf-8",
    )
    config = make_llm_config(custom_prompt_file=str(prompt_file))
    client = LLMClient(config)
    openai = mocker.patch("src.llm_client.LLMClient._call_openai", return_value="review text")

    client.review("+code", [{"file": "component.ts", "additions": 1, "deletions": 0}])

    system_prompt, _ = openai.call_args.args[:2]
    assert "General" in system_prompt
    assert "Java Rules" not in system_prompt


def test_review_includes_multiple_extension_sections(mocker, tmp_path: Path) -> None:
    """Changed files with different extensions should each pull in their matching section."""
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text(
        "<!-- lang: ts -->\n"
        "## TypeScript Rules\n"
        "- No any types.\n"
        "\n"
        "<!-- lang: html -->\n"
        "## Html Rules\n"
        "- No inline styles.\n",
        encoding="utf-8",
    )
    config = make_llm_config(custom_prompt_file=str(prompt_file))
    client = LLMClient(config)
    openai = mocker.patch("src.llm_client.LLMClient._call_openai", return_value="review text")

    client.review(
        "+code",
        [
            {"file": "foo.component.ts", "additions": 1, "deletions": 0},
            {"file": "foo.component.html", "additions": 1, "deletions": 0},
        ],
    )

    system_prompt, _ = openai.call_args.args[:2]
    assert "TypeScript Rules" in system_prompt
    assert "Html Rules" in system_prompt


def test_review_without_lang_tags_keeps_full_custom_prompt(mocker, tmp_path: Path) -> None:
    """Custom prompt with no <!-- lang: --> tags at all should pass through unchanged."""
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("Always mention tests", encoding="utf-8")
    config = make_llm_config(custom_prompt_file=str(prompt_file))
    client = LLMClient(config)
    openai = mocker.patch("src.llm_client.LLMClient._call_openai", return_value="review text")

    client.review("+code", [{"file": "a.py", "additions": 1, "deletions": 0}])

    system_prompt, _ = openai.call_args.args[:2]
    assert "Always mention tests" in system_prompt

def test_review_pr_raises_for_unsupported_provider() -> None:
    """It should reject unsupported providers before any HTTP call."""
    client = LLMClient(make_llm_config(llm_provider="unknown"))

    with pytest.raises(LLMError, match="Unsupported provider"):
        client.review_pr("+code", [])


def test_review_pr_dispatches_and_parses_combined_response(mocker) -> None:
    """It should dispatch a single combined review and normalize summary + JSON comments."""
    client = LLMClient(make_llm_config(llm_provider="copilot"))
    copilot = mocker.patch(
        "src.llm_client.LLMClient._call_copilot",
        return_value=(
            '{"summary": "General review", "comments": '
            '[{"file": "src/app.py", "line": 5, "type": "bug", "comment": "boom", '
            '"suggestion": "fix", "reference": "Docs"}]}'
        ),
    )

    summary, comments = client.review_pr("+code", [{"file": "a.py", "additions": 1, "deletions": 0}])

    assert copilot.called
    assert summary == "General review"
    assert comments == [{"file": "src/app.py", "line": 5, "type": "bug", "comment": "boom", "suggestion": "fix", "reference": "Docs"}]


def test_parse_combined_response_handles_markdown_missing_fields_and_invalid_json() -> None:
    """It should parse code fences, tolerate missing/invalid fields and fall back on invalid payloads."""
    client = LLMClient(make_llm_config())

    fenced_summary, fenced_comments = client._parse_combined_response(
        "```json\n{\"summary\": \"ok\", \"comments\": [{\"file\": \"a.py\", \"line\": 1, \"comment\": \"x\"}]}\n```"
    )
    assert fenced_summary == "ok"
    assert fenced_comments[0]["file"] == "a.py"
    assert fenced_comments[0]["line"] == 1

    # Missing "comments" field defaults to an empty list.
    no_comments_summary, no_comments = client._parse_combined_response(
        '{"summary": "only summary"}'
    )
    assert no_comments_summary == "only summary"
    assert no_comments == []

    # Non-list "comments" field is treated as if no comments were provided.
    bad_comments_summary, bad_comments = client._parse_combined_response(
        '{"summary": "weird", "comments": "not a list"}'
    )
    assert bad_comments_summary == "weird"
    assert bad_comments == []

    # Non-dict JSON value (e.g. a bare array with no nested object) falls back
    # to the raw response text as the summary, with no comments.
    raw_array = "[1, 2, 3]"
    array_summary, array_comments = client._parse_combined_response(raw_array)
    assert array_summary == raw_array
    assert array_comments == []

    # Malformed JSON falls back to raw text as summary with no comments.
    fallback_summary, fallback_comments = client._parse_combined_response("not json at all")
    assert fallback_summary == "not json at all"
    assert fallback_comments == []

    # A non-numeric "line" value must not crash parsing; it defaults to 0.
    bad_line_summary, bad_line_comments = client._parse_combined_response(
        '{"summary": "ok", "comments": [{"file": "a.py", "line": "N/A", "comment": "z"}]}'
    )
    assert bad_line_summary == "ok"
    assert bad_line_comments[0]["line"] == 0


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


def test_bedrock_boto3_success_and_error_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """It should call boto3 when no explicit credentials are set and parse output."""
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

    # No access_key / secret → routes to boto3 path
    client = LLMClient(
        make_llm_config(
            llm_provider="bedrock",
            api_key="",
            model="anthropic.claude-3-5-sonnet",
            bedrock_region="us-east-1",
            bedrock_profile="dev",
            bedrock_access_key_id="",
            bedrock_secret_access_key="",
        )
    )
    assert client._call_bedrock("sys", "user") == "bedrock ok"
    assert client._call_bedrock_boto3("us-east-1", "sys", "user") == "bedrock ok"

    class BrokenSession(FakeSession):
        """Session that raises provider-side failures."""

        def client(self, service_name: str, region_name: str) -> FakeBedrockClient:
            raise FakeClientError("boom")

    boto3_module.Session = BrokenSession
    with pytest.raises(LLMError, match="Error calling AWS Bedrock"):
        client._call_bedrock_boto3("us-east-1", "sys", "user")


def test_call_bedrock_routing_dispatches_correct_method(mocker) -> None:
    """_call_bedrock should route to bearer, sigv4, or boto3 based on credential fields."""
    bearer_mock = mocker.patch("src.llm_client.LLMClient._call_bedrock_bearer", return_value="bearer")
    client_bearer = LLMClient(
        make_llm_config(
            llm_provider="bedrock", api_key="", model="m",
            bedrock_region="us-east-1", bedrock_access_key_id="api-key",
            bedrock_secret_access_key="",
        )
    )
    assert client_bearer._call_bedrock("sys", "user") == "bearer"
    bearer_mock.assert_called_once_with("us-east-1", "api-key", "sys", "user")

    sigv4_mock = mocker.patch("src.llm_client.LLMClient._call_bedrock_sigv4", return_value="sigv4")
    client_sigv4 = LLMClient(
        make_llm_config(
            llm_provider="bedrock", api_key="", model="m",
            bedrock_region="us-east-1", bedrock_access_key_id="AKID",
            bedrock_secret_access_key="secret", bedrock_session_token="tok",
        )
    )
    assert client_sigv4._call_bedrock("sys", "user") == "sigv4"
    sigv4_mock.assert_called_once_with("us-east-1", "AKID", "secret", "tok", "sys", "user")

    boto3_mock = mocker.patch("src.llm_client.LLMClient._call_bedrock_boto3", return_value="boto3")
    client_boto3 = LLMClient(
        make_llm_config(
            llm_provider="bedrock", api_key="", model="m",
            bedrock_region="us-east-1", bedrock_access_key_id="",
            bedrock_secret_access_key="",
        )
    )
    assert client_boto3._call_bedrock("sys", "user") == "boto3"
    boto3_mock.assert_called_once_with("us-east-1", "sys", "user")

    with pytest.raises(LLMError, match="requires bedrock.region"):
        LLMClient(make_llm_config(llm_provider="bedrock", api_key="", bedrock_region=""))._call_bedrock("sys", "user")


def test_call_bedrock_bearer_success_and_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """_call_bedrock_bearer should use Bearer auth, URL-encode the model ARN and parse content."""
    model_arn = "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-3-sonnet"
    client = LLMClient(
        make_llm_config(
            llm_provider="bedrock",
            api_key="",
            model=model_arn,
            bedrock_region="us-east-1",
            bedrock_access_key_id="my-bearer-key",
            bedrock_secret_access_key="",
        )
    )

    # Success path: response parsed correctly, model ARN URL-encoded in URL
    success_resp = FakeResponse(json_data={"content": [{"type": "text", "text": "bearer ok"}]})
    req_module = install_requests_raw(monkeypatch, success_resp)
    result = client._call_bedrock_bearer("us-east-1", "my-bearer-key", "sys", "user")
    assert result == "bearer ok"
    call = req_module._calls[0]
    assert "arn%3Aaws%3Abedrock" in call["url"]  # ':' encoded as %3A
    assert "anthropic.claude-3-sonnet" in call["url"]
    assert call["headers"]["Authorization"] == "Bearer my-bearer-key"
    assert call["headers"]["Content-Type"] == "application/json"

    # 401 → specific authentication error message
    install_requests_raw(monkeypatch, FakeResponse(status_code=401))
    with pytest.raises(LLMError, match=r"authentication failed \(401\)"):
        client._call_bedrock_bearer("us-east-1", "my-bearer-key", "sys", "user")

    # Non-200 → generic HTTP error
    install_requests_raw(monkeypatch, FakeResponse(status_code=500, text="server error"))
    with pytest.raises(LLMError, match="Bedrock returned HTTP 500"):
        client._call_bedrock_bearer("us-east-1", "my-bearer-key", "sys", "user")

    # Empty content list → Unexpected response error
    install_requests_raw(monkeypatch, FakeResponse(json_data={"content": []}))
    with pytest.raises(LLMError, match="Unexpected Bedrock response"):
        client._call_bedrock_bearer("us-east-1", "my-bearer-key", "sys", "user")

    # Content items with no text type → Unexpected response error
    install_requests_raw(monkeypatch, FakeResponse(json_data={"content": [{"type": "image", "data": "..."}]}))
    with pytest.raises(LLMError, match="Unexpected Bedrock response"):
        client._call_bedrock_bearer("us-east-1", "my-bearer-key", "sys", "user")

    # Connection error → wrapped LLMError
    req_module = install_requests_raw(monkeypatch, FakeResponse())
    req_module.post = lambda *a, **kw: (_ for _ in ()).throw(req_module.exceptions.RequestException("conn failed"))
    with pytest.raises(LLMError, match="Bedrock HTTP request failed"):
        client._call_bedrock_bearer("us-east-1", "my-bearer-key", "sys", "user")


def test_call_bedrock_sigv4_success_and_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """_call_bedrock_sigv4 should add SigV4 headers and parse content correctly."""
    client = LLMClient(
        make_llm_config(
            llm_provider="bedrock",
            api_key="",
            model="anthropic.claude-3-sonnet",
            bedrock_region="us-east-1",
            bedrock_access_key_id="AKID",
            bedrock_secret_access_key="secret",
        )
    )
    success_resp = FakeResponse(json_data={"content": [{"type": "text", "text": "sigv4 ok"}]})

    # Success path: SigV4 headers present in request
    req_module = install_requests_raw(monkeypatch, success_resp)
    result = client._call_bedrock_sigv4("us-east-1", "AKID", "secret", "", "sys", "user")
    assert result == "sigv4 ok"
    call = req_module._calls[0]
    assert call["headers"]["Authorization"].startswith("AWS4-HMAC-SHA256 Credential=AKID/")
    assert "x-amz-date" in call["headers"]
    assert "x-amz-content-sha256" in call["headers"]
    assert "x-amz-security-token" not in call["headers"]

    # Session token is forwarded as x-amz-security-token
    req_module = install_requests_raw(monkeypatch, success_resp)
    result2 = client._call_bedrock_sigv4("us-east-1", "AKID", "secret", "sess-tok", "sys", "user")
    assert result2 == "sigv4 ok"
    call2 = req_module._calls[0]
    assert call2["headers"]["x-amz-security-token"] == "sess-tok"

    # 401 → specific authentication error message
    install_requests_raw(monkeypatch, FakeResponse(status_code=401))
    with pytest.raises(LLMError, match=r"authentication failed \(401\)"):
        client._call_bedrock_sigv4("us-east-1", "AKID", "secret", "", "sys", "user")

    # Non-200 → generic HTTP error
    install_requests_raw(monkeypatch, FakeResponse(status_code=503, text="down"))
    with pytest.raises(LLMError, match="Bedrock returned HTTP 503"):
        client._call_bedrock_sigv4("us-east-1", "AKID", "secret", "", "sys", "user")

    # Empty content → Unexpected response error
    install_requests_raw(monkeypatch, FakeResponse(json_data={"content": []}))
    with pytest.raises(LLMError, match="Unexpected Bedrock response"):
        client._call_bedrock_sigv4("us-east-1", "AKID", "secret", "", "sys", "user")

    # Connection error → wrapped LLMError
    req_module = install_requests_raw(monkeypatch, FakeResponse())
    req_module.post = lambda *a, **kw: (_ for _ in ()).throw(req_module.exceptions.RequestException("timeout"))
    with pytest.raises(LLMError, match="Bedrock HTTP request failed"):
        client._call_bedrock_sigv4("us-east-1", "AKID", "secret", "", "sys", "user")