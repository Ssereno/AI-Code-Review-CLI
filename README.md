# AI Code Review CLI

Automated code review tool with Pull Request integration for Azure DevOps/TFS and support for multiple LLM providers.
**Documentation where** [docs/index.md](docs/index.md) for complete guides on CLI usage, LLM configuration, and architecture.

## Features

- **AI Pull Request Review** — Automated code analysis with configurable LLM providers
- **Structured Comments** — Inline suggestions + general summary comments
- **Dry-run Mode** — Validate reviews before posting
- **Multiple LLM Providers** — OpenAI, Azure OpenAI, Gemini, Claude, Ollama, GitHub Copilot, AWS Bedrock
- **Smart Filtering** — Filter by file extensions, limit diff size
- **Project-aware PR Context** — Sends repository and linked work item context while restricting findings to modified PR lines
- **RAG Context** — Enriches the review with related code snippets found via `git grep` in the local repository
- **Single Reviewer Context** — One Markdown context file with local override support
- **Usage Tracking** — Store per-PR token usage and optional cost estimates
- **Interactive CLI** — Menu-driven selection and confirmation

## Local Repository Requirement

> **The CLI must be run from inside the local clone of the repository being reviewed.**

The PR diff is fetched from Azure DevOps/TFS, but several features depend on the local git state:

| Feature | Local dependency |
|---|---|
| PR diff | `git fetch` + three-dot diff against `origin/<branch>` |
| RAG context | `git grep` on the working tree |
| Branch validation | `git branch --show-current` |

### Branch requirement when RAG is enabled

When `review.rag.enabled: true`, the local repository **must be checked out on the PR target branch** (e.g. `development`). If the local branch differs from the target, the review is blocked:

```
Local branch mismatch: you are on 'main' but this PR targets 'development'.
RAG context would be built from the wrong branch, which may corrupt the review.
Please run:  git checkout development
```

**Example:**
```bash
# PR: merge 'feature/my-feature' → 'development'
cd /path/to/my-repo          # must be inside the repo
git checkout development     # must match PR target branch
ai-review pr-review 42
```

To skip the branch check entirely, disable RAG:
```yaml
review:
  rag:
    enabled: false
```

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

review:
  language: pt
  verbosity: detailed                  # or: quick, security
  scope: diff_with_context             # diff_with_context, diff_only
  file_extensions_filter: [".cs", ".ts", ".py"]
  max_diff_files: 50
  max_comments_to_post: 20
  custom_prompt_file: review_context.local.md
  rag:
    enabled: true                      # requires local branch == PR target
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
```

By default, repository context is loaded on demand: the prompt includes explicit
source-branch change packets, full changed-file contents as read-only context,
linked work item documentation, and a repository manifest, then the model
requests any extra files it needs. Inline PR comments must still be grounded in
actual changed lines from the change packets. Set
`review.project_context.mode: full` to send the full eligible repository
snapshot instead. Bedrock uses a default estimated prompt budget of 180000
tokens; override it with `llm.max_prompt_tokens` if your model supports more or
less.

Reviewer context is loaded from exactly one Markdown file. By default the CLI
uses `review_context.local.md` when it exists, otherwise it falls back to the
packaged `src/prompts/review_context.example.md`. Keep local team tweaks in
`review_context.local.md`; it is gitignored so the canonical context cannot
drift across machines.

For Copilot-backed reviews, Claude Sonnet models are often strong choices for
large PR validation, for example `llm.provider: copilot` with a Claude Sonnet
model available to your organization. The tool still enforces the same
source-branch grounding, duplicate checks, and comment cap regardless of model.

## Development & Testing

This is a standard Python project with the following structure:

```
src/
  ai_review.py        # CLI entry point
  config.py           # Configuration management
  llm_client.py       # LLM provider abstraction
  tfs_client.py       # Azure DevOps integration
  git_utils.py        # Git diff processing
  formatter.py        # Output formatting (terminal, markdown, JSON)
  rag_engine.py       # RAG context via git grep

tests/
  test_*.py           # Unit and integration tests
```

## RAG Context

### Current Implementation

The RAG engine enriches the LLM prompt with related code snippets found in the local repository:

1. **Extract identifiers** — function and class names are parsed from the PR diff
2. **Search** — `git grep` finds files containing those identifiers
3. **Extract snippets** — ±10 lines around each match are included as read-only context

All operations run locally with no extra dependencies. The quality of RAG context depends entirely on the local repository state, which is why the **local branch must match the PR target branch**.

### Recommended Stack for Enhanced RAG (Local & Open Source)

For teams wanting semantic similarity instead of keyword search, the recommended local stack is:

| Component | Library | Reason |
|---|---|---|
| **Vector database** | [ChromaDB](https://www.trychroma.com/) | Runs in-memory or persists to a local SQLite file — no server needed, `pip install chromadb` |
| **Embeddings** | [sentence-transformers](https://www.sbert.net/) | Generates vectors locally on CPU — no API calls, no cost |

Example integration pattern:

```python
from sentence_transformers import SentenceTransformer
import chromadb

model = SentenceTransformer("all-MiniLM-L6-v2")   # ~80 MB, CPU-friendly
client = chromadb.Client()                          # in-memory
collection = client.create_collection("repo-index")

# Index
collection.add(
    documents=[snippet_text],
    embeddings=model.encode([snippet_text]).tolist(),
    ids=["file:line"],
)

# Query
results = collection.query(
    query_embeddings=model.encode([query]).tolist(),
    n_results=5,
)
```

This stack keeps the CLI lightweight (`pip install`) and requires no external services or paid APIs.

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


## VS Code Integration

Predefined tasks for quick execution in VS Code:

### Available Tasks

1. **AI Review: Pull Request (Interactive)**
  - Interactive mode with main menu
  - Run with: `Ctrl+Shift+B` → Select task

2. **AI Review: PR (Dry-Run)**
  - Dry-run for a specific PR
  - Prompts for PR ID

3. **AI Review: List Active PRs**
  - Lists active PRs
  - Quick diagnostics

4. **AI Review: Interactive Mode**
  - Full tool menu
  - Runs in background

### How to run tasks

In VS Code:
1. `Ctrl+Shift+P` → "Tasks: Run Task"
2. Select the desired task
3. Fill in parameters if needed
