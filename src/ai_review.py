#!/usr/bin/env python3
"""
AI Code Review - Main Script
==============================
Automated code review tool using Artificial Intelligence.
Main mode: Pull Request review on Azure DevOps/TFS with
automatic comments posted directly to the PR.

Usage:
    python ai_review.py                  # Interactive mode (main menu)
    python ai_review.py pr-review        # List PRs and do interactive review
    python ai_review.py pr-review <id>   # Direct review of a specific PR
    python ai_review.py list-prs         # List active Pull Requests

Options:
    --quick                              # Quick and concise review
    --detailed                           # Detailed review (default)
    --security                           # Security-focused review
    --review-scope <scope>                # Review scope (default: diff_with_context)
    --max-diff-files <n>                 # Max files in diff (overrides config)
    --dry-run                            # Review without posting comments
    --auto-post                          # Post comments without confirmation
    --model <name>                       # LLM model to use
    --provider <name>                    # LLM provider (openai/gemini/claude/ollama/copilot/bedrock)
    --output <file>                      # Save review to a file
    --format <terminal|markdown|json>    # Output format
    --context "<text>"                   # Additional context for the review
    --config <file>                      # Configuration file
    --help                               # Show this help

Examples:
    python ai_review.py                              # Interactive menu
    python ai_review.py pr-review                    # Select PR interactively
    python ai_review.py pr-review 42 --dry-run       # Review PR without posting
    python ai_review.py pr-review 42 --provider bedrock
    python ai_review.py list-prs --author "John Smith"

Author: Development Team
Version: see pyproject.toml
"""

import argparse
import datetime
import importlib.resources
import os
import re
import sys
import time
import threading


def _configure_console_streams() -> None:
    """Configures console streams to handle Unicode output safely."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue

        encoding = (getattr(stream, "encoding", "") or "").lower()
        if encoding == "utf-8":
            continue

        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (LookupError, OSError, ValueError):
            continue


def _ensure_project_root_on_path(script_path: str) -> str:
    """Ensures the repository root is available on sys.path."""
    script_dir = os.path.dirname(os.path.abspath(script_path))
    project_root = os.path.dirname(script_dir)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    return project_root


_configure_console_streams()
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = _ensure_project_root_on_path(__file__)

from src.config import ReviewConfig, VALID_PROVIDERS
from src.git_utils import GitUtils, GitError
from src.llm_client import LLMClient, LLMError
from src.formatter import ReviewFormatter, Colors, save_output
from src.usage_tracker import (
    append_usage_record,
    build_pr_usage_record,
    load_usage_records,
    resolve_usage_file,
    summarize_usage_by_pr,
)
from src import __version__ as VERSION


REVIEW_SCOPE_CHOICES = ["diff_only", "diff_with_context", "full_code"]


def _get_spinner_frames() -> list[str]:
    """Returns spinner frames compatible with the current stdout encoding."""
    unicode_frames = ["\u280b", "\u2819", "\u2839", "\u2838", "\u283c", "\u2834", "\u2826", "\u2827", "\u2807", "\u280f"]
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"

    try:
        "".join(unicode_frames).encode(encoding)
    except (LookupError, UnicodeEncodeError):
        return ["|", "/", "-", "\\"]

    return unicode_frames


# ---------------------------------------------------------------------------
# Progress Indicator
# ---------------------------------------------------------------------------
class ProgressIndicator:
    """Animated progress indicator for long-running operations."""

    def __init__(self, message: str = "Processing"):
        self.message = message
        self._running = False
        self._thread = None
        self._spinners = _get_spinner_frames()

    def start(self):
        """Starts the progress indicator."""
        self._running = True
        self._thread = threading.Thread(target=self._animate, daemon=True)
        self._thread.start()

    def stop(self, final_message: str = ""):
        """Stops the progress indicator."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=1)
        # Clear line
        sys.stdout.write("\r" + " " * 80 + "\r")
        sys.stdout.flush()
        if final_message:
            print(final_message)

    def _animate(self):
        i = 0
        while self._running:
            sys.stdout.write(
                f"\r{Colors.CYAN}{self._spinners[i % len(self._spinners)]} "
                f"{self.message}...{Colors.RESET}"
            )
            sys.stdout.flush()
            time.sleep(0.1)
            i += 1


# ---------------------------------------------------------------------------
# CLI Arguments
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    """Builds the CLI argument parser."""

    parser = argparse.ArgumentParser(
        prog="ai_review",
        description=(
            "🤖 AI Code Review - Automated code review using AI.\n"
            "Main mode: Pull Request review with comments on Azure DevOps.\n"
            "Providers: OpenAI GPT-4 | Google Gemini | Anthropic Claude | Ollama | GitHub Copilot | AWS Bedrock"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Usage examples:\n"
            "  %(prog)s                              # Interactive menu\n"
            "  %(prog)s pr-review                     # List PRs and select\n"
            "  %(prog)s pr-review 42 --dry-run        # Review PR #42 without posting\n"
            "  %(prog)s pr-review 42 --provider bedrock # Review PR #42 with Bedrock\n"
            "  %(prog)s list-prs --status active       # List active PRs\n"
        ),
    )

    # Subcommands
    subparsers = parser.add_subparsers(dest="command", help="Review type")

    # --- pr-review (MAIN MODE) ---
    sub_pr_review = subparsers.add_parser(
        "pr-review", help="🌟 Pull Request Review (recommended main mode)"
    )
    sub_pr_review.add_argument("pr_id", type=int, nargs="?", default=None,
                                help="PR ID (if omitted, shows list for selection)")
    sub_pr_review.add_argument("--repo-name", "-r", default=None,
                                help="Repository name in TFS")
    sub_pr_review.add_argument("--dry-run", action="store_true",
                                help="Review without posting comments")
    sub_pr_review.add_argument("--auto-post", action="store_true",
                                help="Post comments without confirmation")
    sub_pr_review.add_argument("--author", default=None,
                                help="Filter PRs by author")
    sub_pr_review.add_argument("--target-branch", default=None,
                                help="Filter PRs by target branch")

    # --- init ---
    subparsers.add_parser(
        "init", help="Create a config.yaml template in the current directory"
    )

    # --- list-prs ---
    sub_list_prs = subparsers.add_parser(
        "list-prs", help="List active Pull Requests"
    )
    sub_list_prs.add_argument("--repo-name", "-r", default=None,
                              help="Repository name in TFS")
    sub_list_prs.add_argument("--status", default="active",
                              choices=["active", "completed", "abandoned", "all"])
    sub_list_prs.add_argument("--author", default=None,
                              help="Filter by author")

    # --- usage ---
    sub_usage = subparsers.add_parser(
        "usage", help="Check stored token/cost usage by Pull Request"
    )
    sub_usage.add_argument(
        "--usage-file",
        default=None,
        help="Usage JSONL file (defaults to usage.file in config.yaml)",
    )
    sub_usage.add_argument(
        "--config",
        default=None,
        help="Configuration file (config.yaml)",
    )
    sub_usage.add_argument(
        "--no-color",
        action="store_true",
        help="Disable terminal colors",
    )

    # --- Global options ---
    all_subs = [sub_pr_review, sub_list_prs]
    for sub in all_subs:
        _add_global_options(sub)

    # Version
    parser.add_argument("--version", "-v", action="version",
                        version=f"AI Code Review v{VERSION}")

    return parser


def _add_global_options(parser: argparse.ArgumentParser) -> None:
    """Adds global options to a subparser."""

    group_review = parser.add_argument_group("Review Options")
    group_review.add_argument(
        "--quick", "-q", action="store_const", dest="verbosity",
        const="quick", help="Quick and concise review"
    )
    group_review.add_argument(
        "--detailed", "-d", action="store_const", dest="verbosity",
        const="detailed", help="Detailed review (default)"
    )
    group_review.add_argument(
        "--security", "-S", action="store_const", dest="verbosity",
        const="security", help="Security-focused review"
    )
    group_review.add_argument(
        "--review-scope", default=None,
        choices=REVIEW_SCOPE_CHOICES,
        help=(
            "Review scope: diff_with_context (default), diff_only, or full_code"
        )
    )
    group_review.add_argument(
        "--max-diff-files", default=None, type=int, metavar="N",
        help="Max diff files sent to LLM (overrides review.max_diff_files)"
    )
    group_review.add_argument(
        "--context", "-c", default="",
        help="Additional context for the review"
    )
    group_output = parser.add_argument_group("Output Options")
    group_output.add_argument(
        "--format", dest="output_format",
        choices=["terminal", "markdown", "json"],
        help="Output format"
    )
    group_output.add_argument(
        "--output", "-o", default="",
        help="Save review to a file"
    )
    group_output.add_argument(
        "--no-color", action="store_true",
        help="Disable terminal colors"
    )

    group_config = parser.add_argument_group("Configuration")
    group_config.add_argument(
        "--model", "-m", default=None,
        help="LLM model to use (e.g., gpt-4o, gemini-1.5-pro, claude-3-sonnet)"
    )
    group_config.add_argument(
        "--provider", "-p", default=None,
        choices=VALID_PROVIDERS,
        help="LLM provider"
    )
    group_config.add_argument(
        "--config", default=None,
        help="Configuration file (config.yaml)"
    )


