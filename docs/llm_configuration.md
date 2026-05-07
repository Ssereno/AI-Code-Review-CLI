gemini:
# LLM Configuration Guide

Complete guide for configuring and integrating with language models.

## Supported Providers

| Provider | Recommended Model | Setup | 
|----------|-------------------|-------|
| **OpenAI** | `gpt-4o` | API Key | Pay-per-use |
| **Azure OpenAI** | `gpt-4o` | Endpoint + API Key |
| **Google Gemini** | `gemini-2.0-flash` | API Key |
| **Anthropic Claude** | `claude-3-5-sonnet-latest` | API Key |
| **Ollama** | `llama3`, `mistral` | Local | 
| **GitHub Copilot** | `gpt-4o`, `o1` | Token | 
| **AWS Bedrock** | `anthropic.claude-3-5-sonnet-20240620-v1:0`;  `ARNs` | AWS Credentials |

## Provider Configuration

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

**How to get API Key:**
1. Go to [platform.openai.com](https://platform.openai.com)
2. Create an account or log in
3. Go to **API keys** → **Create new secret key**
4. Copy and store it in a safe place

**Available models:**
- `gpt-4o` — Best quality/cost ratio (recommended)
- `gpt-4-turbo` — Cheaper than GPT-4o
- `gpt-4` — More powerful, more expensive
- `gpt-3.5-turbo` — Fast and cheap

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

**How to get credentials:**
1. Go to [Azure Portal](https://portal.azure.com)
2. Create or select an **Azure OpenAI** resource
3. Go to **Keys and Endpoints**
4. Copy the endpoint and the key

**Note:** Azure OpenAI requires pre-configured deployments in Azure.

### Google Gemini

```yaml
llm:
  provider: gemini
  model: gemini-2.0-flash
  max_tokens: 4096
  temperature: 0.3

  temperature: 0.3
  api_key: xxxxxxxxxxxxxxxxxxxxxxxx
```

**How to get API Key:**
1. Go to [Google AI Studio](https://aistudio.google.com)
2. Click **Get API Key**
3. Select a project and create the key
4. Copy and store it

**Available models:**
- `gemini-2.0-flash` — Fastest model (recommended)
- `gemini-1.5-pro` — More powerful
- `gemini-pro` — Previous version

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

**How to get API Key:**
1. Go to [console.anthropic.com](https://console.anthropic.com)
2. Create an account
3. Go to **API Keys** → **Create Key**
4. Copy and store it

**Available models:**
- `claude-3-5-sonnet-latest` — Best quality (recommended)
- `claude-3-opus-latest` — Most powerful
- `claude-3-haiku-20240307` — Fastest and cheapest

### Ollama (Local)

```yaml
llm:
  provider: ollama
  model: llama3
  max_tokens: 4096

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