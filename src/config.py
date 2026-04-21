"""
Configuration Module - AI Code Review
=======================================
Manages system configuration exclusively from config.yaml.

Configuration priority:
1. CLI arguments (highest priority)
2. config.yaml file
3. Default values

Supported LLM providers:
- openai      (GPT-4, GPT-4-turbo, GPT-4o)
- gemini      (Google Gemini Pro, Gemini 1.5 Pro)
- claude      (Anthropic Claude 3 Opus, Sonnet, Haiku)
- ollama      (Local models via Ollama)
- azure_openai (Azure OpenAI Service)
- copilot     (GitHub Copilot - GPT-4o, Claude, etc. via GitHub)
- bedrock     (AWS Bedrock)
"""

import os
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# Optional dependencies – loaded with safe fallback
# ---------------------------------------------------------------------------
try:
    import yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False


# ---------------------------------------------------------------------------
# Default models per provider
# ---------------------------------------------------------------------------
DEFAULT_MODELS = {
    "openai": "gpt-4o",
    "azure_openai": "gpt-4o",
    "gemini": "gemini-1.5-pro",
    "claude": "claude-3-5-sonnet-latest",
    "ollama": "llama3",
    "copilot": "gpt-4o",
    "bedrock": "anthropic.claude-3-5-sonnet-20240620-v1:0",
}

VALID_PROVIDERS = list(DEFAULT_MODELS.keys())