def _save_pr_review_output(output_file: str, pr_id: int, repo_name: str,
                           pr_details: dict, review_text: str,
                           was_truncated: bool) -> None:
    """Saves the PR review output if a target file was provided."""
    if not output_file:
        return

    md_formatter = ReviewFormatter(color=False, output_format="markdown")
    md_output = "\n".join([
        md_formatter.format_header(
            review_type=f"Pull Request #{pr_id}",
            repo_name=repo_name,
            branch=f"{pr_details['source_branch']} → {pr_details['target_branch']}",
        ),
        review_text,
        md_formatter.format_footer(truncated=was_truncated),
    ])
    save_output(md_output, output_file)


def _review_scope_context_note(review_scope: str) -> str:
    """Returns user-facing context wording for the selected review scope."""
    scope = (review_scope or "diff_with_context").lower()
    if scope == "diff_with_context":
        return (
            "Context will be used for understanding only; review comments remain "
            "limited to modified PR lines."
        )
    if scope == "full_code":
        return "Review is running in full_code mode for changed file contents."
    return "Review is running in diff_only mode."


def _build_general_summary_comment(config: ReviewConfig,
                                   run_timestamp: str | None = None) -> str:
    """Builds the compact top-level PR comment for a review run."""
    timestamp = run_timestamp or datetime.datetime.now(
        datetime.timezone.utc
    ).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    return "\n".join([
        "## 🤖 AI Code Review",
        "",
        f"**Provider:** {config.llm_provider}",
        f"**Model:** {config.model}",
        f"**Mode:** {config.verbosity}",
        f"**Scope:** {config.review_scope}",
        f"**Ran at:** {timestamp}",
    ])


def _format_structured_review_text(
    comments: list[dict],
    *,
    discarded_count: int = 0,
    duplicate_count: int = 0,
    resolved_reappeared_count: int = 0,
    metadata_issues: list[str] | None = None,
) -> str:
    """Builds the terminal/saved review from final structured comments only."""
    lines: list[str] = ["## Structured Review"]
    metadata_issues = metadata_issues or []

    if metadata_issues:
        lines.append("")
        lines.append("## PR Metadata Checks")
        for issue in metadata_issues:
            lines.append(f"- {issue}")
        lines.append("")

    if comments:
        plural = "s" if len(comments) != 1 else ""
        lines.append(
            f"{len(comments)} actionable comment{plural} passed the structured "
            "grounding checks."
        )
        lines.append("")

        for index, comment in enumerate(comments, 1):
            file_path = str(comment.get("file", "") or "general")
            line = comment.get("line", 0)
            location = f"{file_path}:{line}" if line else file_path
            comment_type = str(comment.get("type", "suggestion")).title()
            severity = str(comment.get("severity", "info")).upper()

            lines.append(f"### {index}. {location} - {comment_type} ({severity})")
            body = str(comment.get("comment", "")).strip()
            if body:
                lines.append(body)

            problematic_code = str(comment.get("problematic_code", "")).strip()
            if problematic_code:
                lines.append("")
                lines.append(f"**Problematic code:** `{problematic_code}`")

            suggestion = str(comment.get("suggestion", "")).strip()
            if suggestion:
                lines.append("")
                lines.append(f"**Suggestion:** {suggestion}")

            reference = str(comment.get("reference", "")).strip()
            if reference:
                lines.append("")
                lines.append(f"**Reference:** {reference}")

            lines.append("")
    else:
        lines.append(
            "No actionable comments passed the structured grounding checks."
        )
        lines.append("")

    if discarded_count or duplicate_count or resolved_reappeared_count:
        lines.append("## Comment Checks")
        if discarded_count:
            lines.append(
                f"- {discarded_count} generated comment(s) were discarded by "
                "grounding or changed-line validation."
            )
        if duplicate_count:
            lines.append(
                f"- {duplicate_count} generated comment(s) were skipped because "
                "matching tool comments already exist on the PR."
            )
        if resolved_reappeared_count:
            lines.append(
                f"- {resolved_reappeared_count} resolved/closed tool comment(s) "
                "still appear in the latest structured review."
            )

    return "\n".join(lines).strip()


def _build_pr_metadata_issues(
    pr_details: dict,
    *,
    linked_work_item_count: int | None = None,
) -> list[str]:
    """Returns PR metadata issues that should be surfaced with the review."""
    issues: list[str] = []

    if not str(pr_details.get("title", "")).strip():
        issues.append("PR title is empty.")

    if not str(pr_details.get("description", "")).strip():
        issues.append("PR description is empty.")

    merge_status = str(pr_details.get("merge_status", "") or "").strip()
    if merge_status.lower() != "succeeded":
        issues.append(
            f"PR merge status is '{merge_status or 'unknown'}' instead of 'succeeded'."
        )

    if bool(pr_details.get("is_draft", False)):
        issues.append("PR is marked as draft.")

    if linked_work_item_count == 0:
        issues.append("PR has no linked work items.")

    return issues


def _severity_rank(comment: dict) -> int:
    """Returns a sortable severity rank for comment prioritization."""
    ranks = {
        "critical": 0,
        "high": 1,
        "medium": 2,
        "low": 3,
        "info": 4,
    }
    return ranks.get(str(comment.get("severity", "info")).lower(), 4)


def _limit_comments_to_post(
    comments: list[dict],
    max_comments: int,
) -> tuple[list[dict], list[dict]]:
    """Keeps only the highest-impact comments up to the configured cap."""
    if max_comments <= 0 or len(comments) <= max_comments:
        return comments, []

    indexed = list(enumerate(comments))
    ordered = sorted(
        indexed,
        key=lambda item: (_severity_rank(item[1]), item[0]),
    )
    kept_indexes = {index for index, _ in ordered[:max_comments]}
    kept = [
        comment for index, comment in indexed
        if index in kept_indexes
    ]
    omitted = [
        comment for index, comment in indexed
        if index not in kept_indexes
    ]
    return kept, omitted


def _normalize_review_path(path: object) -> str:
    """Normalizes a diff or LLM file path for comparisons."""
    value = str(path or "").replace("\\", "/").strip()
    if value.startswith(("a/", "b/")):
        value = value[2:]
    return value.lstrip("/").lower()


def _changed_lines_by_file(diff: str) -> dict[str, set[int]]:
    """Extracts added/right-side line numbers from a unified diff."""
    changed_lines: dict[str, set[int]] = {}
    current_file = ""
    current_line: int | None = None
    hunk_re = re.compile(r"@@\s+-\d+(?:,\d+)?\s+\+(\d+)(?:,\d+)?\s+@@")

    for raw_line in diff.splitlines():
        if raw_line.startswith("+++ "):
            file_path = raw_line[4:].strip().split("\t", 1)[0]
            if file_path != "/dev/null":
                current_file = _normalize_review_path(file_path)
                changed_lines.setdefault(current_file, set())
            current_line = None
            continue

        if raw_line.startswith("@@"):
            match = hunk_re.match(raw_line)
            if match:
                current_line = int(match.group(1))
            elif raw_line.startswith("@@ Change type:"):
                current_line = 1
            else:
                current_line = None
            continue

        if not current_file or current_line is None:
            continue

        if raw_line.startswith("+") and not raw_line.startswith("+++"):
            changed_lines.setdefault(current_file, set()).add(current_line)
            current_line += 1
        elif raw_line.startswith(" "):
            current_line += 1
        elif raw_line.startswith("-") and not raw_line.startswith("---"):
            continue

    return {path: lines for path, lines in changed_lines.items() if lines}


