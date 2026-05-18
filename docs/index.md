# AI Code Review CLI - Documentation

Automated code review tool using Artificial Intelligence for Pull Requests in Azure DevOps/TFS.

## Installation

Install the CLI from pypi.org

```bash
pip install code-review-ai-cli
```

Initialize configuration by generating templates:

```bash
ai-review init
```

**Output:**
```
config.yaml created at: /home/user/my-project/config.yaml
review_context.example.md created at: /home/user/my-project/review_context.example.md
review_context.local.md created at: /home/user/my-project/review_context.local.md
.gitignore updated with: review_context.local.md

Edit config.yaml and review_context.local.md for local settings.
```

If files already exist, confirmation is requested:

```
config.yaml already exists in the current directory.
Overwrite? [y/N]
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
