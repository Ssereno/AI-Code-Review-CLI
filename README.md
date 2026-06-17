# AI Code Review CLI

Automated code review tool with Pull Request integration for Azure DevOps/TFS and support for multiple LLM providers.
**Documentation where** [docs/index.md](docs/index.md) for complete guides on CLI usage, LLM configuration, and architecture.

> The CLI can run outside the repository being reviewed. When `tfs.local_repo_path`
> is empty, it creates or reuses a managed local clone under `.ai-review/repos`.

## Features

- **AI Pull Request Review** — Automated code analysis with configurable LLM providers
- **Structured Comments** — Inline suggestions + general summary comments
- **Dry-run Mode** — Validate reviews before posting
- **Multiple LLM Providers** — OpenAI, Azure OpenAI, Gemini, Claude, Ollama, GitHub Copilot, AWS Bedrock
- **Smart Filtering** — Filter by file extensions, limit diff size
- **Project-aware PR Context** — Sends repository, linked work item, and PR description/spec context while restricting findings to modified PR lines
- **RAG Context** — Enriches the review with related code snippets found via `git grep` in the local repository
- **Single Reviewer Context** — One Markdown context file with local override support
- **Usage Tracking** — Store per-PR token usage and optional cost estimates
- **Interactive CLI** — Menu-driven selection and confirmation

## Installation & Quick Start

### 1. Install Package

From PyPI:

```bash
pip install code-review-ai-cli
```

With optional LLM SDK extras:

```bash
pip install "code-review-ai-cli[bedrock]"    # AWS Bedrock
pip install "code-review-ai-cli[openai]"     # OpenAI SDK
pip install "code-review-ai-cli[gemini]"     # Google Gemini SDK
pip install "code-review-ai-cli[claude]"     # Anthropic Claude SDK
pip install "code-review-ai-cli[all]"        # All optional SDKs
```

For development:

```bash
pip install -r requirements.txt
pip install "code-review-ai-cli[dev]"        # Test suite + linting
```

### 2. Initialize Configuration

Generate config templates in your project directory:

```bash
ai-review init
```

Creates:
- `config.yaml` — LLM and review settings
- `review_context.example.md` — Canonical example reviewer context
- `review_context.local.md` — Local reviewer context override, ignored by git
- `.gitignore` entry for `review_context.local.md`


## Configuration File

After `ai-review init`, edit `config.yaml`:

```yaml
llm:
  provider: bedrock                    # or: openai, gemini, claude, etc.
  model: anthropic.claude-3-5-sonnet-20240620-v1:0

bedrock:
  region: us-east-1

tfs:
  base_url: https://dev.azure.com/your-org
  project: YourProject
  pat: xxxxxxxxx
  local_clone_root: .ai-review/repos

review:
  language: pt
  verbosity: detailed                  # or: quick, security
  scope: diff_with_context             # diff_with_context, diff_only
  file_extensions_filter: [".cs", ".ts", ".py"]
  max_diff_files: 50
  max_comments_to_post: 20
  custom_prompt_file: review_context.local.md
  rag:
    enabled: true                      # managed clones align to PR target automatically
    max_chars: 40000
  project_context:
    enabled: true
    mode: on_demand                      # on_demand, full
    manifest_max_chars: 60000
    retrieval_max_rounds: 2
    retrieval_max_files: 20
    retrieval_max_chars: 120000
    retrieval_file_max_chars: 30000
  work_item_context:
    enabled: true
    max_items: 20
  pr_description_context:
    enabled: true
    max_chars: 60000
    max_links: 10
```

### 3. Run Your First Review

```bash
# List active PRs
ai-review list-prs

# Review a specific PR
ai-review pr-review 42

# Dry-run (preview without posting)
ai-review pr-review 42 --dry-run

# Check stored token/cost usage
ai-review usage
```

### Run Tests

```bash
python -m pytest --cov=src --cov-report=term
```

### Code Quality

Project follows PEP8 with Black formatter and Ruff linter:

```bash
# Format code
black src/ tests/

# Lint
ruff check src/ tests/

# Type checking (if using mypy)
mypy src/
```
