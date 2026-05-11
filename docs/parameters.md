# Review Parameters

## General Parameters

```yaml
review:
  language: en               # Language for comments
  verbosity: detailed        # detailed | quick | security
  scope: diff_only           # diff_only | full_code
  custom_prompt_file: review_prompt.md  # Custom prompt
  max_diff_files: 50         # Max files sent to LLM
  max_diff_lines: 2000       # Max lines per file
  file_extensions_filter: [".cs", ".ts", ".py"]  # Allowlist (empty = all)
```

## Comment Parameters

```yaml
pr:
  auto_post_comments: false  # Automatically post comments
  dry_run: false             # Do not post, only preview
  comment_mode: structured   # structured | inline
```

## Usage Tracking

Each completed PR review can append one JSON record with the LLM calls, token
totals, and optional cost estimate.

```yaml
usage:
  enabled: true
  file: .ai-review-usage.jsonl
  pricing:
    openai:
      gpt-4o-mini:
        input_per_1m: 0.15
        output_per_1m: 0.60
        currency: USD
```

Cost is only calculated when `usage.pricing` contains the provider/model price.
Prices are intentionally configurable because provider prices and enterprise
contracts can change independently from this CLI.

## File Extension Filtering

The `file_extensions_filter` option works as an **allowlist**: only files with the listed extensions are sent to the LLM.

Review only C#, TypeScript, and Python files

```yaml
review:
  file_extensions_filter: [".cs", ".ts", ".py"]
```

Review all files

```yaml
review:
  file_extensions_filter: []  # Empty list = no filter
```

> **Note:** If no eligible files remain after filtering, the review ends with a warning without calling the LLM.

## Custom Prompt (Markdown)

The `review_prompt.md` file is automatically injected on each run and allows you to give more context to the LLM about your project.

```markdown
# Code Review Guidelines

## Style
- Use English comments
- Be respectful and constructive
- Focus on logic and best practices

## Mandatory Rules
- Check for null pointer exceptions
- Verify SQL injection risks
- Ensure proper error handling

## Examples
- Good: `if user is not None:`
- Bad: `if user:`
```

Customize the prompt to fit your project:

```yaml
review:
  custom_prompt_file: review_prompt.md
```

## Common Issues

### TLS/SSL Error on On-Prem TFS

Use a CA bundle in `config.yaml`:

```yaml
tfs:
  verify_ssl: true
  ca_bundle: C:/certs/corporate-root-ca.pem
```

Avoid `verify_ssl: false` except for temporary troubleshooting.
