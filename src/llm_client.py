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
from .prompt_utils import detect_langs, filter_prompt_by_langs


class LLMError(Exception):
    """Exception for LLM communication errors."""
    pass

# ---------------------------------------------------------------------------
# System Prompts
# ---------------------------------------------------------------------------
# Maps each verbosity level ("quick", "detailed", "security") to its
# English-only system prompt.
#
# Type:
#     dict[str, str] — flat mapping of verbosity -> prompt string. There is
#     no per-language nesting; each value is a plain ``str``.
#
# See Also:
#     get_system_prompt: Resolves a prompt for a given verbosity, with a
#     fallback to "detailed" when the key is not recognised.
SYSTEM_PROMPTS = {
    "quick": (
        "You are an experienced Senior Code Reviewer. Analyze the provided code diff "
        "and give a CONCISE review. Focus on critical issues:\n"
        "- Bugs and logic errors\n"
        "- Security issues\n"
        "- Major performance problems\n\n"
        "Format: Bullet points with file and line when possible. "
    ),
    "detailed": (
        "You are an experienced Senior Code Reviewer. Analyze the provided code and return only inline comments.\n\n"
        "Output format — for each issue found, output exactly:\n"
        "- Line <line_number>: <issue description>\n\n"
        "Rules:\n"
        "- Report ONLY specific issues tied to a concrete line or block of changed code.\n"
        "- Do NOT produce summary sections (e.g. 'Potential Bugs', 'Security Overview').\n"
        "- Do NOT produce introductory or closing paragraphs.\n"
        "- If no issues are found, output only: No issues found.\n"
        "- Be specific, actionable, and concise."
    ),
    "security": (
        "You are an application security. Analyze the "
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
}

# Special prompt for PR review with a combined narrative summary + structured comments.
#
# Type:
#     str — a single English-only prompt (not a per-language dict) instructing
#     the LLM to respond with one JSON object containing a "summary" string
#     and a "comments" array (each with file, line, type, comment,
#     suggestion, and reference fields).
#
# See Also:
#     LLMClient.review_pr: Combines this prompt with the verbosity system
#     prompt (from get_system_prompt) and scope guidance (from
#     get_scope_guidance) to build the final system prompt sent to the LLM.
PR_COMMENT_PROMPT = (
    "Analyze the Pull Request code diff and return your response as a single JSON object with two "
    "fields:\n"
    '- "summary": a narrative text summary of the review (string). If no issues are found, write '
    'something like "No issues found."\n'
    '- "comments": an array of objects, one per issue found, with:\n'
    '  - "file": file path (e.g., "src/auth.py")\n'
    '  - "line": line number in diff (integer, or 0 if general)\n'
    '  - "type": issue type ("bug", "security", "performance", "style", "suggestion", "praise")\n'
    '  - "comment": direct description of the issue, with no greetings and no emojis\n'
    '  - "suggestion": fix suggestion (optional, empty string if not applicable)\n'
    '  - "reference": source or reference for the issue (e.g., "OWASP Top 10", "PEP 8", documentation '
    'URL, standard or principle). Important: ALWAYS include a relevant reference.\n\n'
    "In 'comment', use a short and objective tone. "
    "Do not include intros like 'Hello' or 'As a senior reviewer'.\n"
    "In 'reference', include a trusted source, standard or link to relevant documentation.\n\n"
    "Respond ONLY with a valid JSON object. Example:\n"
    '{\n'
    '  "summary": "Short narrative summary of the review...",\n'
    '  "comments": [\n'
    '    {\n'
    '      "file": "src/auth.py",\n'
    '      "line": 42,\n'
    '      "type": "security",\n'
    '      "comment": "Password stored in plain text without hashing",\n'
    '      "suggestion": "Use bcrypt or argon2 to hash passwords",\n'
    '      "reference": "OWASP - Password Storage Cheat Sheet"\n'
    '    }\n'
    '  ]\n'
    '}\n\n'
)


def get_system_prompt(verbosity: str) -> str:
    """Returns the system prompt for the given verbosity level.

    Selects a prompt from ``SYSTEM_PROMPTS`` keyed by *verbosity*. If the
    requested verbosity is not found, the function falls back to
    ``"detailed"``.

    The ``"detailed"`` prompt instructs the LLM to return **only** inline
    comments in the format ``- Line <n>: <description>``,
    prohibiting summary sections and introductory/closing paragraphs.
    When no issues are found the expected output is ``No issues found.``

    Args:
        verbosity: Review depth key — one of ``"quick"``, ``"detailed"``,
            or ``"security"``. Any unrecognised value falls back to
            ``"detailed"``.

    Returns:
        The system prompt string for the resolved verbosity.
    """
    return SYSTEM_PROMPTS.get(verbosity, SYSTEM_PROMPTS["detailed"])


def get_scope_guidance(review_scope: str, structured: bool = False) -> str:
    """Returns LLM instructions tailored to the active review scope.

    For ``diff_only`` scope, the instructions inform the LLM that:

    * Only added lines (``+``) appear in the diff section.
    * A ``### FULL_FILE_CONTEXT_START: <path> ###`` /
      ``### FULL_FILE_CONTEXT_END ###`` block is embedded in the payload for
      each changed file, containing the complete new-version (after changes)
      file content as **read-only** background.
    * The review must focus **exclusively** on the changed lines (``+``); the
      full-file section exists only to prevent the model from hallucinating
      about the surrounding code.

    For ``full_code`` scope, the instructions inform the LLM that the diff
    represents the entire new file content (every line prefixed with ``+``)
    and that deleted code is absent.

    Args:
        review_scope: ``"diff_only"`` (default) or ``"full_code"``.
        structured: When ``True``, appends additional constraints for the
            structured JSON comment mode (every comment must carry a valid
            file path and line number > 0 for inline posting).

    Returns:
        Instruction string to be appended to the system prompt.
    """
    scope = (review_scope or "diff_only").lower()

    if scope == "full_code":
        return (
            "Review scope: full_code. The diff contains only added lines (+) for each file. "
            "Analyze the complete content of the changed files and identify issues in the new code. "
            "Do not comment on deleted or absent code."
        )

    if structured:
        return (
            "Review scope: diff_only. The diff contains only added lines (+) — context and deletions were removed. "
            "The complete new-version file content, after the changes (between ### FULL_FILE_CONTEXT_START and ### FULL_FILE_CONTEXT_END markers) "
            "is provided for each file as read-only context. "
            "Use it to understand the surrounding code, but focus your review EXCLUSIVELY on the changed lines (marked + in the diff). "
            "Do NOT report issues in unchanged lines unless they directly affect the correctness of the changes. "
            "For every problem, you MUST provide a valid file and line (>0) to allow inline comments. "
            "Do not emit general problem comments without file/line."
        )

    return (
        "Review scope: diff_only. The diff contains only added lines (+). "
        "The new-version (post-change) full file content (between ### FULL_FILE_CONTEXT_START and ### FULL_FILE_CONTEXT_END markers) "
        "is provided for each file as read-only context. "
        "Use it to understand the surrounding code, but focus your review EXCLUSIVELY on the changed lines. "
        "Do NOT report issues in unchanged lines unless they directly affect the correctness of the changes."
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

    def _dump_prompt_debug(self, system_prompt: str, user_message: str) -> None:
        """
        Appends the full prompt/context sent to the LLM to a debug log file.

        Only active when ``config.debug_dump`` is enabled. The log may
        contain source code and PR content, so it must never be committed
        to version control.
        """
        if not getattr(self.config, "debug_dump", False):
            return

        log_path = self.config.debug_dump_file or os.path.join("logs", "llm_prompt_debug.log")
        log_dir = os.path.dirname(log_path)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"\n{'=' * 80}\n")
                f.write(f"[FULL CONTEXT SENT TO LLM] {datetime.datetime.now().isoformat()}\n")
                f.write(f"Provider: {self.config.llm_provider} | Model: {self.config.get_effective_model()}\n")
                f.write(f"{'=' * 80}\n")
                f.write("--- SYSTEM PROMPT ---\n")
                f.write(system_prompt + "\n")
                f.write("--- USER MESSAGE ---\n")
                f.write(user_message + "\n")
        except OSError:
            pass

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

    def review_pr(self, diff: str, files_summary: list[dict],
                  context: str = "", review_scope: str = "diff_only") -> tuple[str, list[dict]]:
        """
        Sends the diff to the LLM in a single call and returns both the
        narrative review text and the structured PR comments, parsed from one
        combined JSON response.

        This replaces the previous two-call approach (separate narrative and
        structured-comments requests), halving the diff/context payload sent
        to the LLM per PR review.

        Args:
            diff: Unified diff (or filtered/truncated diff) to review.
            files_summary: List of dicts with 'file', 'additions', 'deletions'.
            context: Additional free-text context supplied by the user.
            review_scope: "diff_only" (default) or "full_code".

        Returns:
            Tuple of (review_text, structured_comments), where structured_comments
            is a list of dicts with keys: file, line, type, comment, suggestion,
            reference.
        """
        base_prompt = get_system_prompt(self.config.verbosity)
        json_schema_prompt = PR_COMMENT_PROMPT
        custom_prompt = self._load_custom_prompt_text()

        # filter custom_prompt sections by the languages/extensions actually changed
        if custom_prompt:
            file_paths = [f.get("file") for f in files_summary]
            active_langs = detect_langs(file_paths)
            custom_prompt = filter_prompt_by_langs(custom_prompt, active_langs) # can return an empty list

        scope_guidance = get_scope_guidance(
            review_scope=review_scope,
            structured=True,
        )

        combined_base = f"{base_prompt}\n\n{json_schema_prompt}\n\n{scope_guidance}"

        if custom_prompt:
            system_prompt = (
                f"{combined_base}\n\n"
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
            system_prompt = combined_base
            merged_context = context

        user_message = build_user_message(diff, files_summary, merged_context)
        self._dump_prompt_debug(system_prompt, user_message)

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
            raise LLMError(
                f"Unsupported provider: '{provider}'.\n"
                "Available providers: openai, azure_openai, gemini, claude, ollama, copilot, bedrock"
            )

        return self._parse_combined_response(raw)

    def _extract_json_block(self, raw_response: str) -> str:
        """Strips markdown code fences and isolates the outer JSON object.

        Args:
            raw_response: Raw text returned by the LLM, possibly wrapped in a
                markdown code fence (```json ... ```` or ``` ... ````).

        Returns:
            The substring most likely to contain a valid JSON object.
        """
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

        # Try to find JSON object
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1:
            text = text[start:end + 1]

        return text

    def _parse_combined_response(self, raw_response: str) -> tuple[str, list[dict]]:
        """Parses the combined LLM response into (summary, comments).

        Falls back to treating the entire raw response as the summary (with an
        empty comments list) when the response is not valid JSON or is not a
        JSON object, mirroring the previous fallback behavior for malformed
        structured responses.

        Args:
            raw_response: Raw text returned by the LLM.

        Returns:
            Tuple of (summary, comments) where comments is a list of dicts with
            keys: file, line, type, comment, suggestion, reference.
        """
        text = self._extract_json_block(raw_response)

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return raw_response, []

        if not isinstance(data, dict):
            return raw_response, []

        summary = str(data.get("summary", "")).strip()
        raw_comments = data.get("comments", [])
        if not isinstance(raw_comments, list):
            raw_comments = []

        comments = []
        for c in raw_comments:
            if not isinstance(c, dict):
                continue
            try:
                line = int(c.get("line", 0))
            except (TypeError, ValueError):
                line = 0
            comments.append({
                "file": str(c.get("file", "")),
                "line": line,
                "type": str(c.get("type", "suggestion")),
                "comment": str(c.get("comment", "")),
                "suggestion": str(c.get("suggestion", "")),
                "reference": str(c.get("reference", "")),
            })

        return summary or raw_response, comments

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
