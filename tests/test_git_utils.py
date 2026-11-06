# License: Apache-2.0
# Copyright 2026 Sérgio Sereno

"""Tests for the Git utilities module."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from src.git_utils import GitError, GitUtils


def make_git_utils(repo_path: str = "repo") -> GitUtils:
    """Build a GitUtils instance without running repository validation."""
    return GitUtils(repo_path=repo_path, validate=False)


def test_init_sets_repo_path_and_validates(mocker) -> None:
    """It should initialize the repository path and validate the repo unless validate=False."""
    validate = mocker.patch("src.git_utils.GitUtils._validate_repo")

    instance = GitUtils("/tmp/repo")
    assert instance.repo_path == "/tmp/repo"
    validate.assert_called_once_with()

    validate.reset_mock()
    unvalidated = GitUtils("/tmp/other", validate=False)
    assert unvalidated.repo_path == "/tmp/other"
    validate.assert_not_called()


def test_validate_repo_wraps_git_errors() -> None:
    """It should raise a friendly message when the directory is not a Git repo."""
    instance = make_git_utils()
    instance._run_git = lambda *args, **kwargs: (_ for _ in ()).throw(GitError("nope"))

    with pytest.raises(GitError, match="not a valid Git repository"):
        instance._validate_repo()


def test_run_git_success_and_errors(mocker) -> None:
    """It should return stdout and convert subprocess failures into GitError."""
    instance = make_git_utils("repo")
    run_mock = mocker.patch(
        "src.git_utils.subprocess.run",
        return_value=SimpleNamespace(returncode=0, stdout="ok", stderr=""),
    )

    assert instance._run_git("status") == "ok"
    run_mock.assert_called_once()

    run_mock.reset_mock(return_value=True)
    run_mock.return_value = SimpleNamespace(returncode=1, stdout="", stderr="bad")
    with pytest.raises(GitError, match="Git command failed"):
        instance._run_git("status")

    run_mock.side_effect = FileNotFoundError()
    with pytest.raises(GitError, match="Git not found"):
        instance._run_git("status")

    run_mock.side_effect = subprocess.TimeoutExpired(cmd="git status", timeout=60)
    with pytest.raises(GitError, match="Timeout executing"):
        instance._run_git("status")


def test_filter_and_split_diff_helpers(sample_diff: str) -> None:
    """It should filter additions and split multi-file diffs into sections."""
    instance = make_git_utils()
    filtered = instance.filter_diff_additions_only(sample_diff)
    sections, has_sections = instance._split_diff_sections(sample_diff)

    assert "-print('old')" not in filtered
    assert "+print('new')" in filtered
    assert has_sections is True
    assert len(sections) == 2


def test_bundle_diff_files_and_truncate_helpers(sample_diff: str) -> None:
    """It should bundle related files and truncate oversized sections."""
    instance = make_git_utils()

    bundles = instance.bundle_diff_files(sample_diff, max_files_per_bundle=1)
    assert len(bundles) == 2
    assert bundles[0]["files_summary"] == [{"file": "src/app.py", "additions": 2, "deletions": 1}]
    assert bundles[1]["files_summary"] == [{"file": "docs/readme.md", "additions": 2, "deletions": 1}]

    truncated, changed = instance.truncate_diff_per_file(sample_diff, max_lines=3)
    assert changed is True
    assert "TRUNCATED IN THIS FILE" in truncated


def test_smart_file_bundling_groups_related_stems_and_modules() -> None:
    """It should keep interface, implementation, test, and i18n pairs together across directories."""
    instance = make_git_utils()
    multi_diff = "\n".join([
        "diff --git a/src/Interfaces/IAuthService.cs b/src/Interfaces/IAuthService.cs",
        "--- a/src/Interfaces/IAuthService.cs",
        "+++ b/src/Interfaces/IAuthService.cs",
        "@@ -1 +1 @@",
        "+public interface IAuthService {}",
        "diff --git a/src/Orders/OrderService.cs b/src/Orders/OrderService.cs",
        "--- a/src/Orders/OrderService.cs",
        "+++ b/src/Orders/OrderService.cs",
        "@@ -1 +1 @@",
        "+public class OrderService {}",
        "diff --git a/src/Services/AuthService.cs b/src/Services/AuthService.cs",
        "--- a/src/Services/AuthService.cs",
        "+++ b/src/Services/AuthService.cs",
        "@@ -1 +1 @@",
        "+public class AuthService : IAuthService {}",
        "diff --git a/tests/Services/AuthServiceTests.cs b/tests/Services/AuthServiceTests.cs",
        "--- a/tests/Services/AuthServiceTests.cs",
        "+++ b/tests/Services/AuthServiceTests.cs",
        "@@ -1 +1 @@",
        "+public class AuthServiceTests {}",
        "diff --git a/i18n/message_en.properties b/i18n/message_en.properties",
        "--- a/i18n/message_en.properties",
        "+++ b/i18n/message_en.properties",
        "@@ -1 +1 @@",
        "+hello=Hello",
        "diff --git a/i18n/message_zh.properties b/i18n/message_zh.properties",
        "--- a/i18n/message_zh.properties",
        "+++ b/i18n/message_zh.properties",
        "@@ -1 +1 @@",
        "+hello=你好",
    ])

    bundles = instance.bundle_diff_files(multi_diff, max_files_per_bundle=3)

    assert len(bundles) == 2
    bundle1_files = [f["file"] for f in bundles[0]["files_summary"]]
    bundle2_files = [f["file"] for f in bundles[1]["files_summary"]]

    # Auth triad grouped together despite OrderService appearing 2nd in raw diff
    assert bundle1_files == [
        "src/Interfaces/IAuthService.cs",
        "src/Services/AuthService.cs",
        "tests/Services/AuthServiceTests.cs",
    ]
    # OrderService + i18n message pair grouped into the second bundle (1 + 2 = 3 <= 3)
    assert bundle2_files == [
        "src/Orders/OrderService.cs",
        "i18n/message_en.properties",
        "i18n/message_zh.properties",
    ]


def test_truncate_diff_without_sections_and_summary(sample_diff: str) -> None:
    """It should truncate plain text diffs and summarize changed files."""
    instance = make_git_utils()
    plain_diff = "\n".join(f"line-{i}" for i in range(6))
    truncated, was_truncated = instance.truncate_diff_per_file(plain_diff, max_lines=3)
    summary = instance.get_changed_files_summary(sample_diff)

    assert was_truncated is True
    assert "TRUNCATED" in truncated
    assert summary == [
        {"file": "src/app.py", "additions": 2, "deletions": 1},
        {"file": "docs/readme.md", "additions": 2, "deletions": 1},
    ]


def test_truncate_diff_preserves_ast_context_block() -> None:
    """Per-file truncation must retain the complete trailing context block."""
    instance = make_git_utils()
    diff = "\n".join([
        "diff --git a/src/app.py b/src/app.py",
        "--- a/src/app.py",
        "+++ b/src/app.py",
        "@@ -1,4 +1,4 @@",
        "+line one",
        "+line two",
        "+line three",
        '<enclosing_scopes file="src/app.py">',
        "   1: def example():",
        "   2:     return True",
        "</enclosing_scopes>",
    ])

    truncated, changed = instance.truncate_diff_per_file(diff, max_lines=5)

    assert changed is True
    assert "TRUNCATED IN THIS FILE" in truncated
    assert '<enclosing_scopes file="src/app.py">' in truncated
    assert "   2:     return True" in truncated
    assert truncated.rstrip().endswith("</enclosing_scopes>")



def test_filter_diff_additions_only_preserves_xml_context_blocks() -> None:
    """Lines inside XML context blocks (<enclosing_scopes>, etc) must be kept verbatim, regardless of prefix."""
    instance = make_git_utils()
    diff = "\n".join([
        "diff --git a/src/app.py b/src/app.py",
        "--- a/src/app.py",
        "+++ b/src/app.py",
        "@@ -1,2 +1,2 @@",
        " this_context_line_outside",
        "-deleted_line_outside",
        "+added_line_outside",
        "<enclosing_scopes file=\"/src/app.py\">",
        "raw_context_inside",
        " raw_space_inside",
        "-raw_minus_inside",
        "+raw_plus_inside",
        "</enclosing_scopes>",
        " outside_context_line_after",
    ])

    filtered = instance.filter_diff_additions_only(diff)

    assert "this_context_line_outside" in filtered
    assert "deleted_line_outside" not in filtered
    assert "+added_line_outside" in filtered
    assert "<enclosing_scopes file=\"/src/app.py\">" in filtered
    assert "raw_context_inside" in filtered
    assert " raw_space_inside" in filtered
    assert "-raw_minus_inside" in filtered
    assert "+raw_plus_inside" in filtered
    assert "</enclosing_scopes>" in filtered
    assert " outside_context_line_after" in filtered


def test_filter_diff_additions_only_without_context_block_keeps_context_strips_deletions() -> None:
    """filter_diff_additions_only must keep context lines and strip deletion lines."""
    instance = make_git_utils()
    diff = "\n".join([
        "diff --git a/src/app.py b/src/app.py",
        "+added_line",
        " context_line",
        "-removed_line",
    ])

    filtered = instance.filter_diff_additions_only(diff)

    assert "+added_line" in filtered
    assert "context_line" in filtered       # context lines are kept
    assert "removed_line" not in filtered   # deletions are stripped


def test_get_repo_root_name_and_branch() -> None:
    """It should resolve repository root, name, and branch with fallbacks."""
    instance = make_git_utils("/workspace/my-repo")
    calls: list[tuple[str, ...]] = []

    def fake_run(*args: str, **kwargs: object) -> str:
        calls.append(args)
        if args == ("rev-parse", "--show-toplevel"):
            return "/workspace/my-repo\n"
        if args == ("rev-parse", "--abbrev-ref", "HEAD"):
            return "feature/local\n"
        return ""

    instance._run_git = fake_run  # type: ignore[method-assign]
    assert instance.get_repo_root() == "/workspace/my-repo"
    assert instance.get_repo_name() == "my-repo"
    assert instance.get_current_branch() == "feature/local"

    # Detached HEAD fallback
    def fake_detached(*args: str, **kwargs: object) -> str:
        if args == ("rev-parse", "--abbrev-ref", "HEAD"):
            return "HEAD\n"
        if args == ("rev-parse", "--short", "HEAD"):
            return "abc1234\n"
        raise GitError("err")

    instance._run_git = fake_detached  # type: ignore[method-assign]
    assert instance.get_repo_root() == "/workspace/my-repo"
    assert instance.get_current_branch() == "abc1234"


def test_parse_name_status_and_get_local_changed_files() -> None:
    """It should parse git name-status output for working tree, staged, branch, and commit."""
    raw_status = "\n".join([
        "M\tsrc/app.py",
        "A\tsrc/new.py",
        "D\tsrc/old.py",
        "R100\tsrc/before.py\tsrc/after.py",
    ])
    parsed = GitUtils._parse_name_status(raw_status)
    assert parsed == [
        {"path": "/src/app.py", "change_type": "edit", "original_path": "/src/app.py"},
        {"path": "/src/new.py", "change_type": "add", "original_path": "/src/new.py"},
        {"path": "/src/old.py", "change_type": "delete", "original_path": "/src/old.py"},
        {"path": "/src/after.py", "change_type": "rename", "original_path": "/src/before.py"},
    ]

    instance = make_git_utils("/repo")

    def fake_git(*args: str, **kwargs: object) -> str:
        if args == ("diff", "--name-status", "-M", "HEAD"):
            return "M\tsrc/app.py\n"
        if args == ("ls-files", "--others", "--exclude-standard"):
            return "src/untracked.py\n"
        if args == ("diff", "--cached", "--name-status", "-M"):
            return "A\tsrc/staged.py\n"
        if args == ("diff", "--name-status", "-M", "main", "HEAD"):
            return "M\tsrc/branch.py\n"
        if args == ("diff", "--name-status", "-M", "abc~1", "abc"):
            return "M\tsrc/commit.py\n"
        return ""

    instance._run_git = fake_git  # type: ignore[method-assign]

    wt_files = instance.get_local_changed_files()
    assert wt_files == [
        {"path": "/src/app.py", "change_type": "edit", "original_path": "/src/app.py"},
        {"path": "/src/untracked.py", "change_type": "add", "original_path": "/src/untracked.py"},
    ]

    staged_files = instance.get_local_changed_files(staged=True)
    assert staged_files == [
        {"path": "/src/staged.py", "change_type": "add", "original_path": "/src/staged.py"},
    ]

    branch_files = instance.get_local_changed_files(from_ref="main")
    assert branch_files == [
        {"path": "/src/branch.py", "change_type": "edit", "original_path": "/src/branch.py"},
    ]

    commit_files = instance.get_local_changed_files(commit="abc")
    assert commit_files == [
        {"path": "/src/commit.py", "change_type": "edit", "original_path": "/src/commit.py"},
    ]


def test_get_local_diff_diff_only_and_full_code(tmp_path) -> None:
    """It should build unified diffs with AST context or full_code diffs and apply filters."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    src_dir = repo_dir / "src"
    src_dir.mkdir()
    app_file = src_dir / "app.py"
    app_file.write_text("def greet():\n    return 'hello world'\n", encoding="utf-8")

    instance = make_git_utils(str(repo_dir))
    instance.get_repo_root = lambda: str(repo_dir)  # type: ignore[method-assign]
    instance.get_local_changed_files = lambda **kwargs: [  # type: ignore[method-assign]
        {"path": "/src/app.py", "change_type": "edit", "original_path": "/src/app.py"},
        {"path": "/src/deleted.py", "change_type": "delete", "original_path": "/src/deleted.py"},
        {"path": "/libs/gen.py", "change_type": "add", "original_path": "/libs/gen.py"},
        {"path": "/docs/readme.md", "change_type": "add", "original_path": "/docs/readme.md"},
    ]

    def fake_git(*args: str, **kwargs: object) -> str:
        if args == ("show", "HEAD:src/app.py"):
            return "def greet():\n    return 'old'\n"
        return ""

    instance._run_git = fake_git  # type: ignore[method-assign]

    diff_only = instance.get_local_diff(
        review_scope="diff_only",
        excluded_paths=["libs/"],
        file_extensions_filter=[".py"],
    )
    assert "diff --git a/src/app.py b/src/app.py" in diff_only
    assert "+    return 'hello world'" in diff_only
    assert "libs/gen.py" not in diff_only
    assert "docs/readme.md" not in diff_only
    assert ("<enclosing_scopes" in diff_only) or ("<adaptive_diff" in diff_only)

    full_code = instance.get_local_diff(
        review_scope="full_code",
        excluded_paths=["libs/"],
        file_extensions_filter=[".py"],
    )
    assert "@@ Change type: edit @@" in full_code
    assert "+def greet():" in full_code
    assert "+    return 'hello world'" in full_code


