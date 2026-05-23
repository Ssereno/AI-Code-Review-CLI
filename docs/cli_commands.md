# CLI Usage Guide

Complete usage guide for the command-line tool.

## init - Initialize Configuration

Generate the config template and reviewer context files in the current
directory:

```bash
ai-review init
```

Creates:

- `config.yaml`
- `review_context.example.md`
- `review_context.local.md`

## pr-review - Pull Request Review

Main command to review Pull Requests.

### Interactive Mode

Lists active PRs and allows you to select which one to review:

```bash
ai-review pr-review
```

**Interaction:**
1. Select a PR from the list
2. Optionally configure filters (author, branch, etc.)
3. Choose model and provider
4. Review the proposed comments
5. Confirm posting or abort

### Review a Specific PR

Provide the PR ID directly:

```bash
ai-review pr-review 42
```

### Review with Interactive Filters

Filter PRs before selecting:

```bash
ai-review pr-review --author "John Smith" --target-branch main
ai-review pr-review -r backend --author "Jane"
```

**Available filters:**
- `--author TEXT` — Filter by author
- `--repo-name TEXT`, `-r` — Filter by repository
- `--target-branch TEXT` — Filter by target branch

### Review Options

#### Dry-run (without posting comments)

Performs the full review but does not post comments:

```bash
ai-review pr-review 42 --dry-run
```

Useful to validate before real posting.

#### Auto-post (no confirmation)

Automatically posts comments without confirmation:

```bash
ai-review pr-review 42 --auto-post
```

#### Review Scope
Choose the review scope:

```bash
# PR changes with project and work item context (default)
ai-review pr-review 42 --review-scope diff_with_context

# Only the changes present in the diff
ai-review pr-review 42 --review-scope diff_only
```

#### Verbosity

Choose the review style:

```bash
ai-review pr-review 42 --quick
ai-review pr-review 42 --detailed
ai-review pr-review 42 --security
```

### Choose model and provider

#### Via CLI

```bash
ai-review pr-review 42 --provider openai --model gpt-4o
ai-review pr-review 42 --provider bedrock --model anthropic.claude-3-5-sonnet-20240620-v1:0
ai-review pr-review 42 -p gemini -m gemini-2.0-flash
```

### Diff limits

#### Override maximum files

```bash
# Envia apenas os primeiros 20 ficheiros ao LLM
ai-review pr-review 42 --max-diff-files 20
```

Overrides the `review.max_diff_files` option in `config.yaml`.

#### Add context

```bash
ai-review pr-review 42 --context "This is a hotfix for urgent production bug"
ai-review pr-review 42 -c "Refactor project structure, no logic changes"
```

### Output and formatting

#### Output format

```bash
ai-review pr-review 42 --format terminal
ai-review pr-review 42 --format markdown
ai-review pr-review 42 --format json
```

#### Save to file

```bash
ai-review pr-review 42 --output review.md
ai-review pr-review 42 --output review.json --format json
ai-review pr-review 42 -o ./reports/pr-42.txt
```

#### No colors (clean output)

```bash
ai-review pr-review 42 --no-color
```

### Configuration file

#### Use a custom configuration

```bash
ai-review pr-review 42 --config ~/configs/ai-review.yaml
ai-review pr-review 42 --config /etc/ai-review/prod.yaml
```

The tool looks for `config.yaml` in the current directory by default.

### Combined examples

#### Security-focused review

```bash
ai-review pr-review 42 \
  --security \
  --dry-run \
  --format markdown \
  --output security-review.md
```

#### Quick review using Claude via Bedrock

```bash
ai-review pr-review 42 \
  --quick \
  --provider bedrock \
  --model anthropic.claude-3-5-sonnet-20240620-v1:0 \
  --auto-post
```

#### List and review specific PRs

```bash
ai-review pr-review \
  --repo-name backend \
  --author "DevOps Team" \
  --target-branch develop
```


## list-prs - List Pull Requests

Lists PRs with filters.

