"""
LLM Client Module - AI Code Review
=====================================
Responsible for communication with LLM APIs for code analysis.

Supported providers:
- Google Gemini (gemini-pro, gemini-1.5-pro, gemini-2.0-flash)
- Anthropic Claude (claude-3-opus, claude-3-sonnet, claude-3-haiku)
- OpenAI GPT-4 (gpt-4, gpt-4-turbo, gpt-4o)
- Ollama (local models via local API)
- GitHub Copilot (GPT-4o, Claude 3.5 Sonnet, etc. via GitHub)
- AWS Bedrock (Claude, Llama, Mistral, etc. via Runtime API)
"""

import datetime
import json
import os

from .config import ReviewConfig


class LLMError(Exception):
    """Exception for LLM communication errors."""
    pass

# ---------------------------------------------------------------------------
# System Prompts
# ---------------------------------------------------------------------------
SYSTEM_PROMPTS = {
    "quick": {
        "pt": (
            "És um code reviewer experiente em .Net C#, TypeScript e SQL. Analisa o diff de código fornecido "
            "e dá um review CONCISO e direto. Foca-te nos problemas mais críticos:\n"
            "- Bugs e erros lógicos\n"
            "- Problemas de segurança\n"
            "- Problemas de performance graves\n\n"
            "Formato: Lista de bullet points com o ficheiro e linha quando possível. "
        ),
        "en": (
            "You are an experienced .Net C#, TypeScript and SQL code reviewer. Analyze the provided code diff "
            "and give a CONCISE review. Focus on critical issues:\n"
            "- Bugs and logic errors\n"
            "- Security issues\n"
            "- Major performance problems\n\n"
            "Format: Bullet points with file and line when possible. "
        ),
    },
    "detailed": {
        "pt": (
            "És um code reviewer experiente em .Net C#, TypeScript e SQL. Analisa o diff de código "
            "fornecido e retorna apenas comentários inline.\n\n"
            "Formato de output — para cada problema encontrado, escreve exatamente:\n"
            "- Linha <número_linha>: <descrição do problema> \n\n"
            "Regras:\n"
            "- Reporta APENAS problemas específicos associados a uma linha ou bloco concreto de código alterado.\n"
            "- NÃO produzas secções de sumário (ex: 'Bugs Potenciais', 'Visão Geral de Segurança').\n"
            "- NÃO produzas parágrafos introdutórios ou de fecho.\n"
            "- Se não encontrares problemas, escreve apenas: Nenhum problema encontrado.\n"
            "- Sê específico, objetivo e conciso. Responde em português."
        ),
        "en": (
            "You are an expert .Net C#, TypeScript e SQL code reviewer. Analyze the provided code and return only inline comments.\n\n"
            "Output format — for each issue found, output exactly:\n"
            "- Line <line_number>: <issue description>\n\n"
            "Rules:\n"
            "- Report ONLY specific issues tied to a concrete line or block of changed code.\n"
            "- Do NOT produce summary sections (e.g. 'Potential Bugs', 'Security Overview').\n"
            "- Do NOT produce introductory or closing paragraphs.\n"
            "- If no issues are found, output only: No issues found.\n"
            "- Be specific, actionable, and concise."
        ),
    },
    "security": {
        "pt": (
            "És um especialista em segurança de aplicações .Net C#, TypeScript e SQL. Analisa o diff "
            "de código fornecido com foco EXCLUSIVO em segurança.\n\n"
            "Procura por:\n"
            "- SQL Injection\n"
            "- Cross-Site Scripting (XSS)\n"
            "- Cross-Site Request Forgery (CSRF)\n"
            "- Credenciais hardcoded ou secrets expostos\n"
            "- Vulnerabilidades de autenticação/autorização\n"
            "- Insecure deserialization\n"
            "- Path traversal\n"
            "- Command injection\n"
            "- Dependências com vulnerabilidades conhecidas\n"
            "- Logging de informação sensível\n"
            "- Configurações inseguras\n\n"
            "Fornece recomendações de correção para cada problema. "
            "Responde em português."
        ),
        "en": (
            "You are an application security .Net C#, TypeScript and SQL specialist. Analyze the "
            "provided code diff with EXCLUSIVE focus on security.\n\n"
            "Look for:\n"
            "- SQL Injection\n"
            "- Cross-Site Scripting (XSS)\n"
            "- Cross-Site Request Forgery (CSRF)\n"
            "- Hardcoded credentials or exposed secrets\n"
            "- Authentication/authorization vulnerabilities\n"
            "- Insecure deserialization\n"
            "- Path traversal\n"
            "- Command injection\n"
            "- Dependencies with known vulnerabilities\n"
            "- Logging of sensitive information\n"
            "- Insecure configurations\n\n"
            "Provide fix recommendations for each issue."
        ),
    },
}

