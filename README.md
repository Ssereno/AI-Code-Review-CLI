# AI Code Review CLI

Automated code review tool with Pull Request integration for Azure DevOps/TFS and support for multiple LLM providers.
**Documentation where** [docs/index.md](docs/index.md) for complete guides on CLI usage, LLM configuration, and architecture.

## Features

- **AI Pull Request Review** — Automated code analysis with configurable LLM providers
- **Structured Comments** — Inline suggestions + general summary comments
- **Dry-run Mode** — Validate reviews before posting
- **Multiple LLM Providers** — OpenAI, Azure OpenAI, Gemini, Claude, Ollama, GitHub Copilot, AWS Bedrock
- **Smart Filtering** — Filter by file extensions, limit diff size
- **Customizable Prompts** — Markdown-based review guidelines
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
- `review_prompt.md` — Customizable review guidelines

### 3. Run Your First Review

```bash
# List active PRs
ai-review list-prs

# Review a specific PR
ai-review pr-review 42

# Dry-run (preview without posting)
ai-review pr-review 42 --dry-run
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
  file_extensions_filter: [".cs", ".ts", ".py"]
  max_diff_files: 50
```

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

tests/
  test_*.py           # Unit and integration tests
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


## Integração com VS Code

Tarefas predefinidas para execução rápida no VS Code:

### Tasks Disponíveis

1. **AI Review: Pull Request (Interactive)**
   - Modo interativo com menu principal
   - Executar com: `Ctrl+Shift+B` → Selecionar task

2. **AI Review: PR (Dry-Run)**
   - Dry-run de um PR específico
   - Pede o ID do PR

3. **AI Review: List Active PRs**
   - Lista PRs ativos
   - Rápido diagnóstico

4. **AI Review: Interactive Mode**
   - Menu completo da ferramenta
   - Execução em background

### Como executar tasks

Nos VS Code:
1. `Ctrl+Shift+P` → "Tasks: Run Task"
2. Seleciona a task desejada
3. Preenche parâmetros se necessário
