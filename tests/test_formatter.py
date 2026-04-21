"""Tests for the formatter module."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from src import formatter as formatter_module
from src.formatter import Colors, ReviewFormatter, save_output


@pytest.fixture(autouse=True)
def restore_colors() -> None:
    """Restore ANSI color constants after each test."""
    original = {name: getattr(Colors, name) for name in dir(Colors) if name.isupper() and not name.startswith("_")}
    yield
    for name, value in original.items():
        setattr(Colors, name, value)


def test_colors_disable_clears_ansi_values() -> None:
    """It should blank all exported ANSI constants."""
    Colors.disable()

    assert Colors.RED == ""
    assert Colors.BOLD == ""
    assert Colors.RESET == ""


@pytest.mark.parametrize(
    ("env", "isatty", "platform", "expected"),
    [
        ({"NO_COLOR": "1"}, True, "linux", False),
        ({"FORCE_COLOR": "1"}, False, "linux", True),
        ({}, False, "linux", False),
        ({"TERM": "xterm"}, True, "win32", True),
        ({"WT_SESSION": "1"}, True, "win32", True),
        ({}, True, "linux", True),
    ],
)
def test_supports_color(env: dict[str, str], isatty: bool, platform: str, expected: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    """It should detect terminal color support from env and tty state."""
    for key in ("NO_COLOR", "FORCE_COLOR", "TERM", "WT_SESSION"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(formatter_module.sys, "stdout", SimpleNamespace(isatty=lambda: isatty))
    monkeypatch.setattr(formatter_module.sys, "platform", platform)

    assert bool(formatter_module._supports_color()) is expected


def test_formatter_init_disables_colors_when_requested(mocker) -> None:
    """It should disable ANSI colors when color output is disabled."""
    disable = mocker.patch("src.formatter.Colors.disable")

    ReviewFormatter(color=False)

    disable.assert_called_once_with()


def test_format_header_and_footer_variants() -> None:
    """It should render terminal and markdown headers and footers."""
    terminal_formatter = ReviewFormatter(color=False, output_format="terminal")
    markdown_formatter = ReviewFormatter(color=False, output_format="markdown")

    terminal_header = terminal_formatter.format_header("PR Review", repo_name="repo-a", branch="main", extra_info="Info")
    markdown_header = markdown_formatter.format_header("PR Review", repo_name="repo-a", branch="main")

    assert "AI CODE REVIEW" in terminal_header
    assert "# 🤖 AI Code Review" in markdown_header
    assert "Type" in markdown_header
    assert terminal_formatter.format_footer(truncated=True)
    assert markdown_formatter.format_footer(truncated=True)


def test_format_review_json_output() -> None:
    """It should render reviews as JSON when requested."""
    formatter = ReviewFormatter(color=False, output_format="json")

    rendered = formatter.format_review("hello")

    assert '"review": "hello"' in rendered
    assert '"timestamp":' in rendered


@pytest.mark.parametrize(
    ("method", "message", "fragment"),
    [
        ("format_error", "boom", "ERROR"),
        ("format_warning", "warn", "warn"),
        ("format_info", "info", "info"),
        ("format_progress", "step", "step"),
        ("format_success", "done", "done"),
    ],
)
def test_basic_message_formatters(method: str, message: str, fragment: str) -> None:
    """It should format basic terminal messages consistently."""
    formatter = ReviewFormatter(color=False, output_format="terminal")

    rendered = getattr(formatter, method)(message)

    assert fragment in rendered


def test_format_files_summary_for_terminal_and_markdown() -> None:
    """It should render file summaries for both terminal and markdown outputs."""
    files = [{"file": "src/app.py", "additions": 3, "deletions": 1}]
    terminal_formatter = ReviewFormatter(color=False, output_format="terminal")
    markdown_formatter = ReviewFormatter(color=False, output_format="markdown")

    assert "src/app.py" in terminal_formatter.format_files_summary(files)
    assert "| `src/app.py` | +3 | -1 |" in markdown_formatter.format_files_summary(files)


def test_format_pr_list_handles_empty_and_full_entries() -> None:
    """It should render empty and populated PR lists."""
    formatter = ReviewFormatter(color=False)
    empty = formatter.format_pr_list([])
    populated = formatter.format_pr_list([
        {
            "id": 1,
            "title": "Improve authentication flow",
            "source_branch": "feature/auth",
            "target_branch": "main",
            "author": "Alice",
            "repository": "repo-a",
            "reviewers": [{"vote_label": "✅ Approved", "name": "Bob"}],
            "is_draft": True,
        }
    ])

    assert "No Pull Requests found" in empty
    assert "Improve authentication flow" in populated
    assert "[DRAFT]" in populated
    assert "Reviewers:" in populated


def test_format_pr_details_handles_description_commits_and_files() -> None:
    """It should include description, commits and changed files in PR details."""
    formatter = ReviewFormatter(color=False)
    details = formatter.format_pr_details(
        {
            "id": 7,
            "title": "Improve PR details",
            "author": "Alice",
            "source_branch": "feature/pr",
            "target_branch": "main",
            "repository": "repo-a",
            "status": "active",
            "description": "x" * 250,
            "commits": [{"short_id": f"abcd{i}", "message": f"commit-{i}", "author": "Alice"} for i in range(12)],
            "changed_files": [{"path": f"src/file_{i}.py", "change_type": "edit"} for i in range(22)],
        }
    )

    assert "Improve PR details" in details
    assert "Description:" in details
    assert "+2 more commits" in details
    assert "+2 more files" in details


def test_format_structured_comments_variants() -> None:
    """It should render structured comments and discarded warnings."""
    formatter = ReviewFormatter(color=False)
    comments = [
        {
            "file": "src/app.py",
            "line": 10,
            "severity": "high",
            "type": "bug",
            "comment": "Possible crash",
            "suggestion": "Add guard",
            "reference": "Docs",
        }
    ]

    rendered = formatter.format_structured_comments(comments, discarded_count=1)
    empty = formatter.format_structured_comments([], discarded_count=2)

    assert "Possible crash" in rendered
    assert "Add guard" in rendered
    assert "Reference" in rendered
    assert "discarded" in empty


def test_format_post_results_summarizes_statuses() -> None:
    """It should summarize successful, skipped and failed posts."""
    formatter = ReviewFormatter(color=False)
    rendered = formatter.format_post_results([
        {"success": True, "file": "a.py", "line": 4, "thread_id": 1},
        {"success": False, "file": "b.py", "error": "boom"},
        {"success": False, "skipped": True, "file": "c.py", "error": "ignored"},
    ])

    assert "Posted at a.py:4" in rendered
    assert "Skipped at c.py" in rendered
    assert "Failed at b.py" in rendered
    assert "1 posted" in rendered
    assert "2 failed" in rendered


def test_spinner_frame_rotates_frames() -> None:
    """It should cycle spinner frames deterministically."""
    formatter = ReviewFormatter(color=False)

    assert "step" in formatter.format_spinner_frame("step", 0)
    assert "step" in formatter.format_spinner_frame("step", 11)


def test_save_output_creates_parent_directories(tmp_path: Path, mocker) -> None:
    """It should create parent directories and persist review output."""
    target = tmp_path / "nested" / "review.md"
    print_mock = mocker.patch("builtins.print")

    save_output("content", str(target))

    assert target.read_text(encoding="utf-8") == "content"
    print_mock.assert_called_once()


def test_save_output_handles_os_error(tmp_path: Path, mocker) -> None:
    """It should report filesystem errors when saving fails."""
    mocker.patch("builtins.open", side_effect=OSError("disk full"))
    print_mock = mocker.patch("builtins.print")

    save_output("content", str(tmp_path / "review.md"))

    print_mock.assert_called_once()