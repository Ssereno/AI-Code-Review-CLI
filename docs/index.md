# AI Code Review CLI - Documentation

Automated code review tool using Artificial Intelligence for Pull Requests in Azure DevOps/TFS.

## Local Repository Requirement

> **The CLI must be run from inside the local clone of the repository being reviewed.**

The PR diff is computed using `git fetch` and a three-dot diff against the remote branches. RAG context is built via `git grep` on the local working tree. Both operations require an accessible local git repository.

When `review.rag.enabled: true` the local branch **must match the PR target branch**. The tool verifies this before loading RAG context and blocks the review if there is a mismatch.

```bash
# PR: feature/my-feature → development
cd /path/to/my-repo
git checkout development     # must match the PR target branch
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
