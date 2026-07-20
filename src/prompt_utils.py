"""
Prompt Utilities Module - AI Code Review
========================================
Responsible for tailoring the custom review prompt (review_prompt.md) to the
files actually changed in a diff:
- Detects which "languages" (file extensions) are present in the changeset
- Filters the custom prompt content, keeping only the sections tagged for
  those extensions (plus sections tagged "all", which always apply)

No manual extension-to-language mapping is required: the file extension
itself (without the dot, lowercase) is used as the tag. This means the
prompt file can be extended with new languages (e.g. Java, Go) without any
code changes - just add a new <!-- lang: ... --> section to review_prompt.md.
"""

import os
import re

LANG_TAG_RE = re.compile(r"<!--\s*lang:\s*(.*?)\s*-->")


def detect_langs(file_paths: list[str]) -> set[str]:
    """
    Detects active tags directly from the extensions of changed files.
    'all' is always included since general sections always apply.
    No manual mapping: the extension (no dot, lowercase) IS the tag.
    """
    langs = {"all"}
    for path in file_paths:
        if not path:
            continue
        ext = os.path.splitext(path)[1].lstrip(".").lower()
        if ext:
            langs.add(ext)
    return langs


def filter_prompt_by_langs(md_content: str, active_langs: set[str]) -> str:
    """
    Parses review_prompt.md and returns only the sections whose
    <!-- lang: ... --> tag intersects with active_langs.
    """
    active_langs = {l.strip().lower() for l in active_langs}
    blocks = re.split(r"(<!--\s*lang:.*?-->)", md_content)

    output: list[str] = []
    current_langs = {"all"}
    for chunk in blocks:
        match = LANG_TAG_RE.match(chunk.strip())
        if match:
            current_langs = {l.strip().lower() for l in match.group(1).split(",")}
            continue
        if current_langs & active_langs:
            output.append(chunk)

    return "\n".join(output).strip()