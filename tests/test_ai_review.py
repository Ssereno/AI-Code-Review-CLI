"""Tests for the AI review CLI workflow."""

from __future__ import annotations

import argparse
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src import ai_review
from src.usage_tracker import TokenUsage


def make_args(**overrides: object) -> argparse.Namespace:
    """Create a namespace mirroring parsed CLI arguments."""
    base = {
        "command": "pr-review",
        "config": None,
        "verbosity": None,
        "model": None,
        "provider": None,
        "review_scope": None,
        "max_diff_files": None,
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
        "usage_file": None,
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
        "--quick",
        "--review-scope",
        "diff_only",
        "--max-diff-files",
        "3",
        "--output",
        "review.md",
    ])

    assert args.command == "pr-review"
    assert args.pr_id == 42
    assert args.dry_run is True
    assert args.verbosity == "quick"
    assert args.review_scope == "diff_only"
    assert args.max_diff_files == 3
    assert args.output == "review.md"

    contextual_args = parser.parse_args([
        "pr-review",
        "42",
        "--review-scope",
        "diff_with_context",
    ])
    assert contextual_args.review_scope == "diff_with_context"


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


def test_build_parser_parses_usage_options() -> None:
    """It should parse the usage inspection command."""
    parser = ai_review.build_parser()

    args = parser.parse_args([
        "usage",
        "--usage-file",
        "usage.jsonl",
        "--config",
        "alt-config.yaml",
        "--no-color",
    ])

    assert args.command == "usage"
    assert args.usage_file == "usage.jsonl"
    assert args.config == "alt-config.yaml"
    assert args.no_color is True


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


