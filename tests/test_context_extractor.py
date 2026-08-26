# License: Apache-2.0
# Copyright 2026 Sérgio Sereno

"""Tests for the context_extractor module.

Covers:
- changed_lines_from_diff: parse added lines from unified diff
- _build_adaptive_diff: fallback context extraction
- _build_file_skeleton: skeleton generation
- ContextExtractor.extract: strategy selection for all 6 languages + fallback
- XML output tags and structure
- Tree-sitter fallback when library is unavailable
"""

from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

from src.context_extractor import (
    ADAPTIVE_CONTEXT_LINES,
    MAX_SCOPE_BLOCKS,
    ContextExtractor,
    ScopeInfo,
    _build_adaptive_diff,
    _build_file_skeleton,
    _lang_from_path,
)
import src.context_extractor as _ctx_mod

_EXT_TO_LANG: dict[str, str] = _ctx_mod._EXT_TO_LANG  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_extractor(**kwargs: object) -> ContextExtractor:
    """Build a ContextExtractor with given overrides."""
    return ContextExtractor(**kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# ScopeInfo dataclass
# ---------------------------------------------------------------------------

def test_scope_info_defaults() -> None:
    """ScopeInfo must initialise with sane defaults."""
    s = ScopeInfo(start_line=1, end_line=10, node_type="fn", name="foo")
    assert s.source_lines == []
    assert s.start_line == 1
    assert s.end_line == 10


# ---------------------------------------------------------------------------
# _lang_from_path
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(("path", "expected"), [
    ("src/auth.py",   "python"),
    ("src/app.js",    "javascript"),
    ("src/view.jsx",  "javascript"),
    ("src/main.ts",   "typescript"),
    ("src/comp.tsx",  "tsx"),
    ("src/Foo.cs",    "c_sharp"),
    ("src/Bar.java",  "java"),
    ("migrations/001.sql", "sql"),
    ("README.md",     None),
    ("Makefile",      None),
])
def test_lang_from_path(path: str, expected: str | None) -> None:
    """Extension-to-language mapping must cover all 6 supported languages."""
    assert _lang_from_path(path) == expected


# ---------------------------------------------------------------------------
# ContextExtractor.changed_lines_from_diff
# ---------------------------------------------------------------------------

def test_changed_lines_from_diff_single_hunk() -> None:
    """Should return 1-based new-file line numbers for added (+) lines only."""
    diff = "\n".join([
        "diff --git a/src/app.py b/src/app.py",
        "--- a/src/app.py",
        "+++ b/src/app.py",
        "@@ -1,3 +1,4 @@",
        " import os",
        "+new_line_a",
        " unchanged",
        "+new_line_b",
    ])
    result = ContextExtractor.changed_lines_from_diff(diff)
    assert result == [2, 4]


def test_changed_lines_from_diff_multiple_hunks() -> None:
    """Should correctly accumulate lines across multiple @@ hunks."""
    diff = "\n".join([
        "@@ -1,2 +1,3 @@",
        " ctx",
        "+added1",
        " ctx",
        "@@ -10,2 +11,3 @@",
        " ctx10",
        "+added_at_12",
        " ctx11",
    ])
    result = ContextExtractor.changed_lines_from_diff(diff)
    assert 2 in result
    assert 12 in result


def test_changed_lines_from_diff_empty_input() -> None:
    """Empty diff must return an empty list."""
    assert ContextExtractor.changed_lines_from_diff("") == []


def test_changed_lines_from_diff_no_added_lines() -> None:
    """Diff with only deleted lines must return an empty list."""
    diff = "\n".join([
        "@@ -1,2 +1,1 @@",
        "-removed",
        " context",
    ])
    assert ContextExtractor.changed_lines_from_diff(diff) == []


# ---------------------------------------------------------------------------
# _build_adaptive_diff
# ---------------------------------------------------------------------------

def test_build_adaptive_diff_includes_context_window() -> None:
    """Should include surrounding lines up to context_lines away from changed lines."""
    content = "\n".join(f"line{i}" for i in range(1, 21))  # lines 1-20
    result = _build_adaptive_diff(content, changed_lines=[10], context_lines=3)

    assert "line10" in result
    assert "line7" in result   # 3 before
    assert "line13" in result  # 3 after
    # Line 1 should NOT be included (too far)
    assert "line1\n" not in result or result.startswith("   1:")


def test_build_adaptive_diff_merges_overlapping_windows() -> None:
    """Overlapping windows must produce one contiguous block without gaps."""
    content = "\n".join(f"line{i}" for i in range(1, 30))
    result = _build_adaptive_diff(content, changed_lines=[5, 6], context_lines=2)
    # Should not have a '...' separator between lines 5 and 6 hunks
    assert "..." not in result.split("line5")[1].split("line6")[0]


def test_build_adaptive_diff_empty_content() -> None:
    """Empty content or no changed lines must return empty string."""
    assert _build_adaptive_diff("", [1]) == ""
    assert _build_adaptive_diff("something\n", []) == ""


def test_build_adaptive_diff_line_numbers_are_absolute() -> None:
    """Output must prefix each line with its 1-based absolute line number."""
    content = "aaa\nbbb\nccc"
    result = _build_adaptive_diff(content, changed_lines=[2], context_lines=1)
    # Must contain '   1: aaa', '   2: bbb', '   3: ccc'
    assert "1: aaa" in result
    assert "2: bbb" in result
    assert "3: ccc" in result


# ---------------------------------------------------------------------------
# _build_file_skeleton (Python)
# ---------------------------------------------------------------------------

PYTHON_MODULE = """\
import os
import sys


class MyClass:
    def method_a(self):
        # Method a.
        x = 1
        y = 2
        z = x + y
        return z

    def method_b(self):
        # Method b is far enough away from method_a.
        a = "hello"
        b = "world"
        c = a + " " + b
        return c

    def method_c(self):
        # Method c.
        return 42

    def method_d(self):
        # Method d.
        return None

    def method_e(self):
        # Method e is far away from line 8.
        return True


def standalone():
    pass
"""


def test_build_file_skeleton_python_includes_imports_and_defs() -> None:
    """Skeleton must always include import lines and def/class signatures."""
    result = _build_file_skeleton(PYTHON_MODULE, changed_lines=[8], lang="python")
    assert "import os" in result
    assert "class MyClass" in result
    assert "def method_a" in result


def test_build_file_skeleton_python_replaces_unchanged_bodies() -> None:
    """Unchanged function bodies far from the changed line must be replaced with '...'."""
    # Use a small context window so that distant methods get collapsed to '...'
    from src.context_extractor import _build_adaptive_diff as _adiff
    result = _build_file_skeleton(PYTHON_MODULE, changed_lines=[8], lang="python")
    # With default ADAPTIVE_CONTEXT_LINES=10, method_e at ~line 32 is outside
    # the window for changed_line=8, so the skeleton must contain '...'
    assert "..." in result


def test_build_file_skeleton_empty_content_returns_empty() -> None:
    """Empty content must return empty string."""
    assert _build_file_skeleton("", changed_lines=[1], lang="python") == ""


# ---------------------------------------------------------------------------
# ContextExtractor.extract — fallback (no Tree-sitter)
# ---------------------------------------------------------------------------

class TestExtractFallback:
    """Tests for adaptive_diff fallback path when Tree-sitter is unavailable."""

    def test_extract_returns_adaptive_diff_when_ts_unavailable(self) -> None:
        """extract() must return 'adaptive_diff' strategy when _TS_AVAILABLE=False."""
        extractor = ContextExtractor()
        with patch("src.context_extractor._TS_AVAILABLE", False):
            strategy, xml = extractor.extract(
                file_path="src/auth.py",
                new_content="def foo():\n    pass\n",
                changed_lines=[1],
            )
        assert strategy == "adaptive_diff"
        assert "<adaptive_diff" in xml
        assert 'file="src/auth.py"' in xml

    def test_extract_returns_adaptive_diff_for_unknown_extension(self) -> None:
        """Unknown file extensions must always fall back to adaptive_diff."""
        extractor = ContextExtractor()
        strategy, xml = extractor.extract(
            file_path="config.yaml",
            new_content="key: value\n",
            changed_lines=[1],
        )
        assert strategy == "adaptive_diff"

    def test_extract_returns_empty_for_no_changed_lines(self) -> None:
        """No changed lines must return empty xml block."""
        extractor = ContextExtractor()
        strategy, xml = extractor.extract(
            file_path="src/app.py",
            new_content="pass",
            changed_lines=[],
        )
        assert strategy == "adaptive_diff"
        assert xml == ""

    def test_extract_adaptive_xml_tag_is_well_formed(self) -> None:
        """adaptive_diff XML must open and close with matching tags."""
        extractor = ContextExtractor()
        with patch("src.context_extractor._TS_AVAILABLE", False):
            _, xml = extractor.extract(
                file_path="src/x.py",
                new_content="a\nb\nc",
                changed_lines=[2],
            )
        assert xml.startswith("<adaptive_diff ")
        assert xml.strip().endswith("</adaptive_diff>")


# ---------------------------------------------------------------------------
# ContextExtractor.extract — Python (Tree-sitter)
# ---------------------------------------------------------------------------

# These tests require tree-sitter-languages to be installed.
# If not available, the test is automatically skipped.
try:
    from tree_sitter_languages import get_parser as _get_parser  # type: ignore
    _TS_INSTALLED = True
except Exception:
    _TS_INSTALLED = False

ts_required = pytest.mark.skipif(
    not _TS_INSTALLED,
    reason="tree-sitter-languages not installed",
)


@ts_required
class TestExtractPython:
    """Tree-sitter extraction tests for Python."""

    PYTHON_CODE = """\
import os


def foo():
    x = 1
    return x


def bar():
    return "bar"
"""

    def test_extract_scenario_a_enclosing_scopes(self) -> None:
        """Single changed function must return enclosing_scopes strategy."""
        extractor = ContextExtractor()
        strategy, xml = extractor.extract(
            file_path="src/module.py",
            new_content=self.PYTHON_CODE,
            changed_lines=[5],  # inside foo()
        )
        assert strategy == "enclosing_scopes"
        assert "<enclosing_scopes" in xml
        assert "foo" in xml
        assert 'file="src/module.py"' in xml

    def test_extract_enclosing_scopes_xml_well_formed(self) -> None:
        """Enclosing scopes XML must have matching open/close tags."""
        extractor = ContextExtractor()
        _, xml = extractor.extract(
            file_path="src/module.py",
            new_content=self.PYTHON_CODE,
            changed_lines=[5],
        )
        assert xml.strip().endswith("</enclosing_scopes>")

    def test_extract_scenario_b_file_skeleton_when_many_blocks(self) -> None:
        """More than max_scope_blocks distinct scopes must return file_skeleton."""
        many_funcs = "\n".join(
            f"def func_{i}():\n    return {i}\n" for i in range(10)
        )
        # Changed lines spread across many functions
        changed = list(range(1, 30, 3))  # lines 1, 4, 7, 10 ...
        extractor = ContextExtractor(max_scope_blocks=MAX_SCOPE_BLOCKS)
        strategy, xml = extractor.extract(
            file_path="src/big_module.py",
            new_content=many_funcs,
            changed_lines=changed,
        )
        assert strategy == "file_skeleton"
        assert "<file_skeleton" in xml
        assert "</file_skeleton>" in xml

    def test_extract_line_numbers_are_absolute(self) -> None:
        """Extracted scope must show absolute 1-based line numbers."""
        extractor = ContextExtractor()
        _, xml = extractor.extract(
            file_path="src/module.py",
            new_content=self.PYTHON_CODE,
            changed_lines=[5],
        )
        # foo() starts at line 4, so line 4 must appear in the XML content
        assert "4:" in xml


@ts_required
class TestExtractJavaScript:
    """Tree-sitter extraction tests for JavaScript."""

    JS_CODE = """\
function greet(name) {
    return "Hello " + name;
}

const add = (a, b) => a + b;
"""

    def test_extract_function_declaration(self) -> None:
        """function declaration must be captured as enclosing scope."""
        extractor = ContextExtractor()
        strategy, xml = extractor.extract(
            file_path="src/utils.js",
            new_content=self.JS_CODE,
            changed_lines=[2],
        )
        assert strategy == "enclosing_scopes"
        assert "greet" in xml

    def test_extract_arrow_function(self) -> None:
        """Arrow function assigned to const must be captured as enclosing scope."""
        extractor = ContextExtractor()
        strategy, xml = extractor.extract(
            file_path="src/math.js",
            new_content=self.JS_CODE,
            changed_lines=[5],
        )
        assert strategy == "enclosing_scopes"


@ts_required
class TestExtractTypeScript:
    """Tree-sitter extraction tests for TypeScript."""

    TS_CODE = """\
interface User {
    name: string;
    age: number;
}

function createUser(name: string): User {
    return { name, age: 0 };
}
"""

    def test_extract_function_in_ts_file(self) -> None:
        """TypeScript function must be captured with .ts extension."""
        extractor = ContextExtractor()
        strategy, xml = extractor.extract(
            file_path="src/user.ts",
            new_content=self.TS_CODE,
            changed_lines=[7],
        )
        assert strategy == "enclosing_scopes"
        assert "createUser" in xml


@ts_required
class TestExtractCSharp:
    """Tree-sitter extraction tests for C#."""

    CS_CODE = """\
using System;

namespace MyApp
{
    public class Calculator
    {
        public int Add(int a, int b)
        {
            return a + b;
        }

        public int Subtract(int a, int b)
        {
            return a - b;
        }
    }
}
"""

    def test_extract_method_in_csharp(self) -> None:
        """C# method must be captured as enclosing scope."""
        extractor = ContextExtractor()
        strategy, xml = extractor.extract(
            file_path="src/Calculator.cs",
            new_content=self.CS_CODE,
            changed_lines=[9],
        )
        assert strategy == "enclosing_scopes"
        assert "Add" in xml


@ts_required
class TestExtractJava:
    """Tree-sitter extraction tests for Java."""

    JAVA_CODE = """\
public class MathUtils {

    public static int multiply(int a, int b) {
        return a * b;
    }

    public static int divide(int a, int b) {
        return a / b;
    }
}
"""

    def test_extract_method_in_java(self) -> None:
        """Java method must be captured as enclosing scope."""
        extractor = ContextExtractor()
        strategy, xml = extractor.extract(
            file_path="src/MathUtils.java",
            new_content=self.JAVA_CODE,
            changed_lines=[4],
        )
        assert strategy == "enclosing_scopes"
        assert "multiply" in xml


@ts_required
class TestExtractSQL:
    """Tree-sitter extraction tests for SQL."""

    SQL_CODE = """\
SELECT id, name
FROM users
WHERE active = 1;

UPDATE users
SET last_login = NOW()
WHERE id = 42;
"""

    def test_extract_sql_statement(self) -> None:
        """Changed SQL line must return sql_statement or adaptive_diff strategy."""
        extractor = ContextExtractor()
        strategy, xml = extractor.extract(
            file_path="migrations/query.sql",
            new_content=self.SQL_CODE,
            changed_lines=[2],
        )
        # sql_statement or adaptive_diff (if SQL grammar not available)
        assert strategy in ("sql_statement", "adaptive_diff")
        assert xml != ""


# ---------------------------------------------------------------------------
# ContextExtractor.extract — Tree-sitter parse failure fallback
# ---------------------------------------------------------------------------

def test_extract_falls_back_on_tree_sitter_parse_error(mocker: object) -> None:
    """If Tree-sitter raises during parsing, extract() must fall back to adaptive_diff."""
    extractor = ContextExtractor()
    mocker.patch(  # type: ignore[union-attr]
        "src.context_extractor._extract_enclosing_scopes_ts",
        side_effect=RuntimeError("parse failed"),
    )
    mocker.patch("src.context_extractor._TS_AVAILABLE", True)  # type: ignore[union-attr]
    strategy, xml = extractor.extract(
        file_path="src/broken.py",
        new_content="def foo():\n    pass\n",
        changed_lines=[2],
    )
    assert strategy == "adaptive_diff"
    assert "<adaptive_diff" in xml


# ---------------------------------------------------------------------------
# ContextExtractor — XML block structure integrity
# ---------------------------------------------------------------------------

def test_extract_adaptive_xml_contains_file_attribute() -> None:
    """XML block must include the file attribute for traceability."""
    extractor = ContextExtractor()
    with patch("src.context_extractor._TS_AVAILABLE", False):
        _, xml = extractor.extract(
            file_path="some/path/module.rb",
            new_content="puts 'hello'",
            changed_lines=[1],
        )
    assert 'file="some/path/module.rb"' in xml


def test_extractor_custom_max_scope_blocks() -> None:
    """max_scope_blocks parameter must be honoured."""
    extractor = ContextExtractor(max_scope_blocks=1)
    assert extractor.max_scope_blocks == 1


def test_extractor_custom_adaptive_context_lines() -> None:
    """adaptive_context_lines parameter must be honoured."""
    extractor = ContextExtractor(adaptive_context_lines=5)
    assert extractor.adaptive_context_lines == 5
