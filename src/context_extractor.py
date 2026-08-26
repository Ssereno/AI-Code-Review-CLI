# License: Apache-2.0
# Copyright 2026 Sérgio Sereno

"""
Context Extractor Module - AI Code Review
==========================================
Provides AST-based context extraction for diff payloads sent to the LLM.

Strategy selection per file:
  - SQL files            → full SQL statement containing the changed line(s)
  - ≤ ``MAX_SCOPE_BLOCKS`` changed logical blocks → Enclosing Scopes (Scenario A)
  - > ``MAX_SCOPE_BLOCKS`` changed logical blocks → File Skeleton (Scenario B)
  - Tree-sitter unavailable / language unsupported → Adaptive Diff fallback

Output is always wrapped in XML tags so the LLM can distinguish code context
from the actual diff:

  ``<enclosing_scopes file="src/auth.py">...</enclosing_scopes>``
  ``<file_skeleton    file="src/module.py">...</file_skeleton>``
  ``<adaptive_diff    file="src/other.py">...</adaptive_diff>``

Tree-sitter is an *optional* dependency.  If ``tree-sitter-languages`` is not
installed the module automatically falls back to :func:`_build_adaptive_diff`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional, Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Tree-sitter Node protocol (structural subtyping — no runtime dependency)
# ---------------------------------------------------------------------------


@runtime_checkable
class _TSNode(Protocol):
    """Structural interface for tree-sitter Node objects."""

    @property
    def type(self) -> str: ...

    @property
    def start_point(self) -> tuple[int, int]: ...

    @property
    def end_point(self) -> tuple[int, int]: ...

    @property
    def children(self) -> list["_TSNode"]: ...

    @property
    def text(self) -> bytes: ...

# ---------------------------------------------------------------------------
# Tree-sitter availability probe (import happens once at module load)
# ---------------------------------------------------------------------------
_TS_AVAILABLE: bool = False
try:
    from tree_sitter_languages import get_parser  # type: ignore[import-untyped]

    _TS_AVAILABLE = True
except Exception:  # pragma: no cover — optional dependency
    pass

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Maximum number of distinct logical blocks that triggers Scenario A
#: (Enclosing Scopes).  When more blocks are detected Scenario B (File
#: Skeleton) is used instead.
MAX_SCOPE_BLOCKS: int = 3

#: Maximum number of lines in a single enclosing scope.  When a scope
#: exceeds this limit the strategy falls back to File Skeleton to avoid
#: sending large class bodies to the LLM.
MAX_SCOPE_LINES: int = 250

#: Number of surrounding lines kept by the Adaptive Diff fallback.
ADAPTIVE_CONTEXT_LINES: int = 10

# ---------------------------------------------------------------------------
# Extension → Tree-sitter language name mapping
# ---------------------------------------------------------------------------
_EXT_TO_LANG: dict[str, str] = {
    ".py":   "python",
    ".js":   "javascript",
    ".jsx":  "javascript",
    ".ts":   "typescript",
    ".tsx":  "tsx",
    ".cs":   "c_sharp",
    ".java": "java",
    ".sql":  "sql",
}

# ---------------------------------------------------------------------------
# AST node types per language that represent "enclosing scope" boundaries
# ---------------------------------------------------------------------------
_SCOPE_NODES: dict[str, frozenset[str]] = {
    "python": frozenset({
        "function_definition",
        "async_function_definition",
        "class_definition",
        "decorated_definition",
    }),
    "javascript": frozenset({
        "function_declaration",
        "function_expression",
        "arrow_function",
        "method_definition",
        "class_declaration",
        "class_expression",
    }),
    "tsx": frozenset({
        "function_declaration",
        "function_expression",
        "arrow_function",
        "method_definition",
        "class_declaration",
        "class_expression",
    }),
    "typescript": frozenset({
        "function_declaration",
        "function_expression",
        "arrow_function",
        "method_definition",
        "class_declaration",
        "class_expression",
        "interface_declaration",
    }),
    "c_sharp": frozenset({
        "method_declaration",
        "constructor_declaration",
        "destructor_declaration",
        "property_declaration",
        "class_declaration",
        "interface_declaration",
        "struct_declaration",
        "accessor_declaration",
    }),
    "java": frozenset({
        "method_declaration",
        "constructor_declaration",
        "class_declaration",
        "interface_declaration",
        "enum_declaration",
        "annotation_type_declaration",
    }),
    "sql": frozenset({
        "select_statement",
        "update_statement",
        "delete_statement",
        "insert_statement",
        "create_statement",
        "drop_statement",
        "alter_statement",
        "create_table_statement",
        "create_view_statement",
        "create_index_statement",
        "create_procedure_statement",
        "create_function_statement",
        "statement",
    }),
}

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ScopeInfo:
    """Represents a single extracted AST scope (function, method, class …).

    Attributes:
        start_line: 1-based line number of the first line of the scope.
        end_line:   1-based line number of the last line of the scope.
        node_type:  Tree-sitter node type string (e.g. ``"function_definition"``).
        name:       Identifier of the scope when detectable, empty string otherwise.
        source_lines: The actual source lines (1-based indexed from the file).
    """

    start_line: int
    end_line: int
    node_type: str
    name: str
    source_lines: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extension_from_path(file_path: str) -> str:
    """Return the lowercased file extension including the dot."""
    import os

    _, ext = os.path.splitext(file_path)
    return ext.lower()


def _lang_from_path(file_path: str) -> Optional[str]:
    """Resolve the Tree-sitter language name for *file_path*, or ``None``."""
    return _EXT_TO_LANG.get(_extension_from_path(file_path))


def _node_name(node: _TSNode) -> str:
    """Try to extract the identifier name from an AST node.

    Tree-sitter nodes expose their children via ``children``.  This helper
    walks the immediate children looking for ``identifier`` or ``name`` nodes.
    Returns an empty string when not found.
    """
    try:
        for child in node.children:
            if child.type in ("identifier", "name", "type_identifier"):
                return child.text.decode("utf-8", errors="replace")
    except Exception:
        pass
    return ""


def _find_enclosing_node(
    node: _TSNode,
    target_line: int,
    scope_types: frozenset[str],
) -> Optional[_TSNode]:
    """Recursively find the innermost AST node of a scope type that contains
    *target_line* (0-based).

    Args:
        node: Current Tree-sitter node to inspect.
        target_line: 0-based line number to locate.
        scope_types: Set of node type strings that qualify as scopes.

    Returns:
        The innermost qualifying node, or ``None`` when not found.
    """
    try:
        start = node.start_point[0]
        end = node.end_point[0]
    except Exception:
        return None

    if not (start <= target_line <= end):
        return None

    # Recurse into children first (innermost wins)
    best: Optional[_TSNode] = None
    try:
        for child in node.children:
            found = _find_enclosing_node(child, target_line, scope_types)
            if found is not None:
                best = found
    except Exception:
        pass

    if best is not None:
        return best

    if node.type in scope_types:
        return node

    return None


def _build_adaptive_diff(
    content: str,
    changed_lines: list[int],
    context_lines: int = ADAPTIVE_CONTEXT_LINES,
) -> str:
    """Return an adaptive context block without Tree-sitter.

    Extracts *context_lines* lines before and after each changed line,
    merging overlapping windows.  Lines are presented with their 1-based
    absolute line numbers.

    Args:
        content: Full source file content as a string.
        changed_lines: 1-based line numbers that were changed.
        context_lines: Number of surrounding lines to include per hunk.

    Returns:
        Formatted string with line numbers and source lines.
    """
    if not content or not changed_lines:
        return ""

    all_lines = content.splitlines()
    total = len(all_lines)

    # Build a set of all line indices (1-based) to include
    include: set[int] = set()
    for ln in changed_lines:
        lo = max(1, ln - context_lines)
        hi = min(total, ln + context_lines)
        include.update(range(lo, hi + 1))

    # Render sorted lines with numbers
    parts: list[str] = []
    prev: Optional[int] = None
    for ln in sorted(include):
        if prev is not None and ln > prev + 1:
            parts.append("  ...")
        parts.append(f"{ln:4d}: {all_lines[ln - 1]}")
        prev = ln

    return "\n".join(parts)


def _extract_enclosing_scopes_ts(
    content: str,
    changed_lines: list[int],
    lang: str,
) -> list[ScopeInfo]:
    """Use Tree-sitter to find the enclosing scope for each changed line.

    Deduplicates overlapping scopes (same start/end line pair is kept once).

    Args:
        content: Full source file content.
        changed_lines: 1-based line numbers that were changed.
        lang: Tree-sitter language name (e.g. ``"python"``).

    Returns:
        Deduplicated list of :class:`ScopeInfo` objects, sorted by start line.

    Raises:
        Exception: Propagates any Tree-sitter parsing failure to the caller
            so the caller can activate the fallback path.
    """
    parser = get_parser(lang)
    tree = parser.parse(content.encode("utf-8", errors="replace"))
    root: _TSNode = tree.root_node  # type: ignore[assignment]

    scope_types = _SCOPE_NODES.get(lang, frozenset())
    all_lines = content.splitlines()

    seen: set[tuple[int, int]] = set()
    scopes: list[ScopeInfo] = []

    for line_1based in changed_lines:
        node = _find_enclosing_node(root, line_1based - 1, scope_types)
        if node is None:
            continue

        start_1 = node.start_point[0] + 1
        end_1 = node.end_point[0] + 1
        key = (start_1, end_1)

        if key in seen:
            continue
        seen.add(key)

        source = all_lines[start_1 - 1: end_1]
        scopes.append(
            ScopeInfo(
                start_line=start_1,
                end_line=end_1,
                node_type=node.type,
                name=_node_name(node),
                source_lines=source,
            )
        )

    scopes.sort(key=lambda s: s.start_line)
    return scopes


def _format_scope_block(scope: ScopeInfo) -> str:
    """Render a :class:`ScopeInfo` with absolute 1-based line numbers."""
    lines: list[str] = []
    for offset, src_line in enumerate(scope.source_lines):
        abs_line = scope.start_line + offset
        lines.append(f"{abs_line:4d}: {src_line}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# File Skeleton helpers
# ---------------------------------------------------------------------------

#: Regex patterns used to identify structural lines in each language.
_SKELETON_PATTERNS: dict[str, list[str]] = {
    "python": [
        r"^\s*(?:import|from)\s+",           # imports
        r"^\s*(?:def|async\s+def|class)\s+", # definitions
        r"^\s*@\w",                           # decorators
    ],
    "javascript": [
        r"^\s*(?:import|export|require)\s*",
        r"^\s*(?:function|class|const|let|var)\s+\w+\s*(?:=\s*(?:function|\(|\()|\()",
        r"^\s*(?:async\s+function|export\s+(?:default\s+)?(?:function|class))",
    ],
    "typescript": [
        r"^\s*(?:import|export|require)\s*",
        r"^\s*(?:function|class|const|let|var|interface|type|enum|abstract)\s+\w+",
        r"^\s*(?:async\s+function|export\s+(?:default\s+)?(?:function|class|interface))",
    ],
    "tsx": [
        r"^\s*(?:import|export|require)\s*",
        r"^\s*(?:function|class|const|let|var|interface|type|enum|abstract)\s+\w+",
    ],
    "c_sharp": [
        r"^\s*(?:using)\s+",
        r"^\s*(?:namespace|public|private|protected|internal|static|sealed|abstract|partial|override|virtual)\s+",
        r"^\s*(?:\[[\w\.\(\)\"]+\])",        # attributes
    ],
    "java": [
        r"^\s*(?:import|package)\s+",
        r"^\s*(?:public|private|protected|static|final|abstract|synchronized|@)\s*",
    ],
    "sql": [],  # SQL skeleton falls back to individual statements
}

# Body-start patterns (lines after which we can insert "...")
_BODY_START: dict[str, list[str]] = {
    "python":     [r":\s*$"],
    "javascript": [r"\{\s*$"],
    "typescript": [r"\{\s*$"],
    "tsx":        [r"\{\s*$"],
    "c_sharp":    [r"\{\s*$"],
    "java":       [r"\{\s*$"],
    "sql":        [],
}


def _build_file_skeleton(
    content: str,
    changed_lines: list[int],
    lang: str,
) -> str:
    """Build a file skeleton: structural lines + changed blocks + ``...`` placeholders.

    The skeleton includes:
    - All structural/declaration lines (imports, signatures, class headers)
    - Full content of lines inside changed regions (± ADAPTIVE_CONTEXT_LINES)
    - ``...`` for bodies of unchanged functions/methods

    Args:
        content: Full source file content.
        changed_lines: 1-based line numbers that were changed.
        lang: Tree-sitter language name for pattern selection.

    Returns:
        Formatted skeleton string with 1-based line numbers.
    """
    all_lines = content.splitlines()
    total = len(all_lines)

    if not all_lines:
        return ""

    # Build set of "always include" lines (context around changes)
    always: set[int] = set()
    for ln in changed_lines:
        lo = max(1, ln - ADAPTIVE_CONTEXT_LINES)
        hi = min(total, ln + ADAPTIVE_CONTEXT_LINES)
        always.update(range(lo, hi + 1))

    # Structural patterns for this language
    struct_patterns = [re.compile(p) for p in _SKELETON_PATTERNS.get(lang, [])]

    result_lines: list[str] = []
    skipping = False
    prev_shown: Optional[int] = None

    for idx, src in enumerate(all_lines, start=1):
        is_structural = any(p.search(src) for p in struct_patterns)
        in_changed = idx in always

        if in_changed or is_structural:
            if skipping and prev_shown is not None:
                result_lines.append("      ...")
            skipping = False
            result_lines.append(f"{idx:4d}: {src}")
            prev_shown = idx
        else:
            skipping = True

    if skipping:
        result_lines.append("      ...")

    return "\n".join(result_lines)


# ---------------------------------------------------------------------------
# SQL-specific extraction
# ---------------------------------------------------------------------------


def _extract_sql_statement(
    content: str,
    changed_lines: list[int],
    lang: str,
) -> list[ScopeInfo]:
    """Find the SQL statement(s) covering each changed line via Tree-sitter.

    Args:
        content: Full SQL file content.
        changed_lines: 1-based line numbers that were changed.
        lang: Always ``"sql"`` for this path.

    Returns:
        List of :class:`ScopeInfo` for each affected SQL statement.

    Raises:
        Exception: On Tree-sitter parse failure (caller activates fallback).
    """
    return _extract_enclosing_scopes_ts(content, changed_lines, lang)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class ContextExtractor:
    """Selects and builds the optimal context payload for a changed file.

    Usage::

        extractor = ContextExtractor()
        strategy, xml_block = extractor.extract(
            file_path="src/auth.py",
            new_content=open("src/auth.py").read(),
            changed_lines=[42, 43, 57],
        )

    The returned *xml_block* is ready to be embedded in the LLM user message.

    Attributes:
        max_scope_blocks: Threshold for Scenario A vs Scenario B selection.
        max_scope_lines: Maximum lines per scope block before falling back to
            file_skeleton (avoids sending entire large classes to the LLM).
        adaptive_context_lines: Lines of context for the fallback strategy.
    """

    def __init__(
        self,
        max_scope_blocks: int = MAX_SCOPE_BLOCKS,
        max_scope_lines: int = MAX_SCOPE_LINES,
        adaptive_context_lines: int = ADAPTIVE_CONTEXT_LINES,
    ) -> None:
        self.max_scope_blocks = max_scope_blocks
        self.max_scope_lines = max_scope_lines
        self.adaptive_context_lines = adaptive_context_lines

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def extract(
        self,
        file_path: str,
        new_content: str,
        changed_lines: list[int],
    ) -> tuple[str, str]:
        """Select and build the context block for *file_path*.

        Decision logic:

        1. If Tree-sitter is unavailable → ``adaptive_diff``
        2. If the language is not supported → ``adaptive_diff``
        3. If the language is SQL → ``sql_statement`` (falls back on failure)
        4. If ≤ ``max_scope_blocks`` distinct scopes found → ``enclosing_scopes``
        5. Otherwise → ``file_skeleton``

        Args:
            file_path: Path (or name) of the file being reviewed.
            new_content: Complete text of the file's new version.
            changed_lines: Sorted list of 1-based line numbers that were changed.

        Returns:
            A tuple ``(strategy, xml_block)`` where *strategy* is one of
            ``"enclosing_scopes"``, ``"file_skeleton"``, ``"sql_statement"``,
            or ``"adaptive_diff"``, and *xml_block* is the XML-tagged string
            ready for the LLM payload.
        """
        if not changed_lines:
            return "adaptive_diff", ""

        lang = _lang_from_path(file_path)

        # ------------------------------------------------------------------
        # No Tree-sitter or unsupported language → fallback immediately
        # ------------------------------------------------------------------
        if not _TS_AVAILABLE or lang is None:
            return self._adaptive(file_path, new_content, changed_lines)

        # ------------------------------------------------------------------
        # SQL path
        # ------------------------------------------------------------------
        if lang == "sql":
            try:
                scopes = _extract_sql_statement(new_content, changed_lines, lang)
                if scopes:
                    return self._format_enclosing_scopes(
                        file_path, scopes, strategy="sql_statement"
                    )
            except Exception:
                pass
            return self._adaptive(file_path, new_content, changed_lines)

        # ------------------------------------------------------------------
        # Generic AST path
        # ------------------------------------------------------------------
        try:
            scopes = _extract_enclosing_scopes_ts(new_content, changed_lines, lang)
        except Exception:
            return self._adaptive(file_path, new_content, changed_lines)

        if not scopes:
            # No enclosing scope found (top-level code) → adaptive diff
            return self._adaptive(file_path, new_content, changed_lines)

        if len(scopes) <= self.max_scope_blocks:
            # Check if any single scope is too large — if so, fall back to
            # file_skeleton to avoid sending a 900-line class body to the LLM.
            oversized = any(
                (s.end_line - s.start_line) > self.max_scope_lines
                for s in scopes
            )
            if not oversized:
                # Scenario A: Enclosing Scopes
                return self._format_enclosing_scopes(file_path, scopes)

        # Scenario B: File Skeleton
        try:
            skeleton = _build_file_skeleton(new_content, changed_lines, lang)
        except Exception:
            return self._adaptive(file_path, new_content, changed_lines)

        xml = (
            f'<file_skeleton file="{file_path}">\n'
            f"{skeleton}\n"
            f"</file_skeleton>"
        )
        return "file_skeleton", xml

    # ------------------------------------------------------------------
    # Internal formatters
    # ------------------------------------------------------------------

    def _adaptive(
        self,
        file_path: str,
        content: str,
        changed_lines: list[int],
    ) -> tuple[str, str]:
        """Build an ``adaptive_diff`` fallback block."""
        body = _build_adaptive_diff(content, changed_lines, self.adaptive_context_lines)
        if not body:
            return "adaptive_diff", ""
        xml = (
            f'<adaptive_diff file="{file_path}">\n'
            f"{body}\n"
            f"</adaptive_diff>"
        )
        return "adaptive_diff", xml

    def _format_enclosing_scopes(
        self,
        file_path: str,
        scopes: list[ScopeInfo],
        strategy: str = "enclosing_scopes",
    ) -> tuple[str, str]:
        """Render a list of :class:`ScopeInfo` into an XML block."""
        parts: list[str] = []
        for scope in scopes:
            header = f"<!-- {scope.node_type}"
            if scope.name:
                header += f": {scope.name}"
            header += f" (lines {scope.start_line}–{scope.end_line}) -->"
            parts.append(header)
            parts.append(_format_scope_block(scope))

        body = "\n".join(parts)
        tag = "enclosing_scopes" if strategy == "enclosing_scopes" else strategy
        xml = (
            f'<{tag} file="{file_path}">\n'
            f"{body}\n"
            f"</{tag}>"
        )
        return strategy, xml

    # ------------------------------------------------------------------
    # Utility: extract changed lines from a diff hunk for one file
    # ------------------------------------------------------------------

    @staticmethod
    def changed_lines_from_diff(file_diff: str) -> list[int]:
        """Parse a unified diff section and return the 1-based new-file line
        numbers for all added lines.

        Args:
            file_diff: Unified diff text for a single file (may include the
                ``diff --git`` header and one or more ``@@`` hunks).

        Returns:
            Sorted list of 1-based line numbers that correspond to ``+`` lines
            in the new version of the file.
        """
        changed: list[int] = []
        current_new_line: int = 0

        hunk_re = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")

        for line in file_diff.splitlines():
            m = hunk_re.match(line)
            if m:
                current_new_line = int(m.group(1))
                continue

            if line.startswith("+++") or line.startswith("---"):
                continue

            if line.startswith("+"):
                changed.append(current_new_line)
                current_new_line += 1
            elif line.startswith("-"):
                pass  # deleted lines do not advance the new-file counter
            else:
                # Context line (space prefix or no prefix)
                if current_new_line > 0:
                    current_new_line += 1

        return sorted(set(changed))
