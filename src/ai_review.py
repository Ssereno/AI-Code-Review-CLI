#!/usr/bin/env python3
# License: Apache-2.0
# Copyright 2026 Sérgio Sereno

"""
AI Code Review - Main Script
==============================
Automated code review tool using Artificial Intelligence.
Main mode: Pull Request review on Azure DevOps/TFS with
automatic comments posted directly to the PR.

Usage:
    python ai_review.py                          # Interactive mode (main menu)
    python ai_review.py pr-review                # List PRs and do interactive review
    python ai_review.py pr-review <id>           # Direct review of a specific PR
    python ai_review.py local                    # Review local Git changes (uncommitted/staged/branch)
    python ai_review.py full-code-scan [paths]   # Audit full files, folders, or glob patterns
    python ai_review.py list-prs                 # List active Pull Requests

Options:
    --detailed                           # Detailed review (default)
    --security                           # Security-focused review
    --max-files-per-bundle <n>           # Max related files per LLM bundle (overrides config)
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
import importlib.resources
import os
import sys
import time
import threading
from datetime import datetime
from typing import Optional


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

from src.config import ReviewConfig, VALID_PROVIDERS  # noqa: E402
from src.context_extractor import ContextExtractor  # noqa: E402
from src.git_utils import GitError, GitUtils  # noqa: E402
from src.llm_client import LLMClient, LLMError  # noqa: E402
from src.formatter import ReviewFormatter, Colors, save_output  # noqa: E402
from src import __version__ as VERSION  # noqa: E402


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
            "  %(prog)s local                         # Review local uncommitted changes\n"
            "  %(prog)s local --staged                # Review staged changes\n"
            "  %(prog)s local --from main             # Review branch diff against main\n"
            "  %(prog)s full-code-scan src/           # Audit all eligible files in src/\n"
            "  %(prog)s full-code-scan \"src/**/*.cs\"  # Audit files matching a glob\n"
            "  %(prog)s list-prs --status active      # List active PRs\n"
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

    # --- local / local-review (LOCAL GIT MODE) ---
    sub_local = subparsers.add_parser(
        "local",
        aliases=["local-review"],
        help="💻 Local Git Review (uncommitted, staged, branch range, or commit)",
    )
    sub_local.add_argument(
        "--staged", "-s", action="store_true",
        help="Review only staged changes (git diff --cached)",
    )
    sub_local.add_argument(
        "--from", "--base", dest="from_ref", default=None, metavar="REF",
        help="Base branch or commit to compare from (e.g., main, origin/main)",
    )
    sub_local.add_argument(
        "--to", dest="to_ref", default=None, metavar="REF",
        help="Target branch or commit to compare to (default: HEAD when --from is used)",
    )
    sub_local.add_argument(
        "--commit", default=None, metavar="SHA",
        help="Review a specific commit (compares <commit>~1 to <commit>)",
    )
    sub_local.add_argument(
        "--repo-path", default=None, metavar="PATH",
        help="Path to the local Git repository (default: current directory)",
    )

    # --- full-code-scan (FULL FILE AUDIT MODE) ---
    sub_scan = subparsers.add_parser(
        "full-code-scan",
        help="🔎 Full Code Scan (audit local files, directories, or glob patterns without a diff)",
    )
    sub_scan.add_argument(
        "paths",
        nargs="*",
        default=["."],
        metavar="PATH",
        help="Files, directories, or glob patterns to scan (default: current directory)",
    )
    sub_scan.add_argument(
        "--repo-path", default=None, metavar="PATH",
        help="Base directory for relative paths and filters (default: current directory)",
    )

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

    # --- Global options ---
    all_subs = [sub_pr_review, sub_local, sub_scan, sub_list_prs]
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
        "--detailed", "-d", action="store_const", dest="verbosity",
        const="detailed", help="Detailed review (default)"
    )
    group_review.add_argument(
        "--security", "-S", action="store_const", dest="verbosity",
        const="security", help="Security-focused review"
    )
    group_review.add_argument(
        "--max-files-per-bundle", default=None, type=int, metavar="N",
        help="Max related files grouped per LLM bundle (overrides review.max_files_per_bundle)"
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
    group_output.add_argument(
        "--debug-dump", action="store_true",
        help="Dump the diff and full LLM prompt/context sent to the LLM into a debug log file"
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


def _save_review_output(
    output_file: str,
    review_type: str,
    repo_name: str,
    branch: str,
    review_text: str,
    structured_comments: list[dict],
    was_truncated: bool,
) -> None:
    """Saves the review output as Markdown if a target file was provided."""
    if not output_file:
        return

    md_formatter = ReviewFormatter(color=False, output_format="markdown")
    body = md_formatter.format_structured_comments(
        structured_comments,
        summary=review_text,
    )
    md_output = "\n".join([
        md_formatter.format_header(
            review_type=review_type,
            repo_name=repo_name,
            branch=branch,
        ),
        body,
        md_formatter.format_footer(truncated=was_truncated),
    ])
    save_output(md_output, output_file)


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
    if getattr(args, "max_files_per_bundle", None) is not None:
        config.max_files_per_bundle = args.max_files_per_bundle
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
    if getattr(args, "debug_dump", False):
        config.debug_dump = True

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
    command = args.command

    if command == "pr-review":
        return run_pr_review_workflow(args, config, formatter)
    elif command in ("local", "local-review"):
        return run_local_review_workflow(args, config, formatter)
    elif command == "full-code-scan":
        return run_full_code_scan_workflow(args, config, formatter)
    elif command == "list-prs":
        return run_list_prs(args, config, formatter)

    print(formatter.format_error(
        "Unrecognized command. Use --help to see available commands."
    ))
    return 1


def _execute_bundled_review(
    llm: LLMClient,
    bundles: list[dict],
    context: str,
    full_code_scan: bool = False,
) -> tuple[str, list[dict]]:
    """Executes LLM review across one or more cohesive file bundles and merges results."""
    if not bundles:
        return "", []

    if len(bundles) == 1:
        bundle = bundles[0]
        return llm.review_pr(
            diff=bundle["diff"],
            files_summary=bundle["files_summary"],
            context=context,
            full_code_scan=full_code_scan,
        )

    summaries: list[str] = []
    all_comments: list[dict] = []
    total_tokens = 0
    has_token_estimate = True
    last_model = llm.config.get_effective_model()

    for idx, bundle in enumerate(bundles, 1):
        summary_text, comments = llm.review_pr(
            diff=bundle["diff"],
            files_summary=bundle["files_summary"],
            context=context,
            full_code_scan=full_code_scan,
        )
        if summary_text and summary_text.strip():
            summaries.append(f"**Bundle {idx}/{len(bundles)}:** {summary_text.strip()}")
        all_comments.extend(comments)

        est = getattr(llm, "last_token_estimate", None)
        if est is not None and est.total_prompt_tokens is not None:
            total_tokens += est.total_prompt_tokens
            last_model = est.model
        else:
            has_token_estimate = False

    if has_token_estimate:
        from src.token_estimator import TokenEstimate
        llm.last_token_estimate = TokenEstimate(
            total_prompt_tokens=total_tokens,
            model=last_model,
            error=None,
        )

    combined_summary = "\n\n".join(summaries) if summaries else "Review completed."
    return combined_summary, all_comments


def _init_debug_dump(
    config: ReviewConfig,
    formatter: ReviewFormatter,
    prefix: str,
) -> None:
    """Initializes a timestamped debug dump log path when debug_dump is enabled."""
    if config.debug_dump and not config.debug_dump_file:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        config.debug_dump_file = os.path.join("logs", f"{prefix}_{timestamp}_debug.log")
        os.makedirs(os.path.dirname(config.debug_dump_file), exist_ok=True)
        print(formatter.format_info(f"🐛 Debug dump enabled: {config.debug_dump_file}"))


def _prepare_and_execute_review(
    diff: str,
    args: argparse.Namespace,
    config: ReviewConfig,
    formatter: ReviewFormatter,
    git_utils: GitUtils,
    progress_message: str,
    full_code_scan: bool = False,
) -> tuple[int, Optional[tuple[str, list[dict], bool]]]:
    """Filters, truncates, bundles *diff*, executes LLM review, and logs token stats."""
    diff = git_utils.filter_diff_additions_only(diff)
    if not diff.strip():
        print(formatter.format_warning(
            "After filtering additions only, the diff is empty. No new code to review."
        ))
        return 0, None

    diff_truncated, was_truncated = git_utils.truncate_diff_per_file(
        diff,
        config.max_diff_lines,
    )
    if was_truncated:
        print(formatter.format_warning(
            f"Diff truncated to {config.max_diff_lines} lines per file."
        ))

    bundles = git_utils.bundle_diff_files(
        diff_truncated,
        config.max_files_per_bundle,
    )
    total_files = sum(len(b.get("files_summary", [])) for b in bundles)
    if bundles:
        print(formatter.format_info(
            f"📦 Smart File Bundling: grouped {total_files} file(s) into "
            f"{len(bundles)} cohesive bundle(s) (max {config.max_files_per_bundle} files/bundle)"
        ))

    progress = ProgressIndicator(progress_message)
    progress.start()
    start_time = time.time()

    try:
        llm = LLMClient(config)
        review_text, structured_comments = _execute_bundled_review(
            llm=llm,
            bundles=bundles,
            context=getattr(args, "context", ""),
            full_code_scan=full_code_scan,
        )
    except LLMError as exc:
        progress.stop()
        print(formatter.format_error(str(exc)))
        return 1, None

    elapsed = time.time() - start_time
    progress.stop(formatter.format_success(f"Review completed in {elapsed:.1f}s"))

    token_estimate = getattr(llm, "last_token_estimate", None)
    if token_estimate is not None and token_estimate.total_prompt_tokens is not None:
        print(formatter.format_info(
            f"Total Prompt: {token_estimate.total_prompt_tokens} tokens"
        ))
    else:
        print(formatter.format_warning(
            "Total Prompt: unavailable (LiteLLM and local tiktoken could not count the model)"
        ))

    return 0, (review_text, structured_comments, was_truncated)


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

    _init_debug_dump(config, formatter, f"pr_{pr_id}")

    # --- Get PR diff ---
    print(formatter.format_progress("Getting Pull Request diff"))

    try:
        diff = tfs.get_pull_request_diff(
            repo_name,
            pr_id,
            excluded_paths=config.excluded_paths or None,
            file_extensions_filter=config.file_extensions_filter or None,
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
        f"Mode: {config.verbosity}"
    ))

    dry_run = config.dry_run or getattr(args, "dry_run", False)
    auto_post = config.auto_post_comments or getattr(args, "auto_post", False)

    if dry_run:
        print(formatter.format_info("🔍 DRY-RUN mode: comments will NOT be posted"))

    git_utils = GitUtils(validate=False)
    status_code, review_outcome = _prepare_and_execute_review(
        diff=diff,
        args=args,
        config=config,
        formatter=formatter,
        git_utils=git_utils,
        progress_message="Analyzing code with AI (may take 30-60s)",
    )
    if review_outcome is None:
        return status_code

    review_text, structured_comments, was_truncated = review_outcome

    without_inline = [
        c for c in structured_comments
        if not (bool(c.get("file")) and int(c.get("line", 0)) > 0)
        and str(c.get("type", "")).lower() not in ("praise", "")
    ]
    if without_inline:
        print(formatter.format_info(
            f"{len(without_inline)} comment(s) without file/line will be posted as a general PR comment."
        ))

    # --- Show structured comments preview ---
    print(formatter.format_structured_comments(
        structured_comments,
        summary=review_text,
    ))

    output_file = config.output_file or getattr(args, "output", "")
    _save_review_output(
        output_file=output_file,
        review_type=f"Pull Request #{pr_id}",
        repo_name=repo_name,
        branch=f"{pr_details['source_branch']} → {pr_details['target_branch']}",
        review_text=review_text,
        structured_comments=structured_comments,
        was_truncated=was_truncated,
    )

    # --- Post comments to PR ---
    if dry_run:
        print(f"\n{c.YELLOW}{c.BOLD}🔍 DRY-RUN mode: "
              f"No comments were posted to the PR.{c.RESET}")
        print(f"{c.DIM}   Remove --dry-run to post the comments.{c.RESET}\n")
        return 0

    if not structured_comments:
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
            comment_mode=config.pr_comment_mode,
        )
        print(formatter.format_post_results(results))
    except TFSError as exc:
        print(formatter.format_error(f"Error posting comments: {exc}"))
        return 1

    # --- Post general summary as comment ---
    try:
        summary_comment = (
            f"## 🤖 AI Code Review\n\n"
            f"**Provider:** {config.llm_provider} | "
            f"**Model:** {config.model} | "
            f"**Mode:** {config.verbosity}\n\n"

            f"---\n"
            f"*Automatic review generated by AI Code Review v{VERSION}*"
        )
        result = tfs.post_general_comment(
            repo_name, pr_id, summary_comment, dedup_marker="summary"
        )
        if result.get("skipped"):
            print(formatter.format_info(
                "General summary already present, skipped."
            ))
        else:
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
            comment_type = comment.get("type", "suggestion")
            file_info = comment.get("file", "general")
            if comment.get("line", 0) > 0:
                file_info += f":{comment['line']}"

            try:
                ans = input(
                    f"  {c.CYAN}[{i}/{len(comments)}]{c.RESET} "
                    f"{comment_type} at {file_info} - "
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
# Local Git Review
# ---------------------------------------------------------------------------
def run_local_review_workflow(args: argparse.Namespace, config: ReviewConfig,
                              formatter: ReviewFormatter) -> int:
    """
    Local Git review workflow (working tree, staged, branch range, or commit).
    Does not require TFS/Azure DevOps configuration.
    """
    repo_path = getattr(args, "repo_path", None)
    try:
        git_utils = GitUtils(repo_path=repo_path)
    except GitError as exc:
        print(formatter.format_error(str(exc)))
        return 1

    staged = bool(getattr(args, "staged", False))
    from_ref = getattr(args, "from_ref", None)
    to_ref = getattr(args, "to_ref", None)
    commit = getattr(args, "commit", None)

    repo_name = git_utils.get_repo_name()
    branch = git_utils.get_current_branch()

    if commit:
        target_label = f"Commit {commit}"
    elif from_ref:
        target_label = f"{from_ref} → {to_ref or 'HEAD'}"
    elif staged:
        target_label = "Staged changes (--cached)"
    else:
        target_label = "Uncommitted changes (working tree)"

    print(formatter.format_progress(f"Inspecting local Git changes ({target_label})"))

    try:
        changed_files = git_utils.get_local_changed_files(
            staged=staged,
            from_ref=from_ref,
            to_ref=to_ref,
            commit=commit,
        )
    except GitError as exc:
        print(formatter.format_error(str(exc)))
        return 1

    if not changed_files:
        print(formatter.format_warning("No local code changes found to review."))
        return 0

    local_details = {
        "repository": repo_name,
        "branch": branch,
        "target": target_label,
        "changed_files": changed_files,
    }
    print(formatter.format_local_details(local_details))

    _init_debug_dump(config, formatter, "local")

    print(formatter.format_progress("Building local diff and AST context"))

    extractor = ContextExtractor.from_config(config)

    try:
        diff = git_utils.get_local_diff(
            excluded_paths=config.excluded_paths or None,
            file_extensions_filter=config.file_extensions_filter or None,
            staged=staged,
            from_ref=from_ref,
            to_ref=to_ref,
            commit=commit,
            context_extractor=extractor,
        )
    except GitError as exc:
        print(formatter.format_error(str(exc)))
        return 1

    if not diff.strip():
        print(formatter.format_warning("No eligible code changes found after filtering."))
        return 0

    print(formatter.format_info(
        f"Provider: {config.llm_provider} | Model: {config.model} | "
        f"Mode: {config.verbosity}"
    ))

    status_code, review_outcome = _prepare_and_execute_review(
        diff=diff,
        args=args,
        config=config,
        formatter=formatter,
        git_utils=git_utils,
        progress_message="Analyzing local code with AI (may take 30-60s)",
    )
    if review_outcome is None:
        return status_code

    review_text, structured_comments, was_truncated = review_outcome

    print(formatter.format_structured_comments(
        structured_comments,
        summary=review_text,
    ))

    output_file = config.output_file or getattr(args, "output", "")
    _save_review_output(
        output_file=output_file,
        review_type="Local Git Review",
        repo_name=repo_name,
        branch=f"{branch} ({target_label})",
        review_text=review_text,
        structured_comments=structured_comments,
        was_truncated=was_truncated,
    )

    return 0


# ---------------------------------------------------------------------------
# Full Code Scan (static audit of local files / folders / globs)
# ---------------------------------------------------------------------------
def run_full_code_scan_workflow(args: argparse.Namespace, config: ReviewConfig,
                                formatter: ReviewFormatter) -> int:
    """
    Full code static audit workflow for files, folders, or glob patterns.
    Does not require a Git diff or TFS/Azure DevOps configuration.
    """
    repo_path = getattr(args, "repo_path", None)
    git_utils = GitUtils(repo_path=repo_path, validate=False)
    targets = list(getattr(args, "paths", None) or ["."])
    target_label = ", ".join(targets)

    base_abs = os.path.abspath(git_utils.repo_path)
    repo_name = os.path.basename(base_abs.rstrip("/\\")) or "local-scan"
    branch = ""
    try:
        git_utils._validate_repo()
        repo_name = git_utils.get_repo_name()
        branch = git_utils.get_current_branch()
    except GitError:
        pass

    print(formatter.format_progress(f"Scanning files matching ({target_label})"))

    extractor = ContextExtractor.from_config(config)

    diff, discovered_files = git_utils.get_scan_diff(
        targets=targets,
        excluded_paths=config.excluded_paths or None,
        file_extensions_filter=config.file_extensions_filter or None,
        context_extractor=extractor,
    )

    if not discovered_files or not diff.strip():
        print(formatter.format_warning("No eligible files found to scan."))
        return 0

    scan_details = {
        "title": "🔎 Full Code Scan",
        "files_label": "Scanned Files",
        "repository": repo_name,
        "branch": branch,
        "target": target_label,
        "changed_files": discovered_files,
    }
    print(formatter.format_local_details(scan_details))

    _init_debug_dump(config, formatter, "scan")

    print(formatter.format_info(
        f"Provider: {config.llm_provider} | Model: {config.model} | "
        f"Mode: {config.verbosity}"
    ))

    status_code, review_outcome = _prepare_and_execute_review(
        diff=diff,
        args=args,
        config=config,
        formatter=formatter,
        git_utils=git_utils,
        progress_message="Scanning code with AI (may take 30-60s)",
        full_code_scan=True,
    )
    if review_outcome is None:
        return status_code

    review_text, structured_comments, was_truncated = review_outcome

    print(formatter.format_structured_comments(
        structured_comments,
        summary=review_text,
    ))

    output_file = config.output_file or getattr(args, "output", "")
    branch_header = f"{branch} ({target_label})" if branch else target_label
    _save_review_output(
        output_file=output_file,
        review_type="Full Code Scan",
        repo_name=repo_name,
        branch=branch_header,
        review_text=review_text,
        structured_comments=structured_comments,
        was_truncated=was_truncated,
    )

    return 0


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
    print("")
    print(f"  {c.CYAN}{c.BOLD}── Local Git & Static Audit ──{c.RESET}")
    print(f"  {c.CYAN}3{c.RESET}) 💻 Local Git Review (working tree, staged, or branch diff)")
    print(f"  {c.CYAN}4{c.RESET}) 🔎 Full Code Scan (audit files, folders, or glob patterns)")
    print("")
    print(f"  {c.CYAN}{c.BOLD}── Other ──{c.RESET}")
    print(f"  {c.CYAN}5{c.RESET}) ⚙️  Current configuration")
    print(f"  {c.CYAN}0{c.RESET}) Exit")

    try:
        choice = input(f"\n{c.BOLD}Choose [0-5]: {c.RESET}").strip()
    except (KeyboardInterrupt, EOFError):
        print("\n")
        return 0

    if choice == "0":
        return 0

    if choice == "1":
        return _interactive_pr_review(config)

    if choice == "2":
        return _interactive_list_prs()

    if choice == "3":
        return _interactive_local_review()

    if choice == "4":
        return _interactive_full_code_scan()

    if choice == "5":
        _show_config(config)
        return 0

    print(f"{c.RED}Invalid option.{c.RESET}")
    return 1


def _interactive_pr_review(config: Optional[ReviewConfig] = None) -> int:
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


def _interactive_list_prs() -> int:
    """Lists PRs interactively."""
    argv = ["list-prs"]
    parser = build_parser()
    parsed = parser.parse_args(argv)
    return run_review(parsed)


def _interactive_local_review() -> int:
    """Interactive local Git review workflow."""
    c = Colors

    print(f"\n{c.BOLD}Local review target:{c.RESET}")
    print(f"  {c.CYAN}1{c.RESET}) Uncommitted changes (working tree + staged)")
    print(f"  {c.CYAN}2{c.RESET}) Staged changes only (--staged)")
    print(f"  {c.CYAN}3{c.RESET}) Compare against base branch (--from)")

    try:
        target_choice = input(f"{c.BOLD}Choose [1-3, default=1]: {c.RESET}").strip()
    except (KeyboardInterrupt, EOFError):
        print("\n")
        return 0

    argv = ["local"]
    if target_choice == "2":
        argv.append("--staged")
    elif target_choice == "3":
        try:
            base_ref = input(f"{c.BOLD}Base branch/ref (default=main): {c.RESET}").strip() or "main"
        except (KeyboardInterrupt, EOFError):
            print("\n")
            return 0
        argv.extend(["--from", base_ref])

    verbosity = _ask_verbosity()
    if verbosity:
        argv.append(f"--{verbosity}")

    parser = build_parser()
    parsed = parser.parse_args(argv)
    return run_review(parsed)


def _interactive_full_code_scan() -> int:
    """Interactive full-code-scan workflow."""
    c = Colors

    try:
        raw_target = input(
            f"\n{c.BOLD}Path or glob pattern to scan (default=.): {c.RESET}"
        ).strip() or "."
    except (KeyboardInterrupt, EOFError):
        print("\n")
        return 0

    argv = ["full-code-scan", raw_target]
    verbosity = _ask_verbosity()
    if verbosity:
        argv.append(f"--{verbosity}")

    parser = build_parser()
    parsed = parser.parse_args(argv)
    return run_review(parsed)


def _ask_verbosity() -> str:
    """Asks for the verbosity mode."""
    c = Colors
    print(f"\n{c.BOLD}Review mode:{c.RESET}")
    print(f"  {c.CYAN}1{c.RESET}) Detailed")
    print(f"  {c.CYAN}2{c.RESET}) Security")

    try:
        mode_choice = input(f"{c.BOLD}Choose [1-2, default=1]: {c.RESET}").strip()
    except (KeyboardInterrupt, EOFError):
        print("\n")
        return "detailed"

    verbosity_map = {"1": "detailed", "2": "security"}
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
