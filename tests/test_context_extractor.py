# License: Apache-2.0
# Copyright 2026 Sérgio Sereno

"""Tests for the context_extractor module.

Covers:
- changed_lines_from_diff: parse added lines from unified diff
- _build_adaptive_diff: fallback context extraction
- ContextExtractor.extract: strategy selection for all 6 languages + fallback
- XML output tags and structure
- Tree-sitter fallback when library is unavailable
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

import src.context_extractor as _ctx_mod
from src.context_extractor import (
    MAX_SCOPE_BLOCKS,
    ContextExtractor,
    ScopeInfo,
    _build_adaptive_diff,
    _lang_from_path,
    _remove_redundant_ancestor_scopes,
)

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


def test_remove_ancestor_when_child_covers_all_changed_lines() -> None:
    """A containing class is redundant when all changes belong to a method."""
    class_scope = ScopeInfo(1, 10, "class_declaration", "Example")
    method_scope = ScopeInfo(3, 6, "method_declaration", "Run")

    result = _remove_redundant_ancestor_scopes(
        [class_scope, method_scope],
        changed_lines=[4],
    )

    assert result == [method_scope]


def test_keep_ancestor_with_uncovered_class_level_change() -> None:
    """An ancestor remains when it owns a change outside the child scope."""
    class_scope = ScopeInfo(1, 10, "class_declaration", "Example")
    method_scope = ScopeInfo(3, 6, "method_declaration", "Run")

    result = _remove_redundant_ancestor_scopes(
        [class_scope, method_scope],
        changed_lines=[2, 4],
    )

    assert result == [class_scope, method_scope]


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
    """Extension-to-language mapping must cover all supported languages."""
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

# Keep a defensive skip so the fallback-only suite remains usable in reduced
# environments, even though tree-sitter-languages is a core dependency.
try:
    __import__("tree_sitter_languages")
    _TS_INSTALLED = True
except ImportError:
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

    def test_extract_enclosing_scopes(self) -> None:
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

    def test_whitespace_only_changes_use_adaptive_diff(self) -> None:
        """Whitespace-only changes must not select an AST scope."""
        extractor = ContextExtractor()
        strategy, xml = extractor.extract(
            file_path="src/module.py",
            new_content=self.PYTHON_CODE,
            changed_lines=[2],
        )

        assert strategy == "adaptive_diff"
        assert "<adaptive_diff" in xml

    def test_extract_adaptive_diff_when_many_scopes(self) -> None:
        """More than max_scope_blocks distinct scopes must use adaptive_diff."""
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
        assert strategy == "adaptive_diff"
        assert "<adaptive_diff" in xml

    def test_extract_adaptive_diff_when_scope_is_oversized(self) -> None:
        """A scope over max_scope_lines must use adaptive_diff."""
        code = "def large():\n" + "\n".join(
            f"    value_{i} = {i}" for i in range(5)
        )
        extractor = ContextExtractor(max_scope_lines=5)
        strategy, xml = extractor.extract(
            file_path="src/large.py",
            new_content=code,
            changed_lines=[3],
        )
        assert strategy == "adaptive_diff"
        assert "<adaptive_diff" in xml

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
        strategy, _xml = extractor.extract(
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

    def test_whitespace_change_after_method_does_not_select_class(self) -> None:
        """A changed blank line outside a method must not pull in its class."""
        code = """\
public class Example
{
    public int GetValue()
    {
        return 1;
    }