def test_run_review_dispatches_usage_without_validating_credentials(mocker, review_config) -> None:
    """Usage inspection should not require LLM or TFS configuration to be valid."""
    review_config.validate = MagicMock(side_effect=AssertionError("should not validate"))
    mocker.patch("src.ai_review.ReviewConfig.load", return_value=review_config)
    workflow = mocker.patch("src.ai_review.run_usage", return_value=0)
    mocker.patch("src.ai_review.ReviewFormatter")

    result = ai_review.run_review(make_args(command="usage", usage_file="usage.jsonl"))

    assert result == 0
    assert review_config.usage_file == "usage.jsonl"
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
        "changed_files": [{"path": "/a.py", "change_type": "edit"}],
    }
    diff = (
        "diff --git a/a.py b/a.py\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -1,3 +1,4 @@\n"
        " class Example:\n"
        "-    value = 1\n"
        "+    value = 2\n"
        "     other = value\n"
    )
    tfs.obter_dados_pr.return_value = ("origin/main", "origin/feature/test")
    tfs.get_work_item_context.return_value = "WORK ITEM CONTEXT"
    tfs.get_changed_files_context.return_value = "CHANGED FILE CONTEXT"
    tfs.get_project_manifest.return_value = "PROJECT MANIFEST"
    tfs.get_project_files_context.return_value = "REQUESTED PROJECT CONTEXT"

    llm = MagicMock()
    llm.request_context_files.side_effect = [["src/helper.py"], []]
    structured_comments = [
        {
            "file": "a.py",
            "line": 2,
            "type": "bug",
            "severity": "high",
            "comment": "msg",
            "anchor_code": "value = 2",
            "problematic_code": "value = 2",
            "evidence": "value = 2",
        }
    ]
    llm.review_pr_structured.return_value = structured_comments
    tfs.get_source_file_contents.return_value = {"a.py": "class Example:\n    value = 2\n    other = value"}
    tfs.plan_review_comments.return_value = {
        "new_comments": structured_comments,
        "skipped_duplicates": [],
        "resolved_reappeared": [],
        "existing_threads": [],
    }

    git_utils = MagicMock()
    git_utils.limit_diff_files.return_value = (diff, False, 0)
    git_utils.get_changed_files_summary.return_value = [{"file": "a.py", "additions": 1, "deletions": 0}]
    git_utils.truncate_diff.return_value = (diff, False)
    git_utils.get_pr_diff.return_value = diff
    git_utils.filter_diff_noise.return_value = diff
    git_utils.get_current_branch.return_value = "main"

    markdown_formatter = MagicMock()
    markdown_formatter.format_header.return_value = "HEADER"
    markdown_formatter.format_footer.return_value = "FOOTER"

    mocker.patch("src.ai_review.ProgressIndicator")
    mocker.patch("src.ai_review.obter_contexto_rag", return_value="")
    mocker.patch("src.ai_review.LLMClient", return_value=llm)
    mocker.patch("src.ai_review.GitUtils.__new__", return_value=git_utils)
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
    tfs.get_work_item_context.assert_called_once_with(
        "repo-a",
        123,
        max_items=review_config.work_item_context_max_items,
        max_chars=review_config.work_item_context_max_chars,
        fields=review_config.work_item_context_fields,
    )
    tfs.get_project_context.assert_not_called()
    tfs.get_changed_files_context.assert_called_once_with(
        "repo-a",
        "feature/test",
        [{"path": "/a.py", "change_type": "edit"}],
        max_chars=review_config.project_context_retrieval_max_chars,
        file_max_chars=review_config.project_context_retrieval_file_max_chars,
        file_extensions=review_config.project_context_file_extensions,
        exclude_patterns=review_config.project_context_exclude_patterns,
    )
    tfs.get_project_manifest.assert_called_once()
    tfs.get_project_files_context.assert_called_once()
    tfs.get_source_file_contents.assert_called_once_with(
        "repo-a",
        "feature/test",
        ["a.py"],
    )
    assert llm.request_context_files.call_count == 2
    llm.review.assert_not_called()
    assert llm.review_pr_structured.call_args.kwargs["source_files_context"] == "CHANGED FILE CONTEXT"
    assert llm.review_pr_structured.call_args.kwargs["project_context"] == "REQUESTED PROJECT CONTEXT"
    assert llm.review_pr_structured.call_args.kwargs["work_item_context"] == "WORK ITEM CONTEXT"
    assert llm.review_pr_structured.call_args.kwargs["diff"] == diff
    git_utils.filter_diff_additions_only.assert_not_called()
    print_mock.assert_any_call("REVIEW")
    review_text = formatter.format_review.call_args.args[0]
    assert "Structured Review" in review_text
    assert "msg" in review_text
    assert "General review" not in review_text
    saved_markdown = save_output.call_args.args[0]
    assert "Structured Review" in saved_markdown
    assert "msg" in saved_markdown


def test_store_pr_usage_persists_usage_record(mocker, tmp_path, review_config) -> None:
    """It should write token usage for a reviewed PR."""
    review_config.usage_file = str(tmp_path / "usage.jsonl")
    review_config.usage_pricing = {
        "openai": {
            "gpt-4o-mini": {
                "input_per_1m": 0.10,
                "output_per_1m": 0.20,
                "currency": "USD",
            }
        }
    }

    formatter = MagicMock()
    formatter.format_info.side_effect = lambda message: f"INFO:{message}"
    print_mock = mocker.patch("builtins.print")

    ai_review._store_pr_usage(
        review_config,
        formatter,
        repo_name="repo-a",
        pr_id=123,
        dry_run=True,
        comments_generated=2,
        usage_events=[
            TokenUsage(
                provider="openai",
                model="gpt-4o-mini",
                operation="general_review",
                prompt_tokens=100,
                completion_tokens=50,
            )
        ],
    )

    stored = (tmp_path / "usage.jsonl").read_text(encoding="utf-8")
    assert '"pull_request_id": 123' in stored
    print_mock.assert_called_once()


