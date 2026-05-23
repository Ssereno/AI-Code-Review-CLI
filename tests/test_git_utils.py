"""Tests for the Git utilities module."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.git_utils import GitError, GitUtils


def make_git_utils(repo_path: str = "repo") -> GitUtils:
    """Build a GitUtils instance without running repository validation."""
    instance = GitUtils.__new__(GitUtils)
    instance.repo_path = repo_path
    instance._http_auth_header = ""
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


def test_get_repo_name_and_remote_url_fallbacks() -> None:
    """It should derive the repo name and fall back when remote lookup fails."""
    instance = make_git_utils(str(Path("/tmp/repo-name")))
    instance._run_git = lambda *args, **kwargs: "https://example.com/org/project.git\n"
    assert instance.get_repo_name() == "project"
    assert instance.get_remote_url() == "https://example.com/org/project.git"

    def raise_git(*args: str, **kwargs: object) -> str:
        raise GitError("missing")

    instance._run_git = raise_git
    assert instance.get_repo_name() == "repo-name"
    assert instance.get_remote_url() == "(no remote configured)"


def test_list_branches_filters_head_entries() -> None:
    """It should strip branch markers and ignore HEAD aliases."""
    instance = make_git_utils()
    instance._run_git = lambda *args, **kwargs: "* main\n  feature\n  origin/HEAD -> origin/main\n"

    assert instance.list_branches(remote=True) == ["main", "feature"]


def test_get_recent_commits_parses_log_entries() -> None:
    """It should parse git log output into structured commit dictionaries."""
    instance = make_git_utils()
    instance._run_git = lambda *args, **kwargs: "hash|short|Alice|2024-01-01|Initial commit\n\n"

    commits = instance.get_recent_commits(count=1)

    assert commits == [{
        "hash": "hash",
        "short_hash": "short",
        "author": "Alice",
        "date": "2024-01-01",
        "message": "Initial commit",
    }]


@pytest.mark.parametrize(
    ("method_name", "expected_fragment"),
    [
        ("get_staged_diff", "No staged changes"),
        ("get_working_diff", "No changes in working directory"),
        ("get_commit_diff", "contains no code changes"),
    ],
)
def test_diff_methods_raise_on_empty_output(method_name: str, expected_fragment: str) -> None:
    """It should raise helpful errors when git diff output is empty."""
    instance = make_git_utils()
    instance._run_git = lambda *args, **kwargs: "\n"

    with pytest.raises(GitError, match=expected_fragment):
        if method_name == "get_commit_diff":
            getattr(instance, method_name)("abc123")
        else:
            getattr(instance, method_name)()


def test_get_all_changes_diff_combines_sections() -> None:
    """It should combine staged and unstaged changes when both exist."""
    instance = make_git_utils()

    def fake_run(*args: str, **kwargs: object) -> str:
        if args[:2] == ("diff", "--cached"):
            return "staged"
        if args[:1] == ("diff",):
            return "unstaged"
        return ""

    instance._run_git = fake_run

    combined = instance.get_all_changes_diff()

    assert "STAGED CHANGES" in combined
    assert "WORKING DIRECTORY CHANGES" in combined


def test_get_all_changes_diff_raises_when_both_sections_are_empty() -> None:
    """It should raise when there are no staged or unstaged changes."""
    instance = make_git_utils()
    instance._run_git = lambda *args, **kwargs: ""

    with pytest.raises(GitError, match="No changes"):
        instance.get_all_changes_diff()


def test_get_commit_range_diff_and_file_diff() -> None:
    """It should return populated diffs for commit ranges and individual files."""
    instance = make_git_utils()
    instance._run_git = lambda *args, **kwargs: "diff output"

    assert instance.get_commit_range_diff("a", "b") == "diff output"
    assert instance.get_file_diff("src/app.py", staged=True) == "diff output"


def test_get_branch_diff_uses_merge_base_and_fallback() -> None:
    """It should use merge-base when available and fall back to direct diff otherwise."""
    instance = make_git_utils()
    calls: list[tuple[str, ...]] = []

    def fake_run(*args: str, **kwargs: object) -> str:
        calls.append(args)
        if args[0] == "merge-base":
            return "base-hash"
        if args[0] == "diff":
            return "branch diff"
        if args[0] == "branch":
            return "main"
        return ""

    instance._run_git = fake_run
    assert instance.get_branch_diff("feature") == "branch diff"
    assert any(call[0] == "merge-base" for call in calls)

    def fallback_run(*args: str, **kwargs: object) -> str:
        if args[0] == "merge-base":
            raise GitError("boom")
        if args[0] == "diff":
            return "direct diff"
        return "main"

    instance._run_git = fallback_run
    assert instance.get_branch_diff("feature", "main") == "direct diff"


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
    """It should limit files, filter by extension and truncate oversized sections."""
    instance = make_git_utils()

    limited, was_limited, omitted = instance.limit_diff_files(sample_diff, max_files=1)
    assert was_limited is True
    assert omitted == 1
    assert "TRUNCATED" in limited

    filtered = instance.filter_diff_by_extensions(sample_diff, [".py"])
    assert "src/app.py" in filtered
    assert "docs/readme.md" not in filtered

    with pytest.raises(GitError, match="no changes remain"):
        instance.filter_diff_by_extensions(sample_diff, [".js"])

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


# ==============================================================
# PR-specific git operations — new method tests
# ==============================================================

def test_fetch_merge_commit_calls_git_with_correct_args(mocker) -> None:
    """It should strip 'origin/' and call git fetch origin <branch> --quiet."""
    instance = make_git_utils()
    run_mock = mocker.patch.object(instance, "_run_git", return_value="")

    instance.fetch_merge_commit("origin/feature/my-branch")

    run_mock.assert_called_once_with("fetch", "origin", "feature/my-branch", "--quiet")


def test_fetch_merge_commit_without_origin_prefix(mocker) -> None:
    """It should pass the branch name unchanged when it has no 'origin/' prefix."""
    instance = make_git_utils()
    run_mock = mocker.patch.object(instance, "_run_git", return_value="")

    instance.fetch_merge_commit("feature/my-branch")

    run_mock.assert_called_once_with("fetch", "origin", "feature/my-branch", "--quiet")


def test_get_pr_diff_uses_three_dot_syntax(mocker) -> None:
    """It should pass origin/<target>...origin/<source> to git diff."""
    instance = make_git_utils()
    run_mock = mocker.patch.object(instance, "_run_git", return_value="diff output")

    result = instance.get_pr_diff("origin/main", "origin/feature/x")

    run_mock.assert_called_once_with("diff", "origin/main...origin/feature/x", "--no-color", check=False)
    assert result == "diff output"


def test_get_pr_diff_normalises_branch_without_origin_prefix(mocker) -> None:
    """It should prepend origin/ to both branches when they lack it."""
    instance = make_git_utils()
    run_mock = mocker.patch.object(instance, "_run_git", return_value="diff output")

    instance.get_pr_diff("main", "feature/x")

    run_mock.assert_called_once_with("diff", "origin/main...origin/feature/x", "--no-color", check=False)


def test_get_pr_diff_does_not_double_prefix_origin(mocker) -> None:
    """It should NOT produce origin/origin/... when inputs already have the prefix."""
    instance = make_git_utils()
    run_mock = mocker.patch.object(instance, "_run_git", return_value="")

    instance.get_pr_diff("origin/main", "origin/feature/my-branch")

    call_args = run_mock.call_args.args
    ref_arg = call_args[1]
    assert ref_arg == "origin/main...origin/feature/my-branch"
    assert "origin/origin/" not in ref_arg


def test_filter_diff_noise_keeps_normal_py_section() -> None:
    """Normal .py sections should survive filtering."""
    instance = make_git_utils()
    diff = "\n".join([
        "diff --git a/src/app.py b/src/app.py",
        "--- a/src/app.py",
        "+++ b/src/app.py",
        "@@ -1 +1 @@",
        "-old",
        "+new",
    ])

    result = instance.filter_diff_noise(diff)

    assert "src/app.py" in result
    assert "+new" in result


def test_filter_diff_noise_removes_binary_section() -> None:
    """Sections containing 'Binary files' should be dropped."""
    instance = make_git_utils()
    diff = "\n".join([
        "diff --git a/assets/logo.png b/assets/logo.png",
        "index 000..abc 100644",
        "Binary files a/assets/logo.png and b/assets/logo.png differ",
        "diff --git a/src/app.py b/src/app.py",
        "--- a/src/app.py",
        "+++ b/src/app.py",
        "@@ -1 +1 @@",
        "+new",
    ])

    result = instance.filter_diff_noise(diff)

    assert "logo.png" not in result
    assert "src/app.py" in result


def test_filter_diff_noise_removes_lock_file_section() -> None:
    """poetry.lock and other lock files should be dropped."""
    instance = make_git_utils()
    diff = "\n".join([
        "diff --git a/poetry.lock b/poetry.lock",
        "--- a/poetry.lock",
        "+++ b/poetry.lock",
        "@@ -1 +1 @@",
        "+updated",
        "diff --git a/src/app.py b/src/app.py",
        "--- a/src/app.py",
        "+++ b/src/app.py",
        "@@ -1 +1 @@",
        "+new",
    ])

    result = instance.filter_diff_noise(diff)

    assert "poetry.lock" not in result
    assert "src/app.py" in result


def test_filter_diff_noise_removes_oversized_section() -> None:
    """Sections exceeding max_lines_per_file should be dropped."""
    instance = make_git_utils()
    big_section = ["diff --git a/generated.py b/generated.py"] + [f"+line{i}" for i in range(15)]
    small_section = [
        "diff --git a/src/app.py b/src/app.py",
        "+new",
    ]
    diff = "\n".join(big_section + small_section)

    result = instance.filter_diff_noise(diff, max_lines_per_file=10)

    assert "generated.py" not in result
    assert "src/app.py" in result


@pytest.mark.parametrize("lock_name", ["package-lock.json", "yarn.lock", "Pipfile.lock", "custom.lock"])
def test_filter_diff_noise_removes_various_lock_files(lock_name: str) -> None:
    """All recognised lock file names (and *.lock) should be filtered out."""
    instance = make_git_utils()
    diff = "\n".join([
        f"diff --git a/{lock_name} b/{lock_name}",
        f"--- a/{lock_name}",
        f"+++ b/{lock_name}",
        "@@ -1 +1 @@",
        "+updated",
    ])

    result = instance.filter_diff_noise(diff)

    assert lock_name not in result