    private int _otherValue;
}
"""
        extractor = ContextExtractor(max_scope_lines=20)
        strategy, xml = extractor.extract(
            file_path="src/Example.cs",
            new_content=code,
            changed_lines=[5, 7],
        )

        assert strategy == "enclosing_scopes"
        assert "method_declaration: GetValue" in xml
        assert "class_declaration: Example" not in xml
        assert "_otherValue" not in xml


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

    def test_extract_sql_enclosing_scope(self) -> None:
        """A changed SQL statement uses the common enclosing_scopes strategy."""
        extractor = ContextExtractor()
        strategy, xml = extractor.extract(
            file_path="migrations/query.sql",
            new_content=self.SQL_CODE,
            changed_lines=[2],
        )
        assert strategy in ("enclosing_scopes", "adaptive_diff")
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


# ---------------------------------------------------------------------------
# Cross-file deterministic symbol resolution (Option 4A)
# ---------------------------------------------------------------------------


@ts_required
class TestCrossFileSymbolResolution:
    """Tests for AST-based cross-file symbol extraction and resolution."""

    def test_extract_symbol_definitions_csharp_ts_and_python(self) -> None:
        """Should extract compact callable signatures (without body) and contracts."""
        extractor = ContextExtractor()

        cs_code = """\
namespace Orders
{
    public record OrderDto(Guid Id, decimal Total);

    public class OrderService
    {
        public OrderDto GetOrder(Guid id)
        {
            var secretBodyVar = 123;
            return new OrderDto(id, secretBodyVar);
        }
    }
}
"""
        cs_symbols = extractor.extract_symbol_definitions("/src/OrderService.cs", cs_code)
        names = {s.name: s for s in cs_symbols}
        assert "OrderDto" in names
        assert "OrderService" in names
        # Must extract method name GetOrder, NOT return type OrderDto
        assert "GetOrder" in names
        get_order_text = "\n".join(txt for _, txt in names["GetOrder"].numbered_lines)
        assert "public OrderDto GetOrder(Guid id)" in get_order_text
        assert "secretBodyVar" not in get_order_text

        ts_code = """\
export interface AuthClaims {
    userId: string;
    roles: string[];
}

export const verifyToken = (token: string): AuthClaims => {
    const internalSecret = token.trim();
    return { userId: internalSecret, roles: [] };
};
"""
        ts_symbols = extractor.extract_symbol_definitions("/src/auth.ts", ts_code)
        ts_names = {s.name: s for s in ts_symbols}
        assert "AuthClaims" in ts_names
        assert "verifyToken" in ts_names
        verify_text = "\n".join(txt for _, txt in ts_names["verifyToken"].numbered_lines)
        assert "verifyToken" in verify_text
        assert "internalSecret" not in verify_text

        py_code = """\
@cached
def compute_discount(amount: float) -> float:
    hidden_local = amount * 0.1
    return hidden_local
"""
        py_symbols = extractor.extract_symbol_definitions("/src/pricing.py", py_code)
        py_names = {s.name: s for s in py_symbols}
        assert "compute_discount" in py_names
        py_sig = "\n".join(txt for _, txt in py_names["compute_discount"].numbered_lines)
        assert "@cached" in py_sig
        assert "def compute_discount" in py_sig
        assert "hidden_local" not in py_sig

    def test_extract_referenced_symbols_resolves_cross_file_contracts(self) -> None:
        """Should emit <referenced_symbols> with signatures from candidate files."""
        extractor = ContextExtractor()

        service_code = """\
using Orders.Models;

public class OrderController
{
    private readonly IOrderRepository _repo;

    public async Task Submit(OrderDto dto)
    {
        await _repo.SaveOrderAsync(dto);
    }
}
"""
        repo_code = """\
public interface IOrderRepository
{
    Task<bool> SaveOrderAsync(OrderDto dto);
}
"""
        dto_code = """\
public record OrderDto(
    Guid Id,
    decimal Amount
);
"""
        unrelated_code = """\
