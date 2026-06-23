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
import json
import os
import re
import sys
import time
import threading
from dataclasses import dataclass, field


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
from src.local_repo import LocalRepoContext, LocalRepoError, LocalRepoManager
from src.llm_client import LLMClient, LLMError
from src.formatter import ReviewFormatter, Colors, save_output
from src.rag_engine import obter_contexto_rag
from src.usage_tracker import (
    append_usage_record,
    build_pr_usage_record,
    load_usage_records,
    resolve_usage_file,
    summarize_usage_by_pr,
)
from src import __version__ as VERSION


REVIEW_SCOPE_CHOICES = ["diff_only", "diff_with_context"]


@dataclass
class ReviewBatch:
    """One token-bounded review unit for a subset of PR changes."""

    id: int
    diff: str
    changed_files: list[str] = field(default_factory=list)
    files_summary: list[dict] = field(default_factory=list)
    estimated_tokens: int = 0


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
            "Review scope: diff_with_context (default) or diff_only"
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


def _comment_location(comment: dict) -> str:
    """Formats a comment location for diagnostic output."""
    file_path = str(comment.get("file", "") or "general")
    line = comment.get("line", 0)
    end_line = comment.get("end_line", 0)
    if line and end_line and end_line > line + 1:
        return f"{file_path}:{line}-{end_line - 1}"
    if line:
        return f"{file_path}:{line}"
    return file_path


def _append_comment_diagnostics(
    lines: list[str],
    title: str,
    comments: list[dict],
) -> None:
    """Appends discarded or skipped comments for terminal/save diagnostics."""
    if not comments:
        return

    lines.append("")
    lines.append(f"### {title}")
    lines.append(
        "Logged for diagnosis only; these comments are not eligible for posting."
    )

    for index, comment in enumerate(comments, 1):
        comment_type = str(comment.get("type", "suggestion")).title()
        severity = str(comment.get("severity", "info")).upper()
        lines.append("")
        lines.append(
            f"{index}. {_comment_location(comment)} - {comment_type} ({severity})"
        )

        body = str(comment.get("comment", "")).strip()
        if body:
            lines.append(f"   Comment: {body}")

        for label, key in (
            ("Reason", "discard_reason"),
            ("Anchor", "anchor_code"),
            ("Problematic code", "problematic_code"),
            ("Evidence", "evidence"),
            ("Suggestion", "suggestion"),
            ("Reference", "reference"),
        ):
            value = str(comment.get(key, "")).strip()
            if value:
                lines.append(f"   {label}: {value}")


