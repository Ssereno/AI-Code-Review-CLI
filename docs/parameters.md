# Configuration Parameters

This file documents every `config.yaml` option loaded by the CLI. CLI flags
override `config.yaml`; values omitted from `config.yaml` fall back to the
defaults in `ReviewConfig`.

## Complete Example

```yaml
llm:
  provider: openai
  api_key: ""
  api_base_url: ""
  model: ""
  max_tokens: 4096
  max_prompt_tokens: 0
  temperature: 0.3

openai:
  api_key: ""

gemini:
  api_key: ""

claude:
  api_key: ""

ollama:
  base_url: http://localhost:11434

copilot:
  github_token: ""

bedrock:
  region: us-east-1
  profile: ""
  access_key_id: ""
  secret_access_key: ""
  session_token: ""

tfs:
  base_url: https://dev.azure.com/your-org
  collection: DefaultCollection
  project: your-project
  pat: your-personal-access-token
  verify_ssl: true
  ca_bundle: ""
  repository: ""

review:
  language: en
  verbosity: detailed
  scope: diff_with_context
  custom_prompt_file: review_prompt.md
  max_diff_files: 50
  max_diff_lines: 2000
  file_extensions_filter: []
  project_context:
    enabled: true
    mode: on_demand
    max_files: 0
    max_chars: 0
    manifest_max_chars: 60000
    retrieval_max_rounds: 2
    retrieval_max_files: 20
    retrieval_max_chars: 120000
    retrieval_file_max_chars: 30000
    file_extensions: []
    exclude_patterns:
      - .git
      - node_modules
      - dist
      - build
      - .env
      - "*.lock"
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

pr:
  auto_post_comments: false
  dry_run: false
  comment_mode: structured

output:
  format: terminal
  file: ""
  color: true

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

## LLM Parameters

| Parameter | Values / Default | Description |
|---|---:|---|
| `llm.provider` | `openai` | Provider to use. Valid values: `openai`, `azure_openai`, `gemini`, `claude`, `ollama`, `copilot`, `bedrock`. |
| `llm.api_key` | empty | Generic API key. For provider-specific keys, use the provider blocks below. |
| `llm.api_base_url` | empty | Optional API base URL override. Required for Azure OpenAI deployments. |
| `llm.model` | empty | Model name. Empty uses the provider default model. |
| `llm.max_tokens` | `4096` | Maximum response tokens requested from the model. |
| `llm.max_prompt_tokens` | `0` | Estimated prompt budget before repository context is trimmed. `0` means provider default; Bedrock defaults to `180000`. |
| `llm.temperature` | `0.3` | Model temperature. Lower values are more deterministic. |

## Provider Credential Parameters

| Parameter | Description |
|---|---|
| `openai.api_key` | OpenAI or Azure OpenAI API key. Used when `llm.api_key` is empty. |
| `gemini.api_key` | Google Gemini API key. Used when `llm.api_key` is empty. |
| `claude.api_key` | Anthropic API key. Used when `llm.api_key` is empty. |
| `ollama.base_url` | Ollama server URL. Defaults to `http://localhost:11434` when omitted. |
| `copilot.github_token` | GitHub token for GitHub Copilot provider access. Used when `llm.api_key` is empty. |
| `bedrock.region` | AWS region for Bedrock. Required for `bedrock`. |
| `bedrock.profile` | Optional AWS profile name. |
| `bedrock.access_key_id` | Optional Bedrock long-term API key or IAM access key ID. |
| `bedrock.secret_access_key` | Optional IAM secret access key. If set, `bedrock.access_key_id` must also be set. |
| `bedrock.session_token` | Optional AWS session token for temporary credentials. |

Bedrock credential modes:

- `access_key_id` without `secret_access_key`: long-term Bedrock API key.
- `access_key_id` with `secret_access_key`: explicit IAM SigV4 credentials.
- `profile`: named AWS profile.
- no explicit credentials: AWS default credential chain.

## TFS / Azure DevOps Parameters

| Parameter | Values / Default | Description |
|---|---:|---|
| `tfs.base_url` | empty | Azure DevOps or TFS base URL. |
| `tfs.collection` | `DefaultCollection` | TFS collection name. |
| `tfs.project` | empty | Azure DevOps/TFS project name. |
| `tfs.pat` | empty | Personal Access Token used to call Azure DevOps/TFS APIs. |
| `tfs.verify_ssl` | `true` | Whether TLS certificates should be verified. |
| `tfs.ca_bundle` | empty | Optional path to a corporate CA bundle. |
| `tfs.repository` | empty | Default repository filter. Empty means show PRs from all repositories. |

## Review Parameters

| Parameter | Values / Default | Description |
|---|---:|---|
| `review.language` | `pt` | Review language. Valid values: `pt`, `en`. |
| `review.verbosity` | `detailed` | Review style. Valid values: `quick`, `detailed`, `security`. |
| `review.scope` | `diff_with_context` | Review/validation scope. Valid values: `diff_with_context`, `diff_only`, `full_code`. |
| `review.custom_prompt_file` | `review_prompt.md` | Markdown file with extra review rules/context injected into the prompt. |
| `review.max_diff_files` | `50` | Maximum changed files sent to the LLM. Must be greater than `0`. |
| `review.max_diff_lines` | `2000` | Maximum diff lines per file. Must be greater than `0`. |
| `review.file_extensions_filter` | `[]` | Allowlist for files reviewed from the PR diff. Empty list means all file types. |

### Review / Validation Scope

`diff_with_context` is the default. It validates only modified PR lines while
giving the model read-only context from the unified diff, full changed-file
contents, linked work item documentation, and on-demand repository files.

