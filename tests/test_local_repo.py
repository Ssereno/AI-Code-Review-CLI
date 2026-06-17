"""Tests for managed local repository helpers."""

from __future__ import annotations

import json
from types import SimpleNamespace

from src.config import ReviewConfig
from src.local_repo import LocalRepoContext, LocalRepoManager


def _completed(stdout: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=0, stdout=stdout, stderr="")


def test_ensure_repo_available_reuses_existing_managed_clone(mocker, tmp_path) -> None:
    """Existing managed clone folders should be validated and reused."""
    repo_path = tmp_path / ".ai-review" / "repos" / "repo-a-repo-id-1234"
    repo_path.mkdir(parents=True)
    run = mocker.patch("src.local_repo.subprocess.run", return_value=_completed(".git"))
    config = ReviewConfig(tfs_local_clone_root=str(tmp_path / ".ai-review" / "repos"))

    result = LocalRepoManager(config).ensure_repo_available(
        repository_name="repo-a",
        repository_id="repo-id-1234567890",
        clone_url="https://example/repo-a",
    )

    assert result.path == str(repo_path)
    assert result.managed is True
    assert result.updated is True
    assert result.cloned is False
    assert run.call_args.args[0][:3] == ["git", "rev-parse", "--git-dir"]


def test_ensure_repo_available_clones_missing_repo_with_pat(mocker, tmp_path) -> None:
    """Missing managed clones should be created with PAT-backed git auth."""
    run = mocker.patch("src.local_repo.subprocess.run", return_value=_completed())
    config = ReviewConfig(
        tfs_pat="token",
        tfs_local_clone_root=str(tmp_path / ".ai-review" / "repos"),
    )

    result = LocalRepoManager(config).ensure_repo_available(
        repository_name="Repo A",
        repository_id="",
        clone_url="https://example/repo-a",
    )

    assert result.cloned is True
    assert result.managed is True
    cmd = run.call_args.args[0]
    assert cmd[0] == "git"
    assert "http.extraHeader=Authorization: Basic" in cmd[2]
    assert cmd[-3:] == ["https://example/repo-a", result.path, "--quiet"]


def test_map_repo_json_returns_eligible_structure(mocker) -> None:
    """Repository maps should include eligible files and derived directories."""
    config = ReviewConfig(
        project_context_file_extensions=[".py"],
        project_context_exclude_patterns=["build", "*.lock"],
    )
    context = LocalRepoContext("C:/repo-a", config)
    mocker.patch.object(
        context,
        "_run_git",
        return_value="\n".join([
            "100644 blob abc 12\tsrc/app.py",
            "100644 blob def 20\tsrc/nested/helper.py",
            "100644 blob ghi 30\tbuild/out.py",
            "100644 blob jkl 40\tpoetry.lock",
        ]),
    )

    payload = json.loads(context.map_repo_json("repo-a", "feature/test", include_all_files=False))

    assert payload["repository"] == "repo-a"
    assert payload["ref"] == "origin/feature/test"
    assert payload["directories"] == ["src", "src/nested"]
    assert [item["path"] for item in payload["files"]] == [
        "src/app.py",
        "src/nested/helper.py",
    ]


def test_map_repo_json_includes_all_files_by_default(mocker) -> None:
    """Repository maps for context selection should not hide excluded paths."""
    config = ReviewConfig(
        project_context_file_extensions=[".py"],
        project_context_exclude_patterns=["build", "*.lock"],
    )
    context = LocalRepoContext("C:/repo-a", config)
    mocker.patch.object(
        context,
        "_run_git",
        return_value="\n".join([
            "100644 blob abc 12\tsrc/app.py",
            "100644 blob def 20\tbuild/out.py",
            "100644 blob ghi 30\tpoetry.lock",
        ]),
    )

    payload = json.loads(context.map_repo_json("repo-a", "feature/test"))

    assert [item["path"] for item in payload["files"]] == [
        "build/out.py",
        "poetry.lock",
        "src/app.py",
    ]


def test_get_files_context_reads_only_eligible_requested_paths(mocker) -> None:
    """Requested context should reject unknown paths and read from the source ref."""
    config = ReviewConfig(project_context_file_extensions=[".py"])
    context = LocalRepoContext("C:/repo-a", config)
    mocker.patch.object(
        context,
        "_eligible_paths",
        return_value=[
            {"path": "src/app.py"},
            {"path": "src/helper.py"},
        ],
    )
    show = mocker.patch.object(context, "_show_file", return_value="print('ok')\n")

    rendered = context.get_files_context(
        "origin/feature/test",
        ["../secret.txt", "/src/app.py", "missing.py"],
        max_files=5,
    )

    assert "#### /src/app.py" in rendered
    assert "missing.py" not in rendered
    show.assert_called_once_with("origin/feature/test", "src/app.py")


def test_changed_files_context_includes_excluded_and_filtered_files(mocker) -> None:
    """Changed PR files should be available even if normal context rules exclude them."""
    config = ReviewConfig(
        project_context_file_extensions=[".py"],
        project_context_exclude_patterns=["build", "*.lock"],
    )
    context = LocalRepoContext("C:/repo-a", config)
    mocker.patch.object(
        context,
        "_eligible_paths",
        return_value=[
            {"path": "build/out.lock"},
            {"path": "docs/spec.md"},
        ],
    )
    show = mocker.patch.object(
        context,
        "_show_file",
        side_effect=lambda _ref, path: f"contents of {path}",
    )

    rendered = context.get_changed_files_context(
        "feature/test",
        [
            {"path": "/build/out.lock", "change_type": "edit"},
            {"path": "/docs/spec.md", "change_type": "edit"},
        ],
        max_chars=0,
        file_max_chars=0,
    )

    assert "#### /build/out.lock" in rendered
    assert "contents of build/out.lock" in rendered
    assert "#### /docs/spec.md" in rendered
    assert show.call_count == 2
