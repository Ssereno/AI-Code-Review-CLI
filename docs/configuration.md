# Configuration

The tool is configured through a `config.yaml` file, environment variables, or a `.env` file. You can generate a template file (along with the `review_prompt.md`) by running the `ai-review init` command.

## Configuration Sources and Priority

The configuration is resolved from multiple sources, in the following order of priority (highest first):

1. **CLI arguments** — values passed directly on the command line.
2. **Environment variables** — variables set in the shell session. If `python-dotenv` is installed, values from a `.env` file in the working directory are also loaded into the environment and treated the same way.
3. **`config.yaml`** — the YAML configuration file.
4. **Built-in defaults** — used when a value is not provided by any other source.

This means a value set as an environment variable (or in `.env`) will override the same value in `config.yaml`, and a CLI argument will override both.

## Environment Variables

Any configuration parameter can be set via an environment variable by converting the field name to `UPPER_SNAKE_CASE`. For example:

- `llm_provider` → `LLM_PROVIDER`
- `tfs_pat` → `TFS_PAT`

### Type Coercion

Environment variables are strings, so the tool automatically coerces them to the expected type:

| Target Type | Accepted Formats                           | Example |
|-------------|--------------------------------------------|----------------------------------|
| `bool`      | `true`/`1`/`yes`/`y`, `false`/`0`/`no`/`n` | `TFS_VERIFY_SSL=false`           |
| `int`       | Valid integer string | `MAX_TOKENS=8192`   |
| `float`     | Valid float string | `TEMPERATURE=0.5`     |
| `list`      | Comma-separated values                     | `FILE_EXTENSIONS_FILTER=.py,.md` |

### Example `.env` file

```bash
# Azure DevOps credentials
TFS_BASE_URL=https://dev.azure.com/myorg
TFS_PAT=my-personal-access-token

# LLM provider
LLM_PROVIDER=openai
```

## Configuration Parameters

Below is a list of all parameters available in `config.yaml`, organized by category:

### LLM

| Parameter | Description | Example / Default |
|---|---|---|
| `provider` | Provider to use (openai, gemini, claude, ollama, azure_openai, copilot, bedrock) | `openai` |
| `model` | Model to use. Leave empty to use the provider's default. | `""` |
| `max_tokens` | Maximum limit of tokens in the response. | `4096` |
| `temperature` | Response temperature (0.0 = deterministic, 1.0 = creative). | `0.0` |

### Provider

| Parameter | Description | Example / Default |
|---|---|---|
| `[provider].*` | Access credentials vary depending on the provider (e.g. `openai.api_key`, `bedrock.region`). | (See template) |

### TFS

| Parameter | Description | Example / Default |
|---|---|---|
| `base_url` | Base URL of Azure DevOps / TFS. | `https://dev.azure.com/org` |
| `collection` | Name of the collection (usually DefaultCollection). | `DefaultCollection` |
| `project` | Name of the project. | `your-project` |
| `pat` | Personal Access Token for authentication. | |
| `verify_ssl` | Validate SSL certificate. | `true` |
| `ca_bundle` | (Optional) Path to a corporate CA certificate. | `C:/certs/...` |
| `repository` | (Optional) Default repository filter. | |

### Review

| Parameter | Description | Example / Default |
|---|---|---|
| `review.verbosity` | Review detail level (`detailed` or `security`). | `detailed` |
| `review.scope` | Controls how much code is sent. (See **Review Scope** below). | `diff_only` |
| `review.custom_prompt_file` | Path to the markdown file with additional rules. (See **Markdown-Customizable Prompt**). | `review_prompt.md` |
| `review.max_diff_files` | Maximum number of files sent to the LLM per review. | `50` |
| `review.max_diff_lines` | Maximum lines per file before truncating. | `4500` |
| `review.max_scope_blocks` | Changed blocks limit for the *Enclosing Scopes* strategy (`diff_only` mode). | `3` |
| `review.max_scope_lines` | Line limit in a scope before falling back to *file_skeleton*. | `250` |
| `review.adaptive_context_lines`| Surrounding lines to keep for *Adaptive Diff*. | `10` |
| `review.file_extensions_filter`| Restrictive list of extensions to review (empty = all). (See **Filter by File Extension** below). | `[]` |

### PR

| Parameter | Description | Example / Default |
|---|---|---|
| `auto_post_comments`| Post comments to the PR automatically without confirmation. | `false` |
| `dry_run` | Run the review without posting comments to the repository. | `false` |
| `comment_mode` | Comment mode: `structured` (inline) or `general` (single comment). | `structured` |

### Output

| Parameter | Description | Example / Default |
|---|---|---|
| `format` | Format of the locally generated output (`terminal`, `markdown`, `json`). | `terminal` |
| `file` | Path for the review output file (empty = console only). | `""` |
| `color` | Enable or disable console colors. | `true` |

### Debug

| Parameter | Description | Example / Default |
|---|---|---|
| `dump` | Enable logging of raw responses and debug info. | `false` |
| `dump_file` | File where debug info will be saved. | `logs/debug.log` |

## Review Scope

`review.scope` controls how much code is sent to the LLM for each changed file.

| Scope | Description |
|---|---|
| `diff_only` | Default. Unified diff (changed lines only) + AST-based context block (Enclosing Scopes, File Skeleton, or Adaptive Diff). |
| `full_code` | All lines of the new file version, every line prefixed with `+`. No baseline. |

### `diff_only` — diff with AST context (default)

In `diff_only` mode the tool automatically selects the most token-efficient context
strategy for each changed file:

| Strategy | When used | XML tag |
|---|---|---|
| **Enclosing Scopes** | ≤ 3 distinct changed blocks | `<enclosing_scopes file="...">` |
| **File Skeleton** | > 3 distinct changed blocks | `<file_skeleton file="...">` |
| **SQL Statement** | SQL files | `<sql_statement file="...">` |
| **Adaptive Diff** | Tree-sitter unavailable / unsupported language | `<adaptive_diff file="...">` |

Supported languages for AST parsing: **Python, JavaScript, TypeScript, TSX, C#, Java, SQL**.

```yaml
review:
  scope: diff_only
  # scope: full_code
```

## Filter by File Extension

`file_extensions_filter` works as an **allowlist**: only files with listed extensions are sent to the LLM for review. Remaining files are excluded from the diff before any processing.

```yaml
review:
  # Review only C#, TypeScript, and Python code
  file_extensions_filter: [".cs", ".ts", ".py"]
```

To review **all** PR files, leave the list empty:

```yaml
review:
  file_extensions_filter: []
```

> **Note:** If no eligible files remain after filtering, the review ends with a warning without calling the LLM.

## Markdown-Customizable Prompt

`ai-review init` creates a `review_prompt.md` alongside `config.yaml`. This file is loaded automatically and injected into LLM instructions on every run.

Edit it to tailor the review to your team:

- Define comment tone and format
- Add mandatory validation rules
- Include business/architecture context
- Add examples of good/bad comments

**Rules can be scoped** to specific file types using language tags. During a review, the AI detects the files changed in the diff, identifies their extensions, and only loads:

- Rules marked with `<!-- lang: all -->` for all files.
- Rules matching the extensions of the files being reviewed. Example `<!-- lang: cs,ts -->` applied when .cs or .ts files are present.

The path for the **markdown-customizable prompt** is configurable in `config.yaml` (default: `review_prompt.md` in the current directory):

```yaml
review:
  custom_prompt_file: review_prompt.md
```

### Debug Dump

```yaml
debug:
  dump: true
  dump_file: logs/llm_prompt_debug.log
```