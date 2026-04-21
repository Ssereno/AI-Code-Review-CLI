"""Tests for the configuration module."""

from __future__ import annotations

from pathlib import Path

import pytest

from src import config as config_module
from src.config import ReviewConfig


def test_get_effective_model_prefers_explicit_value() -> None:
    """It should prefer the configured model over provider defaults."""
    config = ReviewConfig(llm_provider="openai", model="custom-model")

    assert config.get_effective_model() == "custom-model"


@pytest.mark.parametrize(
    ("provider", "field", "expected"),
    [
        ("openai", {"openai_api_key": "openai-key"}, "openai-key"),
        ("azure_openai", {"openai_api_key": "azure-key"}, "azure-key"),
        ("gemini", {"gemini_api_key": "gemini-key"}, "gemini-key"),
        ("claude", {"anthropic_api_key": "claude-key"}, "claude-key"),
        ("copilot", {"github_token": "copilot-key"}, "copilot-key"),
        ("ollama", {}, ""),
        ("bedrock", {}, ""),
    ],
)
def test_get_effective_api_key_uses_provider_specific_values(
    provider: str,
    field: dict[str, str],
    expected: str,
) -> None:
    """It should resolve the correct key source for each provider."""
    config = ReviewConfig(llm_provider=provider, **field)

    assert config.get_effective_api_key() == expected


def test_get_effective_api_key_prefers_generic_api_key() -> None:
    """It should prefer the generic API key when both are configured."""
    config = ReviewConfig(
        llm_provider="openai",
        api_key="primary",
        openai_api_key="secondary",
    )

    assert config.get_effective_api_key() == "primary"


@pytest.mark.parametrize(
    ("provider", "api_base_url", "ollama_base_url", "expected"),
    [
        ("ollama", "", "", "http://localhost:11434"),
        ("ollama", "", "http://ollama.local", "http://ollama.local"),
        ("copilot", "", "", "https://models.github.ai/inference"),
        ("openai", "https://override.local", "", "https://override.local"),
    ],
)
def test_get_effective_base_url(
    provider: str,
    api_base_url: str,
    ollama_base_url: str,
    expected: str,
) -> None:
    """It should resolve provider-specific base URLs correctly."""
    config = ReviewConfig(
        llm_provider=provider,
        api_base_url=api_base_url,
        ollama_base_url=ollama_base_url,
    )

    assert config.get_effective_base_url() == expected


def test_load_reads_yaml_and_resolves_effective_values(mocker, temp_config_file) -> None:
    """It should load YAML values and resolve derived settings."""
    path = temp_config_file(
        """
llm:
  provider: gemini
gemini:
  api_key: gemini-secret
review:
  verbosity: security
output:
  format: markdown
""".strip()
    )
    mocker.patch("src.config._find_file", return_value=path)

    config = ReviewConfig.load()

    assert config.llm_provider == "gemini"
    assert config.api_key == "gemini-secret"
    assert config.model == config_module.DEFAULT_MODELS["gemini"]
    assert config.verbosity == "security"
    assert config.output_format == "markdown"


def test_load_ignores_missing_file(mocker) -> None:
    """It should fall back to defaults when no config file is found."""
    mocker.patch("src.config._find_file", return_value=None)

    config = ReviewConfig.load()

    assert config.model == config_module.DEFAULT_MODELS[config.llm_provider]


def test_load_yaml_warns_when_yaml_is_unavailable(mocker, temp_config_file) -> None:
    """It should warn and skip loading when PyYAML is unavailable."""
    config = ReviewConfig()
    mocker.patch("src.config._HAS_YAML", new=False)
    print_mock = mocker.patch("builtins.print")

    config._load_yaml(temp_config_file("llm: {}"))

    print_mock.assert_called_once()


def test_load_yaml_warns_on_read_error(mocker) -> None:
    """It should warn when the YAML file cannot be read."""
    config = ReviewConfig()
    open_mock = mocker.patch("builtins.open", side_effect=OSError("boom"))
    print_mock = mocker.patch("builtins.print")

    config._load_yaml("missing.yaml")

    open_mock.assert_called_once()
    print_mock.assert_called_once()


