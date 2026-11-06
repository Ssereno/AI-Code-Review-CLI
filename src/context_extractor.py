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

  ``<enclosing_scopes   file="src/auth.py">...</enclosing_scopes>``
  ``<adaptive_diff      file="src/other.py">...</adaptive_diff>``
  ``<referenced_symbols file="src/auth.py">...</referenced_symbols>``

Tree-sitter is installed as a core dependency. The availability probe remains
defensive: reduced or broken environments fall back to
:func:`_build_adaptive_diff` and regex symbol resolution instead of failing
the review.
"""

from __future__ import annotations

import difflib
import os
import posixpath
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

#: Maximum number of cross-file symbol declarations included per file.
MAX_REFERENCED_SYMBOLS: int = 10

#: Maximum lines extracted for a single type/class symbol declaration.
MAX_SYMBOL_LINES: int = 25

#: Maximum lines extracted for a single callable (function/method) signature.
MAX_CALLABLE_SIGNATURE_LINES: int = 12

#: Maximum total lines inside a single ``<referenced_symbols>`` block.
MAX_REFERENCED_BLOCK_LINES: int = 120

#: Authoritative XML start/end tags for AST context blocks embedded in diffs.
XML_CONTEXT_START_TAGS: tuple[str, ...] = (
    "<enclosing_scopes",
    "<adaptive_diff",
    "<referenced_symbols",
)
XML_CONTEXT_END_TAGS: tuple[str, ...] = (
    "</enclosing_scopes>",
    "</adaptive_diff>",
    "</referenced_symbols>",
)

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


@dataclass
class SymbolInfo:
    """Represents a compact cross-file symbol signature or type declaration.

    Attributes:
        name:           Identifier of the symbol (e.g. ``"SaveOrder"``, ``"OrderDto"``).
        node_type:      AST node type or declaration kind (e.g. ``"interface_declaration"``).
        file_path:      Path of the file where the symbol is defined.
        start_line:     1-based start line of the symbol declaration.
        end_line:       1-based end line of the extracted declaration/signature.
        enclosing_name: Name of the containing class/interface when applicable.
        numbered_lines: List of ``(1_based_line_number, line_text)`` pairs.
    """

    name: str
    node_type: str
    file_path: str
    start_line: int
    end_line: int
    enclosing_name: str = ""
    numbered_lines: list[tuple[int, str]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Cross-file symbol AST node sets & stopword filters
# ---------------------------------------------------------------------------

_CALLABLE_DEF_NODES: frozenset[str] = frozenset({
    "function_definition",
    "async_function_definition",
    "function_declaration",
    "method_definition",
    "method_signature",
    "abstract_method_signature",
    "method_declaration",
    "constructor_declaration",
    "property_declaration",
})

_CONTRACT_DEF_NODES: frozenset[str] = frozenset({
    "interface_declaration",
    "type_alias_declaration",
    "enum_declaration",
    "struct_declaration",
    "record_declaration",
    "delegate_declaration",
    "annotation_type_declaration",
})

_CLASS_DEF_NODES: frozenset[str] = frozenset({
    "class_definition",
    "class_declaration",
})

_BODY_NODE_TYPES: frozenset[str] = frozenset({
    "block",
    "statement_block",
    "class_body",
    "interface_body",
    "declaration_list",
    "object_type",
    "enum_body",
    "enum_member_declaration_list",
    "accessor_list",
    "compound_statement",
})

_SYMBOL_STOPWORDS: frozenset[str] = frozenset({
    # Keywords & control flow
    "def", "class", "return", "async", "await", "import", "from", "export",
    "default", "const", "let", "var", "function", "interface", "type", "enum",
    "struct", "record", "public", "private", "protected", "internal", "static",
    "readonly", "virtual", "override", "abstract", "sealed", "partial", "using",
    "namespace", "package", "extends", "implements", "new", "this", "self",
    "cls", "super", "base", "null", "None", "none", "true", "false", "True",
    "False", "void", "try", "catch", "except", "finally", "raise", "throw",
    "throws", "elif", "else", "for", "while", "switch", "case", "break",
    "continue", "yield", "with", "lambda", "pass", "del", "not", "and", "or",
    "out", "ref", "params", "where", "select", "update", "insert", "delete",
    "get", "set", "init", "value", "values", "key", "keys", "item", "items",
    "data", "result", "results", "args", "kwargs", "err", "error", "errors",
    "msg", "message", "req", "res", "ctx", "config", "options", "name", "info",
    # Built-in primitive & collection types / common framework methods
    "int", "str", "float", "bool", "bytes", "list", "dict", "tuple", "object",
    "string", "number", "boolean", "symbol", "bigint", "any", "unknown", "never",
    "Object", "String", "Integer", "Long", "Double", "Boolean", "Void",
    "Promise", "Task", "ValueTask", "CancellationToken", "IEnumerable",
    "IList", "IDictionary", "ICollection", "IReadOnlyList", "IReadOnlyCollection",
    "List", "Dictionary", "HashSet", "Optional", "Iterable", "Callable",
    "Sequence", "Mapping", "Map", "Set", "ArrayList", "HashMap", "Stream",
    "CompletableFuture", "Exception", "RuntimeError", "ValueError", "TypeError",
    "Error", "Console", "Math", "DateTime", "DateTimeOffset", "TimeSpan", "Guid",
    "UUID", "ToString", "toString", "Equals", "equals", "GetHashCode", "hashCode",
    "Dispose", "DisposeAsync", "main", "Main", "__init__", "__str__", "__repr__",
    "__enter__", "__exit__", "len", "print", "range", "enumerate", "isinstance",
    "getattr", "setattr", "hasattr", "append", "extend", "format", "join",
    "split", "strip", "replace", "lower", "upper", "startswith", "endswith",
    "log", "warn", "warning", "debug", "trace", "add", "remove", "clear",
})

_IDENT_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]{2,})\b")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extension_from_path(file_path: str) -> str:
    """Return the lowercased file extension including the dot."""
    _, ext = os.path.splitext(file_path)
    return ext.lower()


def _lang_from_path(file_path: str) -> Optional[str]:
    """Resolve the Tree-sitter language name for *file_path*, or ``None``."""
    return _EXT_TO_LANG.get(_extension_from_path(file_path))


def _node_name(node: _TSNode) -> str:
    """Try to extract the identifier name from an AST node.

    Prefers ``child_by_field_name("name")`` when available (handles typed
    C#/Java return types before method names), falling back to walking
    immediate children for ``identifier``, ``name``, ``type_identifier``, or
    ``property_identifier`` nodes.
    """
    try:
        name_fn = getattr(node, "child_by_field_name", None)
        if callable(name_fn):
            named_child = name_fn("name")
            if named_child is not None:
                return named_child.text.decode("utf-8", errors="replace")
    except Exception:
        pass

    try:
        for child in node.children:
            if child.type in (
                "identifier",
                "name",
                "type_identifier",
                "property_identifier",
            ):
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


def _format_symbol_block(symbol: SymbolInfo) -> str:
    """Render a :class:`SymbolInfo` with 1-based line numbers and gap markers."""
    parts: list[str] = []
    prev_ln: Optional[int] = None
    for ln, src_line in symbol.numbered_lines:
        if prev_ln is not None and ln > prev_ln + 1:
            parts.append("  ...")
        parts.append(f"{ln:4d}: {src_line}")
        prev_ln = ln
    return "\n".join(parts)


def _norm_repo_path(file_path: str) -> str:
    """Normalize a repository file path for case-insensitive cross-file matching."""
    return file_path.replace("\\", "/").lstrip("/").lower()


def _same_directory_or_stem(path_a: str, path_b: str) -> bool:
    """Return True when two paths share a directory or normalized filename stem."""
    norm_a = _norm_repo_path(path_a)
    norm_b = _norm_repo_path(path_b)
    dir_a, base_a = posixpath.split(norm_a)
    dir_b, base_b = posixpath.split(norm_b)
    if dir_a and dir_a == dir_b:
        return True
    stem_a = base_a.split(".")[0].lstrip("i") if len(base_a) > 2 else base_a
    stem_b = base_b.split(".")[0].lstrip("i") if len(base_b) > 2 else base_b
    return bool(stem_a and stem_a == stem_b)


def _strip_inline_comment(line: str) -> str:
    """Strip obvious trailing line comments before scanning identifiers."""
    for marker in ("//", "#"):
        idx = line.find(marker)
        if idx >= 0:
            line = line[:idx]
    return line


def _extract_identifiers_from_lines(lines: list[str]) -> set[str]:
    """Extract non-trivial identifiers from source lines."""
    found: set[str] = set()
    for line in lines:
        cleaned = _strip_inline_comment(line)
        for match in _IDENT_RE.finditer(cleaned):
            ident = match.group(1)
            if ident not in _SYMBOL_STOPWORDS:
                found.add(ident)
    return found


def _find_body_node(node: _TSNode) -> Optional[_TSNode]:
    """Locate the body child node of a definition AST node, if present."""
    try:
        body_fn = getattr(node, "child_by_field_name", None)
        if callable(body_fn):
            body = body_fn("body")
            if body is not None:
                return body
    except Exception:
        pass

    try:
        for child in node.children:
            if child.type in _BODY_NODE_TYPES:
                return child
    except Exception:
        pass
    return None


def _extract_callable_signature_lines(
    node: _TSNode,
    start_0: int,
    all_lines: list[str],
    max_lines: int = MAX_CALLABLE_SIGNATURE_LINES,
) -> list[tuple[int, str]]:
    """Extract only the compact signature lines of a callable AST node."""
    total = len(all_lines)
    if total == 0 or start_0 >= total:
        return []

    end_0 = min(node.end_point[0], total - 1)

    # Keep short C# property declarations intact (e.g. `public int Id { get; set; }`)
    if node.type == "property_declaration" and (end_0 - start_0) <= 2:
        return [(ln + 1, all_lines[ln]) for ln in range(start_0, end_0 + 1)]

    body = _find_body_node(node)
    if body is None:
        stop_0 = min(end_0, start_0 + max_lines - 1)
        return [(ln + 1, all_lines[ln]) for ln in range(start_0, stop_0 + 1)]

    body_start_0, body_col = body.start_point
    body_start_0 = min(max(body_start_0, start_0), total - 1)
    prefix = all_lines[body_start_0][:body_col].strip()

    numbered: list[tuple[int, str]] = []
    if not prefix:
        sig_end_0 = max(start_0, body_start_0 - 1)
        stop_0 = min(sig_end_0, start_0 + max_lines - 1)
        for ln in range(start_0, stop_0 + 1):
            numbered.append((ln + 1, all_lines[ln]))
    else:
        stop_0 = min(body_start_0, start_0 + max_lines - 1)
        for ln in range(start_0, stop_0):
            numbered.append((ln + 1, all_lines[ln]))
        if stop_0 == body_start_0:
            trimmed = all_lines[body_start_0][:body_col].rstrip()
            numbered.append((body_start_0 + 1, trimmed or all_lines[body_start_0]))

    return numbered


def _extract_class_symbol_lines(
    node: _TSNode,
    start_0: int,
    all_lines: list[str],
    max_lines: int = MAX_SYMBOL_LINES,
) -> list[tuple[int, str]]:
    """Extract either the full small class or a compact member-signature skeleton."""
    total = len(all_lines)
    if total == 0 or start_0 >= total:
        return []

    end_0 = min(node.end_point[0], total - 1)
    if (end_0 - start_0 + 1) <= max_lines:
        return [(ln + 1, all_lines[ln]) for ln in range(start_0, end_0 + 1)]

    # Large class: extract header + compact signatures of direct member callables
    numbered: list[tuple[int, str]] = _extract_callable_signature_lines(
        node, start_0, all_lines, max_lines=5
    )
    seen_lines = {ln for ln, _ in numbered}

    body = _find_body_node(node)
    if body is not None:
        for child in body.children:
            member_start_0 = child.start_point[0]
            target = child
            if child.type in ("decorated_definition", "export_statement"):
                for sub in child.children:
                    if sub.type in _CALLABLE_DEF_NODES:
                        target = sub
                        break
            if target.type in _CALLABLE_DEF_NODES:
                sig = _extract_callable_signature_lines(
                    target, member_start_0, all_lines, max_lines=4
                )
                for ln, text in sig:
                    if ln not in seen_lines:
                        numbered.append((ln, text))
                        seen_lines.add(ln)
                        if len(numbered) >= max_lines:
                            break
            if len(numbered) >= max_lines:
                break

    numbered.sort(key=lambda pair: pair[0])
    return numbered[:max_lines]


def _extract_symbols_ts(
    file_path: str,
    content: str,
    lang: str,
) -> list[SymbolInfo]:
    """Use Tree-sitter to extract compact symbol declarations from *content*."""
    parser = get_parser(lang)
    tree = parser.parse(content.encode("utf-8", errors="replace"))
    root: _TSNode = tree.root_node  # type: ignore[assignment]
    all_lines = content.splitlines()
    if not all_lines:
        return []

    symbols: list[SymbolInfo] = []
    seen_keys: set[tuple[str, int, str]] = set()

    def _walk(
        node: _TSNode,
        enclosing_name: str = "",
        wrapper_start_0: Optional[int] = None,
    ) -> None:
        ntype = node.type

        # Propagate decorator / export line start to the wrapped declaration
        if ntype in ("decorated_definition", "export_statement"):
            eff_start = wrapper_start_0 if wrapper_start_0 is not None else node.start_point[0]
            for child in node.children:
                _walk(child, enclosing_name=enclosing_name, wrapper_start_0=eff_start)
            return

        if ntype in ("lexical_declaration", "variable_declaration"):
            eff_start = wrapper_start_0 if wrapper_start_0 is not None else node.start_point[0]
            for child in node.children:
                _walk(child, enclosing_name=enclosing_name, wrapper_start_0=eff_start)
            return

        start_0 = wrapper_start_0 if wrapper_start_0 is not None else node.start_point[0]
        end_0 = min(node.end_point[0], len(all_lines) - 1)

        # JS/TS arrow function or function expression assigned to a variable
        if ntype == "variable_declarator":
            fn_child = next(
                (
                    c for c in node.children
                    if c.type in ("arrow_function", "function_expression")
                ),
                None,
            )
            name = _node_name(node)
            if fn_child is not None and name:
                sig_lines = _extract_callable_signature_lines(
                    fn_child, start_0, all_lines
                )
                if sig_lines:
                    key = (name, sig_lines[0][0], fn_child.type)
                    if key not in seen_keys:
                        seen_keys.add(key)
                        symbols.append(
                            SymbolInfo(
                                name=name,
                                node_type=fn_child.type,
                                file_path=file_path,
                                start_line=sig_lines[0][0],
                                end_line=sig_lines[-1][0],
                                enclosing_name=enclosing_name,
                                numbered_lines=sig_lines,
                            )
                        )
            return

        if ntype in _CONTRACT_DEF_NODES:
            name = _node_name(node)
            if name:
                stop_0 = min(end_0, start_0 + MAX_SYMBOL_LINES - 1)
                num_lines = [(ln + 1, all_lines[ln]) for ln in range(start_0, stop_0 + 1)]
                if num_lines:
                    key = (name, num_lines[0][0], ntype)
                    if key not in seen_keys:
                        seen_keys.add(key)
                        symbols.append(
                            SymbolInfo(
                                name=name,
                                node_type=ntype,
                                file_path=file_path,
                                start_line=num_lines[0][0],
                                end_line=num_lines[-1][0],
                                enclosing_name=enclosing_name,
                                numbered_lines=num_lines,
                            )
                        )
            # Also index interface method signatures inside the interface body
            next_enclosing = name or enclosing_name
            for child in node.children:
                _walk(child, enclosing_name=next_enclosing)
            return

        if ntype in _CLASS_DEF_NODES:
            name = _node_name(node)
            if name:
                num_lines = _extract_class_symbol_lines(node, start_0, all_lines)
                if num_lines:
                    key = (name, num_lines[0][0], ntype)
                    if key not in seen_keys:
                        seen_keys.add(key)
                        symbols.append(
                            SymbolInfo(
                                name=name,
                                node_type=ntype,
                                file_path=file_path,
                                start_line=num_lines[0][0],
                                end_line=num_lines[-1][0],
                                enclosing_name=enclosing_name,
                                numbered_lines=num_lines,
                            )
                        )
            next_enclosing = name or enclosing_name
            for child in node.children:
                _walk(child, enclosing_name=next_enclosing)
            return

        if ntype in _CALLABLE_DEF_NODES:
            # Skip nested local helper functions inside another function
            name = _node_name(node)
            if name:
                sig_lines = _extract_callable_signature_lines(node, start_0, all_lines)
                if sig_lines:
                    key = (name, sig_lines[0][0], ntype)
                    if key not in seen_keys:
                        seen_keys.add(key)
                        symbols.append(
                            SymbolInfo(
                                name=name,
                                node_type=ntype,
                                file_path=file_path,
                                start_line=sig_lines[0][0],
                                end_line=sig_lines[-1][0],
                                enclosing_name=enclosing_name,
                                numbered_lines=sig_lines,
                            )
                        )
            return

        for child in node.children:
            _walk(child, enclosing_name=enclosing_name)

    _walk(root)
    return symbols


_REGEX_TYPE_DECL = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:public\s+|internal\s+|protected\s+|abstract\s+|sealed\s+|partial\s+)*"
    r"(class|interface|struct|enum|record|type)\s+([A-Za-z_][A-Za-z0-9_]*)"
)
_REGEX_FUNC_DECL = re.compile(
    r"^\s*(?:export\s+)?(?:async\s+)?(?:def|function|func|fn)\s+([A-Za-z_][A-Za-z0-9_]*)\s*[\(<]"
)
_REGEX_TYPED_METHOD_DECL = re.compile(
    r"^\s*(?:public|protected|internal)\s+(?:static\s+|async\s+|virtual\s+|override\s+|abstract\s+)*"
    r"[\w<>\[\]?,.\s]+\s+([A-Za-z_][A-Za-z0-9_]*)\s*\("
)


def _extract_symbols_regex(file_path: str, content: str) -> list[SymbolInfo]:
    """Regex-based symbol signature extractor fallback when AST is unavailable."""
    all_lines = content.splitlines()
    total = len(all_lines)
    symbols: list[SymbolInfo] = []
    current_enclosing = ""

    for idx, line in enumerate(all_lines):
        m_type = _REGEX_TYPE_DECL.match(line)
        if m_type:
            kind_word, name = m_type.group(1), m_type.group(2)
            current_enclosing = name
            stop = min(total - 1, idx + MAX_SYMBOL_LINES - 1)
            # Capture up to matching closing brace if compact
            brace_depth = 0
            saw_brace = False
            end_idx = idx
            for j in range(idx, stop + 1):
                brace_depth += all_lines[j].count("{") - all_lines[j].count("}")
                if "{" in all_lines[j]:
                    saw_brace = True
                end_idx = j
                if saw_brace and brace_depth <= 0:
                    break
                if kind_word == "type" and ";" in all_lines[j]:
                    break
            num_lines = [(j + 1, all_lines[j]) for j in range(idx, end_idx + 1)]
            symbols.append(
                SymbolInfo(
                    name=name,
                    node_type=f"{kind_word}_declaration",
                    file_path=file_path,
                    start_line=idx + 1,
                    end_line=end_idx + 1,
                    numbered_lines=num_lines,
                )
            )
            continue

        m_fn = _REGEX_FUNC_DECL.match(line) or _REGEX_TYPED_METHOD_DECL.match(line)
        if m_fn:
            name = m_fn.group(1)
            if name in _SYMBOL_STOPWORDS:
                continue
            sig_lines: list[tuple[int, str]] = []
            for j in range(idx, min(total, idx + MAX_CALLABLE_SIGNATURE_LINES)):
                raw = all_lines[j]
                if "{" in raw:
                    trimmed = raw[:raw.index("{")].rstrip()
                    sig_lines.append((j + 1, trimmed or raw))
                    break
                sig_lines.append((j + 1, raw))
                if raw.rstrip().endswith((":", ";", ")")):
                    break
            if sig_lines:
                symbols.append(
                    SymbolInfo(
                        name=name,
                        node_type="function_declaration",
                        file_path=file_path,
                        start_line=sig_lines[0][0],
                        end_line=sig_lines[-1][0],
                        enclosing_name=current_enclosing,
                        numbered_lines=sig_lines,
                    )
                )

    return symbols


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
        max_referenced_symbols: int = MAX_REFERENCED_SYMBOLS,
    ) -> None:
        self.max_scope_blocks = max_scope_blocks
        self.max_scope_lines = max_scope_lines
        self.adaptive_context_lines = adaptive_context_lines
        self.max_referenced_symbols = max_referenced_symbols
        self._symbol_cache: dict[tuple[str, int], list[SymbolInfo]] = {}

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
    # Cross-file deterministic symbol resolution (Option 4A)
    # ------------------------------------------------------------------

    def extract_symbol_definitions(
        self,
        file_path: str,
        content: str,
    ) -> list[SymbolInfo]:
        """Extract compact symbol signatures and declarations from *content*.

        Uses Tree-sitter when available for the file's language, falling back
        to a lightweight regex scanner otherwise. Results are memoized per
        ``(file_path, hash(content))`` for the lifetime of this extractor.
        """
        if not content or not content.strip():
            return []

        cache_key = (_norm_repo_path(file_path), hash(content))
        cached = self._symbol_cache.get(cache_key)
        if cached is not None:
            return cached

        lang = _lang_from_path(file_path)
        symbols: list[SymbolInfo] = []
        if _TS_AVAILABLE and lang is not None and lang != "sql":
            try:
                symbols = _extract_symbols_ts(file_path, content, lang)
            except Exception:
                symbols = _extract_symbols_regex(file_path, content)
        else:
            symbols = _extract_symbols_regex(file_path, content)

        self._symbol_cache[cache_key] = symbols
        return symbols

    def _collect_enclosing_header_lines(
        self,
        file_path: str,
        new_content: str,
        changed_lines: list[int],
    ) -> list[str]:
        """Collect signature/header lines of scopes enclosing *changed_lines*."""
        all_lines = new_content.splitlines()
        if not all_lines:
            return []

        lang = _lang_from_path(file_path)
        ast_lines = _non_whitespace_changed_lines(new_content, changed_lines)
        if _TS_AVAILABLE and lang is not None and ast_lines:
            try:
                scopes = _extract_enclosing_scopes_ts(new_content, ast_lines, lang)
                header_lines: list[str] = []
                for sc in scopes:
                    header_lines.extend(sc.source_lines[:4])
                return header_lines
            except Exception:
                pass

        # Fallback: inspect up to 3 lines preceding each changed line
        fallback_indices: set[int] = set()
        for ln in changed_lines:
            for prev in range(max(1, ln - 3), min(len(all_lines), ln) + 1):
                fallback_indices.add(prev)
        return [all_lines[i - 1] for i in sorted(fallback_indices)]

    def extract_referenced_symbols(
        self,
        file_path: str,
        new_content: str,
        changed_lines: list[int],
        candidate_files: dict[str, str],
    ) -> str:
        """Resolve cross-file symbol declarations referenced by *changed_lines*.

        Inspects identifiers used on *changed_lines* (and enclosing method
        signatures) of *file_path*, matches them deterministically against
        symbol declarations extracted from *candidate_files* (other files in
        the PR/changeset or resolved imports), and formats compact signatures
        into ``<referenced_symbols file="...">...</referenced_symbols>``.

        Args:
            file_path: Path of the changed file being reviewed.
            new_content: Full new-version content of *file_path*.
            changed_lines: 1-based line numbers added/modified in *file_path*.
            candidate_files: Mapping ``{other_file_path: source_content}``.

        Returns:
            XML string ``<referenced_symbols file="...">...</referenced_symbols>``
            when cross-file symbols are resolved, or ``""`` when none match.
        """
        if (
            self.max_referenced_symbols <= 0
            or not changed_lines
            or not new_content
            or not candidate_files
        ):
            return ""

        norm_self = _norm_repo_path(file_path)
        other_files = {
            cand_path: cand_content
            for cand_path, cand_content in candidate_files.items()
            if _norm_repo_path(cand_path) != norm_self and cand_content
        }
        if not other_files:
            return ""

        all_lines = new_content.splitlines()
        changed_src_lines = [
            all_lines[ln - 1]
            for ln in changed_lines
            if 1 <= ln <= len(all_lines)
        ]
        primary_idents = _extract_identifiers_from_lines(changed_src_lines)
        header_lines = self._collect_enclosing_header_lines(
            file_path, new_content, changed_lines
        )
        secondary_idents = (
            _extract_identifiers_from_lines(header_lines) - primary_idents
        )

        if not primary_idents and not secondary_idents:
            return ""

        all_file_idents = _extract_identifiers_from_lines(all_lines)
        local_defs = {
            sym.name
            for sym in self.extract_symbol_definitions(file_path, new_content)
        }
        imported_norm_paths = {
            _norm_repo_path(p)
            for p in self.resolve_relative_imports(file_path, new_content)
        }

        scored: list[tuple[int, int, SymbolInfo]] = []
        order_counter = 0

        for cand_path, cand_content in other_files.items():
            cand_norm = _norm_repo_path(cand_path)
            is_imported = cand_norm in imported_norm_paths
            is_neighbor = _same_directory_or_stem(file_path, cand_path)
            cand_symbols = self.extract_symbol_definitions(cand_path, cand_content)

            for sym in cand_symbols:
                if sym.name in _SYMBOL_STOPWORDS or len(sym.name) < 3:
                    continue

                if sym.name in primary_idents:
                    score = 20
                elif sym.name in secondary_idents:
                    score = 8
                else:
                    continue

                enclosing_referenced = bool(
                    sym.enclosing_name and sym.enclosing_name in all_file_idents
                )

                # Avoid pulling same-named methods/helpers from unrelated files
                if sym.name in local_defs:
                    if not (
                        enclosing_referenced
                        or is_imported
                        or sym.node_type == "interface_declaration"
                    ):
                        continue

                if is_imported:
                    score += 12
                if enclosing_referenced:
                    score += 10
                if is_neighbor:
                    score += 4
                if sym.node_type in _CONTRACT_DEF_NODES:
                    score += 2

                order_counter += 1
                scored.append((score, -order_counter, sym))

        if not scored:
            return ""

        # Sort highest score first; prefer broader contract/class blocks over
        # redundant single-line sub-members at equal score so line deduplication
        # keeps the richer contract when both match.
        scored.sort(
            key=lambda item: (
                item[0],
                1 if item[2].node_type in (_CONTRACT_DEF_NODES | _CLASS_DEF_NODES) else 0,
                item[1],
            ),
            reverse=True,
        )

        selected: list[SymbolInfo] = []
        covered_lines_by_file: dict[str, set[int]] = {}
        total_rendered_lines = 0

        for _score, _ord, sym in scored:
            if len(selected) >= self.max_referenced_symbols:
                break

            cand_norm = _norm_repo_path(sym.file_path)
            sym_line_nums = {ln for ln, _ in sym.numbered_lines}
            if not sym_line_nums:
                continue

            already_covered = covered_lines_by_file.setdefault(cand_norm, set())
            if sym_line_nums.issubset(already_covered):
                continue

            rendered_count = len(sym.numbered_lines) + 1  # +1 for XML comment header
            if (
                selected
                and (total_rendered_lines + rendered_count) > MAX_REFERENCED_BLOCK_LINES
            ):
                continue

            selected.append(sym)
            already_covered.update(sym_line_nums)
            total_rendered_lines += rendered_count

        if not selected:
            return ""

        parts: list[str] = []
        for sym in selected:
            qual_name = (
                f"{sym.enclosing_name}.{sym.name}"
                if sym.enclosing_name and sym.enclosing_name != sym.name
                else sym.name
            )
            header = (
                f"<!-- from {sym.file_path}: {sym.node_type} {qual_name} "
                f"(lines {sym.start_line}–{sym.end_line}) -->"
            )
            parts.append(header)
            parts.append(_format_symbol_block(sym))

        body = "\n".join(parts)
        return (
            f'<referenced_symbols file="{file_path}">\n'
            f"{body}\n"
            f"</referenced_symbols>"
        )

    @staticmethod
    def resolve_relative_imports(file_path: str, content: str) -> list[str]:
        """Resolve relative module import specifiers in *content* to candidate repo paths.

        Supports TypeScript/JavaScript (``import ... from './foo'``, ``export ... from``,
        ``require('./foo')``) and Python (``from .foo import bar``).
        """
        if not content or not file_path:
            return []

        has_leading_slash = file_path.replace("\\", "/").startswith("/")
        norm_file = file_path.replace("\\", "/").lstrip("/")
        base_dir = posixpath.dirname(norm_file)
        ext = _extension_from_path(norm_file)

        candidates: list[str] = []
        seen: set[str] = set()

        def _add(rel_candidate: str) -> None:
            cleaned = posixpath.normpath(rel_candidate).lstrip("/")
            if not cleaned or cleaned.startswith("..") or cleaned == norm_file:
                return
            formatted = f"/{cleaned}" if has_leading_slash else cleaned
            if formatted not in seen:
                seen.add(formatted)
                candidates.append(formatted)

        if ext in (".ts", ".tsx", ".js", ".jsx"):
            spec_re = re.compile(
                r"""(?:(?:import|export)\s+(?:[^'"]*?\s+from\s+)?|require\(\s*)['"](\.\.?/[^'"]+)['"]"""
            )
            for m in spec_re.finditer(content):
                spec = m.group(1).strip()
                joined = posixpath.normpath(
                    posixpath.join(base_dir, spec) if base_dir else spec
                )
                if joined.startswith(".."):
                    continue
                _, spec_ext = posixpath.splitext(joined)
                if not spec_ext:
                    if ext in (".ts", ".tsx"):
                        for suffix in (".ts", ".tsx", "/index.ts", ".js"):
                            _add(f"{joined}{suffix}")
                    else:
                        for suffix in (".js", ".jsx", "/index.js", ".ts"):
                            _add(f"{joined}{suffix}")
                elif spec_ext == ".js" and ext in (".ts", ".tsx"):
                    base_no_ext = joined[:-3]
                    _add(f"{base_no_ext}.ts")
                    _add(f"{base_no_ext}.tsx")
                    _add(joined)
                else:
                    _add(joined)

        elif ext == ".py":
            py_from_re = re.compile(
                r"^\s*from\s+(\.+[A-Za-z0-9_.]*)\s+import\s+([^\n#]+)",
                re.MULTILINE,
            )
            for m in py_from_re.finditer(content):
                rel_mod = m.group(1)
                imp_clause = m.group(2)
                dots = len(rel_mod) - len(rel_mod.lstrip("."))
                remainder = rel_mod.lstrip(".")

                cur_dir = base_dir
                valid = True
                for _ in range(dots - 1):
                    if not cur_dir:
                        valid = False
                        break
                    cur_dir = posixpath.dirname(cur_dir)
                if not valid:
                    continue

                if remainder:
                    mod_rel = remainder.replace(".", "/")
                    base_target = (
                        posixpath.join(cur_dir, mod_rel) if cur_dir else mod_rel
                    )
                    _add(f"{base_target}.py")
                    _add(f"{base_target}/__init__.py")
                else:
                    cleaned_clause = imp_clause.strip().strip("()")
                    for part in cleaned_clause.split(","):
                        token = part.strip().split(" as ")[0].strip()
                        if token and token.isidentifier():
                            base_target = (
                                posixpath.join(cur_dir, token) if cur_dir else token
                            )
                            _add(f"{base_target}.py")

        return candidates

    @staticmethod
    def resolve_sibling_type_files(
        file_path: str,
        content: str,
        changed_lines: list[int],
    ) -> list[str]:
        """Suggest convention-based sibling type files for C# and Java files.

        In C# and Java repositories, interfaces and DTOs commonly live in
        ``<TypeName>.cs`` / ``<TypeName>.java`` in the same directory or a
        sibling ``Interfaces/`` or ``Models/`` folder without relative path
        imports.
        """
        ext = _extension_from_path(file_path)
        if ext not in (".cs", ".java") or not content:
            return []

        has_leading_slash = file_path.replace("\\", "/").startswith("/")
        norm_file = file_path.replace("\\", "/").lstrip("/")
        base_dir = posixpath.dirname(norm_file)
        parent_dir = posixpath.dirname(base_dir) if base_dir else ""

        all_lines = content.splitlines()
        sample_lines: list[str] = []
        # Include class header / field declarations (first 40 lines) + changed lines window
        sample_lines.extend(all_lines[:40])
        for ln in changed_lines:
            for idx in range(max(1, ln - 3), min(len(all_lines), ln + 1) + 1):
                sample_lines.append(all_lines[idx - 1])

        pascal_idents = {
            ident
            for ident in _extract_identifiers_from_lines(sample_lines)
            if ident[0].isupper() and any(ch.islower() for ch in ident)
        }

        candidates: list[str] = []
        seen: set[str] = set()
        search_dirs = [base_dir]
        if parent_dir:
            search_dirs.extend([
                posixpath.join(parent_dir, "Interfaces"),
                posixpath.join(parent_dir, "Models"),
                posixpath.join(parent_dir, "Dtos"),
                posixpath.join(parent_dir, "Contracts"),
            ])
        elif base_dir:
            search_dirs.extend([
                posixpath.join(base_dir, "Interfaces"),
                posixpath.join(base_dir, "Models"),
            ])

        for ident in sorted(pascal_idents):
            for sdir in search_dirs:
                rel = posixpath.join(sdir, f"{ident}{ext}") if sdir else f"{ident}{ext}"
                cleaned = posixpath.normpath(rel).lstrip("/")
                if cleaned == norm_file:
                    continue
                formatted = f"/{cleaned}" if has_leading_slash else cleaned
                if formatted not in seen:
                    seen.add(formatted)
                    candidates.append(formatted)

        return candidates

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


# ---------------------------------------------------------------------------
# Shared diff & file-filter builders (used by both TFSClient and GitUtils)
# ---------------------------------------------------------------------------


def normalize_filter_rules(
    excluded_paths: Optional[list[str]],
    file_extensions_filter: Optional[list[str]],
) -> tuple[list[str], list[str]]:
    """Normalize ``excluded_paths`` and ``file_extensions_filter`` once per run."""
    excl_prefixes = [
        p.replace("\\", "/").lstrip("/").lower()
        for p in (excluded_paths or [])
    ]
    ext_filter = [e.lower() for e in (file_extensions_filter or [])]
    return excl_prefixes, ext_filter


def should_include_file(
    file_path: str,
    excl_prefixes: list[str],
    ext_filter: list[str],
) -> bool:
    """Return True when *file_path* passes path exclusion and extension allowlist."""
    norm_path = _norm_repo_path(file_path)
    if excl_prefixes and any(norm_path.startswith(excl) for excl in excl_prefixes):
        return False
    if ext_filter and not any(norm_path.endswith(ext) for ext in ext_filter):
        return False
    return True


def format_full_code_diff(
    file_path: str,
    change_type: str,
    content: Optional[str],
    unavailable_error: bool = False,
) -> list[str]:
    """Build a ``full_code`` pseudo-diff payload for a single changed file."""
    parts = [
        f"diff --git a{file_path} b{file_path}",
        f"--- a{file_path}",
        f"+++ b{file_path}",
        f"@@ Change type: {change_type} @@",
    ]
    if unavailable_error:
        parts.append(f"+[Content not available for {file_path}]")
    elif content:
        for line in content.split("\n"):
            parts.append(f"+{line}")
    return parts


def format_unified_diff_with_context(
    file_path: str,
    original_path: str,
    old_lines: list[str],
    new_lines: list[str],
    extractor: ContextExtractor,
    file_records: Optional[dict[str, tuple[str, list[int]]]] = None,
) -> list[str]:
    """Build a 5-line unified diff plus AST context block for a single file."""
    diff = list(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"a{original_path}",
            tofile=f"b{file_path}",
            lineterm="",
            n=5,
        )
    )

    if not diff:
        return [
            f"diff --git a{original_path} b{file_path}",
            f"--- a{original_path}",
            f"+++ b{file_path}",
            "@@ No textual differences detected (possible binary/metadata change) @@",
        ]

    result = [f"diff --git a{original_path} b{file_path}"] + diff

    if new_lines:
        new_content = "\n".join(new_lines)
        file_diff_text = "\n".join(result)
        changed_lines = ContextExtractor.changed_lines_from_diff(file_diff_text)
        if file_records is not None:
            file_records[file_path] = (new_content, changed_lines)
        _strategy, xml_block = extractor.extract(
            file_path=file_path,
            new_content=new_content,
            changed_lines=changed_lines,
        )
        if xml_block:
            result.append(xml_block)

    return result
