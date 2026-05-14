# Review Parameters

## General Parameters

```yaml
llm:
  max_prompt_tokens: 0      # 0 = provider default/no limit; Bedrock defaults to 180000

review:
  language: en               # Language for comments
  verbosity: detailed        # detailed | quick | security
  scope: diff_with_context   # diff_with_context | diff_only | full_code
  custom_prompt_file: review_prompt.md  # Custom prompt
  max_diff_files: 50         # Max files sent to LLM
  max_diff_lines: 2000       # Max lines per file
  file_extensions_filter: [".cs", ".ts", ".py"]  # Allowlist (empty = all)
  project_context:
    enabled: true            # Include full eligible repository context from the PR source branch
    max_files: 0             # 0 = all eligible files
    max_chars: 0             # 0 = no project-context character limit
    file_extensions: []      # Empty = common text/code files
    exclude_patterns: ["node_modules", "dist", ".env", "*.lock"]
  work_item_context:
    enabled: true            # Include documentation from linked work items
    max_items: 20            # Max linked work items included as context
    max_chars: 100000        # Max work-item context characters
    fields: ["System.Description", "Microsoft.VSTS.Common.AcceptanceCriteria"]
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

Inspect stored usage interactively:

```bash
ai-review usage
ai-review usage --usage-file .ai-review-usage.jsonl
```

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

## Full Repository Context

The default review scope, `diff_with_context`, reviews modified PR lines while also sending the unified diff context plus read-only full repository and work item context to the LLM. Context and deleted lines are used for understanding only; findings and inline comments must still point to added or modified PR lines. Comments outside changed PR lines are discarded before posting. If the combined prompt would exceed `llm.max_prompt_tokens`, only repository context is trimmed; the PR diff and work item documentation are preserved. Use `diff_only` to review only the PR changes without surrounding context, or `full_code` to review the full content of changed files.

The `review.project_context` block controls the full eligible repository snapshot sent to the LLM alongside the PR diff when `scope: diff_with_context` is selected. This context is read-only: the prompt tells the model to use it for architecture, contracts, dependencies, and call sites, while findings and inline comments must still point to modified PR lines. Set `max_files` or `max_chars` to a positive value only when you need to cap very large repositories.

```yaml
review:
  project_context:
    enabled: true
    max_files: 0            # 0 = all eligible files
    max_chars: 0            # 0 = no character limit
    file_extensions: []      # Empty = common text/code files
    exclude_patterns:
      - node_modules
      - dist
      - .env
      - "*.lock"
```

Use `file_extensions` to narrow context for very large repositories:

```yaml
review:
  project_context:
    file_extensions: [".cs", ".ts", ".py", ".yaml"]
```

## Linked Work Item Documentation

The `review.work_item_context` block fetches work items linked to the PR and sends selected documentation fields to the LLM when `scope: diff_with_context` is selected. Common fields include title, description, acceptance criteria, repro steps, and system info. This context is read-only: findings and inline comments must still point to modified PR lines.

```yaml
review:
  work_item_context:
    enabled: true
    max_items: 20
    max_chars: 100000
    fields:
      - System.Title
      - System.WorkItemType
      - System.State
      - System.Description
      - Microsoft.VSTS.Common.AcceptanceCriteria
      - Microsoft.VSTS.TCM.ReproSteps
      - Microsoft.VSTS.TCM.SystemInfo
```

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
