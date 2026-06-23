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
  custom_prompt_file: review_context.local.md
  max_diff_files: 50        # diff_only only
  max_diff_lines: 2000      # diff_only only
  max_comments_to_post: 20
  file_extensions_filter: [] # diff_only only
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
  rag:
    enabled: true
    max_chars: 40000

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
| `tfs.local_repo_path`| empty | Optional explicit local clone path. When set, this path is used instead of the managed clone cache. |
| `tfs.local_clone_root` | `.ai-review/repos` | Project-owned folder for managed repository clones when `local_repo_path` is empty. |

When `tfs.local_repo_path` is empty, the CLI resolves the Azure DevOps/TFS
repository clone URL, creates or reuses a clone under `tfs.local_clone_root`,
fetches the PR source and target refs, and computes the PR diff locally. The
managed clone folder is assumed to be owned by this tool.

## PR Description / Spec Context Parameters

`review.pr_description_context` controls whether the CLI extracts PR description text and supported linked spec pages to use as read-only requirements context.

| Parameter | Values / Default | Description |
|---|---:|---|
| `review.pr_description_context.enabled` | `true` | Enables PR description/spec context loading. |
| `review.pr_description_context.max_chars` | `60000` | Maximum characters included across the PR description and fetched spec pages. |
| `review.pr_description_context.max_links` | `10` | Maximum supported spec links fetched from the PR description. |
| `review.pr_description_context.link_max_chars` | `25000` | Maximum characters included from each fetched spec page. |

This context is read-only and is used only to detect contradictions with changed PR lines. It is not a checklist that the PR must fully implement.

## Review Parameters

| Parameter | Values / Default | Description |
|---|---:|---|
| `review.language` | `pt` | Review language. Valid values: `pt`, `en`. |
| `review.verbosity` | `detailed` | Review style. Valid values: `quick`, `detailed`, `security`. |
| `review.scope` | `diff_with_context` | Review/validation scope. Valid values: `diff_with_context`, `diff_only`. |
| `review.custom_prompt_file` | `review_context.local.md` | One active Markdown reviewer context file. If the configured file is missing, the packaged `src/prompts/review_context.example.md` is used instead. Explicit custom paths continue to work when that file exists. |
| `review.max_diff_files` | `50` | `diff_only` only. Maximum changed files sent to the LLM. `diff_with_context` ignores this and validates every changed file. Must be greater than `0`. |
| `review.max_diff_lines` | `2000` | `diff_only` only. Maximum diff lines per file. `diff_with_context` ignores this and validates every changed line. Must be greater than `0`. |
| `review.max_comments_to_post` | `20` | Maximum actionable inline comments kept after grounding, duplicate checks, and severity prioritization. Must be greater than `0`. |
| `review.file_extensions_filter` | `[]` | `diff_only` only. Allowlist for files reviewed from the PR diff. `diff_with_context` ignores this and validates every changed file. |

### Review / Validation Scope

`diff_with_context` is the default. It validates only modified PR lines while
giving the model read-only context from per-hunk change packets, full changed-file
contents, linked work item documentation, PR description/spec links, and on-demand repository files. Only
lines marked as reviewable in the source-branch change packets can become inline
comments.

`diff_with_context` does not omit changed files or changed lines because of diff
limits, extension filters, lock-file filters, generated-file filters, or
repository context eligibility rules. If the complete prompt is too large for
the provider, the CLI validates the PR in token-safe batches and merges the
results. Each batch receives only its own diff plus batch-specific source,
spec/work-item, and repository context. It fails loudly instead of truncating
when one changed hunk is too large to fit by itself.

The reviewer context is intentionally single-file at runtime. `ai-review init`
creates `review_context.example.md` as the kept example and
`review_context.local.md` as the local override, then adds that local file to
`.gitignore`. The LLM never concatenates multiple Markdown context files.
The packaged example includes the default changed-line boundary, minimum comment
bar, and severity bar used to keep comments meaningful for smaller models.

| Scope | Behavior |
|---|---|
| `diff_with_context` | Default. Reviews PR changes through source-branch change packets, with full changed files, work item docs, and repository context as read-only support. Findings and inline comments must still point to actual modified PR lines. |
| `diff_only` | Reviews only added PR lines. Context and deleted lines are removed before calling the LLM. Project and work item context are not loaded. |

