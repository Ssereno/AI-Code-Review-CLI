# LLM Configuration Guide

Documentação completa de configuração e integração com modelos de linguagem.

## Providers Suportados

| Provider | Modelo Recomendado | Setup | 
|----------|-------------------|-------|
| **OpenAI** | `gpt-4o` | API Key | Pagamento por uso |
| **Azure OpenAI** | `gpt-4o` | Endpoint + API Key |
| **Google Gemini** | `gemini-2.0-flash` | API Key |
| **Anthropic Claude** | `claude-3-5-sonnet-latest` | API Key |
| **Ollama** | `llama3`, `mistral` | Local | 
| **GitHub Copilot** | `gpt-4o`, `o1` | Token | 
| **AWS Bedrock** | `anthropic.claude-3-5-sonnet-20240620-v1:0`;  `ARNs` | AWS Credentials |

## Configuração por Provider

### OpenAI

```yaml
llm:
  provider: openai
  model: gpt-4o
  max_tokens: 4096
  temperature: 0.3

openai:
  api_key: sk-xxxxxxxxxxxxxxxxxxxx
```

**Como obter API Key:**
1. Acede a [platform.openai.com](https://platform.openai.com)
2. Cria uma account ou faz login
3. Vai para **API keys** → **Create new secret key**
4. Copia e guarda num local seguro

**Modelos disponíveis:**
- `gpt-4o` — Melhor relação qualidade/custo (recomendado)
- `gpt-4-turbo` — Mais barato que GPT-4o
- `gpt-4` — Mais poderoso, mais caro
- `gpt-3.5-turbo` — Rápido e barato

### Azure OpenAI

```yaml
llm:
  provider: azure_openai
  model: gpt-4o
  api_base_url: https://your-resource.openai.azure.com/
  max_tokens: 4096
  temperature: 0.3

openai:
  api_key: xxxxxxxxxxxxxxxxxxxxxxxx
```

**Como obter credenciais:**
1. Acede a [Azure Portal](https://portal.azure.com)
2. Cria ou seleciona um recurso **Azure OpenAI**
3. Vai para **Keys and Endpoints**
4. Copia o endpoint e a chave

**Nota:** Azure OpenAI requer deployments pré-configurados no Azure.

### Google Gemini

```yaml
llm:
  provider: gemini
  model: gemini-2.0-flash
  max_tokens: 4096
  temperature: 0.3

gemini:
  api_key: xxxxxxxxxxxxxxxxxxxxxxxx
```

**Como obter API Key:**
1. Acede a [AI Studio do Google](https://aistudio.google.com)
2. Clica em **Get API Key**
3. Seleciona um projeto e cria a chave
4. Copia e guarda

**Modelos disponíveis:**
- `gemini-2.0-flash` — Modelo mais rápido (recomendado)
- `gemini-1.5-pro` — Mais poderoso
- `gemini-pro` — Versão anterior

### Anthropic Claude

```yaml
llm:
  provider: claude
  model: claude-3-5-sonnet-latest
  max_tokens: 4096
  temperature: 0.3

claude:
  api_key: sk-ant-xxxxxxxxxxxxxxxxxxxxxxxx
```

**Como obter API Key:**
1. Acede a [console.anthropic.com](https://console.anthropic.com)
2. Cria uma account
3. Vai para **API Keys** → **Create Key**
4. Copia e guarda

**Modelos disponíveis:**
- `claude-3-5-sonnet-latest` — Melhor qualidade (recomendado)
- `claude-3-opus-latest` — Mais poderoso
- `claude-3-haiku-20240307` — Mais rápido e barato

### Ollama (Local)

```yaml
llm:
  provider: ollama
  model: llama3
  max_tokens: 4096
  temperature: 0.3

ollama:
  base_url: http://localhost:11434
```

**Setup:**
1. Instala [Ollama](https://ollama.ai)
2. Baixa um modelo: `ollama pull llama3`
3. Inicia o servidor: `ollama serve`
4. Testa a conexão: `curl http://localhost:11434/api/tags`

**Modelos disponíveis:**
- `llama3` — Modelo geral poderoso
- `mistral` — Rápido e leve
- `deepseek-coder` — Especializado em código
- `neural-chat` — Bom para conversação

### GitHub Copilot

```yaml
llm:
  provider: copilot
  model: gpt-4o
  max_tokens: 4096
  temperature: 0.3

copilot:
  api_key: ghp_xxxxxxxxxxxxxxxxxxxx
```

**Como obter Token:**
1. Acede a [github.com/settings/tokens](https://github.com/settings/tokens)
2. **Generate new token (classic)**
3. Seleciona scope `codespace` ou `gist`
4. Copia e guarda

### AWS Bedrock

Suporta múltiplas formas de autenticação. Escolhe a que se adequa:

#### Opção 1: Bedrock API Key (Longo termo)

```yaml
llm:
  provider: bedrock
  model: arn:aws:bedrock:eu-north-1:123456789:application-inference-profile/xxxxxxxx

bedrock:
  region: eu-north-1
  access_key_id: ABSK...   # Apenas access_key
```

#### Opção 2: IAM Credentials

```yaml
llm:
  provider: bedrock
  model: anthropic.claude-3-5-sonnet-20240620-v1:0

bedrock:
  region: us-east-1
  access_key_id: AKIA...
  secret_access_key: wJalr...
  # session_token: ...   # Opcional para credenciais STS temporárias
```

#### Opção 3: AWS SSO / Named Profile

```yaml
bedrock:
  region: us-east-1
  profile: my-sso-profile
```

#### Opção 4: Default Credential Chain

```yaml
bedrock:
  region: us-east-1
  # Usa variáveis de ambiente, instance role, etc.
```



## Troubleshooting

### Erro de Autenticação com Bedrock

- Confirma `bedrock.region` — deve corresponder à região do modelo
- Confirma `llm.model` — ARN válido do Bedrock
- **API Key longo termo**: apenas `access_key_id` (sem `secret_access_key`)
- **IAM credentials**: ambos `access_key_id` + `secret_access_key`
- **Profile/SSO**: define `bedrock.profile`
- Se nada, usa **credential chain** (env vars, instance role, etc.)

### Erro de Rate Limit

Reduz `max_tokens` ou adiciona delay entre requisições na configuração.