def _source_changed_hunks_by_file(diff: str) -> dict[str, list[dict]]:
    """Extracts source-branch hunk text and added line anchors from a diff."""
    hunks: dict[str, list[dict]] = {}
    current_file = ""
    current_line: int | None = None
    current_hunk: dict | None = None
    hunk_re = re.compile(r"@@\s+-\d+(?:,\d+)?\s+\+(\d+)(?:,\d+)?\s+@@")

    def flush_hunk() -> None:
        nonlocal current_hunk
        if current_file and current_hunk and current_hunk["added_lines"]:
            current_hunk["text"] = "\n".join(current_hunk["source_lines"])
            hunks.setdefault(current_file, []).append(current_hunk)
        current_hunk = None

    for raw_line in diff.splitlines():
        if raw_line.startswith("+++ "):
            flush_hunk()
            file_path = raw_line[4:].strip().split("\t", 1)[0]
            current_file = (
                _normalize_review_path(file_path)
                if file_path != "/dev/null" else ""
            )
            current_line = None
            continue

        if raw_line.startswith("@@"):
            flush_hunk()
            match = hunk_re.match(raw_line)
            if match:
                current_line = int(match.group(1))
            elif raw_line.startswith("@@ Change type:"):
                current_line = 1
            else:
                current_line = None
            current_hunk = {
                "added_lines": set(),
                "source_lines": [],
                "line_text": {},
                "text": "",
            }
            continue

        if not current_file or current_line is None or current_hunk is None:
            continue

        if raw_line.startswith("+") and not raw_line.startswith("+++"):
            code = raw_line[1:]
            current_hunk["added_lines"].add(current_line)
            current_hunk["source_lines"].append(code)
            current_hunk["line_text"][current_line] = code
            current_line += 1
        elif raw_line.startswith(" "):
            current_hunk["source_lines"].append(raw_line[1:])
            current_line += 1
        elif raw_line.startswith("-") and not raw_line.startswith("---"):
            continue

    flush_hunk()
    return hunks


