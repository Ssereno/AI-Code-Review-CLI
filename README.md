# AI Code Review

AI Code Review is an automated AI-powered code review CLI, compatible with various LLM providers, designed for integration with Azure DevOps, whether in the cloud or on-premises. It allows you to run a review on demand or as part of a pipeline triggered before a commit or when a pull request is created.

## Features

- AI Pull Request review (`pr-review`)
- Local Git review without a PR (`local` / `local-review` — working tree, `--staged`, `--from <branch>`, `--commit <sha>`)
- **Full Code Scan (`full-code-scan`)**: on-demand static quality and security audit of files, folders, or glob patterns without requiring a Git diff or PR
- **Smart File Bundling**: groups related files (interfaces, implementations, unit tests, i18n pairs, and domain folders) into cohesive bundles (`max_files_per_bundle`) so large changesets are reviewed completely without context degradation
- **Deterministic Cross-File Symbol Resolution**: resolves compact signatures and type declarations (`<referenced_symbols>`) via Tree-sitter across PR/local changesets and relative/sibling imports (`max_referenced_symbols`)
- Structured PR comments (inline + general summary)
- `dry-run` mode to validate without posting
- PR listing with filters (`list-prs`)
- Configuration exclusively via `config.yaml`
- Providers LLM: OpenAI, Azure OpenAI, Gemini, Claude, Ollama, GitHub Copilot, AWS Bedrock
- Diff filtering by **excluded path prefixes** (`excluded_paths`) and/or **file extensions** (`file_extensions_filter`)
- Prompt token consumption estimation with LiteLLM (`Total Tokens`)
- Validation to ensure comments are not duplicated in re-runs (CI/CD Pipelines)

## Documentation

- [Configuration Guide](docs/configuration.md) - Details on YAML parameters and settings.
- [Commands Reference](docs/commands.md) - Complete list of CLI commands and arguments.
- [Execution Flow](docs/execution_flow.md) - Mermaid diagram of the application's lifecycle.
- [Troubleshooting](docs/troubleshooting.md) - Common issues and solutions.

## Output Review Format

![Review Output Format Example](/imgs/review_output.png)


## Installation

Install from PyPI:

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
  custom_prompt_file: review_prompt.md
  # maximum related files grouped per LLM review bundle (Smart File Bundling)
  max_files_per_bundle: 5
  # per-file limit
  max_diff_lines: 2000
  # cross-file symbol declarations resolved per file (0 = disabled)
  max_referenced_symbols: 10
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

## License
This project is licensed under the Apache Licence 2.0 — see the [LICENSE](/License).