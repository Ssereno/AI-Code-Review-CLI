# Execution Flow Diagrams

## Main PR Review Flow

```mermaid
flowchart TD
  A["🚀 Start: ai-review pr-review"] --> B{Arguments provided?}
  B -->|No| C["📋 Interactive mode"]
  B -->|Yes| D["📥 Parse CLI command"]

  C --> E{Choose action}
  E -->|PR review| F["🔄 Start PR review workflow"]
  E -->|List PRs| G["📊 List pull requests"]
  E -->|Show config| H["⚙️ Display configuration"]

  D --> I{Command}
  I -->|pr-review| F
  I -->|list-prs| G

  F --> J["⚙️ Load and validate config"]
  J --> K["🔗 Initialize TFS + Git clients\n(repo_path = current directory)"]
  K --> L{PR ID provided?}
  L -->|No| M["🔍 Fetch active PRs and select one"]
  L -->|Yes| N["✅ Use provided PR ID"]
  M --> O["📄 Get PR details from TFS API\n(source_branch, target_branch, changed_files)"]
  N --> O
  O --> P["🔑 Get target_ref and source_ref\nvia TFS API → obter_dados_pr()"]
  P --> Q["📡 git fetch origin/source_branch"]
  Q --> R["🔀 Get PR diff\ngit diff origin/target...origin/source"]
  R --> S{Diff empty?}
  S -->|Yes| Z1["⚠️ No code changes — stop"]
  S -->|No| T["🧹 Filter noise\n(binary files, lock files)"]
  T --> U{File extensions filter?}
  U -->|Yes| V["📂 Keep only allowed extensions"]
  U -->|No| W{Review scope?}
  V --> W
  W -->|diff_only| X["✂️ Keep additions only\n(remove context and deletions)"]
  W -->|diff_with_context| Y["📦 Limit files — max_diff_files"]
  X --> Y
  Y --> AA["✂️ Truncate per file — max_diff_lines"]
  AA --> BB{RAG enabled?}
  BB -->|No| CC["🤖 Build LLM prompt\n(diff + changed files + work items\n+ project context + RAG context)"]
  BB -->|Yes| DD["🔍 Verify: local branch == PR target branch"]
  DD -->|Mismatch| Z2["🚫 Block review\ngit checkout target_branch required"]
  DD -->|Match| EE["🧠 Load RAG context\ngit grep → extract snippets ±10 lines"]
  EE --> CC
  CC --> FF["💬 Call LLM — structured review"]
  FF --> GG["✅ Validate comments\n(grounding, duplicates, severity cap)"]
  GG --> HH["👀 Preview in terminal"]
  HH --> II{Dry-run?}
  II -->|Yes| Z3["🛑 Stop after preview"]
  II -->|No| JJ{Auto-post?}
  JJ -->|Yes| KK["📤 Post all comments"]
  JJ -->|No| LL["🗂️ Select comments to post"]
  LL --> KK
  KK --> MM["📝 Post general PR summary"]
  MM --> NN{Output file configured?}
  NN -->|Yes| OO["💾 Save formatted review output"]
  NN -->|No| PP["✨ Finish"]
  OO --> PP
```

## TFS / Azure DevOps Integration

```mermaid
flowchart TD
  A["🔗 TFS Connection"] --> B["🔑 Authenticate with PAT"]
  B --> C["✅ Connection established"]
  C --> D{Operation}

  D -->|list_pull_requests| E["🔍 GET pullRequests\n(filters: author, branch, repo)"]
  D -->|get_pull_request_details| F["📄 GET pullRequests/id\n→ source_branch, target_branch,\n  changed_files, commits"]
  D -->|obter_dados_pr| G["GET pullRequests/id\n→ targetRefName, sourceRefName\n  formatted as origin/{branch}"]
  D -->|get_changed_files_context| H["📂 GET items per file\nfrom source branch"]
  D -->|post_review_comments| I["📝 POST threads\nper file + line"]

  E --> J["📊 Return PR list with metadata"]
  F --> K["📋 Return PR details"]
  G --> L["🔀 Return (target_ref, source_ref)\nused for git diff"]
  H --> M["📄 Return file contents\nfor LLM context"]
  I --> N["✅ Comments posted to PR"]
```