def _text_contains_evidence(text: str, evidence: str) -> bool:
    """Returns whether evidence is grounded in source text."""
    evidence = str(evidence or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not evidence:
        return False
    if evidence in text:
        return True
    evidence_lines = [line.strip() for line in evidence.splitlines() if line.strip()]
    source_lines = [line.strip() for line in text.splitlines()]
    if not evidence_lines:
        return False
    return all(
        any(evidence_line == source_line for source_line in source_lines)
        for evidence_line in evidence_lines
    )


def _comment_line_range(comment: dict) -> tuple[int, int]:
    """Returns the source-branch line range targeted by a comment."""
    try:
        start = int(comment.get("line", 0))
    except (TypeError, ValueError):
        start = 0

    try:
        end = int(comment.get("end_line", 0) or 0)
    except (TypeError, ValueError):
        end = 0

    if start <= 0:
        return 0, 0
    if end <= start:
        end = start + 1
    return start, end


def _source_text_for_range(source_text: str, start: int, end: int) -> str:
    """Returns source file text for a 1-based, end-exclusive line range."""
    if start <= 0 or end <= start:
        return ""
    lines = str(source_text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(lines[start - 1:end - 1])


def _quoted_code_terms(*parts: str) -> list[str]:
    """Extracts quoted/backticked code terms that a comment claims to discuss."""
    text = "\n".join(str(part or "") for part in parts)
    terms: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r"`([^`]+)`|'([^']+)'|\"([^\"]+)\"", text):
        term = next(group for group in match.groups() if group is not None).strip()
        if len(term) < 3:
            continue
        if not re.search(r"[A-Za-z_]", term):
            continue
        key = term.lower()
        if key in seen:
            continue
        seen.add(key)
        terms.append(term)
    return terms


def _comment_mentions_absent_source_terms(comment: dict, source_text: str) -> bool:
    """Detects quoted code terms in the comment that are absent from source code."""
    for term in _quoted_code_terms(
        comment.get("comment", ""),
        comment.get("problematic_code", ""),
        comment.get("evidence", ""),
    ):
        if term not in source_text:
            return True
    return False


def _suggestion_already_applied(comment: dict, hunk_text: str, source_text: str) -> bool:
    """Detects comments that suggest code already present in source branch."""
    problematic_code = str(comment.get("problematic_code", "")).strip()
    line, end_line = _comment_line_range(comment)
    source_range = _source_text_for_range(source_text, line, end_line).strip()
    suggestion_replacement = str(comment.get("suggestion_replacement", "")).strip()
    if suggestion_replacement and source_range and suggestion_replacement == source_range:
        return True

    for suggested_code in _quoted_code_terms(comment.get("suggestion", "")):
        if not suggested_code or suggested_code == problematic_code:
            continue
        if _text_contains_evidence(hunk_text, suggested_code):
            return True
        if _text_contains_evidence(source_text, suggested_code):
            return True
    return False


def _filter_comments_to_changed_lines(comments: list[dict],
                                      diff: str) -> tuple[list[dict], list[dict]]:
    """Keeps problem comments only when they point to added PR diff lines."""
    changed_lines = _changed_lines_by_file(diff)
    kept: list[dict] = []
    discarded: list[dict] = []

    for comment in comments:
        comment_type = str(comment.get("type", "")).lower()

        file_path = _normalize_review_path(comment.get("file", ""))
        line, end_line = _comment_line_range(comment)
        required_lines = set(range(line, end_line))

        if (
            comment_type not in ("praise", "style", "")
            and file_path
            and required_lines
            and required_lines.issubset(changed_lines.get(file_path, set()))
        ):
            kept.append(comment)
        else:
            discarded.append(comment)

    return kept, discarded


def _filter_comments_to_grounded_source_lines(
    comments: list[dict],
    diff: str,
    source_file_contents: dict[str, str] | None = None,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Keeps problem comments only when grounded in source-branch changed code."""
    source_file_contents = source_file_contents or {}
    source_file_contents = {
        _normalize_review_path(path): content
        for path, content in source_file_contents.items()
    }
    hunks_by_file = _source_changed_hunks_by_file(diff)
    kept: list[dict] = []
    discarded_location: list[dict] = []
    discarded_grounding: list[dict] = []

    for comment in comments:
        comment_type = str(comment.get("type", "")).lower()
        if comment_type in ("praise", "style", ""):
            discarded_location.append(comment)
            continue

        file_path = _normalize_review_path(comment.get("file", ""))
        line, end_line = _comment_line_range(comment)
        required_lines = set(range(line, end_line))

        hunk = next(
            (
                item for item in hunks_by_file.get(file_path, [])
                if required_lines.issubset(item.get("added_lines", set()))
            ),
            None,
        )
        if not file_path or not required_lines or hunk is None:
            discarded_location.append(comment)
            continue

        evidence = str(comment.get("evidence", "")).strip()
        problematic_code = str(comment.get("problematic_code", "")).strip()
        hunk_text = str(hunk.get("text", ""))
        source_text = source_file_contents.get(file_path)
        has_full_source_text = source_text is not None
        if source_text is None:
            source_text = hunk_text
        source_range = (
            _source_text_for_range(source_text, line, end_line)
            if has_full_source_text else hunk_text
        )

        if not _text_contains_evidence(hunk_text, problematic_code):
            discarded_grounding.append(comment)
            continue

        if not _text_contains_evidence(source_range, problematic_code):
            discarded_grounding.append(comment)
            continue

        if not _text_contains_evidence(source_text, problematic_code):
            discarded_grounding.append(comment)
            continue

        if not _text_contains_evidence(hunk_text, evidence):
            discarded_grounding.append(comment)
            continue

        if not _text_contains_evidence(source_text, evidence):
            discarded_grounding.append(comment)
            continue

        if _comment_mentions_absent_source_terms(comment, source_text):
            discarded_grounding.append(comment)
            continue

        if _suggestion_already_applied(comment, hunk_text, source_text):
            discarded_grounding.append(comment)
            continue

        kept.append(comment)

    return kept, discarded_location, discarded_grounding


def _join_context_sections(*sections: str) -> str:
    """Joins non-empty context sections."""
    return "\n\n".join(
        section.strip()
        for section in sections
        if isinstance(section, str) and section.strip()
    )


def _build_on_demand_project_context(
    *,
    llm: LLMClient,
    tfs,
    config: ReviewConfig,
    formatter: ReviewFormatter,
    repo_name: str,
    source_branch: str,
    diff: str,
    files_summary: list[dict],
    user_context: str,
    work_item_context: str,
    source_files_context: str,
) -> str:
    """Builds repository context by letting the model request files from a manifest."""
    project_manifest = ""

    try:
        project_manifest = tfs.get_project_manifest(
            repo_name,
            source_branch,
            max_chars=config.project_context_manifest_max_chars,
            file_extensions=config.project_context_file_extensions,
            exclude_patterns=config.project_context_exclude_patterns,
        )
    except Exception as exc:
        print(formatter.format_warning(
            f"Could not load repository manifest; continuing without on-demand context: {exc}"
        ))

    if not project_manifest:
        return ""

    print(formatter.format_info(
        f"Repository manifest loaded ({len(project_manifest):,} characters). "
        "The model can request additional files from it."
    ))

    fetched_context = ""
    requested_keys: set[str] = set()
    for round_index in range(config.project_context_retrieval_max_rounds):
        remaining_files = config.project_context_retrieval_max_files - len(requested_keys)
        remaining_chars = config.project_context_retrieval_max_chars - len(fetched_context)
        if remaining_files <= 0 or remaining_chars <= 0:
            break

        requested_paths = llm.request_context_files(
            diff=diff,
            files_summary=files_summary,
            project_manifest=project_manifest,
            context=user_context,
            changed_files_context=source_files_context,
            work_item_context=work_item_context,
            fetched_context=fetched_context,
            max_files=remaining_files,
        )
        if not isinstance(requested_paths, list):
            requested_paths = []

        new_paths = []
        for path in requested_paths:
            key = _normalize_review_path(path)
            if not key or key in requested_keys:
                continue
            requested_keys.add(key)
            new_paths.append(path)

        if not new_paths:
            break

        print(formatter.format_info(
            f"Context request round {round_index + 1}: fetching {len(new_paths)} file(s)."
        ))

        try:
            round_context = tfs.get_project_files_context(
                repo_name,
                source_branch,
                new_paths,
                max_files=remaining_files,
                max_chars=remaining_chars,
                file_max_chars=config.project_context_retrieval_file_max_chars,
                file_extensions=config.project_context_file_extensions,
                exclude_patterns=config.project_context_exclude_patterns,
            )
        except Exception as exc:
            print(formatter.format_warning(
                f"Could not fetch requested repository context; continuing: {exc}"
            ))
            continue

        if round_context:
            fetched_context = _join_context_sections(fetched_context, round_context)

    if fetched_context:
        print(formatter.format_info(
            f"On-demand repository context loaded ({len(fetched_context):,} characters)."
        ))

    return fetched_context


def _get_llm_usage_events(llm: LLMClient) -> list:
    """Returns usage events from an LLM client, ignoring test doubles."""
    events = getattr(llm, "usage_events", [])
    return events if isinstance(events, list) else []


def _format_usage_summary(record: dict) -> str:
    """Builds a short terminal summary for persisted usage."""
    tokens = record.get("tokens", {})
    total = tokens.get("total_tokens", 0)
    prompt = tokens.get("prompt_tokens", 0)
    completion = tokens.get("completion_tokens", 0)
    suffix = " estimated" if tokens.get("estimated") else ""

    message = (
        f"LLM usage stored: {total}{suffix} tokens "
        f"(input {prompt}, output {completion})"
    )

    cost = record.get("cost")
    if cost:
        message += f" | estimated cost: {cost['amount']:.6f} {cost['currency']}"
    elif record.get("missing_pricing"):
        message += " | cost not estimated (configure usage.pricing)"

    return message


def _store_pr_usage(
    config: ReviewConfig,
    formatter: ReviewFormatter,
    *,
    repo_name: str,
    pr_id: int,
    dry_run: bool,
    comments_generated: int,
    usage_events: list,
    metadata_issues: list[str] | None = None,
) -> None:
    """Persists token usage for a completed PR review."""
    if not config.usage_tracking_enabled or not usage_events:
        return

    try:
        record = build_pr_usage_record(
            repository=repo_name,
            pr_id=pr_id,
            provider=config.llm_provider,
            model=config.model,
            review_scope=config.review_scope,
            verbosity=config.verbosity,
            dry_run=dry_run,
            comments_generated=comments_generated,
            events=usage_events,
            pricing_config=config.usage_pricing,
            metadata_issues=metadata_issues,
        )
        path = append_usage_record(config.usage_file, record)
        print(formatter.format_info(f"{_format_usage_summary(record)} -> {path}"))
    except OSError as exc:
        print(formatter.format_warning(f"Could not store LLM usage: {exc}"))


def _format_cost_value(cost: dict | None) -> str:
    """Formats a cost value from a usage summary."""
    if not cost:
        return "not estimated"

    amount = float(cost.get("amount") or 0.0)
    currency = str(cost.get("currency") or "USD")
    suffix = " estimated" if cost.get("estimated") else ""
    return f"{amount:.6f} {currency}{suffix}"


def _format_usage_pr_list(summaries: list[dict], usage_file: str) -> str:
    """Formats the reviewed PR usage list."""
    c = Colors
    lines = [
        f"\n{c.BOLD}📊 Reviewed Pull Requests ({len(summaries)}):{c.RESET}",
        f"{c.DIM}Usage file: {usage_file}{c.RESET}\n",
    ]

    for index, summary in enumerate(summaries, 1):
        tokens = summary.get("tokens", {})
        cost = _format_cost_value(summary.get("cost"))
        latest = summary.get("latest_timestamp") or "unknown"
        lines.append(
            f"  {c.CYAN}{index:>3}){c.RESET} "
            f"{c.BOLD}#{summary['pull_request_id']:<6}{c.RESET} "
            f"{summary['repository']} "
            f"{c.DIM}({summary['reviews']} review run(s)){c.RESET}"
        )
        lines.append(
            f"       Tokens: {tokens.get('total_tokens', 0)} total "
            f"(input {tokens.get('prompt_tokens', 0)}, "
            f"output {tokens.get('completion_tokens', 0)}) | Cost: {cost}"
        )
        metadata_issue_count = int(summary.get("metadata_issue_count", 0) or 0)
        if metadata_issue_count:
            lines.append(f"       Metadata issues: {metadata_issue_count}")
        lines.append(f"       {c.DIM}Latest: {latest}{c.RESET}")
        lines.append("")

    return "\n".join(lines)


def _select_usage_summary_interactive(summaries: list[dict]) -> dict | None:
    """Lets the user select a PR usage summary."""
    c = Colors
    try:
        choice = input(
            f"\n{c.BOLD}Select PR usage (list number or PR ID, 0 to cancel): {c.RESET}"
        ).strip()
    except (KeyboardInterrupt, EOFError):
        print("\n")
        return None

    if not choice or choice == "0":
        return None

    try:
        value = int(choice)
    except ValueError:
        print(f"{c.RED}Invalid option.{c.RESET}")
        return None

    if 1 <= value <= len(summaries):
        return summaries[value - 1]

    matching = [s for s in summaries if int(s.get("pull_request_id", 0)) == value]
    if len(matching) == 1:
        return matching[0]

    if len(matching) > 1:
        print(f"{c.YELLOW}Multiple repositories contain PR #{value}. Select by list number.{c.RESET}")
        return None

    print(f"{c.RED}PR #{value} was not found in the usage log.{c.RESET}")
    return None


def _format_usage_details(summary: dict) -> str:
    """Formats detailed token and cost values for one PR."""
    c = Colors
    tokens = summary.get("tokens", {})
    cost = summary.get("cost")

    lines = [
        f"\n{c.BLUE}{c.BOLD}{'─' * 60}{c.RESET}",
        f"{c.BOLD}  Usage for PR #{summary['pull_request_id']}{c.RESET}",
        f"{c.BLUE}{'─' * 60}{c.RESET}",
        f"{c.CYAN}  Repository:{c.RESET} {summary['repository']}",
        f"{c.CYAN}  Review runs:{c.RESET} {summary.get('reviews', 0)}",
        f"{c.CYAN}  LLM calls:{c.RESET} {tokens.get('calls', 0)}",
        f"{c.CYAN}  Comments generated:{c.RESET} {summary.get('comments_generated', 0)}",
        f"{c.CYAN}  Latest review:{c.RESET} {summary.get('latest_timestamp') or 'unknown'}",
        "",
        f"{c.BOLD}  Tokens{c.RESET}",
        f"    Input:  {tokens.get('prompt_tokens', 0)}",
        f"    Output: {tokens.get('completion_tokens', 0)}",
        f"    Total:  {tokens.get('total_tokens', 0)}",
    ]

    if tokens.get("estimated"):
        lines.append(f"    {c.YELLOW}Some token values are estimated.{c.RESET}")

    lines.extend([
        "",
        f"{c.BOLD}  Cost{c.RESET}",
        f"    Total: {_format_cost_value(cost)}",
    ])

    if summary.get("missing_pricing"):
        lines.append(
            "    Missing pricing: "
            + ", ".join(summary.get("missing_pricing", []))
        )

    metadata_issues = summary.get("metadata_issues") or []
    if metadata_issues:
        lines.extend(["", f"{c.BOLD}  Metadata Issues{c.RESET}"])
        lines.extend(f"    - {issue}" for issue in metadata_issues)

    providers = ", ".join(summary.get("providers", [])) or "unknown"
    models = ", ".join(summary.get("models", [])) or "unknown"
    lines.extend([
        "",
        f"{c.CYAN}  Providers:{c.RESET} {providers}",
        f"{c.CYAN}  Models:{c.RESET} {models}",
        f"{c.BLUE}{'─' * 60}{c.RESET}\n",
    ])

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command execution functions
# ---------------------------------------------------------------------------
def run_review(args: argparse.Namespace) -> int:
    """Executes the code review based on the provided arguments."""

    # --- Load configuration ---
    config = ReviewConfig.load(config_path=getattr(args, "config", None))

    # Override configuration with CLI arguments
    if getattr(args, "verbosity", None):
        config.verbosity = args.verbosity
    if getattr(args, "model", None):
        config.model = args.model
    if getattr(args, "provider", None):
        config.llm_provider = args.provider
    if getattr(args, "review_scope", None):
        config.review_scope = args.review_scope
    if getattr(args, "max_diff_files", None) is not None:
        config.max_diff_files = args.max_diff_files
    if getattr(args, "output_format", None):
        config.output_format = args.output_format
    if getattr(args, "output", None):
        config.output_file = args.output
    if getattr(args, "no_color", False):
        config.color_output = False
    if getattr(args, "dry_run", False):
        config.dry_run = True
    if getattr(args, "auto_post", False):
        config.auto_post_comments = True
    if getattr(args, "usage_file", None):
        config.usage_file = args.usage_file

    command = args.command

    # Usage inspection only reads the local usage log, so it should work
    # even when LLM or TFS credentials are not configured.
    if command == "usage":
        formatter = ReviewFormatter(
            color=config.color_output,
            output_format=config.output_format,
        )
        return run_usage(args, config, formatter)

    # --- Validate configuration ---
    issues = config.validate()
    if issues:
        formatter = ReviewFormatter(color=config.color_output)
        for issue in issues:
            print(formatter.format_error(issue))
        print("\nTip: Configure the config.yaml file.")
        print("Check README.md for detailed instructions.")
        return 1

    # --- Initialize components ---
    formatter = ReviewFormatter(
        color=config.color_output,
        output_format=config.output_format,
    )

    # --- Commands that don't need a Git repo ---
    if command == "pr-review":
        return run_pr_review_workflow(args, config, formatter)
    elif command == "list-prs":
        return run_list_prs(args, config, formatter)

    print(formatter.format_error(
        "Unrecognized command. Use --help to see available commands."
    ))
    return 1


def run_pr_review_workflow(args: argparse.Namespace, config: ReviewConfig,
                           formatter: ReviewFormatter) -> int:
    """
    Main Pull Request review workflow.
    1. Lists active PRs
    2. Allows selecting a PR
    3. Reviews with AI
    4. Shows comments preview
    5. Allows posting comments to the PR
    """
    from src.tfs_client import TFSClient, TFSError

    try:
        tfs = TFSClient(config)
    except TFSError as exc:
        print(formatter.format_error(str(exc)))
        print("\nTip: Configure tfs.base_url, tfs.project and tfs.pat in config.yaml")
        return 1

    c = Colors
    repo_name = getattr(args, "repo_name", None) or config.tfs_repository or None
    pr_id = getattr(args, "pr_id", None)

    # --- If no PR ID, list and select ---
    if pr_id is None:
        pr_id, repo_name = _select_pr_interactive(
            tfs, formatter, repo_name,
            author=getattr(args, "author", None),
            target_branch=getattr(args, "target_branch", None),
        )
        if pr_id is None:
            return 0  # User cancelled

    # --- Get PR details ---
    print(formatter.format_progress(f"Getting details for PR #{pr_id}"))

    try:
        if not repo_name:
            # Try to get repo_name from PRs
            prs = tfs.list_pull_requests(repository=None, top=100)
            for pr in prs:
                if pr["id"] == pr_id:
                    repo_name = pr["repository"]
                    break
            if not repo_name:
                print(formatter.format_error(
                    f"PR #{pr_id} not found. Specify the repository with --repo-name."
                ))
                return 1

        pr_details = tfs.get_pull_request_details(repo_name, pr_id)
    except TFSError as exc:
        print(formatter.format_error(str(exc)))
        return 1

    # Show PR details
    print(formatter.format_pr_details(pr_details))

    # --- Get PR diff ---
    print(formatter.format_progress("Getting Pull Request diff"))

    try:
        diff = tfs.get_pull_request_diff(
            repo_name,
            pr_id,
            review_scope=config.review_scope,
        )
    except TFSError as exc:
        print(formatter.format_error(str(exc)))
        return 1

    if not diff.strip():
        print(formatter.format_warning("PR contains no code changes."))
        return 0

    # --- AI Analysis ---
    print(formatter.format_info(
        f"Provider: {config.llm_provider} | Model: {config.model} | "
        f"Mode: {config.verbosity} | Scope: {config.review_scope}"
    ))

    dry_run = config.dry_run or getattr(args, "dry_run", False)
    auto_post = config.auto_post_comments or getattr(args, "auto_post", False)

    if dry_run:
        print(formatter.format_info("🔍 DRY-RUN mode: comments will NOT be posted"))

    # Get diff files summary
    git_utils = GitUtils.__new__(GitUtils)
    git_utils.repo_path = os.getcwd()

    # Filter extensions before limiting/truncating the diff sent to the LLM
    if config.file_extensions_filter:
        try:
            diff = git_utils.filter_diff_by_extensions(
                diff,
                config.file_extensions_filter,
            )
        except GitError as exc:
            print(formatter.format_warning(str(exc)))
            return 0

    review_scope = (config.review_scope or "diff_with_context").lower()

    if review_scope == "diff_only":
        # Keep the legacy PR-only mode compact by removing context and deletions.
        diff = git_utils.filter_diff_additions_only(diff)
        if not diff.strip():
            print(formatter.format_warning(
                "After filtering additions only, the diff is empty. No new code to review."
            ))
            return 0

    # Limit number of diff files if needed
    diff_limited, files_limited, omitted_files = git_utils.limit_diff_files(
        diff,
        config.max_diff_files,
    )
    if files_limited:
        print(formatter.format_warning(
            f"Diff truncated to {config.max_diff_files} files. "
            f"{omitted_files} file(s) omitted."
        ))

    files_summary = git_utils.get_changed_files_summary(diff_limited)

    # Truncate diff if needed
    diff_truncated, was_truncated = git_utils.truncate_diff(
        diff_limited,
        config.max_diff_lines,
    )
    if was_truncated:
        print(formatter.format_warning(
            f"Diff truncated to {config.max_diff_lines} lines per file."
        ))

    use_contextual_review = review_scope == "diff_with_context"

    work_item_context = ""
    linked_work_item_ids: list[int] | None = None
    if use_contextual_review and config.work_item_context_enabled:
        print(formatter.format_progress("Getting documentation from linked work items"))
        try:
            linked_work_item_ids = tfs.list_pull_request_work_item_ids(
                repo_name,
                pr_id,
            )
            if not isinstance(linked_work_item_ids, list):
                linked_work_item_ids = None
        except TFSError as exc:
            linked_work_item_ids = None
            print(formatter.format_warning(
                f"Could not inspect linked work items for metadata checks: {exc}"
            ))

        try:
            work_item_kwargs = {
                "max_items": config.work_item_context_max_items,
                "max_chars": config.work_item_context_max_chars,
                "fields": config.work_item_context_fields,
            }
            if linked_work_item_ids is not None:
                work_item_kwargs["work_item_ids"] = linked_work_item_ids
            work_item_context = tfs.get_work_item_context(
                repo_name,
                pr_id,
                **work_item_kwargs,
            )
        except TFSError as exc:
            print(formatter.format_warning(
                "Could not load linked work item documentation; "
                f"continuing without it: {exc}"
            ))

        if work_item_context:
            print(formatter.format_info(
                f"Linked work item documentation loaded ({len(work_item_context):,} characters). "
                f"{_review_scope_context_note(config.review_scope)}"
            ))

    metadata_issues = _build_pr_metadata_issues(
        pr_details,
        linked_work_item_count=(
            len(linked_work_item_ids)
            if linked_work_item_ids is not None else None
        ),
    )
    for issue in metadata_issues:
        print(formatter.format_warning(f"PR metadata: {issue}"))

    source_files_context = ""
    if use_contextual_review and config.project_context_enabled:
        print(formatter.format_progress(
            "Getting full source-branch contents for changed files"
        ))
        try:
            source_files_context = tfs.get_changed_files_context(
                repo_name,
                pr_details.get("source_branch", ""),
                pr_details.get("changed_files", []),
                max_chars=config.project_context_retrieval_max_chars,
                file_max_chars=config.project_context_retrieval_file_max_chars,
                file_extensions=config.project_context_file_extensions,
                exclude_patterns=config.project_context_exclude_patterns,
            )
        except TFSError as exc:
            print(formatter.format_warning(
                f"Could not load source-branch changed file contents; continuing with diff only: {exc}"
            ))

        if source_files_context:
            print(formatter.format_info(
                f"Source-branch changed file contents loaded ({len(source_files_context):,} characters). "
                f"{_review_scope_context_note(config.review_scope)}"
            ))

    project_context = ""
    project_context_mode = (config.project_context_mode or "on_demand").lower()
    if (
        use_contextual_review
        and config.project_context_enabled
        and project_context_mode == "full"
    ):
        print(formatter.format_progress("Getting full repository context from source branch"))
        try:
            project_context = tfs.get_project_context(
                repo_name,
                pr_details.get("source_branch", ""),
                max_files=config.project_context_max_files,
                max_chars=config.project_context_max_chars,
                file_extensions=config.project_context_file_extensions,
                exclude_patterns=config.project_context_exclude_patterns,
            )
        except TFSError as exc:
            print(formatter.format_warning(
                f"Could not load full repository context; continuing with PR diff only: {exc}"
            ))

        if project_context:
            print(formatter.format_info(
                f"Full repository context loaded ({len(project_context):,} characters). "
                f"{_review_scope_context_note(config.review_scope)}"
            ))

    try:
        llm = LLMClient(config)
        if (
            use_contextual_review
            and config.project_context_enabled
            and project_context_mode == "on_demand"
        ):
            print(formatter.format_progress(
                "Preparing on-demand repository context"
            ))
            project_context = _build_on_demand_project_context(
                llm=llm,
                tfs=tfs,
                config=config,
                formatter=formatter,
                repo_name=repo_name,
                source_branch=pr_details.get("source_branch", ""),
                diff=diff_truncated,
                files_summary=files_summary,
                user_context=getattr(args, "context", ""),
                work_item_context=work_item_context,
                source_files_context=source_files_context,
            )
    except LLMError as exc:
        print(formatter.format_error(str(exc)))
        return 1

    # --- Perform structured review ---
    progress = ProgressIndicator("Analyzing code with AI (may take 30-60s)")
    progress.start()
    start_time = time.time()

    try:
        structured_comments = llm.review_pr_structured(
            diff=diff_truncated,
            files_summary=files_summary,
            context=getattr(args, "context", ""),
            review_scope=config.review_scope,
            project_context=project_context,
            work_item_context=work_item_context,
            source_files_context=source_files_context,
        )
    except LLMError as exc:
        progress.stop()
        print(formatter.format_error(str(exc)))
        return 1

    source_file_contents = {}
    comment_paths = [
        comment.get("file", "")
        for comment in structured_comments
        if comment.get("file")
    ]
    if comment_paths:
        try:
            source_file_contents = tfs.get_source_file_contents(
                repo_name,
                pr_details.get("source_branch", ""),
                comment_paths,
            )
            if not isinstance(source_file_contents, dict):
                source_file_contents = {}
        except TFSError as exc:
            print(formatter.format_warning(
                f"Could not verify source-branch file contents; using diff anchors only: {exc}"
            ))

    structured_comments, discarded_comments, discarded_ungrounded = (
        _filter_comments_to_grounded_source_lines(
            structured_comments,
            diff_truncated,
            source_file_contents,
        )
    )
    if discarded_comments:
        print(formatter.format_info(
            f"Discarded {len(discarded_comments)} comment(s) outside changed source-branch lines."
        ))
    if discarded_ungrounded:
        print(formatter.format_info(
            f"Discarded {len(discarded_ungrounded)} comment(s) without source-branch evidence."
        ))

    # Preserve the old location-only filter as a final guard in case future
    # changes bypass the evidence gate.
    structured_comments, extra_discarded_comments = _filter_comments_to_changed_lines(
        structured_comments,
        diff_truncated,
    )
    if extra_discarded_comments:
        print(formatter.format_info(
            f"Discarded {len(extra_discarded_comments)} comment(s) outside changed PR lines."
        ))

    existing_threads = []
    skipped_duplicates = []
    resolved_reappeared = []
    try:
        comment_plan = tfs.plan_review_comments(repo_name, pr_id, structured_comments)
        structured_comments = comment_plan.get("new_comments", structured_comments)
        skipped_duplicates = comment_plan.get("skipped_duplicates", [])
        resolved_reappeared = comment_plan.get("resolved_reappeared", [])
        existing_threads = comment_plan.get("existing_threads", [])
    except TFSError as exc:
        print(formatter.format_warning(
            f"Could not inspect existing PR comments; duplicate detection skipped: {exc}"
        ))

    if skipped_duplicates:
        print(formatter.format_info(
            f"Skipped {len(skipped_duplicates)} duplicate comment(s) already present on the PR."
        ))

    if resolved_reappeared:
        print(formatter.format_warning(
            f"{len(resolved_reappeared)} previously resolved/closed tool comment(s) "
            "still appear in the latest review."
        ))

    structured_comments, capped_comments = _limit_comments_to_post(
        structured_comments,
        config.max_comments_to_post,
    )
    if capped_comments:
        print(formatter.format_info(
            f"Limited review output to the top {config.max_comments_to_post} "
            f"comment(s); omitted {len(capped_comments)} lower-priority comment(s)."
        ))

    total_discarded_comments = (
        len(discarded_comments)
        + len(discarded_ungrounded)
        + len(extra_discarded_comments)
        + len(capped_comments)
    )
    review_text = _format_structured_review_text(
        structured_comments,
        discarded_count=total_discarded_comments,
        duplicate_count=len(skipped_duplicates),
        resolved_reappeared_count=len(resolved_reappeared),
        metadata_issues=metadata_issues,
    )

    elapsed = time.time() - start_time
    progress.stop(formatter.format_success(f"Review completed in {elapsed:.1f}s"))

    _store_pr_usage(
        config,
        formatter,
        repo_name=repo_name,
        pr_id=pr_id,
        dry_run=dry_run,
        comments_generated=len(structured_comments),
        usage_events=_get_llm_usage_events(llm),
        metadata_issues=metadata_issues,
    )

    # --- Show general review ---
    print(formatter.format_review(review_text))

    # --- Show structured comments preview ---
    print(formatter.format_structured_comments(
        structured_comments,
        discarded_count=total_discarded_comments + len(skipped_duplicates),
    ))

    output_file = config.output_file or getattr(args, "output", "")
    _save_pr_review_output(
        output_file=output_file,
        pr_id=pr_id,
        repo_name=repo_name,
        pr_details=pr_details,
        review_text=review_text,
        was_truncated=was_truncated,
    )

    # --- Post comments to PR ---
    if dry_run:
        if resolved_reappeared:
            print(formatter.format_info(
                f"DRY-RUN: {len(resolved_reappeared)} resolved/closed tool comment(s) "
                "would be reopened."
            ))
        print(f"\n{c.YELLOW}{c.BOLD}🔍 DRY-RUN mode: "
              f"No comments were posted to the PR.{c.RESET}")
        print(f"{c.DIM}   Remove --dry-run to post the comments.{c.RESET}\n")
        return 0

    if resolved_reappeared:
        print(formatter.format_progress(
            f"Reopening {len(resolved_reappeared)} resolved/closed tool comment(s)"
        ))
        reopen_results = tfs.reopen_resolved_tool_comments(
            repo_name,
            pr_id,
            resolved_reappeared,
        )
        reopened_count = sum(1 for result in reopen_results if result.get("success"))
        failed_reopen_count = len(reopen_results) - reopened_count
        if reopened_count:
            print(formatter.format_success(
                f"Reopened {reopened_count} resolved/closed tool comment(s)."
            ))
        if failed_reopen_count:
            print(formatter.format_warning(
                f"Could not reopen {failed_reopen_count} resolved/closed tool comment(s)."
            ))

    if not structured_comments:
        if resolved_reappeared:
            return 0
        print(formatter.format_info("No comments to post."))
        return 0

    # --- Confirmation and selection ---
    if auto_post:
        comments_to_post = structured_comments
    else:
        comments_to_post = _select_comments_to_post(structured_comments, formatter)

    if not comments_to_post:
        print(formatter.format_info("No comments selected for posting."))
        return 0

    # --- Post ---
    print(formatter.format_progress(
        f"Posting {len(comments_to_post)} comments to PR #{pr_id}"
    ))

    try:
        results = tfs.post_review_comments(
            repo_name,
            pr_id,
            comments_to_post,
            review_scope=config.review_scope,
            comment_mode=config.pr_comment_mode,
        )
        print(formatter.format_post_results(results))
    except TFSError as exc:
        print(formatter.format_error(f"Error posting comments: {exc}"))
        return 1

    # --- Post general summary as comment ---
    try:
        summary_body = _build_general_summary_comment(config)
        summary_fingerprint = tfs.text_fingerprint(summary_body)
        if tfs.has_tool_comment_fingerprint(existing_threads, summary_fingerprint):
            print(formatter.format_info("General summary already exists on PR; skipping duplicate."))
            return 0

        summary_comment = tfs.tag_tool_comment(
            summary_body,
            summary_fingerprint,
            kind="summary",
        )
        tfs.post_general_comment(repo_name, pr_id, summary_comment)
        print(formatter.format_success("General summary posted to PR."))
    except TFSError as exc:
        print(formatter.format_warning(f"Could not post general summary: {exc}"))

    return 0


def _select_pr_interactive(tfs, formatter: ReviewFormatter,
                           repo_name=None, author=None,
                           target_branch=None) -> tuple:
    """Interactive PR selection. Returns (pr_id, repo_name) or (None, None)."""
    c = Colors

    print(formatter.format_progress("Fetching Pull Requests list"))

    try:
        prs = tfs.list_pull_requests(
            status="active",
            repository=repo_name,
            author=author,
            target_branch=target_branch,
        )
    except Exception as exc:
        print(formatter.format_error(str(exc)))
        return None, None

    if not prs:
        print(formatter.format_info("No active Pull Requests found."))
        return None, None

    # Show list
    print(formatter.format_pr_list(prs, "Active Pull Requests"))

    # Select
    try:
        choice = input(
            f"\n{c.BOLD}Select PR (list number or PR ID, 0 to cancel): {c.RESET}"
        ).strip()
    except (KeyboardInterrupt, EOFError):
        print("\n")
        return None, None

    if not choice or choice == "0":
        return None, None

    try:
        num = int(choice)
        # If it's a small number, it's a list index
        if 1 <= num <= len(prs):
            selected = prs[num - 1]
            return selected["id"], selected["repository"]
        else:
            # It's a direct PR ID
            for pr in prs:
                if pr["id"] == num:
                    return pr["id"], pr["repository"]
            # Not found in list, try using as direct ID
            return num, repo_name
    except ValueError:
        print(f"{c.RED}Invalid option.{c.RESET}")
        return None, None


def _select_comments_to_post(comments: list[dict],
                              formatter: ReviewFormatter) -> list[dict]:
    """Allows the user to select which comments to post."""
    c = Colors

    print(f"\n{c.BOLD}What would you like to do with the comments?{c.RESET}")
    print(f"  {c.CYAN}1{c.RESET}) Post ALL comments")
    print(f"  {c.CYAN}2{c.RESET}) Select which to post")
    print(f"  {c.CYAN}3{c.RESET}) Post none (cancel)")

    try:
        choice = input(f"\n{c.BOLD}Choose [1-3]: {c.RESET}").strip()
    except (KeyboardInterrupt, EOFError):
        print("\n")
        return []

    if choice == "1":
        return comments
    elif choice == "3" or not choice:
        return []
    elif choice == "2":
        # Individual selection
        selected = []
        for i, comment in enumerate(comments, 1):
            severity = comment.get("severity", "info")
            comment_type = comment.get("type", "suggestion")
            file_info = comment.get("file", "general")
            if comment.get("line", 0) > 0:
                file_info += f":{comment['line']}"

            try:
                ans = input(
                    f"  {c.CYAN}[{i}/{len(comments)}]{c.RESET} "
                    f"{comment_type} ({severity}) at {file_info} - "
                    f"Post? [{c.GREEN}Y{c.RESET}/{c.RED}n{c.RESET}]: "
                ).strip().lower()
            except (KeyboardInterrupt, EOFError):
                print("\n")
                break

            if ans in ("", "y", "yes"):
                selected.append(comment)

        return selected
    else:
        return []


# ---------------------------------------------------------------------------
# List PRs
# ---------------------------------------------------------------------------
def run_list_prs(args: argparse.Namespace, config: ReviewConfig,
                 formatter: ReviewFormatter) -> int:
    """Lists Pull Requests from TFS/Azure DevOps."""
    from src.tfs_client import TFSClient, TFSError

    try:
        tfs = TFSClient(config)
    except TFSError as exc:
        print(formatter.format_error(str(exc)))
        return 1

    repo_name = getattr(args, "repo_name", None) or config.tfs_repository or None
    status = getattr(args, "status", "active")
    author = getattr(args, "author", None)

    print(formatter.format_progress("Fetching Pull Requests list"))

    try:
        prs = tfs.list_pull_requests(
            status=status,
            repository=repo_name,
            author=author,
        )
    except TFSError as exc:
        print(formatter.format_error(str(exc)))
        return 1

    print(formatter.format_pr_list(prs, f"Pull Requests ({status})"))
    return 0


# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------
def run_usage(args: argparse.Namespace, config: ReviewConfig,
              formatter: ReviewFormatter) -> int:
    """Lists reviewed PRs and shows token/cost usage for the selected PR."""
    usage_file = getattr(args, "usage_file", None) or config.usage_file
    resolved_usage_file = resolve_usage_file(usage_file)

    try:
        records = load_usage_records(usage_file)
    except OSError as exc:
        print(formatter.format_error(f"Could not read usage file: {exc}"))
        return 1

    summaries = summarize_usage_by_pr(records)
    if not summaries:
        print(formatter.format_info(
            f"No PR usage records found at {resolved_usage_file}."
        ))
        return 0

    print(_format_usage_pr_list(summaries, resolved_usage_file))
    selected = _select_usage_summary_interactive(summaries)
    if selected is None:
        return 0

    print(_format_usage_details(selected))
    return 0


# ---------------------------------------------------------------------------
# Interactive mode
# ---------------------------------------------------------------------------
def interactive_mode() -> int:
    """Main interactive mode with selection menu."""
    c = Colors

    print(f"\n{c.BLUE}{c.BOLD}{'═' * 60}{c.RESET}")
    print(f"{c.BLUE}{c.BOLD}  🤖 AI Code Review v{VERSION} - Interactive Mode{c.RESET}")
    print(f"{c.BLUE}{c.BOLD}{'═' * 60}{c.RESET}")

    # Load config to show current state
    config = ReviewConfig.load()
    print(f"\n{c.CYAN}   LLM: {config.get_provider_info()}{c.RESET}")

    # Main menu
    print(f"\n{c.BOLD}What would you like to do?{c.RESET}\n")
    print(f"  {c.CYAN}{c.BOLD}── Pull Requests (recommended) ──{c.RESET}")
    print(f"  {c.CYAN}1{c.RESET}) 🌟 Pull Request Review (list PRs and select)")
    print(f"  {c.CYAN}2{c.RESET}) 📋 List active Pull Requests")
    print(f"  {c.CYAN}3{c.RESET}) 📊 Check review usage")
    print(f"")
    print(f"  {c.CYAN}{c.BOLD}── Other ──{c.RESET}")
    print(f"  {c.CYAN}4{c.RESET}) Current configuration")
    print(f"  {c.CYAN}0{c.RESET}) Exit")

    try:
        choice = input(f"\n{c.BOLD}Choose [0-4]: {c.RESET}").strip()
    except (KeyboardInterrupt, EOFError):
        print("\n")
        return 0

    if choice == "0":
        return 0

    # --- PR Review ---
    if choice == "1":
        return _interactive_pr_review(config)

    if choice == "2":
        return _interactive_list_prs(config)

    if choice == "3":
        return _interactive_usage(config)

    if choice == "4":
        _show_config(config)
        return 0

    print(f"{c.RED}Invalid option.{c.RESET}")
    return 1


def _interactive_pr_review(config: ReviewConfig) -> int:
    """Interactive PR review workflow."""
    c = Colors

    # Ask for mode
    print(f"\n{c.BOLD}Review options:{c.RESET}")
    print(f"  {c.CYAN}1{c.RESET}) Full review with comments on PR")
    print(f"  {c.CYAN}2{c.RESET}) Dry-run (review without posting comments)")

    try:
        mode = input(f"{c.BOLD}Choose [1-2, default=1]: {c.RESET}").strip()
    except (KeyboardInterrupt, EOFError):
        print("\n")
        return 0

    dry_run = mode == "2"

    # Ask for verbosity
    verbosity = _ask_verbosity()

    # Build arguments
    argv = ["pr-review"]
    if dry_run:
        argv.append("--dry-run")
    if verbosity:
        argv.append(f"--{verbosity}")

    parser = build_parser()
    parsed = parser.parse_args(argv)
    return run_review(parsed)


def _interactive_list_prs(config: ReviewConfig) -> int:
    """Lists PRs interactively."""
    argv = ["list-prs"]
    parser = build_parser()
    parsed = parser.parse_args(argv)
    return run_review(parsed)


def _interactive_usage(config: ReviewConfig) -> int:
    """Shows usage records interactively."""
    argv = ["usage"]
    parser = build_parser()
    parsed = parser.parse_args(argv)
    return run_review(parsed)


def _ask_verbosity() -> str:
    """Asks for the verbosity mode."""
    c = Colors
    print(f"\n{c.BOLD}Review mode:{c.RESET}")
    print(f"  {c.CYAN}1{c.RESET}) Quick")
    print(f"  {c.CYAN}2{c.RESET}) Detailed")
    print(f"  {c.CYAN}3{c.RESET}) Security")

    try:
        mode_choice = input(f"{c.BOLD}Choose [1-3, default=2]: {c.RESET}").strip()
    except (KeyboardInterrupt, EOFError):
        print("\n")
        return "detailed"

    verbosity_map = {"1": "quick", "2": "detailed", "3": "security"}
    return verbosity_map.get(mode_choice, "detailed")


def _show_config(config: ReviewConfig) -> None:
    """Shows the current configuration."""
    c = Colors
    has_effective_key = bool(config.get_effective_api_key())
    print(f"\n{c.BOLD}⚙️  Current Configuration:{c.RESET}\n")
    print(f"  {c.CYAN}LLM Provider:{c.RESET}  {config.llm_provider}")
    print(f"  {c.CYAN}Model:{c.RESET}         {config.get_effective_model()}")
    print(f"  {c.CYAN}API Key:{c.RESET}       {'✅ Configured' if has_effective_key else '❌ Not configured'}")
    print(f"  {c.CYAN}Temperature:{c.RESET}   {config.temperature}")
    print(f"  {c.CYAN}Max Tokens:{c.RESET}    {config.max_tokens}")
    print(f"  {c.CYAN}Language:{c.RESET}      {config.review_language}")
    print(f"  {c.CYAN}Verbosity:{c.RESET}     {config.verbosity}")
    print(f"  {c.CYAN}Format:{c.RESET}        {config.output_format}")
    print(f"\n  {c.CYAN}TFS URL:{c.RESET}      {config.tfs_base_url or '(not configured)'}")
    print(f"  {c.CYAN}TFS Project:{c.RESET}   {config.tfs_project or '(not configured)'}")
    print(f"  {c.CYAN}TFS PAT:{c.RESET}       {'✅ Configured' if config.tfs_pat else '❌ Not configured'}")
    print(f"  {c.CYAN}Dry Run:{c.RESET}       {config.dry_run}")
    print()


# ---------------------------------------------------------------------------
# Init command
# ---------------------------------------------------------------------------
def cmd_init() -> int:
    """Copies a config.yaml template and review_prompt.md to the current working directory.

    Creates a ``config.yaml`` file pre-populated with all available options
    and inline documentation, and a ``review_prompt.md`` file with default
    review style rules. If either file already exists in the current directory
    the user is prompted for confirmation before overwriting.

    Both files are bundled with the package at ``src/prompts/`` and are
    resolved at runtime via :mod:`importlib.resources`, so they work
    regardless of how the package was installed.

    Returns:
        int: Exit code. ``0`` on success or user cancellation, ``1`` on error.

    Example:
        Run from any directory to bootstrap a new configuration::

            $ ai-review init
            ✅ config.yaml created at: /home/user/my-project/config.yaml
            ✅ review_prompt.md created at: /home/user/my-project/review_prompt.md
               Edit them to add your credentials, preferences and review rules.
    """
    cwd = os.getcwd()
    c = Colors()

    # --- config.yaml ---
    config_dest = os.path.join(cwd, "config.yaml")
    if os.path.exists(config_dest):
        print(f"{c.YELLOW}config.yaml already exists in the current directory.{c.RESET}")
        answer = input("Overwrite? [y/N] ").strip().lower()
        if answer != "y":
            print("Aborted.")
            return 0

    try:
        ref = importlib.resources.files("src.prompts").joinpath("config.yaml.template")
        config_content = ref.read_text(encoding="utf-8")
    except (FileNotFoundError, TypeError) as exc:
        print(f"{c.RED}Error: could not locate config template: {exc}{c.RESET}")
        return 1

    with open(config_dest, "w", encoding="utf-8") as fh:
        fh.write(config_content)

    # --- review_prompt.md ---
    prompt_dest = os.path.join(cwd, "review_prompt.md")
    if os.path.exists(prompt_dest):
        print(f"{c.YELLOW}review_prompt.md already exists in the current directory.{c.RESET}")
        answer = input("Overwrite? [y/N] ").strip().lower()
        if answer != "y":
            print(f"{c.GREEN}✅ config.yaml created at:{c.RESET} {config_dest}")
            print("   Skipped review_prompt.md (kept existing).")
            return 0

    try:
        ref = importlib.resources.files("src.prompts").joinpath("review_prompt.md.template")
        prompt_content = ref.read_text(encoding="utf-8")
    except (FileNotFoundError, TypeError) as exc:
        print(f"{c.RED}Error: could not locate review_prompt template: {exc}{c.RESET}")
        return 1

    with open(prompt_dest, "w", encoding="utf-8") as fh:
        fh.write(prompt_content)

    print(f"{c.GREEN}✅ config.yaml created at:{c.RESET} {config_dest}")
    print(f"{c.GREEN}✅ review_prompt.md created at:{c.RESET} {prompt_dest}")
    print("   Edit them to add your credentials, preferences and review rules.")
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> int:
    """Main entry point."""
    # If no arguments, use interactive mode
    if len(sys.argv) == 1:
        return interactive_mode()

    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    if args.command == "init":
        return cmd_init()

    return run_review(args)


if __name__ == "__main__":
    sys.exit(main())