def test_run_usage_lists_and_shows_selected_pr(mocker, tmp_path, review_config) -> None:
    """It should read usage records, list PRs, and show selected totals."""
    usage_file = tmp_path / "usage.jsonl"
    usage_file.write_text(
        "\n".join([
            '{"repository":"repo-a","pull_request_id":42,"timestamp":"2026-05-11T10:00:00+00:00","provider":"openai","model":"gpt-4o-mini","comments_generated":2,"tokens":{"prompt_tokens":100,"completion_tokens":50,"total_tokens":150,"calls":2,"estimated":false},"cost":{"amount":0.01,"currency":"USD","estimated":false}}',
            '{"repository":"repo-b","pull_request_id":7,"timestamp":"2026-05-11T11:00:00+00:00","provider":"bedrock","model":"m","comments_generated":1,"tokens":{"prompt_tokens":20,"completion_tokens":10,"total_tokens":30,"calls":1,"estimated":true},"cost":null}',
        ]),
        encoding="utf-8",
    )
    review_config.usage_file = str(usage_file)
    formatter = MagicMock()
    formatter.format_info.side_effect = lambda message: f"INFO:{message}"
    mocker.patch("builtins.input", return_value="1")
    print_mock = mocker.patch("builtins.print")

    result = ai_review.run_usage(make_args(command="usage"), review_config, formatter)

    assert result == 0
    rendered = "\n".join(call.args[0] for call in print_mock.call_args_list if call.args)
    assert "Reviewed Pull Requests" in rendered
    assert "Usage for PR #42" in rendered
    assert "Total:  150" in rendered
    assert "0.010000 USD" in rendered


def test_run_usage_handles_empty_file(mocker, tmp_path, review_config) -> None:
    """It should return cleanly when no usage records exist."""
    review_config.usage_file = str(tmp_path / "missing.jsonl")
    formatter = MagicMock()
    formatter.format_info.side_effect = lambda message: f"INFO:{message}"
    print_mock = mocker.patch("builtins.print")

    result = ai_review.run_usage(make_args(command="usage"), review_config, formatter)

    assert result == 0
    print_mock.assert_called_once()
    assert "No PR usage records" in print_mock.call_args.args[0]


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
        "changed_files": [{"path": "/a.py", "change_type": "edit"}],
    }
    diff = "diff --git a/a.py b/a.py\n+++ b/a.py\n@@ -0,0 +1 @@\n+print('x')"
    tfs.obter_dados_pr.return_value = ("origin/main", "origin/feature/test")
    tfs.get_work_item_context.return_value = "WORK ITEM CONTEXT"
    tfs.get_changed_files_context.return_value = "CHANGED FILE CONTEXT"
    tfs.get_project_manifest.return_value = ""
    tfs.post_review_comments.side_effect = FakeTFSError("boom")

    llm = MagicMock()
    llm.request_context_files.return_value = []
    structured_comments = [
        {
            "file": "a.py",
            "line": 1,
            "type": "bug",
            "severity": "high",
            "comment": "msg",
            "anchor_code": "print('x')",
            "problematic_code": "print('x')",
            "evidence": "print('x')",
        }
    ]
    llm.review_pr_structured.return_value = structured_comments
    tfs.get_source_file_contents.return_value = {"a.py": "print('x')"}
    tfs.plan_review_comments.return_value = {
        "new_comments": structured_comments,
        "skipped_duplicates": [],
        "resolved_reappeared": [],
        "existing_threads": [],
    }

    git_utils = MagicMock()
    git_utils.filter_diff_additions_only.return_value = diff
    git_utils.limit_diff_files.return_value = (diff, False, 0)
    git_utils.get_changed_files_summary.return_value = [{"file": "a.py", "additions": 1, "deletions": 0}]
    git_utils.truncate_diff.return_value = (diff, False)
    git_utils.get_pr_diff.return_value = diff
    git_utils.filter_diff_noise.return_value = diff
    git_utils.get_current_branch.return_value = "main"

    progress = MagicMock()
    mocker.patch("src.ai_review.ProgressIndicator", return_value=progress)
    mocker.patch("src.ai_review.obter_contexto_rag", return_value="")
    mocker.patch("src.ai_review.LLMClient", return_value=llm)
    mocker.patch("src.ai_review.GitUtils.__new__", return_value=git_utils)
    mocker.patch("src.ai_review._select_comments_to_post", return_value=llm.review_pr_structured.return_value)
    mocker.patch("src.ai_review._save_pr_review_output")
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