# Special prompt for PR review with structured comments
PR_COMMENT_PROMPT = {
    "pt": (
        "Analisa o diff de código de um Pull Request e retorna os teus comentários em formato JSON estruturado.\n\n"
        "Para CADA problema encontrado, retorna um objeto JSON com:\n"
        '- "file": caminho do ficheiro (ex: "src/auth.py")\n'
        '- "line": número da linha no diff (inteiro, ou 0 se geral)\n'
        '- "type": tipo de issue ("bug", "security", "performance", "style", "suggestion", "praise")\n'
        '- "comment": descrição direta do problema em português, sem saudações e sem emojis\n'
        '- "suggestion": sugestão de correção (opcional, string vazia se não aplicável)\n'
        '- "reference": fonte ou referência para o problema (URL de documentação, padrão ou princípio). Importante: incluir SEMPRE uma referência relevante.\n\n'
        "No campo 'comment', escreve de forma objetiva e curta. "
        "Não uses introduções como 'Olá' ou 'Como code reviewer sénior'.\n"
        "No campo 'reference', inclui uma fonte confiável, padrão ou link para documentação relevante.\n\n"
        "Responde APENAS com um JSON array válido. Exemplo:\n"
        '[\n'
        '  {\n'
        '    "file": "src/auth.py",\n'
        '    "line": 42,\n'
        '    "type": "security",\n'
        '    "comment": "Password armazenada em texto simples sem hashing",\n'
        '    "suggestion": "Usar bcrypt ou argon2 para hash de passwords",\n'
        '    "reference": "OWASP - Password Storage Cheat Sheet"\n'
        '  }\n'
        ']\n\n'
    ),
    "en": (
        "Analyze the Pull Request code diff and return your comments in structured JSON format.\n\n"
        "For EACH issue found, return a JSON object with:\n"
        '- "file": file path (e.g., "src/auth.py")\n'
        '- "line": line number in diff (integer, or 0 if general)\n'
        '- "type": issue type ("bug", "security", "performance", "style", "suggestion", "praise")\n'
        '- "comment": direct description of the issue, with no greetings and no emojis\n'
        '- "suggestion": fix suggestion (optional, empty string if not applicable)\n'
        '- "reference": source or reference for the issue (e.g., "OWASP Top 10", "PEP 8", documentation URL, standard or principle). Important: ALWAYS include a relevant reference.\n\n'
        "In 'comment', use a short and objective tone. "
        "Do not include intros like 'Hello' or 'As a senior reviewer'.\n"
        "In 'reference', include a trusted source, standard or link to relevant documentation.\n\n"
    ),
}


def get_system_prompt(verbosity: str, language: str) -> str:
    """Returns the system prompt for the given verbosity level and language.

    Selects a prompt from ``SYSTEM_PROMPTS`` keyed by *verbosity*. If the
    requested verbosity is not found, the function falls back to
    ``"detailed"``.  Within the selected prompt group, the language is
    resolved by *language*; if the language key is absent, ``"pt"``
    (Portuguese) is used as the default.

    The ``"detailed"`` prompt instructs the LLM to return **only** inline
    comments in the format ``- Line <n>: <description>``,
    prohibiting summary sections and introductory/closing paragraphs.
    When no issues are found the expected output is ``No issues found.``
    (English) or ``Nenhum problema encontrado.`` (Portuguese).

    Args:
        verbosity: Review depth key — one of ``"quick"``, ``"detailed"``,
            or ``"security"``. Any unrecognised value falls back to
            ``"detailed"``.
        language: Response language code — ``"en"`` for English,
            ``"pt"`` for Portuguese.

    Returns:
        The system prompt string for the resolved verbosity and language.
    """
    prompts = SYSTEM_PROMPTS.get(verbosity, SYSTEM_PROMPTS["detailed"])
    return prompts.get(language, prompts["pt"])


def get_pr_comment_prompt(language: str) -> str:
    """Returns the prompt that requests structured JSON PR comments from the LLM.

    Retrieves the prompt from ``PR_COMMENT_PROMPT`` for the given *language*.
    If the language key is not present, ``"pt"`` (Portuguese) is used as the
    default.

    The returned prompt instructs the LLM to produce a valid JSON array where
    each element represents a single review comment with the fields
    ``file``, ``line``, ``type``, ``comment``, ``suggestion``,
    and ``reference``.

    Args:
        language: Response language code — ``"en"`` for English,
            ``"pt"`` for Portuguese.

    Returns:
        The prompt string used to request structured PR comments.
    """
    return PR_COMMENT_PROMPT.get(language, PR_COMMENT_PROMPT["pt"])


