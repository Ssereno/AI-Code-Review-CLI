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
    --review-scope <diff_only|full_code> # Review scope (default: diff_only)
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
import importlib.resources
import os
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
from src import __version__ as VERSION


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
        choices=["diff_only", "full_code"],
        help="Review scope: diff_only (default) or full_code"
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

    # Keep only added lines (+): ignore context and removed lines
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

    # --- Perform structured review ---
    progress = ProgressIndicator("Analyzing code with AI (may take 30-60s)")
    progress.start()
    start_time = time.time()

    try:
        llm = LLMClient(config)

        # Get general review as text
        review_text = llm.review(
            diff=diff_truncated,
            files_summary=files_summary,
            context=getattr(args, "context", ""),
            review_scope=config.review_scope,
        )

        # Get structured comments to post
        structured_comments = llm.review_pr_structured(
            diff=diff_truncated,
            files_summary=files_summary,
            context=getattr(args, "context", ""),
            review_scope=config.review_scope,
        )
    except LLMError as exc:
        progress.stop()
        print(formatter.format_error(str(exc)))
        return 1

    without_inline = [
        c for c in structured_comments
        if not (bool(c.get("file")) and int(c.get("line", 0)) > 0)
        and str(c.get("type", "")).lower() not in ("praise", "")
    ]
    if without_inline:
        print(formatter.format_info(
            f"{len(without_inline)} comment(s) without file/line will be posted as a general PR comment."
        ))
    discarded_comments = []  # nothing is discarded

    elapsed = time.time() - start_time
    progress.stop(formatter.format_success(f"Review completed in {elapsed:.1f}s"))

    # --- Show general review ---
    print(formatter.format_review(review_text))

    # --- Show structured comments preview ---
    print(formatter.format_structured_comments(
        structured_comments,
        discarded_count=len(discarded_comments),
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
            review_scope=config.review_scope,
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
            f"{review_text}\n\n"
            f"---\n"
            f"*Automatic review generated by AI Code Review v{VERSION}*"
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
    print(f"")
    print(f"  {c.CYAN}{c.BOLD}── Other ──{c.RESET}")
    print(f"  {c.CYAN}3{c.RESET}) Current configuration")
    print(f"  {c.CYAN}0{c.RESET}) Exit")

    try:
        choice = input(f"\n{c.BOLD}Choose [0-3]: {c.RESET}").strip()
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