def test_load_yaml_maps_nested_configuration(temp_config_file) -> None:
    """It should map nested YAML sections into dataclass fields."""
    config = ReviewConfig()
    config._load_yaml(
        temp_config_file(
            """
llm:
  provider: copilot
  api_key: generic-key
  api_base_url: https://proxy.local
  model: gpt-4o-mini
  max_tokens: 1024
  temperature: 0.7
openai:
  api_key: openai-key
gemini:
  api_key: gemini-key
claude:
  api_key: claude-key
ollama:
  base_url: http://ollama.local
copilot:
  github_token: gh-token
bedrock:
  region: eu-west-1
  access_key_id: key-id
  secret_access_key: key-secret
  session_token: session-token
  profile: dev
tfs:
  base_url: https://dev.azure.com/org
  collection: Coll
  project: ProjectX
  pat: tfs-pat
  verify_ssl: false
  ca_bundle: ~/ca.pem
  repository: repo-a
review:
  language: en
  verbosity: quick
  scope: full_code
  max_diff_files: 12
  max_diff_lines: 456
  custom_prompt_file: prompt.md
  file_extensions_filter:
    - .py
    - .md
pr:
  auto_post_comments: true
  dry_run: true
  comment_mode: general
output:
  format: json
  file: review.json
  color: false
""".strip()
        )
    )

    assert config.llm_provider == "copilot"
    assert config.api_key == "generic-key"
    assert config.api_base_url == "https://proxy.local"
    assert config.model == "gpt-4o-mini"
    assert config.max_tokens == 1024
    assert config.temperature == 0.7
    assert config.openai_api_key == "openai-key"
    assert config.gemini_api_key == "gemini-key"
    assert config.anthropic_api_key == "claude-key"
    assert config.ollama_base_url == "http://ollama.local"
    assert config.github_token == "gh-token"
    assert config.bedrock_region == "eu-west-1"
    assert config.bedrock_access_key_id == "key-id"
    assert config.bedrock_secret_access_key == "key-secret"
    assert config.bedrock_session_token == "session-token"
    assert config.bedrock_profile == "dev"
    assert config.tfs_base_url == "https://dev.azure.com/org"
    assert config.tfs_collection == "Coll"
    assert config.tfs_project == "ProjectX"
    assert config.tfs_pat == "tfs-pat"
    assert config.tfs_verify_ssl is False
    assert config.tfs_ca_bundle == "~/ca.pem"
    assert config.tfs_repository == "repo-a"
    assert config.review_language == "en"
    assert config.verbosity == "quick"
    assert config.review_scope == "full_code"
    assert config.max_diff_files == 12
    assert config.max_diff_lines == 456
    assert config.custom_prompt_file == "prompt.md"
    assert config.file_extensions_filter == [".py", ".md"]
    assert config.auto_post_comments is True
    assert config.dry_run is True
    assert config.pr_comment_mode == "general"
    assert config.output_format == "json"
    assert config.output_file == "review.json"
    assert config.color_output is False


def test_validate_returns_early_for_unknown_provider() -> None:
    """It should stop validation immediately for unsupported providers."""
    issues = ReviewConfig(llm_provider="unknown").validate()

    assert len(issues) == 1
    assert "Unknown provider" in issues[0]


@pytest.mark.parametrize(
    ("changes", "expected_fragments"),
    [
        ({"llm_provider": "openai", "api_key": "", "openai_api_key": ""}, ["requires an API key"]),
        ({"llm_provider": "azure_openai", "api_key": "", "openai_api_key": "", "api_base_url": ""}, ["requires an API key", "Azure OpenAI requires API_BASE_URL"]),
        ({"llm_provider": "gemini", "api_key": "", "gemini_api_key": ""}, ["Provider 'gemini' requires an API key"]),
        ({"llm_provider": "claude", "api_key": "", "anthropic_api_key": ""}, ["Provider 'claude' requires an API key"]),
        ({"llm_provider": "copilot", "api_key": "", "github_token": ""}, ["Provider 'copilot' requires a GitHub token"]),
        ({"llm_provider": "bedrock", "bedrock_region": "", "bedrock_access_key_id": "a", "bedrock_secret_access_key": ""}, ["requires an AWS region", "secret_access_key is missing"]),
    ],
)
def test_validate_provider_specific_errors(
    review_config_factory,
    changes: dict[str, object],
    expected_fragments: list[str],
) -> None:
    """It should report provider-specific configuration problems."""
    issues = review_config_factory(**changes).validate()

    for fragment in expected_fragments:
        assert any(fragment in issue for issue in issues)


def test_validate_reports_generic_limits(review_config_factory) -> None:
    """It should report invalid generic review settings."""
    config = review_config_factory(
        verbosity="verbose",
        review_scope="everything",
        max_diff_files=0,
        max_diff_lines=0,
    )

    issues = config.validate()

    assert any("Invalid verbosity" in issue for issue in issues)
    assert any("Invalid review scope" in issue for issue in issues)
    assert any("Invalid max_diff_files" in issue for issue in issues)
    assert any("Invalid max_diff_lines" in issue for issue in issues)


def test_validate_accepts_valid_bedrock_credentials(review_config_factory) -> None:
    """It should accept a valid Bedrock configuration."""
    config = review_config_factory(
        llm_provider="bedrock",
        api_key="",
        bedrock_region="us-east-1",
        bedrock_access_key_id="access",
        bedrock_secret_access_key="secret",
    )

    assert config.validate() == []


def test_get_provider_info_variants(review_config_factory) -> None:
    """It should render provider information according to provider type."""
    ollama_info = review_config_factory(llm_provider="ollama", api_key="", model="", ollama_base_url="").get_provider_info()
    bedrock_info = review_config_factory(llm_provider="bedrock", api_key="", model="", bedrock_region="eu-west-1", bedrock_profile="dev").get_provider_info()
    default_info = review_config_factory(llm_provider="openai", api_key="secret").get_provider_info()

    assert "URL: http://localhost:11434" in ollama_info
    assert "Region: eu-west-1" in bedrock_info
    assert "Credentials: profile" in bedrock_info
    assert "API Key: ✅ Configured" in default_info


def test_find_file_checks_cwd_then_repo_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """It should search the working directory before the repository root fallback."""
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    direct_file = cwd / "config.yaml"
    direct_file.write_text("direct", encoding="utf-8")
    monkeypatch.chdir(cwd)

    found = config_module._find_file("config.yaml")

    assert found == str(direct_file.resolve())