# ---------------------------------------------------------------------------
# Main configuration dataclass
# ---------------------------------------------------------------------------
@dataclass
class ReviewConfig:
    """Stores all configuration needed for AI Code Review."""

    # --- LLM Provider -------------------------------------------------
    llm_provider: str = "openai"           # LLM provider to use
    api_key: str = ""                       # Provider API key
    api_base_url: str = ""                  # Base URL for APIs (override)
    model: str = ""                         # Model to use (empty = provider default)
    max_tokens: int = 4096
    temperature: float = 0.3

    # --- Provider-specific keys (alternatives) -------------------------
    openai_api_key: str = ""
    gemini_api_key: str = ""
    anthropic_api_key: str = ""
    ollama_base_url: str = ""               # E.g., http://localhost:11434
    github_token: str = ""                  # GitHub token for Copilot
    bedrock_region: str = ""                # E.g., us-east-1
    bedrock_access_key_id: str = ""
    bedrock_secret_access_key: str = ""
    bedrock_session_token: str = ""
    bedrock_profile: str = ""               # Optional profile ~/.aws/credentials

    # --- Azure DevOps / TFS ------------------------------------------
    tfs_base_url: str = ""                  # E.g., https://tfs.company.com/tfs
    tfs_collection: str = "DefaultCollection"
    tfs_project: str = ""
    tfs_pat: str = ""                       # Personal Access Token
    tfs_verify_ssl: bool = True              # Verify TLS certificates
    tfs_ca_bundle: str = ""                 # Path to corporate CA bundle (.pem)
    tfs_repository: str = ""                # Default repository (empty = all)

    # --- Review -------------------------------------------------------
    review_language: str = "pt"             # Review language (pt/en)
    verbosity: str = "detailed"             # "quick" | "detailed" | "security"
    review_scope: str = "diff_only"         # "diff_only" | "full_code"
    max_diff_files: int = 50                 # Max diff files sent to LLM
    max_diff_lines: int = 2000              # Max diff lines
    custom_prompt_file: str = "review_prompt.md"  # Markdown file with extra rules/context
    file_extensions_filter: list = field(default_factory=list)

    # --- PR Review ----------------------------------------------------
    auto_post_comments: bool = False        # Post comments automatically
    dry_run: bool = False                   # Review without posting
    pr_comment_mode: str = "structured"     # "structured" | "general"

    # --- Output -------------------------------------------------------
    output_format: str = "terminal"         # "terminal" | "markdown" | "json"
    output_file: str = ""                   # Path to save output
    color_output: bool = True               # Terminal colors

    def get_effective_model(self) -> str:
        """Returns the effective model (configured or provider default)."""
        if self.model:
            return self.model
        return DEFAULT_MODELS.get(self.llm_provider, "gpt-4o")

    def get_effective_api_key(self) -> str:
        """Returns the effective API key for the current provider."""
        if self.api_key:
            return self.api_key

        provider = self.llm_provider.lower()
        if provider == "openai" or provider == "azure_openai":
            return self.openai_api_key
        elif provider == "gemini":
            return self.gemini_api_key
        elif provider == "claude":
            return self.anthropic_api_key
        elif provider == "copilot":
            return self.github_token
        elif provider == "ollama":
            return ""  # Ollama does not require an API key
        elif provider == "bedrock":
            return ""  # Bedrock uses AWS credentials in dedicated fields
        return ""

    def get_effective_base_url(self) -> str:
        """Returns the effective base URL for the current provider."""
        if self.api_base_url:
            return self.api_base_url

        provider = self.llm_provider.lower()
        if provider == "ollama":
            return self.ollama_base_url or "http://localhost:11434"
        elif provider == "copilot":
            return "https://models.github.ai/inference"
        return ""

    @classmethod
    def load(cls, config_path: Optional[str] = None) -> "ReviewConfig":
        """
        Loads configuration with the following priority:
        1. config.yaml file
        2. Default values
        """
        cfg = cls()

        # --- Load config.yaml if it exists ---
        yaml_path = config_path or _find_file("config.yaml")
        if yaml_path and os.path.isfile(yaml_path):
            cfg._load_yaml(yaml_path)

        # --- Resolve effective model and API key ---
        if not cfg.model:
            cfg.model = cfg.get_effective_model()
        if not cfg.api_key:
            cfg.api_key = cfg.get_effective_api_key()
        if not cfg.api_base_url:
            cfg.api_base_url = cfg.get_effective_base_url()

        return cfg

    # ------------------------------------------------------------------
    # Private methods
    # ------------------------------------------------------------------
    def _load_yaml(self, path: str) -> None:
        """Loads values from the YAML file."""
        if not _HAS_YAML:
            print("[WARNING] PyYAML not installed. Ignoring config.yaml.")
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception as exc:
            print(f"[WARNING] Error reading {path}: {exc}")
            return

        mapping = {
            "llm_provider": ("llm", "provider"),
            "api_key": ("llm", "api_key"),
            "api_base_url": ("llm", "api_base_url"),
            "model": ("llm", "model"),
            "max_tokens": ("llm", "max_tokens"),
            "temperature": ("llm", "temperature"),
            # Provider-specific
            "openai_api_key": ("openai", "api_key"),
            "gemini_api_key": ("gemini", "api_key"),
            "anthropic_api_key": ("claude", "api_key"),
            "ollama_base_url": ("ollama", "base_url"),
            "github_token": ("copilot", "github_token"),
            "bedrock_region": ("bedrock", "region"),
            "bedrock_access_key_id": ("bedrock", "access_key_id"),
            "bedrock_secret_access_key": ("bedrock", "secret_access_key"),
            "bedrock_session_token": ("bedrock", "session_token"),
            "bedrock_profile": ("bedrock", "profile"),
            # TFS
            "tfs_base_url": ("tfs", "base_url"),
            "tfs_collection": ("tfs", "collection"),
            "tfs_project": ("tfs", "project"),
            "tfs_pat": ("tfs", "pat"),
            "tfs_verify_ssl": ("tfs", "verify_ssl"),
            "tfs_ca_bundle": ("tfs", "ca_bundle"),
            "tfs_repository": ("tfs", "repository"),
            # Review
            "review_language": ("review", "language"),
            "verbosity": ("review", "verbosity"),
            "review_scope": ("review", "scope"),
            "max_diff_files": ("review", "max_diff_files"),
            "max_diff_lines": ("review", "max_diff_lines"),
            "custom_prompt_file": ("review", "custom_prompt_file"),
            "file_extensions_filter": ("review", "file_extensions_filter"),
            # PR
            "auto_post_comments": ("pr", "auto_post_comments"),
            "dry_run": ("pr", "dry_run"),
            "pr_comment_mode": ("pr", "comment_mode"),
            # Output
            "output_format": ("output", "format"),
            "output_file": ("output", "file"),
            "color_output": ("output", "color"),
        }

        for attr, keys in mapping.items():
            val = data
            for k in keys:
                if isinstance(val, dict):
                    val = val.get(k)
                else:
                    val = None
                    break
            if val is not None:
                setattr(self, attr, val)

    def validate(self) -> list[str]:
        """Validates the configuration and returns a list of warnings/errors."""
        issues: list[str] = []

        provider = self.llm_provider.lower()

        if provider not in VALID_PROVIDERS:
            issues.append(
                f"Unknown provider: '{provider}'.\n"
                f"  Valid providers: {', '.join(VALID_PROVIDERS)}"
            )
            return issues

        # Validate API key per provider
        if provider == "ollama":
            # Ollama does not need an API key, but needs a URL
            pass
        elif provider in ("openai", "azure_openai"):
            if not self.api_key and not self.openai_api_key:
                issues.append(
                    f"Provider '{provider}' requires an API key in config.yaml.\n"
                    "  Configure llm.api_key or openai.api_key."
                )
            if provider == "azure_openai" and not self.api_base_url:
                issues.append(
                    "Azure OpenAI requires API_BASE_URL to be configured.\n"
                    "  E.g., https://your-resource.openai.azure.com/openai/deployments/your-deploy"
                )
        elif provider == "gemini":
            if not self.api_key and not self.gemini_api_key:
                issues.append(
                    "Provider 'gemini' requires an API key in config.yaml.\n"
                    "  Get it at: https://aistudio.google.com/app/apikey\n"
                    "  Configure llm.api_key or gemini.api_key."
                )
        elif provider == "claude":
            if not self.api_key and not self.anthropic_api_key:
                issues.append(
                    "Provider 'claude' requires an API key in config.yaml.\n"
                    "  Get it at: https://console.anthropic.com/settings/keys\n"
                    "  Configure llm.api_key or claude.api_key."
                )
        elif provider == "copilot":
            if not self.api_key and not self.github_token:
                issues.append(
                    "Provider 'copilot' requires a GitHub token in config.yaml.\n"
                    "  Create at: https://github.com/settings/tokens\n"
                    "  Configure llm.api_key or copilot.github_token.\n"
                    "  Requires an active GitHub Copilot subscription."
                )
        elif provider == "bedrock":
            if not self.bedrock_region:
                issues.append(
                    "Provider 'bedrock' requires an AWS region.\n"
                    "  Configure bedrock.region in config.yaml (e.g., us-east-1)."
                )
            if self.bedrock_access_key_id and not self.bedrock_secret_access_key:
                issues.append(
                    "Provider 'bedrock': secret_access_key is missing.\n"
                    "  Configure bedrock.secret_access_key when access_key_id is set."
                )
            if self.bedrock_secret_access_key and not self.bedrock_access_key_id:
                issues.append(
                    "Provider 'bedrock': access_key_id is missing.\n"
                    "  Configure bedrock.access_key_id when secret_access_key is set."
                )

        if self.verbosity not in ("quick", "detailed", "security"):
            issues.append(
                f"Invalid verbosity: '{self.verbosity}'. "
                "Use 'quick', 'detailed' or 'security'."
            )

        if self.review_scope not in ("diff_only", "full_code"):
            issues.append(
                f"Invalid review scope: '{self.review_scope}'. "
                "Use 'diff_only' or 'full_code'."
            )

        if self.max_diff_files <= 0:
            issues.append(
                f"Invalid max_diff_files: '{self.max_diff_files}'. "
                "Use an integer greater than 0."
            )

        if self.max_diff_lines <= 0:
            issues.append(
                f"Invalid max_diff_lines: '{self.max_diff_lines}'. "
                "Use an integer greater than 0."
            )

        return issues

    def get_provider_info(self) -> str:
        """Returns formatted information about the configured provider."""
        provider = self.llm_provider
        model = self.get_effective_model()
        has_key = bool(self.api_key or self.get_effective_api_key())

        if provider == "ollama":
            url = self.get_effective_base_url()
            return f"{provider} | {model} | URL: {url}"
        if provider == "bedrock":
            creds_mode = "profile" if self.bedrock_profile else "explicit/default"
            region = self.bedrock_region or "(not configured)"
            return f"{provider} | {model} | Region: {region} | Credentials: {creds_mode}"
        else:
            key_status = "✅ Configured" if has_key else "❌ Not configured"
            return f"{provider} | {model} | API Key: {key_status}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _find_file(name: str) -> Optional[str]:
    """Searches for a file in the current directory and the script directory."""
    candidates = [
        os.path.join(os.getcwd(), name),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", name),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return os.path.abspath(c)
    return None