### List all active PRs

```bash
ai-review list-prs
```

### List by status

```bash
ai-review list-prs --status active
ai-review list-prs --status completed
ai-review list-prs --status abandoned
ai-review list-prs --status all
```

### Filter by author

```bash
ai-review list-prs --author "John Smith"
ai-review list-prs --author "Jane"
```

### Filter by repository

```bash
ai-review list-prs --repo-name backend
ai-review list-prs -r frontend
```

### Combine filters

```bash
ai-review list-prs --repo-name backend --author "DevOps" --status active
ai-review list-prs -r frontend --status completed
```

## Help

### Mostrar ajuda geral

```bash
ai-review --help
ai-review -h
```

### Show help for a specific command

```bash
ai-review pr-review --help
ai-review list-prs --help
ai-review init --help
```


## Options for `pr-review`

| Flag | Values | Description |
|------|--------|-------------|
| `pr_id` | number | PR ID to review |
| `--repo-name`, `-r` | text | Filter by repository |
| `--author` | text | Filter by author |
| `--target-branch` | text | Filter by target branch |
| `--dry-run` | — | Do not post comments |
| `--auto-post` | — | Post without confirmation |
| `--quick` | — | Short review |
| `--detailed` | — | Detailed review (default) |
| `--security` | — | Security-focused review |
| `--review-scope` | `diff_with_context`, `diff_only` | Review scope |
| `--max-diff-files` | number | Maximum number of files |
| `--context`, `-c` | text | Additional context |
| `--format` | `terminal`, `markdown`, `json` | Output format |
| `--output`, `-o` | path | Save results to file |
| `--no-color` | — | Disable colors |
| `--provider`, `-p` | text | LLM provider |
| `--model`, `-m` | text | LLM model |

## Options for `list-prs`

| Flag | Values | Description |
|------|--------|-------------|
| `--repo-name`, `-r` | text | Filter by repository |
| `--author` | text | Filter by author |
| `--status` | `active`, `completed`, `abandoned`, `all` | PR status |


## usage - Check token usage and cost

Lists reviewed Pull Requests from the usage file and lets you select one PR
to view its token and cost totals.

```bash
ai-review usage
```

Use a specific usage file:

```bash
ai-review usage --usage-file ./reports/ai-review-usage.jsonl
```

The command shows PRs grouped by repository and PR ID. After selecting a PR by
list number or PR ID, it displays:

- Input tokens
- Output tokens
- Total tokens
- Estimated total cost, if `usage.pricing` is configured
- Providers/models used
- Number of review runs and LLM calls

## `usage` Options

| Flag | Values | Description |
|------|--------|-----------|
| `--usage-file` | path | JSONL file with saved usage records |
| `--config` | path | Configuration file |
| `--no-color` | — | Disable colors |



## Configuration from file

Although many options are available via the CLI, defaults come from `config.yaml`:

```yaml
llm:
  provider: bedrock
  model: anthropic.claude-3-5-sonnet-20240620-v1:0
  temperature: 0.3

review:
  language: pt
  verbosity: detailed
  scope: diff_with_context
  custom_prompt_file: review_context.local.md
  file_extensions_filter: [".cs", ".ts", ".py"]
  max_diff_files: 50
  max_diff_lines: 2000
  project_context:
    enabled: true
    mode: on_demand

pr:
  auto_post_comments: false
  dry_run: false
```

**Priority:**
1. CLI flags (high priority)
2. Config file `config.yaml` (default)
3. Hardcoded values (fallback)

## Full flow examples

### Scenario 1: Quick review before merge

```bash
# List PRs
ai-review list-prs --status active

# Review a PR with dry-run
ai-review pr-review 123 --quick --dry-run

# If OK, post the real review
ai-review pr-review 123 --quick --auto-post
```

### Scenario 2: Security audit

```bash
# Security-focused review with full context
ai-review pr-review 456 \
  --security \
  --review-scope diff_with_context \
  --format json \
  --output security-audit.json
```