def test_run_pr_review_workflow_diff_only_skips_extra_context(mocker, review_config_factory) -> None:
    """It should keep the old PR-only behavior when diff_only is selected."""
    config = review_config_factory(review_scope="diff_only")
    formatter = MagicMock()
    formatter.format_progress.side_effect = lambda message: message
    formatter.format_pr_details.return_value = "PR DETAILS"
    formatter.format_info.side_effect = lambda message: message
    formatter.format_review.return_value = "REVIEW"
    formatter.format_structured_comments.return_value = "COMMENTS"
    formatter.format_warning.side_effect = lambda message: f"WARN:{message}"
    formatter.format_success.side_effect = lambda message: f"OK:{message}"

    tfs = MagicMock()
    tfs.get_pull_request_details.return_value = {
        "source_branch": "feature/test",
        "target_branch": "main",
    }
    diff = (
        "diff --git a/a.py b/a.py\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -1,3 +1,4 @@\n"
        " class Example:\n"
        "-    value = 1\n"
        "+    value = 2\n"
        "     other = value\n"
    )
    filtered_diff = "diff --git a/a.py b/a.py\n+++ b/a.py\n@@ -1,3 +1,4 @@\n+    value = 2"
    tfs.obter_dados_pr.return_value = ("origin/main", "origin/feature/test")

    llm = MagicMock()
    llm.review_pr_structured.return_value = []
    tfs.plan_review_comments.return_value = {
        "new_comments": [],
        "skipped_duplicates": [],
        "resolved_reappeared": [],
        "existing_threads": [],
    }

    git_utils = MagicMock()
    git_utils.filter_diff_additions_only.return_value = filtered_diff
    git_utils.limit_diff_files.return_value = (filtered_diff, False, 0)
    git_utils.get_changed_files_summary.return_value = [{"file": "a.py", "additions": 1, "deletions": 0}]
    git_utils.truncate_diff.return_value = (filtered_diff, False)
    git_utils.get_pr_diff.return_value = diff
    git_utils.filter_diff_noise.return_value = diff
    git_utils.get_current_branch.return_value = "main"

    mocker.patch("src.ai_review.ProgressIndicator")
    mocker.patch("src.ai_review.obter_contexto_rag", return_value="")
    mocker.patch("src.ai_review.LLMClient", return_value=llm)
    mocker.patch("src.ai_review.GitUtils.__new__", return_value=git_utils)
    mocker.patch("src.tfs_client.TFSClient", return_value=tfs)
    mocker.patch("src.tfs_client.TFSError", new=Exception)
    mocker.patch("builtins.print")

    result = ai_review.run_pr_review_workflow(
        make_args(dry_run=True, repo_name="repo-a"),
        config,
        formatter,
    )

    assert result == 0
    tfs.get_work_item_context.assert_not_called()
    tfs.get_project_context.assert_not_called()
    llm.review.assert_not_called()
    assert llm.review_pr_structured.call_args.kwargs["project_context"] == ""
    assert llm.review_pr_structured.call_args.kwargs["work_item_context"] == ""
    git_utils.filter_diff_additions_only.assert_called_once_with(diff)
    assert llm.review_pr_structured.call_args.kwargs["diff"] == filtered_diff


def test_review_scope_context_note_matches_scope() -> None:
    """It should describe context behavior according to the active scope."""
    contextual = ai_review._review_scope_context_note("diff_with_context")
    diff_only = ai_review._review_scope_context_note("diff_only")

    assert "modified PR lines" in contextual
    assert "diff_only mode" in diff_only


def test_build_pr_metadata_issues_flags_review_risks() -> None:
    """It should report metadata problems that affect PR validation quality."""
    issues = ai_review._build_pr_metadata_issues(
        {
            "title": "",
            "description": "",
            "merge_status": "conflicts",
            "is_draft": True,
        },
        linked_work_item_count=0,
    )

    assert issues == [
        "PR title is empty.",
        "PR description is empty.",
        "PR merge status is 'conflicts' instead of 'succeeded'.",
        "PR is marked as draft.",
        "PR has no linked work items.",
    ]