def _format_structured_review_text(
    comments: list[dict],
    *,
    discarded_count: int = 0,
    duplicate_count: int = 0,
    resolved_reappeared_count: int = 0,
    metadata_issues: list[str] | None = None,
    discarded_location_comments: list[dict] | None = None,
    discarded_grounding_comments: list[dict] | None = None,
    discarded_changed_line_comments: list[dict] | None = None,
    discarded_quality_comments: list[dict] | None = None,
    duplicate_generated_comments: list[dict] | None = None,
    capped_comments: list[dict] | None = None,
    duplicate_comments: list[dict] | None = None,
) -> str:
    """Builds the terminal/saved review with kept and diagnostic comments."""
    lines: list[str] = ["## Structured Review"]
    metadata_issues = metadata_issues or []
    discarded_location_comments = discarded_location_comments or []
    discarded_grounding_comments = discarded_grounding_comments or []
    discarded_changed_line_comments = discarded_changed_line_comments or []
    discarded_quality_comments = discarded_quality_comments or []
    duplicate_generated_comments = duplicate_generated_comments or []
    capped_comments = capped_comments or []
    duplicate_comments = duplicate_comments or []

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
                "grounding, quality, or changed-line validation."
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

    _append_comment_diagnostics(
        lines,
        "Discarded: Outside Changed Source Lines",
        discarded_location_comments,
    )
    _append_comment_diagnostics(
        lines,
        "Discarded: Failed Source Evidence Checks",
        discarded_grounding_comments,
    )
    _append_comment_diagnostics(
        lines,
        "Discarded: Failed Final Changed-Line Check",
        discarded_changed_line_comments,
    )
    _append_comment_diagnostics(
        lines,
        "Discarded: Failed Quality Checks",
        discarded_quality_comments,
    )
    _append_comment_diagnostics(
        lines,
        "Discarded: Duplicate Generated Comments",
        duplicate_generated_comments,
    )
    _append_comment_diagnostics(
        lines,
        "Omitted: Lower Priority Than Comment Limit",
        capped_comments,
    )
    _append_comment_diagnostics(
        lines,
        "Skipped: Duplicate Existing PR Comments",
        duplicate_comments,
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


def _comment_quality_issue(comment: dict) -> str:
    """Returns why a grounded comment is still too weak to post, or empty."""
    allowed_types = {
        "bug",
        "security",
        "performance",
        "null_safety",
        "data_integrity",
        "api_contract",
        "error_handling",
        "resource",
        "work_item",
        "suggestion",
    }
    allowed_severities = {"critical", "high", "medium", "low", "info"}
    comment_type = str(comment.get("type", "")).strip().lower()
    severity = str(comment.get("severity", "")).strip().lower()
    body = str(comment.get("comment", "")).strip()
    normalized_body = re.sub(r"\s+", " ", body.lower()).strip(" .:;!-")

    if comment_type not in allowed_types:
        return f"unsupported issue type '{comment_type or 'missing'}'"
    if severity not in allowed_severities:
        return f"unsupported severity '{severity or 'missing'}'"
    if len(body) < 12:
        return "comment is too short to explain an actionable problem"
    if not re.search(r"[A-Za-zÀ-ÿ]{4,}", body):
        return "comment does not contain a meaningful problem statement"

    vague_comments = {
        "possible issue",
        "potential issue",
        "possible bug",
        "potential bug",
        "issue here",
        "bug here",
        "problem here",
        "fix this",
        "check this",
        "review this",
        "needs improvement",
        "improve this",
        "bad code",
        "not good",
    }
    if normalized_body in vague_comments:
        return "comment is too vague to be actionable"

    if comment_type == "suggestion" and not str(comment.get("suggestion", "")).strip():
        return "suggestion comment has no concrete suggested fix"

    return ""


def _filter_comments_to_quality(comments: list[dict]) -> tuple[list[dict], list[dict]]:
    """Keeps grounded comments only when they are actionable enough to post."""
    kept: list[dict] = []
    discarded: list[dict] = []
    for comment in comments:
        issue = _comment_quality_issue(comment)
        if issue:
            enriched = dict(comment)
            enriched["discard_reason"] = issue
            discarded.append(enriched)
        else:
            kept.append(comment)
    return kept, discarded


def _normalize_dedupe_text(value: object) -> str:
    """Returns a stable text key for generated-comment duplicate checks."""
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _generated_comment_key(comment: dict) -> tuple:
    """Builds a conservative key for duplicate model findings in one run."""
    line, end_line = _comment_line_range(comment)
    code_anchor = (
        _normalize_dedupe_text(comment.get("problematic_code"))
        or _normalize_dedupe_text(comment.get("anchor_code"))
        or _normalize_dedupe_text(comment.get("evidence"))
    )
    return (
        _normalize_review_path(comment.get("file", "")),
        line,
        end_line,
        str(comment.get("type", "")).strip().lower(),
        code_anchor,
    )


def _better_duplicate_comment(candidate: dict, current: dict) -> bool:
    """Prefers higher severity, then richer text, for generated duplicates."""
    candidate_rank = _severity_rank(candidate)
    current_rank = _severity_rank(current)
    if candidate_rank != current_rank:
        return candidate_rank < current_rank
    return len(str(candidate.get("comment", ""))) > len(str(current.get("comment", "")))


def _deduplicate_generated_comments(comments: list[dict]) -> tuple[list[dict], list[dict]]:
    """Removes repeated findings produced by a single model response."""
    kept_by_key: dict[tuple, dict] = {}
    order: list[tuple] = []
    duplicates: list[dict] = []

    for index, comment in enumerate(comments):
        key = _generated_comment_key(comment)
        if not key[-1]:
            key = (*key, index)
            order.append(key)
            kept_by_key[key] = comment
            continue

        existing = kept_by_key.get(key)
        if existing is None:
            kept_by_key[key] = comment
            order.append(key)
            continue

        duplicate = dict(existing if _better_duplicate_comment(comment, existing) else comment)
        duplicate["discard_reason"] = "duplicate generated finding for the same changed line"
        duplicates.append(duplicate)
        if _better_duplicate_comment(comment, existing):
            kept_by_key[key] = comment

    return [kept_by_key[key] for key in order if key in kept_by_key], duplicates


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


def _comment_mentions_non_anchor_terms(comment: dict, anchor_text: str) -> bool:
    """Detects quoted claim terms that are absent from the reviewable anchor."""
    for term in _quoted_code_terms(
        comment.get("comment", ""),
        comment.get("anchor_code", ""),
        comment.get("problematic_code", ""),
    ):
        if term not in anchor_text:
            return True
    return False


def _changed_text_for_required_lines(hunk: dict, required_lines: set[int]) -> str:
    """Returns the exact changed source text covered by a comment range."""
    line_text = hunk.get("line_text", {})
    lines = []
    for line_no in sorted(required_lines):
        if line_no not in line_text:
            return ""
        lines.append(str(line_text[line_no]))
    return "\n".join(lines)


def _repair_comment_grounding_fields(comment: dict, anchor_text: str) -> dict:
    """Fills missing exact-code fields when evidence already matches the anchor."""
    repaired = dict(comment)
    exact_fields = [
        str(repaired.get("evidence", "")).strip(),
        str(repaired.get("anchor_code", "")).strip(),
        str(repaired.get("problematic_code", "")).strip(),
    ]
    replacement = next(
        (
            value for value in exact_fields
            if value and _text_contains_evidence(anchor_text, value)
        ),
        "",
    )
    if not replacement:
        return repaired

    if replacement and not str(repaired.get("anchor_code", "")).strip():
        repaired["anchor_code"] = replacement
    if replacement and not str(repaired.get("problematic_code", "")).strip():
        repaired["problematic_code"] = replacement
    if replacement and not str(repaired.get("evidence", "")).strip():
        repaired["evidence"] = replacement

    return repaired


def _suggestion_already_applied(comment: dict, anchor_text: str, source_text: str) -> bool:
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
        if _text_contains_evidence(anchor_text, suggested_code):
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
        anchor_code = str(comment.get("anchor_code", "")).strip()
        problematic_code = str(comment.get("problematic_code", "")).strip()
        anchor_text = _changed_text_for_required_lines(hunk, required_lines)
        source_text = source_file_contents.get(file_path)
        has_full_source_text = source_text is not None
        if source_text is None:
            source_text = anchor_text
        source_range = (
            _source_text_for_range(source_text, line, end_line)
            if has_full_source_text else anchor_text
        )

        if not anchor_text:
            discarded_grounding.append(comment)
            continue

        comment = _repair_comment_grounding_fields(comment, anchor_text)
        if not evidence:
            evidence = str(comment.get("evidence", "")).strip()
            if not evidence:
                discarded_grounding.append(comment)
                continue
        anchor_code = str(comment.get("anchor_code", "")).strip()
        problematic_code = str(comment.get("problematic_code", "")).strip()

        if not _text_contains_evidence(anchor_text, anchor_code):
            discarded_grounding.append(comment)
            continue

        if not _text_contains_evidence(anchor_text, problematic_code):
            discarded_grounding.append(comment)
            continue

        if not _text_contains_evidence(source_range, problematic_code):
            discarded_grounding.append(comment)
            continue

        if not _text_contains_evidence(source_text, problematic_code):
            discarded_grounding.append(comment)
            continue

        if _comment_mentions_absent_source_terms(comment, source_text):
            discarded_grounding.append(comment)
            continue

        if _comment_mentions_non_anchor_terms(comment, anchor_text):
            discarded_grounding.append(comment)
            continue

        if _suggestion_already_applied(comment, anchor_text, source_text):
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


def _split_diff_file_sections(diff: str) -> list[str]:
    """Splits a unified diff into complete file sections."""
    sections: list[list[str]] = []
    current: list[str] = []
    for line in (diff or "").splitlines():
        if line.startswith("diff --git") and current:
            sections.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        sections.append(current)
    return ["\n".join(section) for section in sections if any(line.strip() for line in section)]


def _diff_section_path(section: str) -> str:
    """Returns the source/right-side path represented by a diff section."""
    for line in (section or "").splitlines():
        if line.startswith("+++ "):
            value = line[4:].strip().split("\t", 1)[0]
            if value != "/dev/null":
                return _normalize_review_path(value)
        if line.startswith("diff --git ") and " b/" in line:
            return _normalize_review_path(line.split(" b/", 1)[1])
    return ""


def _filter_files_summary_for_diff(files_summary: list[dict], diff: str) -> list[dict]:
    """Keeps file summary rows that belong to a batch diff."""
    paths = set(_changed_lines_by_file(diff).keys())
    if not paths:
        paths = {
            path for path in (_diff_section_path(section) for section in _split_diff_file_sections(diff))
            if path
        }
    return [
        item for item in files_summary
        if _normalize_review_path(item.get("file", "")) in paths
    ]


def _split_diff_section_hunks(section: str) -> list[str]:
    """Splits a single file diff section into complete hunk-level sections."""
    lines = section.splitlines()
    first_hunk = next((index for index, line in enumerate(lines) if line.startswith("@@")), -1)
    if first_hunk < 0:
        return [section]

    header = lines[:first_hunk]
    hunks: list[list[str]] = []
    current: list[str] = []
    for line in lines[first_hunk:]:
        if line.startswith("@@") and current:
            hunks.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        hunks.append(current)
    return [
        "\n".join([*header, *hunk])
        for hunk in hunks
        if hunk
    ]


def _batch_token_target(llm: LLMClient) -> int:
    """Returns a conservative target token budget for each review batch."""
    limit = llm.structured_review_prompt_token_limit()
    if not isinstance(limit, int) or limit <= 0:
        limit = 60000
    return max(1, int(limit * 0.65))


def _estimate_review_tokens(
    llm: LLMClient,
    *,
    diff: str,
    files_summary: list[dict],
    context: str,
    review_scope: str,
    project_context: str,
    work_item_context: str,
    source_files_context: str,
    pr_description_context: str,
) -> int:
    """Estimates review prompt tokens with a safe fallback for test doubles."""
    estimate = llm.estimate_structured_review_prompt_tokens(
        diff=diff,
        files_summary=files_summary,
        context=context,
        review_scope=review_scope,
        project_context=project_context,
        work_item_context=work_item_context,
        source_files_context=source_files_context,
        pr_description_context=pr_description_context,
    )
    return estimate if isinstance(estimate, int) else 0


def _plan_review_batches(
    *,
    llm: LLMClient,
    diff: str,
    files_summary: list[dict],
    context: str,
    review_scope: str,
    project_context: str,
    work_item_context: str,
    source_files_context: str,
    pr_description_context: str,
) -> list[ReviewBatch]:
    """Plans token-bounded review batches without dropping changed lines."""
    target = _batch_token_target(llm)
    full_tokens = _estimate_review_tokens(
        llm,
        diff=diff,
        files_summary=files_summary,
        context=context,
        review_scope=review_scope,
        project_context=project_context,
        work_item_context=work_item_context,
        source_files_context=source_files_context,
        pr_description_context=pr_description_context,
    )
    if full_tokens and full_tokens <= target:
        return [
            ReviewBatch(
                id=1,
                diff=diff,
                changed_files=sorted(_changed_lines_by_file(diff).keys()),
                files_summary=files_summary,
                estimated_tokens=full_tokens,
            )
        ]

    batches: list[ReviewBatch] = []
    current_sections: list[str] = []
    batch_id = 1

    def make_batch(batch_diff: str, estimated_tokens: int = 0) -> ReviewBatch:
        nonlocal batch_id
        batch = ReviewBatch(
            id=batch_id,
            diff=batch_diff,
            changed_files=sorted(_changed_lines_by_file(batch_diff).keys()),
            files_summary=_filter_files_summary_for_diff(files_summary, batch_diff),
            estimated_tokens=estimated_tokens,
        )
        batch_id += 1
        return batch

    def estimate(batch_diff: str) -> int:
        return _estimate_review_tokens(
            llm,
            diff=batch_diff,
            files_summary=_filter_files_summary_for_diff(files_summary, batch_diff),
            context=context,
            review_scope=review_scope,
            project_context=project_context,
            work_item_context=work_item_context,
            source_files_context=source_files_context,
            pr_description_context=pr_description_context,
        )

    for section in _split_diff_file_sections(diff):
        section_tokens = estimate(section)
        section_units = [section]
        if section_tokens and section_tokens > target:
            section_units = _split_diff_section_hunks(section)
            if len(section_units) == 1:
                first_line = next(
                    (line for line in section.splitlines() if line.startswith("diff --git")),
                    "[unknown file section]",
                )
                raise LLMError(
                    "A single changed file is too large to validate without truncation "
                    f"for the configured batch target ({target} estimated tokens): "
                    f"{first_line}"
                )

        for unit in section_units:
            unit_tokens = estimate(unit)
            if unit_tokens and unit_tokens > target:
                first_line = next(
                    (line for line in unit.splitlines() if line.startswith("diff --git")),
                    "[unknown file section]",
                )
                hunk_line = next(
                    (line for line in unit.splitlines() if line.startswith("@@")),
                    "[unknown hunk]",
                )
                raise LLMError(
                    "A single changed hunk is too large to validate without truncation "
                    f"for the configured batch target ({target} estimated tokens): "
                    f"{first_line} {hunk_line}"
                )

            candidate_sections = [*current_sections, unit]
            candidate = "\n".join(candidate_sections)
            candidate_tokens = estimate(candidate)
            if current_sections and candidate_tokens and candidate_tokens > target:
                batch_diff = "\n".join(current_sections)
                batches.append(make_batch(batch_diff, estimate(batch_diff)))
                current_sections = [unit]
            else:
                current_sections = candidate_sections

    if current_sections:
        batch_diff = "\n".join(current_sections)
        batches.append(make_batch(batch_diff, estimate(batch_diff)))
    return batches or [make_batch(diff, full_tokens)]


def _chunk_diff_for_prompt_budget(**kwargs) -> list[str]:
    """Compatibility wrapper returning planned batch diffs."""
    return [batch.diff for batch in _plan_review_batches(**kwargs)]


def _changed_file_records_for_batch(changed_files: list[dict], batch: ReviewBatch) -> list[dict]:
    """Returns changed-file metadata records that belong to one review batch."""
    batch_paths = {_normalize_review_path(path) for path in batch.changed_files}
    return [
        item for item in changed_files
        if _normalize_review_path(item.get("path", "")) in batch_paths
    ]


def _extract_batch_terms(batch: ReviewBatch) -> list[str]:
    """Extracts deterministic keywords from a batch for context excerpting."""
    terms: list[str] = []
    seen: set[str] = set()
    for path in batch.changed_files:
        parts = re.split(r"[^A-Za-z0-9_]+", path)
        for part in parts:
            if len(part) >= 3 and part.lower() not in seen:
                seen.add(part.lower())
                terms.append(part)
    for line in batch.diff.splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", line):
            key = token.lower()
            if key not in seen:
                seen.add(key)
                terms.append(token)
            if len(terms) >= 40:
                return terms
    return terms


def _excerpt_context_for_batch(
    context: str,
    batch: ReviewBatch,
    *,
    max_chars: int,
    label: str,
) -> str:
    """Returns a deterministic excerpt of large read-only context for a batch."""
    text = str(context or "").strip()
    if not text or max_chars <= 0 or len(text) <= max_chars:
        return text

    terms = [term.lower() for term in _extract_batch_terms(batch)]
    lines = text.splitlines()
    selected_indexes: set[int] = set()
    for index, line in enumerate(lines):
        normalized = line.lower()
        if line.startswith("#") or any(term and term in normalized for term in terms):
            for nearby in range(max(0, index - 2), min(len(lines), index + 3)):
                selected_indexes.add(nearby)

    selected_lines = [lines[index] for index in sorted(selected_indexes)]
    excerpt = "\n".join(selected_lines).strip()
    if not excerpt:
        excerpt = text[:max_chars].rstrip()
    elif len(excerpt) > max_chars:
        excerpt = excerpt[:max_chars].rstrip()

    return (
        f"[{label} excerpted for review batch {batch.id}; omitted text must not be "
        "treated as an unmet requirement.]\n"
        f"{excerpt}"
    )


def _slice_repo_map_json(project_manifest: str, batch: ReviewBatch, max_files: int = 200) -> str:
    """Narrows a repository structure JSON payload to paths relevant to a batch."""
    text = str(project_manifest or "").strip()
    if not text:
        return ""
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return text[:60000]

    batch_paths = {_normalize_review_path(path) for path in batch.changed_files}
    batch_dirs = {
        os.path.dirname(path).replace("\\", "/")
        for path in batch_paths
        if os.path.dirname(path)
    }
    batch_roots = {path.split("/", 1)[0] for path in batch_paths if path}
    selected = []
    for item in payload.get("files", []):
        path = _normalize_review_path(item.get("path", ""))
        directory = os.path.dirname(path).replace("\\", "/")
        root = path.split("/", 1)[0] if path else ""
        if path in batch_paths or directory in batch_dirs or root in batch_roots:
            selected.append(item)
        if len(selected) >= max_files:
            break

    directories = sorted({
        directory
        for item in selected
        for directory in _parent_review_directories(str(item.get("path", "")))
    })
    sliced = {
        "repository": payload.get("repository", ""),
        "ref": payload.get("ref", ""),
        "batch_id": batch.id,
        "directories": directories,
        "files": selected,
        "counts": {
            "directories": len(directories),
            "files": len(selected),
            "total_repository_files": payload.get("counts", {}).get("files", len(payload.get("files", []))),
        },
        "note": "Repository map sliced for this review batch.",
    }
    return json.dumps(sliced, ensure_ascii=False, indent=2, sort_keys=True)


def _parent_review_directories(path: str) -> list[str]:
    """Returns parent directories for a normalized review path."""
    parts = _normalize_review_path(path).split("/")[:-1]
    return ["/".join(parts[:index]) for index in range(1, len(parts) + 1)]


def _batch_context_budget(llm: LLMClient) -> int:
    """Returns a conservative character budget for per-batch fetched context."""
    return max(4000, _batch_token_target(llm) * 2)


def _review_pr_structured_with_complete_diff(
    *,
    llm: LLMClient,
    formatter: ReviewFormatter,
    diff: str,
    files_summary: list[dict],
    context: str,
    review_scope: str,
    project_context: str,
    work_item_context: str,
    source_files_context: str,
    pr_description_context: str,
    local_context: LocalRepoContext | None = None,
    tfs=None,
    config: ReviewConfig | None = None,
    repo_name: str = "",
    source_branch: str = "",
    changed_files: list[dict] | None = None,
    project_manifest: str = "",
    user_context: str = "",
    project_context_mode: str = "on_demand",
) -> list[dict]:
    """Reviews all diff file sections, chunking only when the provider needs it."""
    if review_scope != "diff_with_context":
        return llm.review_pr_structured(
            diff=diff,
            files_summary=files_summary,
            context=context,
            review_scope=review_scope,
            project_context=project_context,
            work_item_context=work_item_context,
            source_files_context=source_files_context,
            pr_description_context=pr_description_context,
        )

    batches = _plan_review_batches(
        llm=llm,
        diff=diff,
        files_summary=files_summary,
        context=context,
        review_scope=review_scope,
        project_context="",
        work_item_context=work_item_context,
        source_files_context="",
        pr_description_context=pr_description_context,
    )
    if len(batches) > 1:
        print(formatter.format_info(
            f"Validating all changes in {len(batches)} token-safe batch(es)."
        ))

    comments: list[dict] = []
    changed_files = changed_files or []
    for batch in batches:
        if len(batches) > 1:
            print(formatter.format_info(
                f"Validating review batch {batch.id}/{len(batches)} ({len(batch.changed_files)} file(s))."
            ))
        batch_source_context = source_files_context
        if local_context is not None and config is not None:
            try:
                batch_source_context = local_context.get_changed_files_context(
                    source_branch,
                    _changed_file_records_for_batch(changed_files, batch),
                    max_chars=_batch_context_budget(llm),
                    file_max_chars=max(2000, _batch_context_budget(llm) // 2),
                )
            except LocalRepoError as exc:
                print(formatter.format_warning(
                    f"Could not load changed-file context for batch {batch.id}; continuing with diff only: {exc}"
                ))

        batch_project_context = project_context
        batch_manifest = _slice_repo_map_json(project_manifest, batch)
        if config is not None and (project_context_mode or "on_demand").lower() == "full":
            try:
                manifest_payload = json.loads(batch_manifest) if batch_manifest else {}
                requested_paths = [
                    str(item.get("path", ""))
                    for item in manifest_payload.get("files", [])
                    if item.get("path")
                ]
                if local_context is not None and requested_paths:
                    batch_project_context = local_context.get_files_context(
                        source_branch,
                        requested_paths,
                        title=f"Repository context for review batch {batch.id}",
                        intro=(
                            "These files are read-only support context selected for "
                            "this review batch. The review target remains the batch diff only."
                        ),
                        max_files=min(len(requested_paths), 50),
                        max_chars=_batch_context_budget(llm),
                        file_max_chars=max(2000, _batch_context_budget(llm) // 4),
                        include_all_files=True,
                    )
            except (LocalRepoError, ValueError, TypeError) as exc:
                print(formatter.format_warning(
                    f"Could not load repository context for batch {batch.id}; continuing without it: {exc}"
                ))
        elif config is not None and (project_context_mode or "on_demand").lower() == "on_demand":
            if batch_manifest and (local_context is not None or tfs is not None):
                batch_project_context = _build_on_demand_project_context(
                    llm=llm,
                    tfs=tfs,
                    config=config,
                    formatter=formatter,
                    local_context=local_context,
                    pr_description_context=_excerpt_context_for_batch(
                        pr_description_context,
                        batch,
                        max_chars=max(2000, _batch_context_budget(llm) // 6),
                        label="PR description/spec context",
                    ),
                    repo_name=repo_name,
                    source_branch=source_branch,
                    diff=batch.diff,
                    files_summary=batch.files_summary,
                    user_context=user_context,
                    work_item_context=_excerpt_context_for_batch(
                        work_item_context,
                        batch,
                        max_chars=max(2000, _batch_context_budget(llm) // 6),
                        label="Work item context",
                    ),
                    source_files_context=batch_source_context,
                    unlimited_context=False,
                    project_manifest=batch_manifest,
                )

        batch_pr_context = _excerpt_context_for_batch(
            pr_description_context,
            batch,
            max_chars=max(3000, _batch_context_budget(llm) // 5),
            label="PR description/spec context",
        )
        batch_work_context = _excerpt_context_for_batch(
            work_item_context,
            batch,
            max_chars=max(3000, _batch_context_budget(llm) // 5),
            label="Work item context",
        )

        chunk_comments = llm.review_pr_structured(
            diff=batch.diff,
            files_summary=batch.files_summary,
            context=_join_context_sections(
                context,
                f"Review batch {batch.id} of {len(batches)}. Validate only REVIEWABLE lines present in this batch; final aggregation will deduplicate findings across batches.",
            ),
            review_scope=review_scope,
            project_context=batch_project_context,
            work_item_context=batch_work_context,
            source_files_context=batch_source_context,
            pr_description_context=batch_pr_context,
        )
        if isinstance(chunk_comments, list):
            comments.extend(chunk_comments)
    return comments


def _build_on_demand_project_context(
    *,
    llm: LLMClient,
    tfs,
    config: ReviewConfig,
    formatter: ReviewFormatter,
    local_context: LocalRepoContext | None,
    pr_description_context: str,
    repo_name: str,
    source_branch: str,
    diff: str,
    files_summary: list[dict],
    user_context: str,
    work_item_context: str,
    source_files_context: str,
    unlimited_context: bool = False,
    project_manifest: str = "",
) -> str:
    """Builds repository context by letting the model request files from a manifest."""
    if not project_manifest:
        try:
            if local_context is not None:
                project_manifest = local_context.map_repo_json(repo_name, source_branch)
            else:
                project_manifest = tfs.get_project_manifest(
                    repo_name,
                    source_branch,
                    max_chars=config.project_context_manifest_max_chars,
                    file_extensions=config.project_context_file_extensions,
                    exclude_patterns=config.project_context_exclude_patterns,
                )
        except Exception as exc:
            print(formatter.format_warning(
                f"Could not load repository structure; continuing without on-demand context: {exc}"
            ))

    if not project_manifest:
        return ""

    print(formatter.format_info(
        f"Repository structure loaded ({len(project_manifest):,} characters). "
        "The model can request additional files from it."
    ))

    fetched_context = ""
    requested_keys: set[str] = set()
    max_rounds = (
        config.project_context_retrieval_max_rounds
        if not unlimited_context else
        max(config.project_context_retrieval_max_rounds, 1)
    )
    for round_index in range(max_rounds):
        if unlimited_context:
            remaining_files = 1000000
            remaining_chars = 1000000000
        else:
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
            pr_description_context=pr_description_context,
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
            if local_context is not None:
                round_context = local_context.get_files_context(
                    source_branch,
                    new_paths,
                    max_files=0 if unlimited_context else remaining_files,
                    max_chars=0 if unlimited_context else remaining_chars,
                    file_max_chars=0 if unlimited_context else config.project_context_retrieval_file_max_chars,
                    include_all_files=unlimited_context,
                )
            else:
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


def _get_repository_metadata(tfs, repo_name: str) -> dict:
    """Returns repository metadata from Azure DevOps/TFS list output."""
    for repo in tfs.list_repositories():
        if str(repo.get("name", "")).lower() == str(repo_name or "").lower():
            return repo
    return {"name": repo_name, "id": "", "url": ""}


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

    # --- Get PR diff via git local ---
    print(formatter.format_progress("Getting Pull Request diff"))

    repo_metadata = {"name": repo_name, "id": "", "url": ""}
    if not (config.tfs_local_repo_path or "").strip():
        try:
            repo_metadata = _get_repository_metadata(tfs, repo_name)
        except TFSError as exc:
            print(formatter.format_error(
                f"Could not resolve repository clone metadata: {exc}"
            ))
            return 1

    try:
        local_resolution = LocalRepoManager(config).ensure_repo_available(
            repository_name=repo_name,
            repository_id=str(repo_metadata.get("id", "")),
            clone_url=str(repo_metadata.get("url", "")),
        )
    except LocalRepoError as exc:
        print(formatter.format_error(str(exc)))
        return 1

    if local_resolution.cloned:
        print(formatter.format_info(
            f"Cloned repository into managed cache: {local_resolution.path}"
        ))
    elif local_resolution.managed:
        print(formatter.format_info(
            f"Using managed repository cache: {local_resolution.path}"
        ))

    git_utils_pr = GitUtils(repo_path=local_resolution.path, pat=config.tfs_pat)
    local_context = LocalRepoContext(local_resolution.path, config)

    try:
        target_ref, source_ref = tfs.obter_dados_pr(pr_id)
    except TFSError as exc:
        print(formatter.format_error(str(exc)))
        return 1

    try:
        git_utils_pr.fetch_merge_commit(target_ref)
        git_utils_pr.fetch_merge_commit(source_ref)
    except GitError as exc:
        print(formatter.format_warning(f"Could not fetch PR branches: {exc}"))

    if local_resolution.managed:
        try:
            local_context.checkout_target_for_managed_cache(target_ref)
        except LocalRepoError as exc:
            print(formatter.format_warning(
                f"Could not align managed clone to target branch for local context: {exc}"
            ))

    try:
        diff = git_utils_pr.get_pr_diff(target_ref, source_ref)
    except GitError as exc:
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
    git_utils = git_utils_pr

    review_scope = (config.review_scope or "diff_with_context").lower()
    validation_diff = diff
    diff_for_review = validation_diff
    was_truncated = False

    if review_scope == "diff_only":
        # Keep the legacy PR-only mode compact. All configured filters and
        # limits apply only to diff_only, never to diff_with_context.
        diff_for_review = git_utils_pr.filter_diff_noise(diff_for_review, config.max_diff_lines)
        if not diff_for_review.strip():
            print(formatter.format_warning("After filtering noise (binary/lock files), the diff is empty."))
            return 0

        if config.file_extensions_filter:
            try:
                diff_for_review = git_utils.filter_diff_by_extensions(
                    diff_for_review,
                    config.file_extensions_filter,
                )
            except GitError as exc:
                print(formatter.format_warning(str(exc)))
                return 0

        # Keep the legacy PR-only mode compact by removing context and deletions.
        diff_for_review = git_utils.filter_diff_additions_only(diff_for_review)
        if not diff_for_review.strip():
            print(formatter.format_warning(
                "After filtering additions only, the diff is empty. No new code to review."
            ))
            return 0

        diff_limited, files_limited, omitted_files = git_utils.limit_diff_files(
            diff_for_review,
            config.max_diff_files,
        )
        if files_limited:
            print(formatter.format_warning(
                f"Diff truncated to {config.max_diff_files} files. "
                f"{omitted_files} file(s) omitted."
            ))

        diff_for_review = diff_limited

        diff_truncated, was_truncated = git_utils.truncate_diff(
            diff_for_review,
            config.max_diff_lines,
        )
        if was_truncated:
            print(formatter.format_warning(
                f"Diff truncated to {config.max_diff_lines} lines per file."
            ))
        diff_for_review = diff_truncated

    files_summary = git_utils.get_changed_files_summary(diff_for_review)

    use_contextual_review = review_scope == "diff_with_context"
    load_changed_file_context = review_scope == "diff_with_context"

    # --- RAG context ---
    rag_context = ""
    if config.rag_enabled and diff_for_review.strip():
        # Verify local branch matches the PR target branch to avoid RAG context contamination.
        _target_branch = target_ref.removeprefix("origin/")
        try:
            _local_branch = git_utils_pr.get_current_branch()
        except GitError as exc:
            print(formatter.format_error(
                f"Cannot determine local branch: {exc}\n"
                "Ensure the local repository is accessible and try again."
            ))
            return 1

        if _local_branch != _target_branch:
            print(formatter.format_error(
                f"Local branch mismatch: you are on '{_local_branch}' "
                f"but this PR targets '{_target_branch}'.\n"
                f"RAG context would be built from the wrong branch, which may corrupt the review.\n"
                f"Please run:  git checkout {_target_branch}"
            ))
            return 1

        print(formatter.format_progress("Loading RAG context from local repository"))
        rag_context = obter_contexto_rag(
            diff_for_review,
            repo_path=git_utils.repo_path,
            max_chars=config.rag_max_chars,
        )
        if rag_context:
            print(formatter.format_info(
                f"RAG context loaded ({len(rag_context):,} characters)."
            ))

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

    pr_description_context = ""
    if config.pr_description_context_enabled:
        print(formatter.format_progress(
            "Getting pull request description and linked spec context"
        ))
        try:
            pr_description_context = tfs.get_pull_request_description_context(
                pr_details,
                max_links=config.pr_description_context_max_links,
                max_chars=config.pr_description_context_max_chars,
                link_max_chars=config.pr_description_context_link_max_chars,
            )
        except TFSError as exc:
            print(formatter.format_warning(
                f"Could not load PR description/spec context; continuing without it: {exc}"
            ))

        if pr_description_context:
            print(formatter.format_info(
                f"PR description/spec context loaded ({len(pr_description_context):,} characters). "
                f"{_review_scope_context_note(config.review_scope)}"
            ))
        if not isinstance(pr_description_context, str):
            pr_description_context = ""

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
    if load_changed_file_context and config.project_context_enabled:
        print(formatter.format_info(
            "Changed-file source context will be loaded per token-safe review batch."
        ))

    project_context = ""
    project_manifest = ""
    project_context_mode = (config.project_context_mode or "on_demand").lower()
    if (
        use_contextual_review
        and config.project_context_enabled
        and project_context_mode == "full"
    ):
        print(formatter.format_info(
            "Full repository context mode will be narrowed per token-safe review batch."
        ))

    try:
        llm = LLMClient(config)
        if (
            use_contextual_review
            and config.project_context_enabled
        ):
            print(formatter.format_progress(
                "Preparing repository structure for token-safe batch context"
            ))
            try:
                project_manifest = local_context.map_repo_json(
                    repo_name,
                    pr_details.get("source_branch", ""),
                )
            except Exception as exc:
                project_manifest = ""
                print(formatter.format_warning(
                    f"Could not load repository structure; continuing without on-demand context: {exc}"
                ))
    except LLMError as exc:
        print(formatter.format_error(str(exc)))
        return 1

    # --- Perform structured review ---
    progress = ProgressIndicator("Analyzing code with AI (may take 30-60s)")
    progress.start()
    start_time = time.time()

    try:
        structured_comments = _review_pr_structured_with_complete_diff(
            llm=llm,
            formatter=formatter,
            diff=diff_for_review,
            files_summary=files_summary,
            context="\n\n".join(filter(None, [getattr(args, "context", ""), rag_context])),
            review_scope=config.review_scope,
            project_context=project_context,
            work_item_context=work_item_context,
            source_files_context=source_files_context,
            pr_description_context=pr_description_context,
            local_context=local_context,
            tfs=tfs,
            config=config,
            repo_name=repo_name,
            source_branch=pr_details.get("source_branch", ""),
            changed_files=pr_details.get("changed_files", []),
            project_manifest=project_manifest,
            user_context=getattr(args, "context", ""),
            project_context_mode=project_context_mode,
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
            source_file_contents = local_context.get_source_file_contents(
                pr_details.get("source_branch", ""),
                comment_paths,
            )
            if not isinstance(source_file_contents, dict):
                source_file_contents = {}
        except LocalRepoError as exc:
            print(formatter.format_warning(
                f"Could not verify source-branch file contents; using diff anchors only: {exc}"
            ))

    structured_comments, discarded_comments, discarded_ungrounded = (
        _filter_comments_to_grounded_source_lines(
            structured_comments,
            validation_diff,
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
        validation_diff,
    )
    if extra_discarded_comments:
        print(formatter.format_info(
            f"Discarded {len(extra_discarded_comments)} comment(s) outside changed PR lines."
        ))

    structured_comments, discarded_quality_comments = _filter_comments_to_quality(
        structured_comments
    )
    if discarded_quality_comments:
        print(formatter.format_info(
            f"Discarded {len(discarded_quality_comments)} vague or non-actionable comment(s)."
        ))

    structured_comments, duplicate_generated_comments = _deduplicate_generated_comments(
        structured_comments
    )
    if duplicate_generated_comments:
        print(formatter.format_info(
            f"Discarded {len(duplicate_generated_comments)} duplicate generated comment(s)."
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
        + len(discarded_quality_comments)
        + len(duplicate_generated_comments)
        + len(capped_comments)
    )
    review_text = _format_structured_review_text(
        structured_comments,
        discarded_count=total_discarded_comments,
        duplicate_count=len(skipped_duplicates),
        resolved_reappeared_count=len(resolved_reappeared),
        metadata_issues=metadata_issues,
        discarded_location_comments=discarded_comments,
        discarded_grounding_comments=discarded_ungrounded,
        discarded_changed_line_comments=extra_discarded_comments,
        discarded_quality_comments=discarded_quality_comments,
        duplicate_generated_comments=duplicate_generated_comments,
        capped_comments=capped_comments,
        duplicate_comments=skipped_duplicates,
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
def _ensure_local_context_gitignored(cwd: str, c: Colors) -> None:
    """Ensures the generated local reviewer context is ignored by git."""
    gitignore_dest = os.path.join(cwd, ".gitignore")
    ignore_entry = "review_context.local.md"

    try:
        existing = ""
        if os.path.exists(gitignore_dest):
            with open(gitignore_dest, "r", encoding="utf-8") as fh:
                existing = fh.read()
            if any(line.strip() == ignore_entry for line in existing.splitlines()):
                return

        with open(gitignore_dest, "a", encoding="utf-8") as fh:
            if existing and not existing.endswith(("\n", "\r")):
                fh.write("\n")
            fh.write(f"{ignore_entry}\n")

        print(f"{c.GREEN}✅ .gitignore updated with:{c.RESET} {ignore_entry}")
    except OSError as exc:
        print(
            f"{c.YELLOW}Warning: could not update .gitignore for "
            f"{ignore_entry}: {exc}{c.RESET}"
        )


def cmd_init() -> int:
    """Copies config.yaml and reviewer context files to the current directory.

    Creates a ``config.yaml`` file pre-populated with all available options,
    a kept ``review_context.example.md`` file, and a user-editable
    ``review_context.local.md`` file. The local context file is added to
    ``.gitignore``. Existing files are preserved unless the user confirms
    overwrite.

    The templates are bundled with the package at ``src/prompts/`` and are
    resolved at runtime via :mod:`importlib.resources`, so they work regardless
    of how the package was installed.

    Returns:
        int: Exit code. ``0`` on success or user cancellation, ``1`` on error.

    Example:
        Run from any directory to bootstrap a new configuration::

            $ ai-review init
            ✅ config.yaml created at: /home/user/my-project/config.yaml
            ✅ review_context.example.md created at: /home/user/my-project/review_context.example.md
            ✅ review_context.local.md created at: /home/user/my-project/review_context.local.md
            ✅ .gitignore updated with: review_context.local.md
               Edit config.yaml and review_context.local.md for local settings.
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

    try:
        ref = importlib.resources.files("src.prompts").joinpath("review_context.example.md")
        prompt_content = ref.read_text(encoding="utf-8")
    except (FileNotFoundError, TypeError) as exc:
        print(f"{c.RED}Error: could not locate review context example: {exc}{c.RESET}")
        return 1

    print(f"{c.GREEN}✅ config.yaml created at:{c.RESET} {config_dest}")
    for filename in ("review_context.example.md", "review_context.local.md"):
        prompt_dest = os.path.join(cwd, filename)
        if os.path.exists(prompt_dest):
            print(f"{c.YELLOW}{filename} already exists in the current directory.{c.RESET}")
            answer = input("Overwrite? [y/N] ").strip().lower()
            if answer != "y":
                print(f"   Skipped {filename} (kept existing).")
                continue

        with open(prompt_dest, "w", encoding="utf-8") as fh:
            fh.write(prompt_content)

        print(f"{c.GREEN}✅ {filename} created at:{c.RESET} {prompt_dest}")

    _ensure_local_context_gitignored(cwd, c)
    print("   Edit config.yaml and review_context.local.md for local settings.")
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
