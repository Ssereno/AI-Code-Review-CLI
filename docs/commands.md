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
- `--detailed` / `--security`
- `--review-scope {diff_only,full_code}` (default: `diff_only`)
- `--max-files-per-bundle N` — overrides `review.max_files_per_bundle` from config.yaml
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
ai-review
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

### List Pull Requests (`list-prs`)

```bash
ai-review list-prs
ai-review list-prs --status completed
ai-review list-prs --repo-name backend --author "John"
```

Options:
- `--repo-name`, `-r`
- `--status {active,completed,abandoned,all}`
- `--author`

### Local Git Review (`local` / `local-review`)

Review local Git changes before opening a Pull Request (no TFS/Azure DevOps configuration required):

```bash
# Review uncommitted changes (working tree + staged + untracked files)
ai-review local

# Review only staged changes (git diff --cached)
ai-review local --staged

# Compare current branch against a base branch (e.g. main)
ai-review local --from main

# Compare two specific branches or refs
ai-review local --from main --to feature/my-branch

# Review a single commit
ai-review local --commit abc1234
```

Options:
- `--staged`, `-s` — Review only staged changes (`git diff --cached`)
- `--from REF`, `--base REF` — Base branch or commit to compare from
- `--to REF` — Target branch or commit to compare to (default: `HEAD` when `--from` is used)
- `--commit SHA` — Review a single commit (`<commit>~1..<commit>`)
- `--repo-path PATH` — Path to the local Git repository (default: current directory)
- Supports all global review, output, and configuration options (`--detailed`, `--security`, `--review-scope`, `--output`, `--provider`, `--model`, etc.)
