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
  max_prompt_tokens: 0
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

`max_prompt_tokens` controls the estimated prompt budget before repository
context is trimmed. Use `0` for the provider default. Bedrock defaults to
`180000` so repository-context prompts stay below its hard prompt limit.

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
1. Install Ollama: https://ollama.ai
2. Pull a model: `ollama pull llama3`
3. Start the server: `ollama serve`
4. Test the connection: `curl http://localhost:11434/api/tags`

**Available models:**
- `llama3` — powerful general-purpose model
- `mistral` — fast and lightweight
- `deepseek-coder` — specialized for code
- `neural-chat` — good for conversational use

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

**How to get a token:**
1. Go to https://github.com/settings/tokens
2. Click **Generate new token (classic)**
3. Select scopes such as `codespace` or `gist`
4. Copy and store the token securely

### AWS Bedrock

Supports multiple authentication methods. Choose the one that fits:

#### Option 1: Bedrock API Key (long-term)
```yaml
llm:
  provider: bedrock
  model: arn:aws:bedrock:eu-north-1:123456789:application-inference-profile/xxxxxxxx

bedrock:
  region: eu-north-1
  access_key_id: ABSK...   # Access key only
```

#### Option 2: IAM Credentials

```yaml
llm:
  provider: bedrock
  model: anthropic.claude-3-5-sonnet-20240620-v1:0

bedrock:
  region: us-east-1
  access_key_id: AKIA...
  secret_access_key: wJalr...
  # session_token: ...   # Optional for temporary STS credentials
```

#### Option 3: AWS SSO / Named Profile

```yaml
bedrock:
  region: us-east-1
  profile: my-sso-profile
```

#### Option 4: Default Credential Chain

```yaml
bedrock:
  region: us-east-1
  # Uses environment variables, instance role, etc.
```



## Troubleshooting

### Bedrock authentication error

- Confirm `bedrock.region` — it must match the model's region
- Confirm `llm.model` — a valid Bedrock ARN
- **Long-term API Key**: only `access_key_id` is required (no `secret_access_key`)
- **IAM credentials**: provide both `access_key_id` and `secret_access_key`
- **Profile/SSO**: set `bedrock.profile`
- If nothing works, fall back to the credential chain (env vars, instance role, etc.)

### Rate limit error

Lower `max_tokens` or add a delay between requests in the configuration.
