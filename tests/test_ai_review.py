# License: Apache-2.0
# Copyright 2026 Sérgio Sereno

"""Tests for the AI review CLI workflow."""

from __future__ import annotations

import argparse
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src import ai_review


def make_args(**overrides: object) -> argparse.Namespace:
    """Create a namespace mirroring parsed CLI arguments."""
    base = {
        "command": "pr-review",
        "config": None,
        "verbosity": None,
        "model": None,
        "provider": None,
        "max_files_per_bundle": None,
        "output_format": None,
        "output": "",
        "no_color": False,
        "dry_run": False,
        "auto_post": False,
        "context": "",
        "repo_name": None,
        "pr_id": 123,
        "author": None,
        "target_branch": None,
        "status": "active",
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def test_get_spinner_frames_falls_back_for_cp1252(mocker) -> None:
    """It should use ASCII spinner frames on consoles that cannot encode braille."""
    mocker.patch("src.ai_review.sys.stdout", new=SimpleNamespace(encoding="cp1252"))

    assert ai_review._get_spinner_frames() == ["|", "/", "-", "\\"]


def test_get_spinner_frames_keeps_unicode_for_utf8(mocker) -> None:
    """It should keep the richer spinner when the console supports it."""
    mocker.patch("src.ai_review.sys.stdout", new=SimpleNamespace(encoding="utf-8"))

    assert ai_review._get_spinner_frames()[0] == "⠋"


def test_ensure_project_root_on_path_inserts_parent_directory(mocker) -> None:
    """It should add the repository root instead of the src directory itself."""
    import os

    fake_sys = SimpleNamespace(path=[])
    mocker.patch("src.ai_review.sys", new=fake_sys)

    fake_script = os.path.join("repo", "src", "ai_review.py")
    expected_root = os.path.abspath(os.path.join(fake_script, "..", ".."))

    project_root = ai_review._ensure_project_root_on_path(fake_script)

    assert project_root == expected_root
    assert fake_sys.path == [expected_root]


def test_configure_console_streams_reconfigures_non_utf8_streams(mocker) -> None:
    """It should switch console streams to UTF-8 when possible."""
    stdout = SimpleNamespace(encoding="cp1252", reconfigure=MagicMock())
    stderr = SimpleNamespace(encoding="cp1252", reconfigure=MagicMock())
    fake_sys = SimpleNamespace(stdout=stdout, stderr=stderr)
    mocker.patch("src.ai_review.sys", new=fake_sys)

    ai_review._configure_console_streams()

    stdout.reconfigure.assert_called_once_with(encoding="utf-8", errors="replace")
    stderr.reconfigure.assert_called_once_with(encoding="utf-8", errors="replace")


def test_build_parser_parses_pr_review_options() -> None:
    """It should parse the main PR review command and global options."""
    parser = ai_review.build_parser()

    args = parser.parse_args([
        "pr-review",
        "42",
        "--dry-run",
        "--security",
        "--max-files-per-bundle",
        "3",
        "--output",
        "review.md",
    ])

    assert args.command == "pr-review"
    assert args.pr_id == 42
    assert args.dry_run is True
    assert args.verbosity == "security"
    assert args.max_files_per_bundle == 3
    assert args.output == "review.md"


def test_build_parser_applies_global_options_to_list_prs() -> None:
    """It should accept shared CLI options on the list-prs command too."""
    parser = ai_review.build_parser()

    args = parser.parse_args([
        "list-prs",
        "--config",
        "alt-config.yaml",
        "--no-color",
        "--format",
        "markdown",
    ])

    assert args.command == "list-prs"
    assert args.config == "alt-config.yaml"
    assert args.no_color is True
    assert args.output_format == "markdown"


def test_build_parser_parses_local_review_options() -> None:
    """It should parse the local review command and its target flags."""
    parser = ai_review.build_parser()

    args = parser.parse_args([
        "local",
        "--from",
        "main",
        "--to",
        "feature/x",
        "--security",
        "--output",
        "local.md",
    ])

    assert args.command == "local"
    assert args.from_ref == "main"
    assert args.to_ref == "feature/x"
    assert args.verbosity == "security"
    assert args.output == "local.md"


def test_run_review_dispatches_to_local_workflow(mocker, review_config) -> None:
    """It should route the local command to run_local_review_workflow."""
    mocker.patch("src.ai_review.ReviewConfig.load", return_value=review_config)
    workflow = mocker.patch("src.ai_review.run_local_review_workflow", return_value=0)
    mocker.patch("src.ai_review.ReviewFormatter")

    result = ai_review.run_review(make_args(command="local"))

    assert result == 0
    workflow.assert_called_once()


def test_build_parser_and_run_full_code_scan_workflow(mocker, review_config) -> None:
    """It should parse full-code-scan arguments and execute the static scan workflow."""
    parser = ai_review.build_parser()
    parsed = parser.parse_args([
        "full-code-scan",
        "src/",
        "tests/*.py",
        "--security",
        "--output",
        "scan.md",
    ])
    assert parsed.command == "full-code-scan"
    assert parsed.paths == ["src/", "tests/*.py"]
    assert parsed.verbosity == "security"
    assert parsed.output == "scan.md"

    formatter = MagicMock()
    formatter.format_progress.side_effect = lambda msg: msg
    formatter.format_local_details.return_value = "SCAN DETAILS"
    formatter.format_info.side_effect = lambda msg: msg
    formatter.format_structured_comments.return_value = "SCAN COMMENTS"
    formatter.format_warning.side_effect = lambda msg: msg
    formatter.format_success.side_effect = lambda msg: msg

    diff = "diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n@@ -0,0 +1,1 @@\n+x = 1"
    git_utils = MagicMock()
    git_utils.repo_path = "/workspace/my-repo"
    git_utils.get_repo_name.return_value = "my-repo"
    git_utils.get_current_branch.return_value = "main"
    git_utils.get_scan_diff.return_value = (
        diff,
        [{"path": "/src/a.py", "abs_path": "/workspace/my-repo/src/a.py", "change_type": "scan"}],
    )
    git_utils.filter_diff_additions_only.return_value = diff
    git_utils.truncate_diff_per_file.return_value = (diff, False)
    git_utils.bundle_diff_files.return_value = [{
        "diff": diff,
        "files_summary": [{"file": "src/a.py", "additions": 1, "deletions": 0}],
    }]

    llm = MagicMock()
    llm.review_pr.return_value = (
        "Scan summary",
        [{"file": "src/a.py", "line": 1, "type": "security", "comment": "issue"}],
    )

    markdown_formatter = MagicMock()
    markdown_formatter.format_header.return_value = "HEADER"
    markdown_formatter.format_structured_comments.return_value = "BODY"
    markdown_formatter.format_footer.return_value = "FOOTER"

    mocker.patch("src.ai_review.ProgressIndicator")
    mocker.patch("src.ai_review.GitUtils", return_value=git_utils)
    mocker.patch("src.ai_review.LLMClient", return_value=llm)
    save_output = mocker.patch("src.ai_review.save_output")
    mocker.patch(
        "src.ai_review.ReviewFormatter",
        side_effect=lambda *args, **kwargs: (
            markdown_formatter if kwargs.get("output_format") == "markdown" else formatter
        ),
    )
    print_mock = mocker.patch("builtins.print")

    args = make_args(
        command="full-code-scan",
        paths=["src/"],
        repo_path=None,
        output="scan.md",
    )
    result = ai_review.run_full_code_scan_workflow(args, review_config, formatter)

    assert result == 0
    git_utils.get_scan_diff.assert_called_once()
    llm.review_pr.assert_called_once_with(
        diff=diff,
        files_summary=[{"file": "src/a.py", "additions": 1, "deletions": 0}],
        context="",
        full_code_scan=True,
    )
    save_output.assert_called_once()
    print_mock.assert_any_call("SCAN COMMENTS")


def test_run_local_review_workflow_completes_and_saves_output(mocker, review_config) -> None:
    """It should inspect local Git changes, run LLM review, and save output without TFS."""
    formatter = MagicMock()
    formatter.format_progress.side_effect = lambda msg: msg
    formatter.format_local_details.return_value = "LOCAL DETAILS"
    formatter.format_info.side_effect = lambda msg: msg
    formatter.format_structured_comments.return_value = "LOCAL COMMENTS"
    formatter.format_warning.side_effect = lambda msg: msg
    formatter.format_success.side_effect = lambda msg: msg

    diff = "diff --git a/src/a.py b/src/a.py\n+++ b/src/a.py\n@@ -0,0 +1 @@\n+print('local')"
    git_utils = MagicMock()
    git_utils.get_repo_name.return_value = "my-repo"
    git_utils.get_current_branch.return_value = "feature/local"
    git_utils.get_local_changed_files.return_value = [
        {"path": "/src/a.py", "change_type": "edit", "original_path": "/src/a.py"}
    ]
    git_utils.get_local_diff.return_value = diff
    git_utils.filter_diff_additions_only.return_value = diff
    git_utils.truncate_diff_per_file.return_value = (diff, False)
    git_utils.bundle_diff_files.return_value = [{
        "diff": diff,
        "files_summary": [{"file": "src/a.py", "additions": 1, "deletions": 0}],
    }]

    llm = MagicMock()
    llm.review_pr.return_value = (
        "Local review summary",
        [{"file": "src/a.py", "line": 1, "type": "suggestion", "comment": "check"}],
    )

    markdown_formatter = MagicMock()
    markdown_formatter.format_header.return_value = "HEADER"
    markdown_formatter.format_structured_comments.return_value = "BODY"
    markdown_formatter.format_footer.return_value = "FOOTER"

    mocker.patch("src.ai_review.ProgressIndicator")
    mocker.patch("src.ai_review.GitUtils", return_value=git_utils)
    mocker.patch("src.ai_review.LLMClient", return_value=llm)
    save_output = mocker.patch("src.ai_review.save_output")
    mocker.patch(
        "src.ai_review.ReviewFormatter",
        side_effect=lambda *args, **kwargs: (
            markdown_formatter if kwargs.get("output_format") == "markdown" else formatter
        ),
    )
    print_mock = mocker.patch("builtins.print")

    args = make_args(
        command="local",
        staged=False,
        from_ref="main",
        to_ref=None,
        commit=None,
        repo_path=None,
        output="local_review.md",
    )
    result = ai_review.run_local_review_workflow(args, review_config, formatter)

    assert result == 0
    git_utils.get_local_diff.assert_called_once()
    llm.review_pr.assert_called_once()
    save_output.assert_called_once()
    print_mock.assert_any_call("LOCAL COMMENTS")


def test_run_review_returns_error_for_invalid_config(mocker, review_config) -> None:
    """It should stop before command dispatch when configuration validation fails."""
    formatter = MagicMock()
    formatter.format_error.side_effect = lambda message: f"ERR:{message}"
    config = review_config
    config.validate = MagicMock(return_value=["missing api key"])
    mocker.patch("src.ai_review.ReviewConfig.load", return_value=config)
    formatter_class = mocker.patch("src.ai_review.ReviewFormatter", return_value=formatter)
    print_mock = mocker.patch("builtins.print")

    result = ai_review.run_review(make_args(command="pr-review"))

    assert result == 1
    formatter_class.assert_called_once_with(color=config.color_output)
    print_mock.assert_any_call("ERR:missing api key")


def test_run_review_dispatches_to_pr_workflow(mocker, review_config) -> None:
    """It should forward PR review commands with CLI overrides applied."""
    mocker.patch("src.ai_review.ReviewConfig.load", return_value=review_config)
    workflow = mocker.patch("src.ai_review.run_pr_review_workflow", return_value=7)
    formatter_class = mocker.patch("src.ai_review.ReviewFormatter")

    result = ai_review.run_review(make_args(verbosity="security", model="gpt-4.1"))

    assert result == 7
    assert review_config.verbosity == "security"
    assert review_config.model == "gpt-4.1"
    formatter_class.assert_called_once_with(
        color=review_config.color_output,
        output_format=review_config.output_format,
    )
    workflow.assert_called_once()


def test_run_review_dispatches_to_list_prs(mocker, review_config) -> None:
    """It should route the list command to the dedicated workflow."""
    mocker.patch("src.ai_review.ReviewConfig.load", return_value=review_config)
    workflow = mocker.patch("src.ai_review.run_list_prs", return_value=5)
    mocker.patch("src.ai_review.ReviewFormatter")

    result = ai_review.run_review(make_args(command="list-prs"))

    assert result == 5
    workflow.assert_called_once()


def test_run_pr_review_workflow_dry_run_saves_output(mocker, review_config) -> None:
    """It should persist the markdown output even when comments are not posted."""
    formatter = MagicMock()
    formatter.format_progress.side_effect = lambda message: f"PROGRESS:{message}"
    formatter.format_pr_details.return_value = "PR DETAILS"
    formatter.format_info.side_effect = lambda message: f"INFO:{message}"
    formatter.format_review.return_value = "REVIEW"
    formatter.format_structured_comments.return_value = "COMMENTS"
    formatter.format_warning.side_effect = lambda message: f"WARN:{message}"
    formatter.format_success.side_effect = lambda message: f"OK:{message}"

    tfs = MagicMock()
    tfs.get_pull_request_details.return_value = {
        "source_branch": "feature/test",
        "target_branch": "main",
    }
    diff = "diff --git a/a.py b/a.py\n+++ b/a.py\n@@ -0,0 +1 @@\n+print('x')"
    tfs.get_pull_request_diff.return_value = diff

    llm = MagicMock()
    llm.review_pr.return_value = (
        "General review",
        [{"file": "a.py", "line": 1, "type": "bug", "comment": "msg"}],
    )

    git_utils = MagicMock()
    git_utils.filter_diff_additions_only.return_value = diff
    git_utils.truncate_diff_per_file.return_value = (diff, False)
    git_utils.bundle_diff_files.return_value = [{
        "diff": diff,
        "files_summary": [{"file": "a.py", "additions": 1, "deletions": 0}],
    }]

    markdown_formatter = MagicMock()
    markdown_formatter.format_header.return_value = "HEADER"
    markdown_formatter.format_structured_comments.return_value = "BODY"
    markdown_formatter.format_footer.return_value = "FOOTER"

    mocker.patch("src.ai_review.ProgressIndicator")
    mocker.patch("src.ai_review.LLMClient", return_value=llm)
    mocker.patch("src.ai_review.GitUtils", return_value=git_utils)
    mocker.patch("src.tfs_client.TFSClient", return_value=tfs)
    mocker.patch("src.tfs_client.TFSError", new=Exception)
    save_output = mocker.patch("src.ai_review.save_output")
    mocker.patch(
        "src.ai_review.ReviewFormatter",
        side_effect=lambda *args, **kwargs: (
            markdown_formatter if kwargs.get("output_format") == "markdown" else formatter
        ),
    )
    print_mock = mocker.patch("builtins.print")

    result = ai_review.run_pr_review_workflow(
        make_args(dry_run=True, output="review.md", repo_name="repo-a"),
        review_config,
        formatter,
    )

    assert result == 0
    save_output.assert_called_once()
    tfs.post_review_comments.assert_not_called()
    print_mock.assert_any_call("COMMENTS")


def test_run_pr_review_workflow_returns_error_when_posting_fails(mocker, review_config) -> None:
    """It should surface posting failures after the AI review completes."""
    formatter = MagicMock()
    formatter.format_progress.side_effect = lambda message: message
    formatter.format_pr_details.return_value = "PR DETAILS"
    formatter.format_info.side_effect = lambda message: message
    formatter.format_review.return_value = "REVIEW"
    formatter.format_structured_comments.return_value = "COMMENTS"
    formatter.format_error.side_effect = lambda message: f"ERR:{message}"
    formatter.format_warning.side_effect = lambda message: f"WARN:{message}"
    formatter.format_success.side_effect = lambda message: f"OK:{message}"

    class FakeTFSError(Exception):
        """Local error type matching the workflow contract."""

    tfs = MagicMock()
    tfs.get_pull_request_details.return_value = {
        "source_branch": "feature/test",
        "target_branch": "main",
    }
    diff = "diff --git a/a.py b/a.py\n+++ b/a.py\n@@ -0,0 +1 @@\n+print('x')"
    tfs.get_pull_request_diff.return_value = diff
    tfs.post_review_comments.side_effect = FakeTFSError("boom")

    llm = MagicMock()
    structured_comments = [{"file": "a.py", "line": 1, "type": "bug", "comment": "msg"}]
    llm.review_pr.return_value = ("General review", structured_comments)

    git_utils = MagicMock()
    git_utils.filter_diff_additions_only.return_value = diff
    git_utils.truncate_diff_per_file.return_value = (diff, False)
    git_utils.bundle_diff_files.return_value = [{
        "diff": diff,
        "files_summary": [{"file": "a.py", "additions": 1, "deletions": 0}],
    }]

    progress = MagicMock()
    mocker.patch("src.ai_review.ProgressIndicator", return_value=progress)
    mocker.patch("src.ai_review.LLMClient", return_value=llm)
    mocker.patch("src.ai_review.GitUtils", return_value=git_utils)
    mocker.patch("src.ai_review._select_comments_to_post", return_value=structured_comments)
    mocker.patch("src.ai_review._save_review_output")
    mocker.patch("src.tfs_client.TFSClient", return_value=tfs)
    mocker.patch("src.tfs_client.TFSError", new=FakeTFSError)
    print_mock = mocker.patch("builtins.print")

    result = ai_review.run_pr_review_workflow(
        make_args(repo_name="repo-a"),
        review_config,
        formatter,
    )

    assert result == 1
    progress.stop.assert_called()
    print_mock.assert_any_call("ERR:Error posting comments: boom")


def test_execute_bundled_review_merges_multiple_bundles_and_tokens() -> None:
    """It should review each bundle separately and aggregate summaries, comments, and tokens."""
    from src.token_estimator import TokenEstimate

    llm = MagicMock()
    llm.config.get_effective_model.return_value = "gpt-4o"

    def fake_review_pr(diff: str, files_summary: list[dict], **kwargs: object) -> tuple[str, list[dict]]:
        file_name = files_summary[0]["file"]
        llm.last_token_estimate = TokenEstimate(total_prompt_tokens=150, model="gpt-4o")
        return (
            f"Reviewed {file_name}",
            [{"file": file_name, "line": 10, "type": "bug", "comment": f"Issue in {file_name}"}],
        )

    llm.review_pr.side_effect = fake_review_pr

    bundles = [
        {"diff": "+a", "files_summary": [{"file": "a.cs", "additions": 1, "deletions": 0}]},
        {"diff": "+b", "files_summary": [{"file": "b.ts", "additions": 1, "deletions": 0}]},
    ]

    summary, comments = ai_review._execute_bundled_review(
        llm=llm,
        bundles=bundles,
        context="",
        full_code_scan=False,
    )

    assert "Bundle 1/2:" in summary
    assert "Bundle 2/2:" in summary
    assert len(comments) == 2
    assert comments[0]["file"] == "a.cs"
    assert comments[1]["file"] == "b.ts"
    assert llm.last_token_estimate.total_prompt_tokens == 300


def test_select_pr_interactive_uses_list_index(mocker) -> None:
    """It should map a small numeric choice to the corresponding PR in the list."""
    tfs = MagicMock()
    tfs.list_pull_requests.return_value = [
        {"id": 10, "repository": "repo-a"},
        {"id": 20, "repository": "repo-b"},
    ]
    formatter = MagicMock()
    formatter.format_progress.side_effect = lambda message: message
    formatter.format_pr_list.return_value = "LIST"
    mocker.patch("builtins.input", return_value="2")
    mocker.patch("builtins.print")

    assert ai_review._select_pr_interactive(tfs, formatter) == (20, "repo-b")


def test_select_pr_interactive_rejects_invalid_choice(mocker) -> None:
    """It should return no selection when the user enters a non-numeric value."""
    tfs = MagicMock()
    tfs.list_pull_requests.return_value = [{"id": 10, "repository": "repo-a"}]
    formatter = MagicMock()
    formatter.format_progress.side_effect = lambda message: message
    formatter.format_pr_list.return_value = "LIST"
    mocker.patch("builtins.input", return_value="abc")
    mocker.patch("builtins.print")

    assert ai_review._select_pr_interactive(tfs, formatter) == (None, None)


def test_select_comments_to_post_respects_individual_answers(mocker) -> None:
    """It should collect only the comments accepted during manual selection."""
    comments = [
        {"file": "a.py", "line": 10, "type": "bug", "comment": "one"},
            {"file": "b.py", "line": 20, "type": "style", "comment": "two"},
    ]
    mocker.patch("builtins.input", side_effect=["2", "", "n"])
    mocker.patch("builtins.print")

    selected = ai_review._select_comments_to_post(comments, MagicMock())

    assert selected == [comments[0]]


@pytest.mark.parametrize(
    ("choice", "expected"),
    [("1", "detailed"), ("2", "security"), ("", "detailed"), ("99", "detailed")],
)
def test_ask_verbosity(choice: str, expected: str, mocker) -> None:
    """It should map menu choices to supported verbosity values."""
    mocker.patch("builtins.input", return_value=choice)
    mocker.patch("builtins.print")

    assert ai_review._ask_verbosity() == expected


def test_show_config_uses_effective_api_key(mocker, review_config_factory) -> None:
    """It should show the provider key status based on the effective key."""
    config = review_config_factory(api_key="", openai_api_key="provider-key")
    print_mock = mocker.patch("builtins.print")

    ai_review._show_config(config)

    rendered = "\n".join(call.args[0] for call in print_mock.call_args_list if call.args)
    assert "Configured" in rendered


def test_interactive_pr_review_builds_expected_argv(mocker, review_config) -> None:
    """It should translate the interactive choices into parser arguments."""
    parser = MagicMock()
    parsed = make_args(command="pr-review", dry_run=True, verbosity="security")
    parser.parse_args.return_value = parsed
    mocker.patch("builtins.input", return_value="2")
    mocker.patch("src.ai_review._ask_verbosity", return_value="security")
    mocker.patch("src.ai_review.build_parser", return_value=parser)
    run_review = mocker.patch("src.ai_review.run_review", return_value=11)

    result = ai_review._interactive_pr_review(review_config)

    assert result == 11
    parser.parse_args.assert_called_once_with(["pr-review", "--dry-run", "--security"])
    run_review.assert_called_once_with(parsed)


def test_main_uses_interactive_mode_without_arguments(mocker) -> None:
    """It should enter interactive mode when no subcommand was provided."""
    mocker.patch("src.ai_review.sys.argv", ["ai_review.py"])
    interactive_mode = mocker.patch("src.ai_review.interactive_mode", return_value=13)

    assert ai_review.main() == 13
    interactive_mode.assert_called_once_with()


def test_main_prints_help_when_no_command_is_parsed(mocker) -> None:
    """It should show help when parsing succeeds but no command is selected."""
    parser = MagicMock()
    parser.parse_args.return_value = make_args(command=None)
    mocker.patch("src.ai_review.sys.argv", ["ai_review.py", "--format", "json"])
    mocker.patch("src.ai_review.build_parser", return_value=parser)

    assert ai_review.main() == 0
    parser.print_help.assert_called_once_with()


# ---------------------------------------------------------------------------
# cmd_init
# ---------------------------------------------------------------------------

def test_cmd_init_creates_both_files(mocker, tmp_path) -> None:
    """It should create config.yaml and review_prompt.md in the current directory."""
    mocker.patch("src.ai_review.os.getcwd", return_value=str(tmp_path))
    mocker.patch(
        "src.ai_review.importlib.resources.files",
        side_effect=_fake_pkg_resources,
    )
    mocker.patch("builtins.print")

    result = ai_review.cmd_init()

    assert result == 0
    assert (tmp_path / "config.yaml").read_text() == "config-template"
    assert (tmp_path / "review_prompt.md").read_text() == "prompt-template"


def test_cmd_init_aborts_when_user_declines_config_overwrite(mocker, tmp_path) -> None:
    """It should abort without writing either file when user declines config overwrite."""
    (tmp_path / "config.yaml").write_text("existing")
    mocker.patch("src.ai_review.os.getcwd", return_value=str(tmp_path))
    mocker.patch("builtins.input", return_value="n")
    mocker.patch("builtins.print")

    result = ai_review.cmd_init()

    assert result == 0
    assert (tmp_path / "config.yaml").read_text() == "existing"
    assert not (tmp_path / "review_prompt.md").exists()


def test_cmd_init_skips_prompt_overwrite_when_user_declines(mocker, tmp_path) -> None:
    """It should write config.yaml but keep existing review_prompt.md when user declines."""
    (tmp_path / "review_prompt.md").write_text("existing-prompt")
    mocker.patch("src.ai_review.os.getcwd", return_value=str(tmp_path))
    mocker.patch(
        "src.ai_review.importlib.resources.files",
        side_effect=_fake_pkg_resources,
    )
    mocker.patch("builtins.input", return_value="n")
    mocker.patch("builtins.print")

    result = ai_review.cmd_init()

    assert result == 0
    assert (tmp_path / "config.yaml").read_text() == "config-template"
    assert (tmp_path / "review_prompt.md").read_text() == "existing-prompt"


def test_cmd_init_returns_error_when_template_missing(mocker, tmp_path) -> None:
    """It should return 1 when the bundled config template cannot be found."""
    mocker.patch("src.ai_review.os.getcwd", return_value=str(tmp_path))

    def _raise(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError("missing")

    mocker.patch("src.ai_review.importlib.resources.files", side_effect=_raise)
    mocker.patch("builtins.print")

    result = ai_review.cmd_init()

    assert result == 1


def test_cmd_init_returns_error_when_prompt_template_missing(mocker, tmp_path) -> None:
    """It should return 1 when the review_prompt.md template cannot be found.

    config.yaml is already written at that point; the function still surfaces the error.
    """
    mocker.patch("src.ai_review.os.getcwd", return_value=str(tmp_path))

    class _ConfigOnlyResources(_FakeResource):
        def __init__(self) -> None:
            super().__init__("")

        def joinpath(self, name: str) -> "_FakeResource":
            if name == "config.yaml.template":
                return _FakeResource("config-template")
            raise FileNotFoundError(name)

    mocker.patch(
        "src.ai_review.importlib.resources.files",
        return_value=_ConfigOnlyResources(),
    )
    mocker.patch("builtins.print")

    result = ai_review.cmd_init()

    assert result == 1
    # config.yaml was already written before the error
    assert (tmp_path / "config.yaml").read_text() == "config-template"
    assert not (tmp_path / "review_prompt.md").exists()


def test_cmd_init_overwrites_both_files_when_user_accepts(mocker, tmp_path) -> None:
    """It should overwrite both existing files when the user confirms both prompts."""
    (tmp_path / "config.yaml").write_text("old-config")
    (tmp_path / "review_prompt.md").write_text("old-prompt")
    mocker.patch("src.ai_review.os.getcwd", return_value=str(tmp_path))
    mocker.patch(
        "src.ai_review.importlib.resources.files",
        side_effect=_fake_pkg_resources,
    )
    mocker.patch("builtins.input", return_value="y")
    mocker.patch("builtins.print")

    result = ai_review.cmd_init()

    assert result == 0
    assert (tmp_path / "config.yaml").read_text() == "config-template"
    assert (tmp_path / "review_prompt.md").read_text() == "prompt-template"


def test_main_dispatches_to_cmd_init(mocker) -> None:
    """It should call cmd_init when the init subcommand is provided."""
    mocker.patch("src.ai_review.sys.argv", ["ai_review.py", "init"])
    cmd_init_mock = mocker.patch("src.ai_review.cmd_init", return_value=0)

    result = ai_review.main()

    assert result == 0
    cmd_init_mock.assert_called_once_with()


# ---------------------------------------------------------------------------
# Helpers for cmd_init tests
# ---------------------------------------------------------------------------

class _FakeResource:
    """Minimal stand-in for an importlib.resources path object."""

    def __init__(self, text: str) -> None:
        self._text = text

    def joinpath(self, name: str) -> "_FakeResource":
        return self

    def read_text(self, encoding: str = "utf-8") -> str:
        return self._text


def _fake_pkg_resources(package: str) -> _FakeResource:
    """Return a fake resource root whose joinpath distinguishes template names."""

    class _Dispatcher(_FakeResource):
        def __init__(self) -> None:
            super().__init__("")

        def joinpath(self, name: str) -> _FakeResource:
            if name == "config.yaml.template":
                return _FakeResource("config-template")
            if name == "review_prompt.md.template":
                return _FakeResource("prompt-template")
            raise FileNotFoundError(name)

    return _Dispatcher()