public class AuditLogger
{
    public void WriteAudit(string entry) {}
}
"""
        xml = extractor.extract_referenced_symbols(
            file_path="/src/Controllers/OrderController.cs",
            new_content=service_code,
            changed_lines=[9],  # await _repo.SaveOrderAsync(dto);
            candidate_files={
                "/src/Controllers/OrderController.cs": service_code,
                "/src/Interfaces/IOrderRepository.cs": repo_code,
                "/src/Models/OrderDto.cs": dto_code,
                "/src/Logging/AuditLogger.cs": unrelated_code,
            },
        )

        assert xml.startswith('<referenced_symbols file="/src/Controllers/OrderController.cs">')
        assert xml.endswith("</referenced_symbols>")
        assert "SaveOrderAsync" in xml
        assert "OrderDto" in xml
        assert "WriteAudit" not in xml
        assert "AuditLogger" not in xml

    def test_interface_contract_kept_while_unrelated_same_name_skipped(self) -> None:
        """Same-named method is kept from implemented interface, skipped from unrelated class."""
        extractor = ContextExtractor()

        impl_code = """\
public class AuthService : IAuthService
{
    public bool ValidateSession(string token)
    {
        return token.Length > 10;
    }
}
"""
        iface_code = """\
public interface IAuthService
{
    bool ValidateSession(string token);
}
"""
        unrelated_code = """\
public class LegacyValidator
{
    public bool ValidateSession(string token)
    {
        return false;
    }
}
"""
        xml = extractor.extract_referenced_symbols(
            file_path="/src/AuthService.cs",
            new_content=impl_code,
            changed_lines=[5],
            candidate_files={
                "/src/IAuthService.cs": iface_code,
                "/src/LegacyValidator.cs": unrelated_code,
            },
        )

        assert "IAuthService" in xml
        assert "LegacyValidator" not in xml


def test_extract_referenced_symbols_regex_fallback_and_disable() -> None:
    """Regex fallback must resolve cross-file symbols when Tree-sitter is unavailable."""
    caller_py = "from .helpers import calculate_tax\n\ndef total(x):\n    return calculate_tax(x)\n"
    helper_py = "def calculate_tax(amount: float) -> float:\n    secret = 0.23\n    return amount * secret\n"

    with patch("src.context_extractor._TS_AVAILABLE", False):
        extractor = ContextExtractor()
        xml = extractor.extract_referenced_symbols(
            file_path="/src/billing.py",
            new_content=caller_py,
            changed_lines=[4],
            candidate_files={"/src/helpers.py": helper_py},
        )

    assert '<referenced_symbols file="/src/billing.py">' in xml
    assert "def calculate_tax(amount: float) -> float:" in xml
    assert "secret = 0.23" not in xml

    disabled = ContextExtractor(max_referenced_symbols=0)
    assert disabled.extract_referenced_symbols(
        file_path="/src/billing.py",
        new_content=caller_py,
        changed_lines=[4],
        candidate_files={"/src/helpers.py": helper_py},
    ) == ""


def test_resolve_relative_imports_and_sibling_types() -> None:
    """Should resolve TS/JS/Python relative imports and C# sibling type paths."""
    ts_imports = ContextExtractor.resolve_relative_imports(
        "/src/components/login.ts",
        "import { User } from '../models/user';\nimport { fmt } from './format.js';\n",
    )
    assert "/src/models/user.ts" in ts_imports
    assert "/src/components/format.ts" in ts_imports

    py_imports = ContextExtractor.resolve_relative_imports(
        "/src/pkg/sub/service.py",
        "from .dto import ItemDto\nfrom ..common import auth_utils\n",
    )
    assert "/src/pkg/sub/dto.py" in py_imports
    assert "/src/pkg/common.py" in py_imports

    cs_siblings = ContextExtractor.resolve_sibling_type_files(
        "/src/Services/OrderService.cs",
        "public class OrderService : IOrderService {\n    public void Run(OrderDto dto) {}\n}\n",
        changed_lines=[2],
    )
    assert "/src/Services/IOrderService.cs" in cs_siblings
    assert "/src/Interfaces/IOrderService.cs" in cs_siblings
    assert "/src/Models/OrderDto.cs" in cs_siblings

