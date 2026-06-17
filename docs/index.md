# AI Code Review CLI - Documentation

Automated code review tool using Artificial Intelligence for Pull Requests in Azure DevOps/TFS.

## Local Repository Handling

The CLI can run outside the repository being reviewed. When `tfs.local_repo_path`
is empty, it resolves the Azure DevOps/TFS clone URL and creates or reuses a
managed clone under `.ai-review/repos`.

The PR diff is computed locally using `git fetch` and a three-dot diff against
the remote target/source branches. Repository structure and requested file
context are also read from the local clone.

When `review.rag.enabled: true`, managed clones are aligned to the PR target
branch automatically. If you set `tfs.local_repo_path` explicitly, that local
repository must already be checked out on the PR target branch.

```bash
# PR: feature/my-feature → development
ai-review pr-review 42
```

## Installation

Install the CLI from pypi.org

```bash
pip install code-review-ai-cli
```

Initialize configuration by generating templates:

```bash
ai-review init
```

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Initialize configuration
ai-review init

# Edit config.yaml with credentials and preferences
config.yaml

# List active PRs
ai-review list-prs

# Review a specific PR
ai-review pr-review 42

# Dry-run mode (without posting comments)
ai-review pr-review 42 --dry-run
```

## Documentation

### [Parameters](./parameters.md)

### [Supported LLM's](./llm_configuration.md)

### [CLI Commands](./cli_commands.md)

### [Execution Flow & Diagrams](./flow_diagrams.md)
