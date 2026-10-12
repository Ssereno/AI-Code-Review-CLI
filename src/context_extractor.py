# License: Apache-2.0
# Copyright 2026 Sérgio Sereno

"""
Context Extractor Module - AI Code Review
==========================================
Provides AST-based context extraction for diff payloads sent to the LLM.

Strategy selection per file:
  - AST scopes within the configured limits → Enclosing Scopes
  - Tree-sitter unavailable, unsupported language, parse failure, or scopes
    outside the configured limits → Adaptive Diff fallback

Output is always wrapped in XML tags so the LLM can distinguish code context
from the actual diff:

  ``<enclosing_scopes file="src/auth.py">...</enclosing_scopes>``
  ``<adaptive_diff    file="src/other.py">...</adaptive_diff>``

Tree-sitter is installed as a core dependency. The availability probe remains
defensive: reduced or broken environments fall back to
:func:`_build_adaptive_diff` instead of failing the review.
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

#: Maximum number of distinct logical blocks included as Enclosing Scopes.
#: When more blocks are detected the extractor uses Adaptive Diff instead.
MAX_SCOPE_BLOCKS: int = 8

#: Maximum number of lines in a single enclosing scope.  When a scope
#: exceeds this limit the strategy falls back to Adaptive Diff to avoid
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


def _non_whitespace_changed_lines(content: str, changed_lines: list[int]) -> list[int]:
    """Return changed lines that contain meaningful code in the new file.

    Whitespace-only additions must not influence AST scope selection. For
    example, a blank line immediately after a method belongs to its containing
    class and would otherwise cause the entire class to be selected.
    """
    all_lines = content.splitlines()
    return [
        line_number
        for line_number in changed_lines
        if 1 <= line_number <= len(all_lines)
        and all_lines[line_number - 1].strip()
    ]


def _remove_redundant_ancestor_scopes(
    scopes: list[ScopeInfo],
    changed_lines: list[int],
) -> list[ScopeInfo]:
    """Drop ancestor scopes whose changed lines are covered by child scopes.

    An ancestor is retained when it contains at least one meaningful changed
    line outside every selected descendant. This avoids duplicate class and
    method bodies without losing class-level changes.
    """
    retained: list[ScopeInfo] = []

    for scope in scopes:
        descendants = [
            candidate
            for candidate in scopes
            if candidate is not scope
            and scope.start_line <= candidate.start_line
            and candidate.end_line <= scope.end_line
            and (
                scope.start_line < candidate.start_line
                or candidate.end_line < scope.end_line
            )
        ]
        scope_changes = [
            line_number
            for line_number in changed_lines
            if scope.start_line <= line_number <= scope.end_line
        ]
        changes_covered_by_descendants = (
            bool(scope_changes)
            and bool(descendants)
            and all(
                any(
                    child.start_line <= line_number <= child.end_line
                    for child in descendants
                )
                for line_number in scope_changes
            )
        )

        if not changes_covered_by_descendants:
            retained.append(scope)

    return retained


def _format_scope_block(scope: ScopeInfo) -> str:
    """Render a :class:`ScopeInfo` with absolute 1-based line numbers."""
    lines: list[str] = []
    for offset, src_line in enumerate(scope.source_lines):
        abs_line = scope.start_line + offset
        lines.append(f"{abs_line:4d}: {src_line}")
    return "\n".join(lines)


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
        max_scope_blocks: Maximum number of AST scopes before falling back to
            adaptive_diff.
        max_scope_lines: Maximum lines per scope block before falling back to
            adaptive_diff (avoids sending entire large classes to the LLM).
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
        3. If the extracted scopes fit the configured limits → ``enclosing_scopes``
        4. Otherwise → ``adaptive_diff``

        Args:
            file_path: Path (or name) of the file being reviewed.
            new_content: Complete text of the file's new version.
            changed_lines: Sorted list of 1-based line numbers that were changed.

        Returns:
            A tuple ``(strategy, xml_block)`` where *strategy* is one of
            ``"enclosing_scopes"`` or ``"adaptive_diff"``, and *xml_block*
            is the XML-tagged string ready for the LLM payload.
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
        # Generic AST path
        # ------------------------------------------------------------------
        ast_changed_lines = _non_whitespace_changed_lines(new_content, changed_lines)
        if not ast_changed_lines:
            return self._adaptive(file_path, new_content, changed_lines)

        try:
            scopes = _extract_enclosing_scopes_ts(
                new_content,
                ast_changed_lines,
                lang,
            )
        except Exception:
            return self._adaptive(file_path, new_content, changed_lines)

        if not scopes:
            # No enclosing scope found (top-level code) → adaptive diff
            return self._adaptive(file_path, new_content, changed_lines)

        scopes = _remove_redundant_ancestor_scopes(scopes, ast_changed_lines)

        too_many_scopes = len(scopes) > self.max_scope_blocks
        oversized_scope = any(
            (s.end_line - s.start_line + 1) > self.max_scope_lines
            for s in scopes
        )
        if too_many_scopes or oversized_scope:
            return self._adaptive(file_path, new_content, changed_lines)

        return self._format_enclosing_scopes(file_path, scopes)

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
        xml = (
            f'<enclosing_scopes file="{file_path}">\n'
            f"{body}\n"
            f"</enclosing_scopes>"
        )
        return "enclosing_scopes", xml

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