def test_filter_and_truncate_preserve_referenced_symbols_block() -> None:
    """filter_diff_additions_only and truncate_diff_per_file must preserve <referenced_symbols>."""
    instance = make_git_utils()
    diff = "\n".join([
        "diff --git a/src/app.py b/src/app.py",
        "--- a/src/app.py",
        "+++ b/src/app.py",
        "@@ -1,2 +1,4 @@",
        "-deleted_line",
        "+line_1",
        "+line_2",
        "+line_3",
        '<enclosing_scopes file="src/app.py">',
        "   1: def run():",
        "</enclosing_scopes>",
        '<referenced_symbols file="src/app.py">',
        "<!-- from src/dto.py: class_definition OrderDto (lines 1–2) -->",
        "   1: class OrderDto:",
        "-keep_inside_referenced_symbols",
        "</referenced_symbols>",
    ])

    filtered = instance.filter_diff_additions_only(diff)
    assert "-deleted_line" not in filtered
    assert '<referenced_symbols file="src/app.py">' in filtered
    assert "-keep_inside_referenced_symbols" in filtered
    assert "</referenced_symbols>" in filtered

    truncated, changed = instance.truncate_diff_per_file(filtered, max_lines=5)
    assert changed is True
    assert '<referenced_symbols file="src/app.py">' in truncated
    assert truncated.rstrip().endswith("</referenced_symbols>")