def test_limit_comments_to_post_keeps_highest_severity() -> None:
    """It should cap comments by severity while preserving kept input order."""
    comments = [
        {"severity": "low", "comment": "low"},
        {"severity": "critical", "comment": "critical"},
        {"severity": "high", "comment": "high"},
    ]

    kept, omitted = ai_review._limit_comments_to_post(comments, 2)

    assert kept == [comments[1], comments[2]]
    assert omitted == [comments[0]]


def test_filter_comments_to_changed_lines_discards_context_comments(sample_diff: str) -> None:
    """It should keep problem comments anchored to added lines only."""
    comments = [
        {"file": "src/app.py", "line": 2, "type": "bug", "comment": "changed"},
        {"file": "src/app.py", "line": 1, "type": "bug", "comment": "context"},
        {"file": "", "line": 0, "type": "suggestion", "comment": "general"},
        {"file": "", "line": 0, "type": "praise", "comment": "looks good"},
    ]

    kept, discarded = ai_review._filter_comments_to_changed_lines(comments, sample_diff)

    assert kept == [comments[0]]
    assert discarded == [comments[1], comments[2], comments[3]]


def test_filter_comments_to_grounded_source_lines_requires_evidence(sample_diff: str) -> None:
    """It should keep only comments grounded in source-branch changed code."""
    comments = [
        {
            "file": "src/app.py",
            "line": 2,
            "type": "bug",
            "comment": "changed",
            "anchor_code": "print('new')",
            "problematic_code": "print('new')",
            "evidence": "print('new')",
        },
        {
            "file": "src/app.py",
            "line": 1,
            "type": "bug",
            "comment": "context",
            "anchor_code": "import os",
            "problematic_code": "import os",
            "evidence": "import os",
        },
        {
            "file": "src/app.py",
            "line": 3,
            "type": "bug",
            "comment": "mentions absent `GeDemandViewFiltersInput`",
            "anchor_code": "print('extra')",
            "problematic_code": "print('extra')",
            "evidence": "print('extra')",
        },
        {
            "file": "src/app.py",
            "line": 3,
            "type": "bug",
            "comment": "missing evidence",
            "anchor_code": "print('extra')",
            "problematic_code": "print('extra')",
            "evidence": "",
        },
        {"file": "", "line": 0, "type": "praise", "comment": "looks good"},
    ]

    kept, discarded_location, discarded_grounding = (
        ai_review._filter_comments_to_grounded_source_lines(
            comments,
            sample_diff,
            {"src/app.py": "import os\nprint('new')\nprint('extra')"},
        )
    )

    assert kept == [comments[0]]
    assert discarded_location == [comments[1], comments[4]]
    assert discarded_grounding == [comments[2], comments[3]]


def test_filter_comments_to_grounded_source_lines_checks_latest_source_file(sample_diff: str) -> None:
    """It should discard comments when evidence is absent from latest source code."""
    comment = {
        "file": "src/app.py",
        "line": 2,
        "type": "bug",
        "comment": "changed",
        "anchor_code": "print('new')",
        "problematic_code": "print('new')",
        "evidence": "print('new')",
    }

    kept, discarded_location, discarded_grounding = (
        ai_review._filter_comments_to_grounded_source_lines(
            [comment],
            sample_diff,
            {"src/app.py": "import os\nprint('extra')"},
        )
    )

    assert kept == []
    assert discarded_location == []
    assert discarded_grounding == [comment]


def test_filter_comments_rejects_context_only_claim_on_changed_anchor(sample_diff: str) -> None:
    """It should reject comments whose problem is grounded in unchanged context."""
    comments = [
        {
            "file": "src/app.py",
            "line": 2,
            "type": "bug",
            "comment": "changed line incorrectly depends on context",
            "anchor_code": "print('new')",
            "problematic_code": "import os",
            "evidence": "import os",
        },
        {
            "file": "src/app.py",
            "line": 2,
            "type": "bug",
            "comment": "changed line references stale `import os` context",
            "anchor_code": "print('new')",
            "problematic_code": "print('new')",
            "evidence": "print('new')",
        },
    ]

    kept, discarded_location, discarded_grounding = (
        ai_review._filter_comments_to_grounded_source_lines(
            comments,
            sample_diff,
            {"src/app.py": "import os\nprint('new')\nprint('extra')"},
        )
    )

    assert kept == []
    assert discarded_location == []
    assert discarded_grounding == comments


