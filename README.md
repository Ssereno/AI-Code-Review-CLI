# AI Code Review

AI Code Review is an automated AI-powered code review CLI, compatible with various LLM providers, designed for integration with Azure DevOps, whether in the cloud or on-premises. It allows you to run a review on demand or as part of a pipeline triggered before a commit or when a pull request is created.

## Features

- AI Pull Request review (`pr-review`)
- Structured PR comments (inline + general summary)
- `dry-run` mode to validate without posting
- PR listing with filters (`list-prs`)
- Configuration via `config.yaml`, environment variables, or `.env` files
- Providers LLM: OpenAI, Azure OpenAI, Gemini, Claude, Ollama, GitHub Copilot, AWS Bedrock
- Diff filtering by **excluded path prefixes** (`excluded_paths`) and/or **file extensions** (`file_extensions_filter`)

## Documentation

- [Configuration Guide](docs/configuration.md) - Details on YAML parameters and settings.
- [Commands Reference](docs/commands.md) - Complete list of CLI commands and arguments.
- [Execution Flow](docs/execution_flow.md) - Mermaid diagram of the application's lifecycle.
- [Troubleshooting](docs/troubleshooting.md) - Common issues and solutions.

## Output Review Format

![Review Output Format Example](/imgs/review_output.png)

## Installation

Install from PyPI:
s
```bash
pip install code-review-ai-cli
```

After installing the package, generate ready-to-edit configuration files in your working directory:

```bash
ai-review init
```

This copies two bundled templates:

- **`config.yaml`** — all available options with inline documentation
- **`review_prompt.md`** — default review style rules, injected into every LLM prompt

The tool resolves configuration in the following order of priority:

1. CLI arguments (e.g., `--config`, `--provider`)
2. Environment variables, including values from a `.env` file in the current working directory
3. `config.yaml` in the current working directory
4. Built-in defaults

You can pass a different config path with `--config`:

```bash
ai-review pr-review --config ~/configs/ai-review.yaml
```

Environment variables are named by converting each YAML setting to `UPPER_SNAKE_CASE` — for example, `TFS_PAT`, `LLM_PROVIDER`, or `OPENAI_API_KEY`. See the [Configuration Guide](docs/configuration.md) for the full mapping, type coercion rules, and `.env` examples.

If you plan to run the test suite locally, also install development dependencies:

```bash
pip install "code-review-ai-cli[dev]"
```

## Minimal Example

Example `config.yaml`:

```yaml
llm:
  provider: openai
  model: gpt-4o

openai:
  api_key: sk-xxxx

tfs:
  base_url: https://dev.azure.com/your-organization
  project: ProjectName
  pat: xxxxxxxxx
  verify_ssl: true
  # ca_bundle: C:/certs/corporate-root-ca.pem

review:
  verbosity: detailed
  scope: diff_only
  custom_prompt_file: review_prompt.md
  # file limit sent to the LLM
  max_diff_files: 50
  # per-file limit
  max_diff_lines: 2000
  # extension allowlist (empty list = all files)
  file_extensions_filter: [".cs", ".ts", ".py"]
  # exclude path prefixes from the diff (applied before file_extensions_filter)
  # leading slash is optional; add trailing slash for exact folder boundary
  excluded_paths: ["libs/generated"]

pr:
  auto_post_comments: false
  dry_run: false
  comment_mode: structured

output:
  format: terminal
  file: ""
  color: true
```

## Available VS Code Tasks

- `AI Review: Pull Request (Interactive)`
- `AI Review: PR (Dry-Run)`
- `AI Review: List Active PRs`
- `AI Review: Interactive Mode`
