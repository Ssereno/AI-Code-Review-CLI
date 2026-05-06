# AI Code Review CLI - Documentation

Ferramenta automatizada de revisão de código utilizando Inteligência Artificial para Pull Requests em Azure DevOps/TFS.

## Installation

Install the CLI from pypi.org

```bash
pip install code-review-ai-cli
```

Inicializa a configuração gerando templates:

```bash
ai-review init
```

**Saída:**
```
config.yaml created at: /home/user/my-project/config.yaml
review_prompt.md created at: /home/user/my-project/review_prompt.md

Edit them to add your credentials, preferences and review rules.
```

Se ficheiros já existem, é pedida confirmação:

```
config.yaml already exists in the current directory.
Overwrite? [y/N]
```

## Quick Start

```bash
# Instalação
pip install -r requirements.txt

# Inicializar configuração
ai-review init

# Editar config.yaml com credenciais e preferências
config.yaml

# Listar PRs ativos
ai-review list-prs

# Revisar um PR específico
ai-review pr-review 42

# Modo dry-run (sem postar comentários)
ai-review pr-review 42 --dry-run
```

## Documentation

### [Parameters](./parameters.md)

### [Supported LLM's](./llm_configuration.md)

### [CLI Commands](./cli_commands.md)

### [Execution Flow & Diagrams](./flow_diagrams.md)