def test_filter_comments_discards_already_applied_suggestion() -> None:
    """It should reject comments that suggest code already present in source branch."""
    diff = "\n".join([
        "diff --git a/UpdateDemanPlanScenario.cs b/UpdateDemanPlanScenario.cs",
        "--- a/UpdateDemanPlanScenario.cs",
        "+++ b/UpdateDemanPlanScenario.cs",
        "@@ -158,1 +159,1 @@",
        "-var relationsToUpdate = relationPlannedFiguresCollection.Where(x => plannedFiguresToUpdate.ContainsKey(x.TargetEntity.Id));",
        "+var relationsToUpdate = relationPlannedFiguresCollection.Where(x => plannedFiguresToUpdate.ContainsKey(x.TargetEntity.Id.ToString()));",
    ])
    comment = {
        "file": "UpdateDemanPlanScenario.cs",
        "line": 159,
        "type": "bug",
        "comment": "Dictionary lookup uses the old `plannedFiguresToUpdate.ContainsKey(x.TargetEntity.Id)` key type.",
        "anchor_code": "var relationsToUpdate = relationPlannedFiguresCollection.Where(x => plannedFiguresToUpdate.ContainsKey(x.TargetEntity.Id.ToString()));",
        "problematic_code": "var relationsToUpdate = relationPlannedFiguresCollection.Where(x => plannedFiguresToUpdate.ContainsKey(x.TargetEntity.Id.ToString()));",
        "suggestion": "Use `plannedFiguresToUpdate.ContainsKey(x.TargetEntity.Id.ToString())`.",
        "evidence": "var relationsToUpdate = relationPlannedFiguresCollection.Where(x => plannedFiguresToUpdate.ContainsKey(x.TargetEntity.Id.ToString()));",
    }

    kept, discarded_location, discarded_grounding = (
        ai_review._filter_comments_to_grounded_source_lines(
            [comment],
            diff,
            {
                "UpdateDemanPlanScenario.cs": (
                    "var relationsToUpdate = relationPlannedFiguresCollection.Where("
                    "x => plannedFiguresToUpdate.ContainsKey(x.TargetEntity.Id.ToString()));"
                )
            },
        )
    )

    assert kept == []
    assert discarded_location == []
    assert discarded_grounding == [comment]


def test_build_general_summary_comment_is_compact(review_config) -> None:
    """It should keep the top-level PR comment to metadata only."""
    rendered = ai_review._build_general_summary_comment(
        review_config,
        run_timestamp="2026-05-13T15:20:00Z",
    )

    assert rendered == "\n".join([
        "## 🤖 AI Code Review",
        "",
        f"**Provider:** {review_config.llm_provider}",
        f"**Model:** {review_config.model}",
        f"**Mode:** {review_config.verbosity}",
        f"**Scope:** {review_config.review_scope}",
        "**Ran at:** 2026-05-13T15:20:00Z",
    ])
    assert "General review" not in rendered
    assert "Automatic review generated" not in rendered


def test_format_structured_review_text_includes_discarded_comment_details() -> None:
    """It should expose discarded comments in the terminal/saved review text."""
    discarded = [{
        "file": "src/app.py",
        "line": 42,
        "type": "bug",
        "severity": "high",
        "comment": "This was not grounded on the changed line.",
        "anchor_code": "call()",
        "problematic_code": "call()",
        "evidence": "call()",
        "suggestion": "Use the changed-line anchor.",
    }]

    rendered = ai_review._format_structured_review_text(
        [],
        discarded_count=1,
        discarded_grounding_comments=discarded,
    )

    assert "Discarded: Failed Source Evidence Checks" in rendered
    assert "Logged for diagnosis only" in rendered
    assert "src/app.py:42" in rendered
    assert "This was not grounded on the changed line." in rendered
    assert "Use the changed-line anchor." in rendered


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
        {"file": "a.py", "line": 10, "type": "bug", "severity": "high", "comment": "one"},
        {"file": "b.py", "line": 20, "type": "style", "severity": "low", "comment": "two"},
    ]
    mocker.patch("builtins.input", side_effect=["2", "", "n"])
    mocker.patch("builtins.print")

    selected = ai_review._select_comments_to_post(comments, MagicMock())

    assert selected == [comments[0]]


