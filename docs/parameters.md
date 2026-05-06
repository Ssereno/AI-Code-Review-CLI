# Parâmetros de Revisão

## Parâmetros Gerais

```yaml
review:
  language: pt               # Idioma dos comentários
  verbosity: detailed        # detailed | quick | security
  scope: diff_only           # diff_only | full_code
  custom_prompt_file: review_prompt.md  # Prompt customizado
  max_diff_files: 50         # Máx ficheiros enviados ao LLM
  max_diff_lines: 2000       # Máx linhas por ficheiro
  file_extensions_filter: [".cs", ".ts", ".py"]  # Allowlist (vazio = todos)
```

## Parâmetros de Comentários

```yaml
pr:
  auto_post_comments: false  # Postar automaticamente
  dry_run: false             # Não postar, apenas visualizar
  comment_mode: structured   # structured | inline
```

## Filtragem por Extensão de Ficheiro

A opção `file_extensions_filter` funciona como um **allowlist**: apenas ficheiros com as extensões listadas são enviados ao LLM.

Revisar apenas C#, TypeScript e Python

```yaml
review:
  file_extensions_filter: [".cs", ".ts", ".py"]
```

Revisar todos os ficheiros

```yaml
review:
  file_extensions_filter: []  # Lista vazia = sem filtro
```

> **Nota:** Se nenhum ficheiro elegível permanecer após filtragem, a revisão termina com aviso sem chamar o LLM.

## Prompt Customizado (Markdown)

O ficheiro `review_prompt.md` é injetado automaticamente em cada execução e permite dar mais contexto a LLM sobre o teu projecto.

```markdown
# Code Review Guidelines

## Style
- Use Portuguese comments
- Be respectful and constructive
- Focus on logic and best practices

## Mandatory Rules
- Check for null pointer exceptions
- Verify SQL injection risks
- Ensure proper error handling

## Examples
- Good: `if user is not None:`
- Bad: `if user:`
```

Personaliza o prompt para adequar a revisão ao teu projeto:

```yaml
review:
  custom_prompt_file: review_prompt.md
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