## Project Context Parameters

`review.project_context` applies only when `review.scope: diff_with_context`.
This context is read-only and is never a review target by itself.

| Parameter | Values / Default | Description |
|---|---:|---|
| `review.project_context.enabled` | `true` | Enables repository context loading. |
| `review.project_context.mode` | `on_demand` | `on_demand` sends changed files plus local repository structure JSON, then lets the model request extra files. `full` sends the full eligible repository snapshot. |
| `review.project_context.max_files` | `0` | Full mode only. Maximum eligible repository files. `0` means no file-count limit. |
| `review.project_context.max_chars` | `0` | Full mode only. Maximum repository-context characters. `0` means no character limit before prompt-budget trimming. |
| `review.project_context.manifest_max_chars` | `60000` | On-demand mode. Maximum characters used for the repository file manifest. |
| `review.project_context.retrieval_max_rounds` | `2` | On-demand mode. Maximum LLM context-request rounds before the final review. |
| `review.project_context.retrieval_max_files` | `20` | On-demand mode. Maximum extra repository files the model may request. |
| `review.project_context.retrieval_max_chars` | `120000` | On-demand mode. Maximum characters fetched for requested repository files. |
| `review.project_context.retrieval_file_max_chars` | `30000` | On-demand mode. Maximum characters fetched per requested file. |
| `review.project_context.file_extensions` | `[]` | Allowlist for repository context files. Empty means common text/code files. |
| `review.project_context.exclude_patterns` | built-in list | Paths or glob-like patterns excluded from repository context. |

The default on-demand flow maps the local cloned repository before the first
context request LLM call. The model receives JSON describing eligible files and
directories, then can request specific files. Requested paths are validated
against eligible repository files before content is fetched from the local clone.

## RAG Context Parameters

`review.rag` controls keyword-based context retrieval from the local repository.
When enabled, the CLI runs `git grep` to find code snippets related to the
identifiers changed in the PR diff and appends them to the LLM prompt as
read-only context.

> **Local branch requirement.** Explicit `tfs.local_repo_path` repositories must
> be checked out on the PR **target branch**. Managed clones are aligned to the
> target branch automatically before local RAG context is loaded.

| Parameter | Values / Default | Description |
|---|---:|---|
| `review.rag.enabled` | `true` | Enables RAG context loading. When `false`, no local git operations are performed and no branch check is enforced. |
| `review.rag.max_chars` | `40000` | Maximum characters of RAG context appended to the prompt. Snippets are truncated once this limit is reached. |

### How RAG context is built

1. **Extract identifiers** — function names, class names, and file basenames are parsed from the PR diff with regex
2. **Search** — `git grep -l -i <identifier>` finds files containing those names
3. **Extract snippets** — `git grep -n` gets line numbers; ±10 lines around each match are read from disk
4. **Truncate** — results are concatenated and capped at `review.rag.max_chars`

### Recommended enhanced stack (Local & Open Source)

The current implementation uses `git grep` (zero extra dependencies). For teams
wanting semantic similarity search instead of keyword matching, the recommended
local stack is:

| Component | Library | Notes |
|---|---|---|
| **Vector database** | [ChromaDB](https://www.trychroma.com/) | In-memory or local SQLite — no server needed; `pip install chromadb` |
| **Embeddings** | [sentence-transformers](https://www.sbert.net/) | Local CPU inference — no API calls or costs; `pip install sentence-transformers` |

This keeps the CLI lightweight and self-contained for teams that cannot use
paid embedding APIs.

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
Inline comments also include a visible `` `#AI` `` marker and may include Azure
DevOps/TFS suggestion blocks when the replacement text exactly matches the
selected source-branch line range.

The PR reviewer only keeps actionable structured findings. Praise, style-only
comments, general suggestions, deleted-file comments, target-only evidence,
comments outside modified source-branch lines, and comments whose quoted evidence
does not match the exact reviewable changed line are discarded before posting.

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

## Common Issues

### TLS/SSL Error on On-Prem TFS

Use a CA bundle in `config.yaml`:

```yaml
tfs:
  verify_ssl: true
  ca_bundle: C:/certs/corporate-root-ca.pem
```

Avoid `verify_ssl: false` except for temporary troubleshooting.