def test_get_local_diff_resolves_cross_file_referenced_symbols(tmp_path) -> None:
    """get_local_diff should resolve symbols from unchanged local relative-imported files."""
    repo_dir = tmp_path / "repo"
    src_dir = repo_dir / "src"
    src_dir.mkdir(parents=True)

    (src_dir / "models.py").write_text(
        "class InvoicePayload:\n    invoice_id: str\n    amount: float\n",
        encoding="utf-8",
    )
    (src_dir / "service.py").write_text(
        "from .models import InvoicePayload\n\ndef process(p: InvoicePayload) -> bool:\n    return p.amount > 0\n",
        encoding="utf-8",
    )

    instance = make_git_utils(str(repo_dir))
    instance.get_repo_root = lambda: str(repo_dir)  # type: ignore[method-assign]
    instance.get_local_changed_files = lambda **kwargs: [  # type: ignore[method-assign]
        {"path": "/src/service.py", "change_type": "edit", "original_path": "/src/service.py"},
    ]
    instance._run_git = lambda *args, **kwargs: (  # type: ignore[method-assign]
        "def process(p):\n    return False\n"
        if args == ("show", "HEAD:src/service.py")
        else ""
    )

    diff = instance.get_local_diff(review_scope="diff_only")
    assert '<referenced_symbols file="/src/service.py">' in diff
    assert "InvoicePayload" in diff
    assert "from /src/models.py" in diff



