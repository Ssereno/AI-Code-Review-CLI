"""RAG Engine — extracts locally-related code context for a given git diff."""

import os
import re
import subprocess
from pathlib import Path


_IDENTIFIER_PATTERN = re.compile(
    r"\b(?:class|def|function|interface|public|private|protected|static|async)\s+(\w+)"
)
_MAX_IDENTIFIERS = 30
_MAX_FILES = 10
_CONTEXT_LINES = 50


def _extract_identifiers_from_diff(diff: str) -> list[str]:
    """
    Extracts relevant identifiers (file basenames + symbol names) from a diff.

    Args:
        diff: Raw git diff string.

    Returns:
        Deduplicated list of identifier strings, capped at 30.
    """
    identifiers: list[str] = []
    seen: set[str] = set()

    for line in diff.splitlines():
        if line.startswith("diff --git") and " b/" in line:
            file_path = line.split(" b/")[-1].strip()
            basename = Path(file_path).stem
            if basename and basename not in seen:
                seen.add(basename)
                identifiers.append(basename)
        elif line.startswith("+") and not line.startswith("+++"):
            for match in _IDENTIFIER_PATTERN.finditer(line):
                symbol = match.group(1)
                if symbol and symbol not in seen:
                    seen.add(symbol)
                    identifiers.append(symbol)

        if len(identifiers) >= _MAX_IDENTIFIERS:
            break

    return identifiers[:_MAX_IDENTIFIERS]


def _find_related_snippets(
    identifiers: list[str],
    repo_path: str,
    max_chars: int,
) -> list[dict[str, str]]:
    """
    Finds code snippets related to the given identifiers using git grep.

    Args:
        identifiers: List of symbol or filename identifiers to search for.
        repo_path: Absolute path to the git repository root.
        max_chars: Maximum total characters to accumulate across all snippets.

    Returns:
        List of dicts with keys 'file' and 'snippet'.
    """
    results: list[dict[str, str]] = []
    total_chars = 0
    files_found: set[str] = set()

    try:
        for identifier in identifiers:
            if len(files_found) >= _MAX_FILES or total_chars >= max_chars:
                break

            try:
                grep_list = subprocess.run(
                    ["git", "grep", "-l", "-i", identifier],
                    cwd=repo_path,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
            except Exception:
                continue

            for filepath in grep_list.stdout.splitlines():
                filepath = filepath.strip()
                if not filepath:
                    continue
                if filepath in files_found:
                    continue
                if filepath.endswith(identifier):
                    continue
                if len(files_found) >= _MAX_FILES or total_chars >= max_chars:
                    break

                try:
                    grep_line = subprocess.run(
                        ["git", "grep", "-n", identifier, filepath],
                        cwd=repo_path,
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    first_line_output = grep_line.stdout.splitlines()[0]
                    line_number = int(first_line_output.split(":")[1]) - 1
                except Exception:
                    line_number = 0

                try:
                    abs_path = os.path.join(repo_path, filepath)
                    with open(abs_path, "r", encoding="utf-8", errors="replace") as fh:
                        all_lines = fh.readlines()

                    start = max(0, line_number - 10)
                    end = min(len(all_lines), line_number + _CONTEXT_LINES - 10)
                    snippet = "".join(all_lines[start:end])
                except Exception:
                    continue

                files_found.add(filepath)
                results.append({"file": filepath, "snippet": snippet})
                total_chars += len(snippet)
    except Exception:
        return []

    return results


def obter_contexto_rag(
    git_diff_output: str,
    repo_path: str | None = None,
    max_chars: int = 40000,
) -> str:
    """
    Builds a RAG context string from code related to the given diff.

    Args:
        git_diff_output: Raw git diff output to analyse.
        repo_path: Absolute path to the repository root. Defaults to cwd.
        max_chars: Maximum characters of context to return.

    Returns:
        Markdown-formatted string with related code snippets, or empty string
        if no relevant context is found.
    """
    if repo_path is None:
        repo_path = os.getcwd()

    identifiers = _extract_identifiers_from_diff(git_diff_output)
    if not identifiers:
        return ""

    snippets = _find_related_snippets(identifiers, repo_path, max_chars)
    if not snippets:
        return ""

    parts: list[str] = ["### RAG Context: Related code found in repository\n"]
    for item in snippets:
        parts.append(f"\n#### `{item['file']}`\n```python\n{item['snippet']}\n```")

    return "\n".join(parts)