def get_scope_guidance(review_scope: str, language: str, structured: bool = False) -> str:
    """Returns LLM instructions tailored to the active review scope.

    For ``diff_only`` scope, the instructions inform the LLM that:

    * Only added lines (``+``) appear in the diff section.
    * A ``### FULL_FILE_CONTEXT_START: <path> ###`` /
      ``### FULL_FILE_CONTEXT_END ###`` block is embedded in the payload for
      each changed file, containing the complete old-version (before changes)
      file content as **read-only** background.
    * The review must focus **exclusively** on the changed lines (``+``); the
      full-file section exists only to prevent the model from hallucinating
      about the surrounding code.

    For ``full_code`` scope, the instructions inform the LLM that the diff
    represents the entire new file content (every line prefixed with ``+``)
    and that deleted code is absent.

    Args:
        review_scope: ``"diff_only"`` (default) or ``"full_code"``.
        language: Response language code — ``"en"`` for English,
            ``"pt"`` for Portuguese.
        structured: When ``True``, appends additional constraints for the
            structured JSON comment mode (every comment must carry a valid
            file path and line number > 0 for inline posting).

    Returns:
        Instruction string to be appended to the system prompt.
    """
    scope = (review_scope or "diff_only").lower()

    if scope == "full_code":
        if language == "en":
            return (
                "Review scope: full_code. The diff contains only added lines (+) for each file. "
                "Analyze the complete content of the changed files and identify issues in the new code. "
                "Do not comment on deleted or absent code."
            )
        return (
            "Ambito de review: full_code. O diff contém apenas linhas adicionadas (+) de cada ficheiro. "
            "Analisa o conteúdo completo dos ficheiros alterados e identifica problemas no novo código. "
            "Não comentes código eliminado ou ausente."
        )

    if structured:
        if language == "en":
            return (
                "Review scope: diff_only. The diff contains only added lines (+) — context and deletions were removed. "
                "A full file content section (between ### FULL_FILE_CONTEXT_START and ### FULL_FILE_CONTEXT_END markers) "
                "is provided for each file as read-only context. "
                "Use it to understand the surrounding code, but focus your review EXCLUSIVELY on the changed lines (marked + in the diff). "
                "Do NOT report issues in unchanged lines unless they directly affect the correctness of the changes. "
                "For every problem, you MUST provide a valid file and line (>0) to allow inline comments. "
                "Do not emit general problem comments without file/line."
            )
        return (
            "Ambito de review: diff_only. O diff contém apenas linhas adicionadas (+) — contexto e eliminações foram removidos. "
            "Uma secção com o conteúdo completo do ficheiro (entre os marcadores ### FULL_FILE_CONTEXT_START e ### FULL_FILE_CONTEXT_END) "
            "é fornecida como contexto de leitura. "
            "Usa-a para compreender o código envolvente, mas foca o teu review EXCLUSIVAMENTE nas linhas alteradas (marcadas com + no diff). "
            "NÃO reportes problemas em linhas não alteradas, exceto se afetarem diretamente a correção das alterações. "
            "Para cada problema, DEVE ser fornecido file e line válidos (>0) para comentário inline. "
            "Não emitas comentários gerais de problema sem file/line."
        )

    if language == "en":
        return (
            "Review scope: diff_only. The diff contains only added lines (+). "
            "A full file content section (between ### FULL_FILE_CONTEXT_START and ### FULL_FILE_CONTEXT_END markers) "
            "is provided for each file as read-only context. "
            "Use it to understand the surrounding code, but focus your review EXCLUSIVELY on the changed lines. "
            "Do NOT report issues in unchanged lines unless they directly affect the correctness of the changes."
        )
    return (
        "Ambito de review: diff_only. O diff contém apenas linhas adicionadas (+). "
        "Uma secção com o conteúdo completo do ficheiro (entre os marcadores ### FULL_FILE_CONTEXT_START e ### FULL_FILE_CONTEXT_END) "
        "é fornecida como contexto de leitura. "
        "Usa-a para compreender o código envolvente, mas foca o teu review EXCLUSIVAMENTE nas linhas alteradas. "
        "NÃO reportes problemas em linhas não alteradas, exceto se afetarem diretamente a correção das alterações."
    )


def build_user_message(diff: str, files_summary: list[dict], context: str = "") -> str:
    """
    Builds the user message with the diff and context.
    """
    parts = []

    if files_summary:
        parts.append("### Changed Files:")
        for f in files_summary:
            parts.append(
                f"  - `{f['file']}` (+{f['additions']}/-{f['deletions']})"
            )
        parts.append("")

    if context:
        parts.append(f"### Additional context:\n{context}\n")

    parts.append("### Diff for review:")
    parts.append(f"```diff\n{diff}\n```")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Main LLM client class
