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
    instance = GitUtils.__new__(GitUtils)
    instance.repo_path = repo_path
    return instance


def test_init_sets_repo_path_and_validates(mocker) -> None:
    """It should initialize the repository path and validate the repo."""
    validate = mocker.patch("src.git_utils.GitUtils._validate_repo")

    instance = GitUtils.__new__(GitUtils)
    GitUtils.__init__(instance, "/tmp/repo")

    assert instance.repo_path == "/tmp/repo"
    validate.assert_called_once_with()


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


def test_limit_filter_and_truncate_helpers(sample_diff: str) -> None:
    """It should limit files and truncate oversized sections."""
    instance = make_git_utils()

    limited, was_limited, omitted = instance.limit_diff_files(sample_diff, max_files=1)
    assert was_limited is True
    assert omitted == 1
    assert "TRUNCATED" in limited

    truncated, changed = instance.truncate_diff_per_file(sample_diff, max_lines=3)
    assert changed is True
    assert "TRUNCATED IN THIS FILE" in truncated


def test_truncate_diff_without_sections_and_summary(sample_diff: str) -> None:
    """It should truncate plain text diffs and summarize changed files."""
    instance = make_git_utils()
    plain_diff = "\n".join(f"line-{i}" for i in range(6))
    truncated, was_truncated = instance.truncate_diff(plain_diff, max_lines=3)
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


