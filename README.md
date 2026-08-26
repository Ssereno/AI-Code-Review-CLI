# AI Code Review

AI Code Review is an automated AI-powered code review CLI, compatible with various LLM providers, designed for integration with Azure DevOps, whether in the cloud or on-premises. It allows you to run a review on demand or as part of a pipeline triggered before a commit or when a pull request is created.

## Features

- AI Pull Request review (`pr-review`)
- Structured PR comments (inline + general summary)
- `dry-run` mode to validate without posting
- PR listing with filters (`list-prs`)
- Configuration exclusively via `config.yaml`
- Providers LLM: OpenAI, Azure OpenAI, Gemini, Claude, Ollama, GitHub Copilot, AWS Bedrock
- Diff filtering by **excluded path prefixes** (`excluded_paths`) and/or **file extensions** (`file_extensions_filter`)
- Prompt token estimation with LiteLLM (`Total Prompt`)

## Documentation

- [Configuration Guide](docs/configuration.md) - Details on YAML parameters and settings.
- [Commands Reference](docs/commands.md) - Complete list of CLI commands and arguments.
- [Execution Flow](docs/execution_flow.md) - Mermaid diagram of the application's lifecycle.
- [Troubleshooting](docs/troubleshooting.md) - Common issues and solutions.

## Output Review Format

![Review Output Format Example](/imgs/review_output.png)

During a PR review, the terminal also displays `Total Prompt`, an estimate of
the input tokens in the complete payload sent to the configured provider. The
provider comes from `llm.provider` and the model comes from `llm.model` or its
default one. If LiteLLM cannot resolve the provider/model combination,
the tool tries the model without a provider prefix and then uses local
`tiktoken` as a final fallback.

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

The tool looks for `config.yaml` in the **current working directory** at runtime. You can also pass a different path with `--config`:

```bash
ai-review pr-review --config ~/configs/ai-review.yaml
```

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
