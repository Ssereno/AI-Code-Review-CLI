# Supported Commands and Options

```bash
ai-review pr-review [pr_id]
```

Options:

- `--repo-name`, `-r`
- `--dry-run`
- `--auto-post`
- `--author`
- `--target-branch`
- `--quick` / `--detailed` / `--security`
- `--review-scope {diff_only,full_code}` (default: `diff_only`)
- `--max-diff-files N` — overrides `review.max_diff_files` from config.yaml
- `--context`, `-c`
- `--format {terminal,markdown,json}`
- `--output`, `-o`
- `--no-color`
- `--debug-dump`
- `--model`, `-m`
- `--provider`, `-p`
- `--config`

## CLI Usage

### Help

```bash
ai-review pr-review --help
```

### Bootstrap configuration

Generate `config.yaml` and `review_prompt.md` templates in the current directory:

```bash
ai-review init
```

### Interactive Mode

```bash
ai-review pr-review
```

### Pull Request Review

List PRs and select interactively:

```bash
ai-review pr-review
```

Review a specific PR:

```bash
ai-review pr-review 42
```

Dry-run:

```bash
ai-review pr-review 42 --dry-run
```

Full review of changed files (in addition to diff-focused review):

```bash
ai-review pr-review 42 --review-scope full_code
```

Automatic posting (without confirmation):

```bash
ai-review pr-review 42 --auto-post
```

Filter PRs in interactive selection:

```bash
ai-review pr-review --author "John Smith" --target-branch main
```

Choose provider/model via CLI:

```bash
ai-review pr-review 42 --provider bedrock --model anthropic.claude-3-5-sonnet-20240620-v1:0
```

### List Pull Requests

```bash
ai-review pr-review list-prs
ai-review pr-review list-prs --status completed
ai-review pr-review list-prs --repo-name backend --author "John"
```

### `list-prs`

```bash
ai-review pr-review list-prs
```

Options:
- `--repo-name`, `-r`
- `--status {active,completed,abandoned,all}`
- `--author`