@pytest.mark.parametrize(
    ("choice", "expected"),
    [("1", "quick"), ("2", "detailed"), ("3", "security"), ("", "detailed"), ("99", "detailed")],
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

def test_cmd_init_creates_config_and_context_files(mocker, tmp_path) -> None:
    """It should create config.yaml and both reviewer context files."""
    mocker.patch("src.ai_review.os.getcwd", return_value=str(tmp_path))
    mocker.patch(
        "src.ai_review.importlib.resources.files",
        side_effect=_fake_pkg_resources,
    )
    mocker.patch("builtins.print")

    result = ai_review.cmd_init()

    assert result == 0
    assert (tmp_path / "config.yaml").read_text() == "config-template"
    assert (tmp_path / "review_context.example.md").read_text() == "prompt-template"
    assert (tmp_path / "review_context.local.md").read_text() == "prompt-template"
    assert "review_context.local.md" in (tmp_path / ".gitignore").read_text()


def test_cmd_init_aborts_when_user_declines_config_overwrite(mocker, tmp_path) -> None:
    """It should abort without writing either file when user declines config overwrite."""
    (tmp_path / "config.yaml").write_text("existing")
    mocker.patch("src.ai_review.os.getcwd", return_value=str(tmp_path))
    mocker.patch("builtins.input", return_value="n")
    mocker.patch("builtins.print")

    result = ai_review.cmd_init()

    assert result == 0
    assert (tmp_path / "config.yaml").read_text() == "existing"
    assert not (tmp_path / "review_context.example.md").exists()
    assert not (tmp_path / "review_context.local.md").exists()


def test_cmd_init_skips_local_context_overwrite_when_user_declines(mocker, tmp_path) -> None:
    """It should write config/example but keep existing local context on decline."""
    (tmp_path / "review_context.local.md").write_text("existing-local")
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
    assert (tmp_path / "review_context.example.md").read_text() == "prompt-template"
    assert (tmp_path / "review_context.local.md").read_text() == "existing-local"
    assert "review_context.local.md" in (tmp_path / ".gitignore").read_text()


def test_cmd_init_returns_error_when_template_missing(mocker, tmp_path) -> None:
    """It should return 1 when the bundled config template cannot be found."""
    mocker.patch("src.ai_review.os.getcwd", return_value=str(tmp_path))

    def _raise(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError("missing")

    mocker.patch("src.ai_review.importlib.resources.files", side_effect=_raise)
    mocker.patch("builtins.print")

    result = ai_review.cmd_init()

    assert result == 1


def test_cmd_init_returns_error_when_context_example_missing(mocker, tmp_path) -> None:
    """It should return 1 when the review context example cannot be found.

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
    assert not (tmp_path / "review_context.example.md").exists()
    assert not (tmp_path / "review_context.local.md").exists()


def test_cmd_init_overwrites_existing_files_when_user_accepts(mocker, tmp_path) -> None:
    """It should overwrite existing init files when the user confirms prompts."""
    (tmp_path / "config.yaml").write_text("old-config")
    (tmp_path / "review_context.example.md").write_text("old-example")
    (tmp_path / "review_context.local.md").write_text("old-local")
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
    assert (tmp_path / "review_context.example.md").read_text() == "prompt-template"
    assert (tmp_path / "review_context.local.md").read_text() == "prompt-template"


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
            if name == "review_context.example.md":
                return _FakeResource("prompt-template")
            raise FileNotFoundError(name)

    return _Dispatcher()