| Scope | Behavior |
|---|---|
| `diff_with_context` | Default. Reviews PR changes with surrounding diff context, full changed files, work item docs, and repository context. Findings and inline comments must still point to modified PR lines. |
| `diff_only` | Reviews only added PR lines. Context and deleted lines are removed before calling the LLM. Project and work item context are not loaded. |
| `full_code` | Reviews the full contents represented for changed files. Project and work item context are not loaded. |

## Project Context Parameters

`review.project_context` applies only when `review.scope: diff_with_context`.
This context is read-only and is never a review target by itself.

| Parameter | Values / Default | Description |
|---|---:|---|
| `review.project_context.enabled` | `true` | Enables repository context loading. |
| `review.project_context.mode` | `on_demand` | `on_demand` sends changed files plus a manifest, then lets the model request extra files. `full` sends the full eligible repository snapshot. |
| `review.project_context.max_files` | `0` | Full mode only. Maximum eligible repository files. `0` means no file-count limit. |
| `review.project_context.max_chars` | `0` | Full mode only. Maximum repository-context characters. `0` means no character limit before prompt-budget trimming. |
| `review.project_context.manifest_max_chars` | `60000` | On-demand mode. Maximum characters used for the repository file manifest. |
| `review.project_context.retrieval_max_rounds` | `2` | On-demand mode. Maximum LLM context-request rounds before the final review. |
| `review.project_context.retrieval_max_files` | `20` | On-demand mode. Maximum extra repository files the model may request. |
| `review.project_context.retrieval_max_chars` | `120000` | On-demand mode. Maximum characters fetched for requested repository files. |
| `review.project_context.retrieval_file_max_chars` | `30000` | On-demand mode. Maximum characters fetched per requested file. |
| `review.project_context.file_extensions` | `[]` | Allowlist for repository context files. Empty means common text/code files. |
| `review.project_context.exclude_patterns` | built-in list | Paths or glob-like patterns excluded from repository context. |

The default on-demand flow loads project structure before the first context
request LLM call. The model receives the manifest and can request specific
files; requested paths are validated against eligible repository files before
content is fetched.

## Linked Work Item Documentation

`review.work_item_context` applies only when `review.scope: diff_with_context`.
This context is read-only; findings still need modified PR lines.

| Parameter | Values / Default | Description |
|---|---:|---|
| `review.work_item_context.enabled` | `true` | Enables fetching documentation from work items linked to the PR. |
| `review.work_item_context.max_items` | `20` | Maximum linked work items included as context. Must be greater than `0`. |
| `review.work_item_context.max_chars` | `100000` | Maximum work-item documentation characters. Must be greater than `0`. |
| `review.work_item_context.fields` | common docs fields | Work item fields rendered into the prompt. `System.Description` is always requested so descriptions remain available. |

Common fields:

```yaml
fields:
  - System.Title
  - System.WorkItemType
  - System.State
  - System.Description
  - Microsoft.VSTS.Common.AcceptanceCriteria
  - Microsoft.VSTS.TCM.ReproSteps
  - Microsoft.VSTS.TCM.SystemInfo
```

## Pull Request Comment Parameters

| Parameter | Values / Default | Description |
|---|---:|---|
| `pr.auto_post_comments` | `false` | Posts review comments without confirmation. |
| `pr.dry_run` | `false` | Runs the review without posting comments. |
| `pr.comment_mode` | `structured` | `structured` posts inline comments plus a summary. `general` posts a single general comment. |

Comments produced by the tool are tagged with hidden metadata so later runs can
detect duplicates and check whether previously resolved tool comments reappear.

## Output Parameters

| Parameter | Values / Default | Description |
|---|---:|---|
| `output.format` | `terminal` | Output renderer. Valid values: `terminal`, `markdown`, `json`. |
| `output.file` | empty | Optional path to save review output. Empty means terminal only. |
| `output.color` | `true` | Enables terminal colors. |

## Usage Tracking

Each completed PR review can append one JSONL record with LLM calls, token
totals, and optional cost estimates.

| Parameter | Values / Default | Description |
|---|---:|---|
| `usage.enabled` | `true` | Enables per-PR usage tracking. |
| `usage.file` | `.ai-review-usage.jsonl` | JSON Lines file where usage records are appended. |
| `usage.pricing` | `{}` | Optional provider/model pricing table for cost estimates. |

Pricing keys are provider names, then model names. Use `default` as a model key
to provide fallback pricing for a provider.

```yaml
usage:
  pricing:
    bedrock:
      default:
        input_per_1m: 0.00
        output_per_1m: 0.00
        currency: USD
```

Cost is calculated only when `usage.pricing` contains matching provider/model
pricing. Prices are configurable because provider prices and enterprise
contracts can change independently from this CLI.

Inspect stored usage interactively:

```bash
ai-review usage
ai-review usage --usage-file .ai-review-usage.jsonl
```

## Validation Rules

The config validator reports issues for:

- Unknown `llm.provider`.
- Missing required provider credentials or Bedrock region.
- Invalid `review.verbosity` or `review.scope`.
- Non-positive `review.max_diff_files` or `review.max_diff_lines`.
- Negative `llm.max_prompt_tokens`.
- Invalid `review.project_context.mode`.
- Negative full-mode project context limits.
- Non-positive on-demand project context limits.
- Non-positive work item context limits.

## Common Issues

### TLS/SSL Error on On-Prem TFS

Use a CA bundle in `config.yaml`:

```yaml
tfs:
  verify_ssl: true
  ca_bundle: C:/certs/corporate-root-ca.pem
```

Avoid `verify_ssl: false` except for temporary troubleshooting.