# ---------------------------------------------------------------------------
class LLMClient:
    """Client for communication with LLM APIs."""

    def __init__(self, config: ReviewConfig):
        self.config = config

    def _load_custom_prompt_text(self) -> str:
        """Loads extra instructions from a configurable Markdown file."""
        path = (self.config.custom_prompt_file or "").strip()
        if not path:
            return ""

        abs_path = os.path.abspath(path)
        if not os.path.isfile(abs_path):
            return ""

        try:
            with open(abs_path, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            return ""

    def review(self, diff: str, files_summary: list[dict],
               context: str = "", review_scope: str = "diff_only") -> str:
        """
        Sends the diff to the LLM and returns the review as text.
        """
        base_prompt = get_system_prompt(
            self.config.verbosity,
            self.config.review_language,
        )
        custom_prompt = self._load_custom_prompt_text()

        scope_guidance = get_scope_guidance(
            review_scope=review_scope,
            language=self.config.review_language,
            structured=False,
        )

        if custom_prompt:
            system_prompt = (
                f"{base_prompt}\n\n"
                f"{scope_guidance}\n\n"
                "---\n"
                "Custom user instructions (follow with priority):\n"
                f"{custom_prompt}"
            )
            merged_context = (
                f"{context}\n\n[Custom context loaded from {self.config.custom_prompt_file}]"
                if context else
                f"[Custom context loaded from {self.config.custom_prompt_file}]"
            )
        else:
            system_prompt = f"{base_prompt}\n\n{scope_guidance}"
            merged_context = context

        user_message = build_user_message(diff, files_summary, merged_context)

        provider = self.config.llm_provider.lower()

        if provider == "openai":
            return self._call_openai(system_prompt, user_message)
        elif provider == "azure_openai":
            return self._call_openai(system_prompt, user_message, azure=True)
        elif provider == "gemini":
            return self._call_gemini(system_prompt, user_message)
        elif provider == "claude":
            return self._call_claude(system_prompt, user_message)
        elif provider == "ollama":
            return self._call_ollama(system_prompt, user_message)
        elif provider == "copilot":
            return self._call_copilot(system_prompt, user_message)
        elif provider == "bedrock":
            return self._call_bedrock(system_prompt, user_message)
        else:
            raise LLMError(
                f"Unsupported provider: '{provider}'.\n"
                "Available providers: openai, azure_openai, gemini, claude, ollama, copilot, bedrock"
            )

    def review_pr_structured(self, diff: str, files_summary: list[dict],
                             context: str = "", review_scope: str = "diff_only") -> list[dict]:
        """
        Sends the diff to the LLM and returns structured PR comments.
        
        Returns:
            List of dicts with keys: file, line, type, comment, suggestion
        """
        base_prompt = get_pr_comment_prompt(self.config.review_language)
        custom_prompt = self._load_custom_prompt_text()

        scope_guidance = get_scope_guidance(
            review_scope=review_scope,
            language=self.config.review_language,
            structured=True,
        )

        if custom_prompt:
            system_prompt = (
                f"{base_prompt}\n\n"
                f"{scope_guidance}\n\n"
                "---\n"
                "Custom user instructions (follow with priority):\n"
                f"{custom_prompt}"
            )
            merged_context = (
                f"{context}\n\n[Custom context loaded from {self.config.custom_prompt_file}]"
                if context else
                f"[Custom context loaded from {self.config.custom_prompt_file}]"
            )
        else:
            system_prompt = f"{base_prompt}\n\n{scope_guidance}"
            merged_context = context

        user_message = build_user_message(diff, files_summary, merged_context)

        provider = self.config.llm_provider.lower()

        if provider == "openai":
            raw = self._call_openai(system_prompt, user_message)
        elif provider == "azure_openai":
            raw = self._call_openai(system_prompt, user_message, azure=True)
        elif provider == "gemini":
            raw = self._call_gemini(system_prompt, user_message)
        elif provider == "claude":
            raw = self._call_claude(system_prompt, user_message)
        elif provider == "ollama":
            raw = self._call_ollama(system_prompt, user_message)
        elif provider == "copilot":
            raw = self._call_copilot(system_prompt, user_message)
        elif provider == "bedrock":
            raw = self._call_bedrock(system_prompt, user_message)
        else:
            raise LLMError(f"Unsupported provider: '{provider}'")

        return self._parse_structured_comments(raw)

    def _parse_structured_comments(self, raw_response: str) -> list[dict]:
        """Parses the LLM JSON response."""
        # Try to extract JSON from possible markdown
        text = raw_response.strip()
        if text.startswith("```"):
            # Remove markdown code blocks
            lines = text.split("\n")
            json_lines = []
            in_block = False
            for line in lines:
                if line.strip().startswith("```"):
                    in_block = not in_block
                    continue
                if in_block or not line.strip().startswith("```"):
                    json_lines.append(line)
            text = "\n".join(json_lines).strip()

        # Try to find JSON array
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end != -1:
            text = text[start:end + 1]

        try:
            comments = json.loads(text)
            if not isinstance(comments, list):
                comments = [comments]
        except json.JSONDecodeError:
            # Fallback: return as a general comment
            return [{
                "file": "",
                "line": 0,
                "type": "suggestion",
                "comment": raw_response,
                "suggestion": "",
                "reference": "",
            }]

        # Validate and normalize each comment
        validated = []
        for c in comments:
            validated.append({
                "file": str(c.get("file", "")),
                "line": int(c.get("line", 0)),
                "type": str(c.get("type", "suggestion")),
                "comment": str(c.get("comment", "")),
                "suggestion": str(c.get("suggestion", "")),
                "reference": str(c.get("reference", "")),
            })
        return validated

    # ------------------------------------------------------------------
    # OpenAI / Azure OpenAI
    # ------------------------------------------------------------------
    def _call_openai(self, system_prompt: str, user_message: str,
                     azure: bool = False) -> str:
        """
        Calls the OpenAI API (GPT-4, GPT-4-turbo, GPT-4o).
        Also supports Azure OpenAI.
        """
        try:
            import requests  # noqa: F401
        except ImportError:
            raise LLMError("Module 'requests' not installed: pip install requests")

        api_key = self.config.api_key or self.config.openai_api_key
        if not api_key:
            raise LLMError(
                "OpenAI API key not configured.\n"
                "Configure llm.api_key or openai.api_key in config.yaml"
            )

        if azure:
            base_url = self.config.api_base_url
            if not base_url:
                raise LLMError(
                    "Azure OpenAI requires API_BASE_URL to be configured.\n"
                    "E.g., https://your-resource.openai.azure.com/openai/deployments/your-deploy"
                )
            url = f"{base_url}/chat/completions?api-version=2024-02-01"
            headers = {
                "api-key": api_key,
                "Content-Type": "application/json",
            }
        else:
            base_url = self.config.api_base_url or "https://api.openai.com/v1"
            url = f"{base_url}/chat/completions"
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }

        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "max_tokens": self.config.max_tokens,
            "temperature": self.config.temperature,
        }

        return self._http_openai_compatible(url, headers, payload)

    # ------------------------------------------------------------------
    # Google Gemini
    # ------------------------------------------------------------------
    def _call_gemini(self, system_prompt: str, user_message: str) -> str:
        """
        Calls the Google Gemini API (gemini-pro, gemini-1.5-pro, gemini-2.0-flash).
        Uses the Google AI Generative Language API.
        """
        try:
            import requests
        except ImportError:
            raise LLMError("Module 'requests' not installed: pip install requests")

        api_key = self.config.api_key or self.config.gemini_api_key
        if not api_key:
            raise LLMError(
                "Google Gemini API key not configured.\n"
                "Get it at: https://aistudio.google.com/app/apikey\n"
                "Configure llm.api_key or gemini.api_key in config.yaml"
            )

        model = self.config.model or "gemini-1.5-pro"
        base_url = (
            self.config.api_base_url
            or "https://generativelanguage.googleapis.com/v1beta"
        )
        url = f"{base_url}/models/{model}:generateContent?key={api_key}"

        headers = {"Content-Type": "application/json"}

        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": f"{system_prompt}\n\n{user_message}"}],
                }
            ],
            "systemInstruction": {
                "parts": [{"text": system_prompt}]
            },
            "generationConfig": {
                "temperature": self.config.temperature,
                "maxOutputTokens": self.config.max_tokens,
            },
        }

        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=180)

            if resp.status_code == 400:
                error_data = resp.json()
                msg = error_data.get("error", {}).get("message", resp.text[:500])
                raise LLMError(f"Gemini error (400): {msg}")
            elif resp.status_code == 403:
                raise LLMError(
                    "Gemini API key invalid or insufficient permissions.\n"
                    "Check at: https://aistudio.google.com/app/apikey"
                )
            elif resp.status_code == 429:
                raise LLMError("Gemini rate limit exceeded. Wait and try again.")
            elif resp.status_code >= 400:
                raise LLMError(f"Gemini API error ({resp.status_code}): {resp.text[:500]}")

            data = resp.json()

            # Extract text from response
            candidates = data.get("candidates", [])
            if candidates:
                content = candidates[0].get("content", {})
                parts = content.get("parts", [])
                if parts:
                    return parts[0].get("text", "")

            raise LLMError(f"Unexpected Gemini response: {json.dumps(data)[:500]}")

        except requests.exceptions.ConnectionError:
            raise LLMError(
                f"Could not connect to Gemini ({url[:80]}).\n"
                "Check your network connection."
            )
        except requests.exceptions.Timeout:
            raise LLMError("Gemini request timed out. Try again.")
        except requests.exceptions.RequestException as exc:
            raise LLMError(f"HTTP error calling Gemini: {exc}")

    # ------------------------------------------------------------------
    # Anthropic Claude
    # ------------------------------------------------------------------
    def _call_claude(self, system_prompt: str, user_message: str) -> str:
        """
        Calls the Anthropic Claude API (claude-3-opus, claude-3-sonnet, claude-3-haiku).
        Uses the Anthropic Messages API.
        """
        try:
            import requests
        except ImportError:
            raise LLMError("Module 'requests' not installed: pip install requests")

        api_key = self.config.api_key or self.config.anthropic_api_key
        if not api_key:
            raise LLMError(
                "Anthropic Claude API key not configured.\n"
                "Get it at: https://console.anthropic.com/settings/keys\n"
                "Configure llm.api_key or claude.api_key in config.yaml"
            )

        model = self.config.model or "claude-3-5-sonnet-latest"
        base_url = self.config.api_base_url or "https://api.anthropic.com"
        url = f"{base_url}/v1/messages"

        headers = {
            "x-api-key": api_key,
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        }

        payload = {
            "model": model,
            "max_tokens": self.config.max_tokens,
            "temperature": self.config.temperature,
            "system": system_prompt,
            "messages": [
                {"role": "user", "content": user_message},
            ],
        }

        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=180)

            if resp.status_code == 401:
                raise LLMError(
                    "Claude API key invalid.\n"
                    "Check at: https://console.anthropic.com/settings/keys"
                )
            elif resp.status_code == 429:
                raise LLMError("Claude rate limit exceeded. Wait and try again.")
            elif resp.status_code >= 400:
                error_data = {}
                try:
                    error_data = resp.json()
                except Exception:
                    pass
                msg = error_data.get("error", {}).get("message", resp.text[:500])
                raise LLMError(f"Claude API error ({resp.status_code}): {msg}")

            data = resp.json()

            # Extract text from response
            content = data.get("content", [])
            if content:
                text_parts = [
                    block.get("text", "")
                    for block in content
                    if block.get("type") == "text"
                ]
                if text_parts:
                    return "\n".join(text_parts)

            raise LLMError(f"Unexpected Claude response: {json.dumps(data)[:500]}")

        except requests.exceptions.ConnectionError:
            raise LLMError(
                "Could not connect to Anthropic Claude.\n"
                "Check your network connection."
            )
        except requests.exceptions.Timeout:
            raise LLMError("Claude request timed out. Try again.")
        except requests.exceptions.RequestException as exc:
            raise LLMError(f"HTTP error calling Claude: {exc}")

    # ------------------------------------------------------------------
    # Ollama (local models)
    # ------------------------------------------------------------------
    def _call_ollama(self, system_prompt: str, user_message: str) -> str:
        """
        Calls the Ollama API (local models).
        Uses the OpenAI-compatible endpoint.
        """
        try:
            import requests
        except ImportError:
            raise LLMError("Module 'requests' not installed: pip install requests")

        base_url = self.config.api_base_url or "http://localhost:11434"
        model = self.config.model or "llama3"

        # Ollama supports the OpenAI-compatible endpoint
        url = f"{base_url}/v1/chat/completions"

        headers = {"Content-Type": "application/json"}

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "temperature": self.config.temperature,
            "stream": False,
        }

        # Ollama does not require an API key, but we add max_tokens if configured
        if self.config.max_tokens:
            payload["max_tokens"] = self.config.max_tokens

        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=300)

            if resp.status_code == 404:
                # Try Ollama native endpoint as fallback
                return self._call_ollama_native(base_url, model, system_prompt, user_message)
            elif resp.status_code >= 400:
                raise LLMError(
                    f"Ollama error ({resp.status_code}): {resp.text[:500]}\n"
                    "Check if Ollama is running and the model is installed.\n"
                    f"Install the model with: ollama pull {model}"
                )

            data = resp.json()

            if "choices" in data and data["choices"]:
                return data["choices"][0]["message"]["content"]
            else:
                raise LLMError(f"Unexpected Ollama response: {json.dumps(data)[:500]}")

        except requests.exceptions.ConnectionError:
            raise LLMError(
                f"Could not connect to Ollama at {base_url}.\n"
                "Check if Ollama is running:\n"
                "  1. Install: https://ollama.ai\n"
                "  2. Start: ollama serve\n"
                f"  3. Install the model: ollama pull {model}"
            )
        except requests.exceptions.Timeout:
            raise LLMError(
                "Ollama request timed out. Local models may take longer "
                "depending on hardware."
            )
        except requests.exceptions.RequestException as exc:
            raise LLMError(f"HTTP error calling Ollama: {exc}")

    def _call_ollama_native(self, base_url: str, model: str,
                            system_prompt: str, user_message: str) -> str:
        """Fallback for Ollama native API (/api/chat)."""
        import requests

        url = f"{base_url}/api/chat"

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "stream": False,
        }

        try:
            resp = requests.post(url, json=payload, timeout=300)
            resp.raise_for_status()
            data = resp.json()
            return data.get("message", {}).get("content", str(data))
        except Exception as exc:
            raise LLMError(f"Error in Ollama native call: {exc}")

    # ------------------------------------------------------------------
    # GitHub Copilot
    # ------------------------------------------------------------------
    def _call_copilot(self, system_prompt: str, user_message: str) -> str:
        """
        Calls the GitHub Copilot API.

        Uses the GitHub Models API which requires:
        - GitHub token (PAT) with adequate permissions
        - Active GitHub Copilot subscription

        The endpoint is compatible with OpenAI Chat Completions format.
        Available models: gpt-4o, gpt-4o-mini, o1, o1-mini,
        claude-3.5-sonnet (via GitHub), etc.
        """
        try:
            import requests
        except ImportError:
            raise LLMError("Module 'requests' not installed: pip install requests")

        api_key = (
            self.config.api_key
            or self.config.github_token
        )
        if not api_key:
            raise LLMError(
                "GitHub token not configured for the Copilot provider.\n"
                "Configure llm.api_key or copilot.github_token in config.yaml.\n"
                "The token must have the necessary permissions and an active\n"
                "GitHub Copilot subscription is required.\n"
                "Create at: https://github.com/settings/tokens"
            )

        model = self.config.model or "gpt-4o"
        base_url = (
            self.config.api_base_url
            or "https://models.github.ai/inference"
        )
        url = f"{base_url}/chat/completions"

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "temperature": self.config.temperature,
        }

        # Add max_tokens if configured (some Copilot models
        # may not support this parameter)
        if self.config.max_tokens:
            payload["max_tokens"] = self.config.max_tokens

        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=180)

            if resp.status_code == 401:
                raise LLMError(
                    "GitHub token invalid or insufficient permissions.\n"
                    "Check:\n"
                    "  1. The token is correct\n"
                    "  2. You have an active GitHub Copilot subscription\n"
                    "  3. The token has the required permissions\n"
                    "Create/check at: https://github.com/settings/tokens"
                )
            elif resp.status_code == 403:
                raise LLMError(
                    "Access denied to GitHub Copilot.\n"
                    "Check:\n"
                    "  1. You have an active GitHub Copilot subscription\n"
                    "  2. API access is enabled in your organization\n"
                    "  3. The token has the correct permissions"
                )
            elif resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After", "60")
                raise LLMError(
                    f"GitHub Copilot rate limit exceeded.\n"
                    f"Wait {retry_after}s and try again.\n"
                    "Copilot has usage limits that vary by plan."
                )
            elif resp.status_code >= 400:
                error_msg = resp.text[:500]
                try:
                    error_data = resp.json()
                    error_msg = error_data.get("error", {}).get("message", error_msg)
                except Exception:
                    pass
                raise LLMError(
                    f"GitHub Copilot API error ({resp.status_code}): {error_msg}"
                )

            data = resp.json()

            if "choices" in data and data["choices"]:
                return data["choices"][0]["message"]["content"]
            else:
                raise LLMError(
                    f"Unexpected GitHub Copilot response: {json.dumps(data)[:500]}"
                )

        except requests.exceptions.ConnectionError:
            raise LLMError(
                f"Could not connect to GitHub Copilot ({base_url}).\n"
                "Check your network connection."
            )
        except requests.exceptions.Timeout:
            raise LLMError(
                "GitHub Copilot request timed out. Try again."
            )
        except requests.exceptions.RequestException as exc:
            raise LLMError(f"HTTP error calling GitHub Copilot: {exc}")

    # ------------------------------------------------------------------
    # AWS Bedrock
    # ------------------------------------------------------------------
    def _call_bedrock(self, system_prompt: str, user_message: str) -> str:
        """Calls the AWS Bedrock Runtime, auto-detecting the auth mode.

        Routing: Bearer (access_key_id only) → SigV4 (key + secret) → boto3 (default chain).

        Args:
            system_prompt: System-role instructions for the model.
            user_message: User-role message containing the diff.

        Returns:
            The model's text response.

        Raises:
            LLMError: On missing region, auth failure, or unexpected response.
        """
        region = self.config.bedrock_region
        if not region:
            raise LLMError(
                "Provider 'bedrock' requires bedrock.region in config.yaml."
            )

        access_key = self.config.bedrock_access_key_id
        secret_key = self.config.bedrock_secret_access_key

        # Bedrock long-term API key: single value, no secret — use HTTP Bearer
        if access_key and not secret_key:
            return self._call_bedrock_bearer(region, access_key, system_prompt, user_message)

        # IAM key pair: use SigV4 signing
        if access_key and secret_key:
            return self._call_bedrock_sigv4(
                region, access_key, secret_key,
                self.config.bedrock_session_token,
                system_prompt, user_message,
            )

        # Profile / SSO / default credential chain: delegate to boto3
        return self._call_bedrock_boto3(region, system_prompt, user_message)

    def _call_bedrock_bearer(
        self,
        region: str,
        api_key: str,
        system_prompt: str,
        user_message: str,
    ) -> str:
        """Calls Bedrock InvokeModel using an HTTP Bearer token (long-term API key).

        Args:
            region: AWS region, e.g. ``us-east-1``.
            api_key: Bedrock long-term API key used as the Bearer token.
            system_prompt: System-role instructions for the model.
            user_message: User-role message containing the diff.

        Returns:
            The model's text response.

        Raises:
            LLMError: On auth failure (401), non-200 status, or invalid response.
        """
        import urllib.parse

        try:
            import requests
        except ImportError:
            raise LLMError("'requests' is not installed.\nInstall with: pip install requests")

        # The model ARN contains ':' and '/' that must be URL-encoded in the path
        model_encoded = urllib.parse.quote(self.config.model, safe="")
        url = f"https://bedrock-runtime.{region}.amazonaws.com/model/{model_encoded}/invoke"

        payload = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": self.config.max_tokens,
            "temperature": self.config.temperature,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_message}],
        }, separators=(",", ":"))

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        try:
            resp = requests.post(url, headers=headers, data=payload, timeout=180)
        except requests.exceptions.RequestException as exc:
            raise LLMError(f"Bedrock HTTP request failed: {exc}") from exc

        if resp.status_code == 401:
            raise LLMError(
                "Bedrock authentication failed (401). "
                "Check that bedrock.access_key_id is a valid long-term API key."
            )
        if resp.status_code != 200:
            raise LLMError(f"Bedrock returned HTTP {resp.status_code}: {resp.text[:500]}")

        try:
            data = resp.json()
        except ValueError as exc:
            raise LLMError(f"Invalid JSON from Bedrock: {resp.text[:500]}") from exc

        content = data.get("content", [])
        text_parts = [
            item["text"] for item in content
            if item.get("type") == "text" and "text" in item
        ]
        text = "\n".join(part for part in text_parts if part).strip()
        if not text:
            raise LLMError(f"Unexpected Bedrock response: {json.dumps(data)[:500]}")
        return text

    def _call_bedrock_sigv4(
        self,
        region: str,
        access_key_id: str,
        secret_access_key: str,
        session_token: str,
        system_prompt: str,
        user_message: str,
    ) -> str:
        """Calls Bedrock InvokeModel with manual AWS SigV4 HMAC-SHA256 signing.

        Equivalent to the C# BedrockLlmClient implementation.

        Args:
            region: AWS region, e.g. ``us-east-1``.
            access_key_id: IAM access key ID.
            secret_access_key: IAM secret access key.
            session_token: Optional STS session token (empty string if unused).
            system_prompt: System-role instructions for the model.
            user_message: User-role message containing the diff.

        Returns:
            The model's text response.

        Raises:
            LLMError: On auth failure (401), non-200 status, or invalid response.
        """
        import hashlib
        import hmac
        import urllib.parse

        try:
            import requests
        except ImportError:
            raise LLMError(
                "'requests' is not installed.\n"
                "Install with: pip install requests"
            )

        host = f"bedrock-runtime.{region}.amazonaws.com"
        model_encoded = urllib.parse.quote(self.config.model, safe="")
        endpoint = f"https://{host}/model/{model_encoded}/invoke"
        service = "bedrock"

        payload = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": self.config.max_tokens,
            "temperature": self.config.temperature,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_message}],
        }, separators=(",", ":"))

        now = datetime.datetime.now(datetime.timezone.utc)
        date_stamp = now.strftime("%Y%m%d")
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")

        payload_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()

        # --- Canonical request ---
        # Use the ORIGINAL (unencoded) model to build the canonical URI.
        # Splitting by '/' and encoding each segment mirrors the C# SigV4 implementation.
        canonical_uri = "/".join(
            urllib.parse.quote(seg, safe="")
            for seg in f"/model/{self.config.model}/invoke".split("/")
        )

        headers_to_sign = {
            "content-type": "application/json",
            "host": host,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
        }
        if session_token:
            headers_to_sign["x-amz-security-token"] = session_token

        signed_headers = ";".join(sorted(headers_to_sign))
        canonical_headers = "".join(
            f"{k}:{v}\n" for k, v in sorted(headers_to_sign.items())
        )
        canonical_request = "\n".join([
            "POST", canonical_uri, "",
            canonical_headers, signed_headers, payload_hash,
        ])

        # --- String to sign ---
        credential_scope = f"{date_stamp}/{region}/{service}/aws4_request"
        string_to_sign = "\n".join([
            "AWS4-HMAC-SHA256", amz_date, credential_scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ])

        # --- Signing key ---
        def _hmac(key: bytes, msg: str) -> bytes:
            return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

        k_date = _hmac(f"AWS4{secret_access_key}".encode("utf-8"), date_stamp)
        k_region = _hmac(k_date, region)
        k_service = _hmac(k_region, service)
        k_signing = _hmac(k_service, "aws4_request")
        signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

        # --- Authorization header ---
        authorization = (
            f"AWS4-HMAC-SHA256 Credential={access_key_id}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )

        http_headers = {
            "Authorization": authorization,
            "Content-Type": "application/json",
            "x-amz-date": amz_date,
            "x-amz-content-sha256": payload_hash,
            **({} if not session_token else {"x-amz-security-token": session_token}),
        }

        try:
            resp = requests.post(endpoint, headers=http_headers, data=payload, timeout=180)
        except requests.exceptions.RequestException as exc:
            raise LLMError(f"Bedrock HTTP request failed: {exc}") from exc

        if resp.status_code == 401:
            raise LLMError(
                "Bedrock authentication failed (401). "
                "Check bedrock.access_key_id and bedrock.secret_access_key in config.yaml."
            )
        if resp.status_code != 200:
            raise LLMError(f"Bedrock returned HTTP {resp.status_code}: {resp.text[:500]}")

        try:
            data = resp.json()
        except ValueError as exc:
            raise LLMError(f"Invalid JSON from Bedrock: {resp.text[:500]}") from exc

        content = data.get("content", [])
        text_parts = [
            item["text"] for item in content
            if item.get("type") == "text" and "text" in item
        ]
        text = "\n".join(part for part in text_parts if part).strip()
        if not text:
            raise LLMError(f"Unexpected Bedrock response: {json.dumps(data)[:500]}")
        return text

    def _call_bedrock_boto3(self, region: str, system_prompt: str, user_message: str) -> str:
        """Calls Bedrock via the boto3 ``converse()`` API.

        Supports AWS SSO, named profiles, and the default credential chain.

        Args:
            region: AWS region, e.g. ``us-east-1``.
            system_prompt: System-role instructions for the model.
            user_message: User-role message containing the diff.

        Returns:
            The model's text response.

        Raises:
            LLMError: On boto3 import failure, BotoCoreError, or unexpected response.
        """
        try:
            import boto3  # type: ignore[import-untyped]
            from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]
        except ImportError:
            raise LLMError(
                "AWS dependency not installed.\n"
                "Install with: pip install boto3"
            )

        try:
            session_kwargs: dict = {}
            if self.config.bedrock_profile:
                session_kwargs["profile_name"] = self.config.bedrock_profile

            # Explicit IAM credentials override the default credential chain.
            if self.config.bedrock_access_key_id and self.config.bedrock_secret_access_key:
                session_kwargs["aws_access_key_id"] = self.config.bedrock_access_key_id
                session_kwargs["aws_secret_access_key"] = self.config.bedrock_secret_access_key
                if self.config.bedrock_session_token:
                    session_kwargs["aws_session_token"] = self.config.bedrock_session_token

            session = boto3.Session(**session_kwargs)
            client = session.client("bedrock-runtime", region_name=region)

            response = client.converse(
                modelId=self.config.model,
                system=[{"text": system_prompt}],
                messages=[
                    {
                        "role": "user",
                        "content": [{"text": user_message}],
                    }
                ],
                inferenceConfig={
                    "temperature": self.config.temperature,
                    "maxTokens": self.config.max_tokens,
                },
            )

            content = (
                response.get("output", {})
                .get("message", {})
                .get("content", [])
            )
            text_parts = [item.get("text", "") for item in content if "text" in item]
            text = "\n".join(part for part in text_parts if part).strip()
            if not text:
                raise LLMError(
                    f"Unexpected Bedrock response: {json.dumps(response)[:500]}"
                )

            return text

        except (BotoCoreError, ClientError) as exc:
            raise LLMError(f"Error calling AWS Bedrock: {exc}")

    # ------------------------------------------------------------------
    # HTTP helper for OpenAI-compatible APIs
    # ------------------------------------------------------------------
    def _http_openai_compatible(self, url: str, headers: dict, payload: dict) -> str:
        """Makes an HTTP call to OpenAI-compatible format APIs."""
        import requests

        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=180)

            if resp.status_code == 401:
                raise LLMError(
                    "API key invalid or expired. Check your configuration."
                )
            elif resp.status_code == 429:
                raise LLMError(
                    "Rate limit exceeded. Wait a few seconds and try again."
                )
            elif resp.status_code >= 400:
                raise LLMError(
                    f"API error ({resp.status_code}): {resp.text[:500]}"
                )

            data = resp.json()

            if "choices" in data and data["choices"]:
                return data["choices"][0]["message"]["content"]
            else:
                raise LLMError(f"Unexpected API response: {json.dumps(data)[:500]}")

        except requests.exceptions.ConnectionError:
            raise LLMError(
                f"Could not connect to {url}.\n"
                "Check the URL and your network connection."
            )
        except requests.exceptions.Timeout:
            raise LLMError("API request timed out. Try again.")
        except requests.exceptions.RequestException as exc:
            raise LLMError(f"HTTP request error: {exc}")
