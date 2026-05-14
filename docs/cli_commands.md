# CLI Usage Guide

Complete usage guide for the command-line tool.

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

Escolhe o escopo da revisão:

```bash
# PR changes with project and work item context (default)
ai-review pr-review 42 --review-scope diff_with_context

# Apenas as mudanças no diff
ai-review pr-review 42 --review-scope diff_only

# Código completo dos ficheiros modificados
ai-review pr-review 42 --review-scope full_code
```

#### Verbosidade

Define o estilo de revisão:

```bash
ai-review pr-review 42 --quick
ai-review pr-review 42 --detailed
ai-review pr-review 42 --security
```

### Escolher Modelo e Provider

#### Por CLI

```bash
ai-review pr-review 42 --provider openai --model gpt-4o
ai-review pr-review 42 --provider bedrock --model anthropic.claude-3-5-sonnet-20240620-v1:0
ai-review pr-review 42 -p gemini -m gemini-2.0-flash
```

### Limites de Diff

#### Override do ficheiro máximo

```bash
# Envia apenas os primeiros 20 ficheiros ao LLM
ai-review pr-review 42 --max-diff-files 20
```

Override da opção `review.max_diff_files` no config.yaml.

#### Adicionar Contexto

```bash
ai-review pr-review 42 --context "This is a hotfix for urgent production bug"
ai-review pr-review 42 -c "Refactor project structure, no logic changes"
```

### Output e Formatação

#### Formato de Output

```bash
ai-review pr-review 42 --format terminal
ai-review pr-review 42 --format markdown
ai-review pr-review 42 --format json
```

#### Guardar em Ficheiro

```bash
ai-review pr-review 42 --output review.md
ai-review pr-review 42 --output review.json --format json
ai-review pr-review 42 -o ./reports/pr-42.txt
```

#### Sem Cores (output limpo)

```bash
ai-review pr-review 42 --no-color
```

### Ficheiro de Configuração

#### Usar configuração customizada

```bash
ai-review pr-review 42 --config ~/configs/ai-review.yaml
ai-review pr-review 42 --config /etc/ai-review/prod.yaml
```

A ferramenta procura `config.yaml` no **diretório atual** por padrão.

### Exemplos Combinados

#### Reviewear com segurança

```bash
ai-review pr-review 42 \
  --security \
  --dry-run \
  --format markdown \
  --output security-review.md
```

#### Review rápido com Claude via Bedrock

```bash
ai-review pr-review 42 \
  --quick \
  --provider bedrock \
  --model anthropic.claude-3-5-sonnet-20240620-v1:0 \
  --auto-post
```

#### Listar e revisar PRs específicos

```bash
ai-review pr-review \
  --repo-name backend \
  --author "DevOps Team" \
  --target-branch develop
```


## list-prs - Listar Pull Requests

Lista PRs com filtros.

### Listar todos os PRs ativos

```bash
ai-review list-prs
```

### Listar com status específico

```bash
ai-review list-prs --status active
ai-review list-prs --status completed
ai-review list-prs --status abandoned
ai-review list-prs --status all
```

### Filtrar por autor

```bash
ai-review list-prs --author "John Smith"
ai-review list-prs --author "Jane"
```

### Filtrar por repositório

```bash
ai-review list-prs --repo-name backend
ai-review list-prs -r frontend
```

### Combinar filtros

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

### Mostrar ajuda de comando específico

```bash
ai-review pr-review --help
ai-review list-prs --help
ai-review init --help
```

## Opções de `pr-review`

| Flag | Valores | Descrição |
|------|--------|-----------|
| `pr_id` | número | ID do PR a revisar |
| `--repo-name`, `-r` | texto | Filtrar por repositório |
| `--author` | texto | Filtrar por autor |
| `--target-branch` | texto | Filtrar por branch de destino |
| `--dry-run` | — | Não postar comentários |
| `--auto-post` | — | Postar sem confirmação |
| `--quick` | — | Revisão concisa |
| `--detailed` | — | Revisão detalhada (padrão) |
| `--security` | — | Revisão focada em segurança |
| `--review-scope` | `diff_with_context`, `diff_only`, `full_code` | Escopo da revisão |
| `--max-diff-files` | número | Máximo de ficheiros |
| `--context`, `-c` | texto | Contexto adicional |
| `--format` | `terminal`, `markdown`, `json` | Formato de output |
| `--output`, `-o` | caminho | Guardar resultado em ficheiro |
| `--no-color` | — | Desabilitar cores |
| `--provider`, `-p` | texto | Provider LLM |
| `--model`, `-m` | texto | Modelo LLM |

## Opções de `list-prs`

| Flag | Valores | Descrição |
|------|--------|-----------|
| `--repo-name`, `-r` | texto | Filtrar por repositório |
| `--author` | texto | Filtrar por autor |
| `--status` | `active`, `completed`, `abandoned`, `all` | Status do PR |


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


## Configuração por Ficheiro

Embora muitas opções sejam disponíveis via CLI, a configuração padrão vem de `config.yaml`:

```yaml
llm:
  provider: bedrock
  model: anthropic.claude-3-5-sonnet-20240620-v1:0
  temperature: 0.3

review:
  language: pt
  verbosity: detailed
  scope: diff_with_context
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

**Prioridade:**
1. CLI flags (alta prioridade)
2. Ficheiro `config.yaml` (padrão)
3. Valores hardcoded (fallback)

## Exemplos de Fluxo Completo

### Scenario 1: Review rápido antes de merge

```bash
# Listar PRs
ai-review list-prs --status active

# Revisar um PR com dry-run
ai-review pr-review 123 --quick --dry-run

# Se OK, fazer review real
ai-review pr-review 123 --quick --auto-post
```

### Scenario 2: Security audit

```bash
# Review focado em segurança
ai-review pr-review 456 \
  --security \
  --review-scope full_code \
  --format json \
  --output security-audit.json
```
