"""
Configuration Module - AI Code Review
=======================================
Manages system configuration from config.yaml, environment variables and .env files.

Configuration priority:
1. CLI arguments (highest priority)
2. Environment variables or .env file
3. config.yaml file
4. Default values

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

try:
    from dotenv import load_dotenv
    _HAS_DOTENV = True
except ImportError:
    _HAS_DOTENV = False


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
    verbosity: str = "detailed"             # "detailed" | "security"
    review_scope: str = "diff_only"         # "diff_only" | "full_code"
    max_diff_files: int = 50                 # Max diff files sent to LLM
    max_diff_lines: int = 2000              # Max diff lines
    max_scope_blocks: int = 3               # Max changed blocks for Enclosing Scopes strategy
    max_scope_lines: int = 250              # Max lines in a single scope block before fallback to file_skeleton
    adaptive_context_lines: int = 10        # Context lines for Adaptive Diff fallback
    custom_prompt_file: str = "review_prompt.md"  # Markdown file with extra rules/context
    file_extensions_filter: list = field(default_factory=list)  # File extensions to include (empty = all)
    excluded_paths: list = field(default_factory=list)  # Folder path segments to exclude from diff

    # --- PR Review ----------------------------------------------------
    auto_post_comments: bool = False        # Post comments automatically
    dry_run: bool = False                   # Review without posting
    pr_comment_mode: str = "structured"     # "structured" | "general"

    # --- Output -------------------------------------------------------
    output_format: str = "terminal"         # "terminal" | "markdown" | "json"
    output_file: str = ""                   # Path to save output
    color_output: bool = True               # Terminal colors

    # --- Debug ----------------------------------------------------------
    debug_dump: bool = False                # Dump diff and full LLM prompt to a log file
    debug_dump_file: str = ""               # Custom path (empty = logs/pr_<id>_debug.log)

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
        1. CLI arguments (highest priority)
        2. Environment variables or .env file
        3. config.yaml file
        4. Default values
        """
        cfg = cls()

        # --- Load .env file if python-dotenv is available ---
        if _HAS_DOTENV:
            load_dotenv()

        # --- Load config.yaml if it exists ---
        yaml_path = config_path or _find_file("config.yaml")
        if yaml_path and os.path.isfile(yaml_path):
            cfg._load_yaml(yaml_path)

        # --- Override with environment variables ---
        cfg._load_env_vars()

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
            "verbosity": ("review", "verbosity"),
            "review_scope": ("review", "scope"),
            "max_diff_files": ("review", "max_diff_files"),
            "max_diff_lines": ("review", "max_diff_lines"),
            "max_scope_blocks": ("review", "max_scope_blocks"),
            "max_scope_lines": ("review", "max_scope_lines"),
            "adaptive_context_lines": ("review", "adaptive_context_lines"),
            "custom_prompt_file": ("review", "custom_prompt_file"),
            "file_extensions_filter": ("review", "file_extensions_filter"),
            "excluded_paths": ("review", "excluded_paths"),
            # PR
            "auto_post_comments": ("pr", "auto_post_comments"),
            "dry_run": ("pr", "dry_run"),
            "pr_comment_mode": ("pr", "comment_mode"),
            # Output
            "output_format": ("output", "format"),
            "output_file": ("output", "file"),
            "color_output": ("output", "color"),
            # Debug
            "debug_dump": ("debug", "dump"),
            "debug_dump_file": ("debug", "dump_file"),
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

    def _load_env_vars(self) -> None:
        """Override configuration values from environment variables.

        Any environment variable matching the uppercase version of a dataclass
        field name is applied automatically (e.g., ``TFS_PAT`` for
        ``tfs_pat``). Provider-specific keys also support their conventional
        environment variable names for convenience.
        """
        fields = ReviewConfig.__dataclass_fields__.keys()

        # Automatic mapping: snake_case field -> UPPER_SNAKE_CASE env var.
        env_mapping = {attr: attr.upper() for attr in fields}

        # Conventional aliases for provider-specific API keys and URLs.
        aliases = {
            "openai_api_key": "OPENAI_API_KEY",
            "gemini_api_key": "GEMINI_API_KEY",
            "anthropic_api_key": "ANTHROPIC_API_KEY",
            "ollama_base_url": "OLLAMA_BASE_URL",
            "github_token": "GITHUB_TOKEN",
        }
        for attr, alias in aliases.items():
            if alias not in os.environ:
                continue
            env_mapping[attr] = alias

        for attr, env_name in env_mapping.items():
            value = os.environ.get(env_name)
            if value is None or value == "":
                continue
            coerced = self._coerce_env_value(attr, value)
            if coerced is not None:
                setattr(self, attr, coerced)

    def _coerce_env_value(self, attr: str, value: str):
        """Coerce an environment variable string to the dataclass field type."""
        field_info = ReviewConfig.__dataclass_fields__.get(attr)
        if field_info is None:
            return value

        target_type = field_info.type

        if target_type is bool:
            normalized = value.strip().lower()
            if normalized in ("true", "1", "yes", "y"):
                return True
            if normalized in ("false", "0", "no", "n"):
                return False
            return None
        if target_type is int:
            try:
                return int(value)
            except ValueError:
                return None
        if target_type is float:
            try:
                return float(value)
            except ValueError:
                return None
        if target_type is list:
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

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
            if self.bedrock_secret_access_key and not self.bedrock_access_key_id:
                issues.append(
                    "Provider 'bedrock': access_key_id is missing.\n"
                    "  Configure bedrock.access_key_id when secret_access_key is set."
                )

        if self.verbosity not in ("detailed", "security"):
            issues.append(
                f"Invalid verbosity: '{self.verbosity}'. "
                "Use 'detailed' or 'security'."
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
        """Returns a formatted string describing the configured LLM provider.

        For ``bedrock``, reports one of four credential modes: long-term API key
        (Bearer), IAM explicit (SigV4), named profile, or default credential chain.

        Returns:
            Human-readable summary of provider, model, and credential status.
        """
        provider = self.llm_provider
        model = self.get_effective_model()
        has_key = bool(self.api_key or self.get_effective_api_key())

        if provider == "ollama":
            url = self.get_effective_base_url()
            return f"{provider} | {model} | URL: {url}"
        if provider == "bedrock":
            if self.bedrock_access_key_id and not self.bedrock_secret_access_key:
                creds_mode = "long-term API key (Bearer)"
            elif self.bedrock_access_key_id and self.bedrock_secret_access_key:
                creds_mode = "IAM explicit (SigV4)"
            elif self.bedrock_profile:
                creds_mode = f"profile: {self.bedrock_profile}"
            else:
                creds_mode = "default credential chain"
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
