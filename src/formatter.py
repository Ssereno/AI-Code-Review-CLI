"""
Formatting Module - AI Code Review
====================================
Responsible for formatting and presenting review results.
Supports terminal output (with colors), Markdown and JSON.
Includes specific formatting for PRs and structured comments.
"""

import json
import os
import sys
from datetime import datetime


# ---------------------------------------------------------------------------
# ANSI color codes for terminal output
# ---------------------------------------------------------------------------
class Colors:
    """ANSI colors for terminal output."""
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    UNDERLINE = "\033[4m"

    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"

    BG_RED = "\033[41m"
    BG_GREEN = "\033[42m"
    BG_BLUE = "\033[44m"

    @classmethod
    def disable(cls):
        """Disables colors (for output without ANSI support)."""
        for attr in dir(cls):
            if attr.isupper() and not attr.startswith("_"):
                setattr(cls, attr, "")


def _supports_color() -> bool:
    """Checks whether the terminal supports colors."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    if not hasattr(sys.stdout, "isatty") or not sys.stdout.isatty():
        return False
    if sys.platform == "win32":
        return os.environ.get("TERM") == "xterm" or os.environ.get("WT_SESSION")
    return True


# ---------------------------------------------------------------------------
# Main Formatter
# ---------------------------------------------------------------------------
class ReviewFormatter:
    """Formats and presents the review result."""

    def __init__(self, color: bool = True, output_format: str = "terminal"):
        self.output_format = output_format
        if not color or not _supports_color():
            Colors.disable()

    def format_header(self, review_type: str, repo_name: str = "",
                      branch: str = "", extra_info: str = "") -> str:
        """Formats the review header."""
        if self.output_format == "terminal":
            return self._terminal_header(review_type, repo_name, branch, extra_info)
        elif self.output_format == "markdown":
            return self._markdown_header(review_type, repo_name, branch, extra_info)
        return ""

    def format_files_summary(self, files: list[dict]) -> str:
        """Formats the changed files summary."""
        if self.output_format == "terminal":
            return self._terminal_files_summary(files)
        elif self.output_format == "markdown":
            return self._markdown_files_summary(files)
        return ""

    def format_review(self, review_text: str) -> str:
        """Formats the review text."""
        if self.output_format == "terminal":
            return self._terminal_review(review_text)
        elif self.output_format == "markdown":
            return review_text
        elif self.output_format == "json":
            return json.dumps({
                "review": review_text,
                "timestamp": datetime.now().isoformat(),
            }, indent=2, ensure_ascii=False)
        return review_text

    def format_footer(self, truncated: bool = False) -> str:
        """Formats the review footer."""
        if self.output_format == "terminal":
            return self._terminal_footer(truncated)
        elif self.output_format == "markdown":
            return self._markdown_footer(truncated)
        return ""

    def format_error(self, error_msg: str) -> str:
        """Formats an error message."""
        if self.output_format == "terminal":
            return (
                f"\n{Colors.RED}{Colors.BOLD}❌ ERROR{Colors.RESET}\n"
                f"{Colors.RED}{error_msg}{Colors.RESET}\n"
            )
        return f"\n❌ **ERROR**: {error_msg}\n"

    def format_warning(self, warning_msg: str) -> str:
        """Formats a warning message."""
        if self.output_format == "terminal":
            return f"{Colors.YELLOW}⚠️  {warning_msg}{Colors.RESET}"
        return f"⚠️ {warning_msg}"

    def format_info(self, info_msg: str) -> str:
        """Formats an informational message."""
        if self.output_format == "terminal":
            return f"{Colors.CYAN}ℹ️  {info_msg}{Colors.RESET}"
        return f"ℹ️ {info_msg}"

    def format_progress(self, step: str) -> str:
        """Formats a progress message."""
        if self.output_format == "terminal":
            return f"{Colors.DIM}⏳ {step}...{Colors.RESET}"
        return f"⏳ {step}..."

    def format_success(self, msg: str) -> str:
        """Formats a success message."""
        if self.output_format == "terminal":
            return f"{Colors.GREEN}✅ {msg}{Colors.RESET}"
        return f"✅ {msg}"

    # ------------------------------------------------------------------
    # Pull Request formatting
    # ------------------------------------------------------------------
    def format_pr_list(self, prs: list[dict], title: str = "Pull Requests") -> str:
        """Formats the Pull Requests list for the terminal."""
        c = Colors
        if not prs:
            return f"\n{c.DIM}No Pull Requests found.{c.RESET}\n"

        lines = [f"\n{c.BOLD}📋 {title} ({len(prs)}):{c.RESET}\n"]

        for i, pr in enumerate(prs, 1):
            draft = f" {c.DIM}[DRAFT]{c.RESET}" if pr.get("is_draft") else ""
            lines.append(
                f"  {c.CYAN}{i:>3}){c.RESET} "
                f"{c.BOLD}#{pr['id']:<6}{c.RESET} "
                f"{c.WHITE}{pr['title'][:55]:<55}{c.RESET}{draft}"
            )
            lines.append(
                f"       {c.GREEN}{pr['source_branch']}{c.RESET} → "
                f"{c.YELLOW}{pr['target_branch']}{c.RESET}  "
                f"{c.DIM}by {pr['author']} ({pr['repository']}){c.RESET}"
            )

            # Show reviewers if present
            reviewers = pr.get("reviewers", [])
            if reviewers:
                reviewer_strs = [
                    f"{r['vote_label']} {r['name']}" for r in reviewers[:3]
                ]
                if len(reviewers) > 3:
                    reviewer_strs.append(f"+{len(reviewers)-3} more")
                lines.append(
                    f"       {c.DIM}Reviewers: {', '.join(reviewer_strs)}{c.RESET}"
                )
            lines.append("")

        return "\n".join(lines)

    def format_pr_details(self, pr: dict) -> str:
        """Formats PR details for the terminal."""
        c = Colors
        width = 60
        line = "─" * width

        output = f"\n{c.BLUE}{c.BOLD}{line}{c.RESET}\n"
        output += f"{c.BOLD}  📝 Pull Request #{pr['id']}{c.RESET}\n"
        output += f"{c.BLUE}{line}{c.RESET}\n"
        output += f"{c.CYAN}  Title:      {c.WHITE}{pr['title']}{c.RESET}\n"
        output += f"{c.CYAN}  Author:     {c.WHITE}{pr['author']}{c.RESET}\n"
        output += (
            f"{c.CYAN}  Branch:     {c.GREEN}{pr['source_branch']}{c.RESET}"
            f" → {c.YELLOW}{pr['target_branch']}{c.RESET}\n"
        )
        output += f"{c.CYAN}  Repository: {c.WHITE}{pr['repository']}{c.RESET}\n"
        output += f"{c.CYAN}  Status:     {c.WHITE}{pr['status']}{c.RESET}\n"

        if pr.get("description"):
            desc = pr["description"][:200]
            if len(pr["description"]) > 200:
                desc += "..."
            output += f"{c.CYAN}  Description:{c.DIM}{desc}{c.RESET}\n"

        # Commits
        commits = pr.get("commits", [])
        if commits:
            output += f"\n{c.BOLD}  📦 Commits ({len(commits)}):{c.RESET}\n"
            for cm in commits[:10]:
                output += (
                    f"    {c.YELLOW}{cm['short_id']}{c.RESET} "
                    f"{cm['message'][:50]} "
                    f"{c.DIM}({cm['author']}){c.RESET}\n"
                )
            if len(commits) > 10:
                output += f"    {c.DIM}... +{len(commits)-10} more commits{c.RESET}\n"

        # Changed files
        changed_files = pr.get("changed_files", [])
        if changed_files:
            output += f"\n{c.BOLD}  📁 Changed Files ({len(changed_files)}):{c.RESET}\n"
            for f in changed_files[:20]:
                change_icon = {"add": "🟢", "edit": "🟡", "delete": "🔴",
                              "rename": "🔵"}.get(f["change_type"], "⚪")
                output += f"    {change_icon} {f['path']}\n"
            if len(changed_files) > 20:
                output += f"    {c.DIM}... +{len(changed_files)-20} more files{c.RESET}\n"

        output += f"\n{c.BLUE}{line}{c.RESET}\n"
        return output

    def format_structured_comments(self, comments: list[dict],
                                   discarded_count: int = 0) -> str:
        """Formats LLM structured comments for terminal preview."""
        c = Colors
        if not comments:
            if discarded_count > 0:
                return (
                    f"\n{c.YELLOW}⚠ {discarded_count} comment(s) discarded "
                    f"due to missing file/line in diff_only mode.{c.RESET}\n"
                )
            return f"\n{c.DIM}No comments generated.{c.RESET}\n"

        severity_colors = {
            "critical": c.RED + c.BOLD,
            "high": c.RED,
            "medium": c.YELLOW,
            "low": c.GREEN,
            "info": c.CYAN,
        }
        severity_icons = {
            "critical": "🔴",
            "high": "🟠",
            "medium": "🟡",
            "low": "🟢",
            "info": "ℹ️",
        }
        type_labels = {
            "bug": "🐛 Bug",
            "security": "🔒 Security",
            "performance": "⚡ Performance",
            "style": "📝 Style",
            "suggestion": "💡 Suggestion",
            "praise": "👍 Positive",
        }

        lines = [f"\n{c.BOLD}🤖 AI Review Comments ({len(comments)}):{c.RESET}\n"]

        if discarded_count > 0:
            lines.append(
                f"  {c.YELLOW}⚠ {discarded_count} comment(s) discarded "
                f"due to missing file/line in diff_only mode.{c.RESET}"
            )
            lines.append("")

        for i, comment in enumerate(comments, 1):
            severity = comment.get("severity", "info")
            comment_type = comment.get("type", "suggestion")
            sev_color = severity_colors.get(severity, c.WHITE)
            icon = severity_icons.get(severity, "ℹ️")
            label = type_labels.get(comment_type, comment_type.title())

            file_info = ""
            if comment.get("file"):
                file_info = f"{c.DIM}{comment['file']}"
                if comment.get("line", 0) > 0:
                    file_info += f":{comment['line']}"
                file_info += f"{c.RESET} "

            lines.append(
                f"  {c.CYAN}{i:>3}){c.RESET} "
                f"{icon} {sev_color}{label} ({severity.upper()}){c.RESET}"
            )
            if file_info:
                lines.append(f"       {file_info}")
            lines.append(f"       {comment.get('comment', '')}")

            suggestion = comment.get("suggestion", "")
            if suggestion:
                lines.append(f"       {c.GREEN}💡 {suggestion}{c.RESET}")

            reference = comment.get("reference", "")
            if reference:
                lines.append(f"       {c.DIM}📚 Reference: {reference}{c.RESET}")
            
            lines.append("")

        return "\n".join(lines)

    def format_post_results(self, results: list[dict]) -> str:
        """Formats the results of posting comments to the PR."""
        c = Colors
        lines = [f"\n{c.BOLD}📤 Comment posting results:{c.RESET}\n"]

        success_count = sum(1 for r in results if r.get("success"))
        fail_count = len(results) - success_count

        for r in results:
            if r.get("success"):
                file_info = r.get("file", "geral")
                if r.get("line", 0) > 0:
                    file_info += f":{r['line']}"
                lines.append(
                    f"  {c.GREEN}✅ Posted at {file_info} "
                    f"(thread #{r.get('thread_id', '?')}){c.RESET}"
                )
            elif r.get("skipped"):
                lines.append(
                    f"  {c.YELLOW}⚠ Skipped at {r.get('file', 'general')}: "
                    f"{r.get('error', 'no reason')}{c.RESET}"
                )
            else:
                lines.append(
                    f"  {c.RED}❌ Failed at {r.get('file', 'general')}: "
                    f"{r.get('error', 'unknown error')}{c.RESET}"
                )

        lines.append(
            f"\n  {c.BOLD}Total: "
            f"{c.GREEN}{success_count} posted{c.RESET}, "
            f"{c.RED}{fail_count} failed{c.RESET}"
        )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Spinner / Progress helpers
    # ------------------------------------------------------------------
    def format_spinner_frame(self, step: str, frame: int) -> str:
        """Returns a spinner frame for progress display."""
        c = Colors
        spinners = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        spinner = spinners[frame % len(spinners)]
        return f"\r{c.CYAN}{spinner} {step}...{c.RESET}"

    # ------------------------------------------------------------------
    # Terminal formatting
    # ------------------------------------------------------------------
    def _terminal_header(self, review_type: str, repo_name: str,
                         branch: str, extra_info: str) -> str:
        c = Colors
        width = 60
        line = "═" * width

        header = f"\n{c.BLUE}{c.BOLD}{line}{c.RESET}\n"
        header += f"{c.BLUE}{c.BOLD}  🤖 AI CODE REVIEW{c.RESET}\n"
        header += f"{c.BLUE}{line}{c.RESET}\n"

        header += f"{c.CYAN}  Type:       {c.WHITE}{review_type}{c.RESET}\n"
        if repo_name:
            header += f"{c.CYAN}  Repository: {c.WHITE}{repo_name}{c.RESET}\n"
        if branch:
            header += f"{c.CYAN}  Branch:     {c.WHITE} {branch}{c.RESET}\n"
        if extra_info:
            header += f"{c.CYAN}  {extra_info}{c.RESET}\n"

        header += f"{c.CYAN}  Date:       {c.WHITE} {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}{c.RESET}\n"
        header += f"{c.BLUE}{line}{c.RESET}\n"
        return header

    def _terminal_files_summary(self, files: list[dict]) -> str:
        c = Colors
        summary = f"\n{c.BOLD}📁 Changed Files ({len(files)}):{c.RESET}\n"

        total_add = 0
        total_del = 0
        for f in files:
            add = f["additions"]
            delete = f["deletions"]
            total_add += add
            total_del += delete

            bar_add = "+" * min(add, 20)
            bar_del = "-" * min(delete, 20)
            bar = f"{c.GREEN}{bar_add}{c.RED}{bar_del}{c.RESET}"

            summary += (
                f"  {c.WHITE}{f['file']:<50}{c.RESET} "
                f"{c.GREEN}+{add:<4}{c.RED}-{delete:<4}{c.RESET} {bar}\n"
            )

        summary += f"\n  {c.BOLD}Total: {c.GREEN}+{total_add} {c.RED}-{total_del}{c.RESET}\n"
        return summary

    def _terminal_review(self, review_text: str) -> str:
        c = Colors
        output = f"\n{c.BOLD}{'─' * 60}{c.RESET}\n"
        output += f"{c.BOLD}📝 REVIEW:{c.RESET}\n"
        output += f"{c.BOLD}{'─' * 60}{c.RESET}\n\n"
        output += review_text
        output += "\n"
        return output

    def _terminal_footer(self, truncated: bool) -> str:
        c = Colors
        footer = f"\n{c.BLUE}{'═' * 60}{c.RESET}\n"
        if truncated:
            footer += (
                f"{c.YELLOW}⚠️  The diff was truncated. For a full review, "
                f"reduce the scope of changes.{c.RESET}\n"
            )
        footer += (
            f"{c.DIM}  Review generated by AI Code Review v2.0.0\n"
            f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}{c.RESET}\n"
        )
        footer += f"{c.BLUE}{'═' * 60}{c.RESET}\n"
        return footer

    # ------------------------------------------------------------------
    # Markdown formatting
    # ------------------------------------------------------------------
    def _markdown_header(self, review_type: str, repo_name: str,
                         branch: str, extra_info: str) -> str:
        header = "# 🤖 AI Code Review\n\n"
        header += f"| Field | Value |\n|-------|-------|\n"
        header += f"| **Type** | {review_type} |\n"
        if repo_name:
            header += f"| **Repository** | {repo_name} |\n"
        if branch:
            header += f"| **Branch** | {branch} |\n"
        if extra_info:
            header += f"| **Info** | {extra_info} |\n"
        header += f"| **Data** | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} |\n"
        header += "\n---\n\n"
        return header

    def _markdown_files_summary(self, files: list[dict]) -> str:
        summary = "## 📁 Changed Files\n\n"
        summary += "| File | Additions | Deletions |\n"
        summary += "|------|-----------|----------|\n"
        for f in files:
            summary += f"| `{f['file']}` | +{f['additions']} | -{f['deletions']} |\n"
        summary += "\n"
        return summary

    def _markdown_footer(self, truncated: bool) -> str:
        footer = "\n---\n\n"
        if truncated:
            footer += "> ⚠️ **Note**: The diff was truncated due to size.\n\n"
        footer += (
            f"*Review generated by AI Code Review v2.0.0 at "
            f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*\n"
        )
        return footer


def save_output(content: str, file_path: str) -> None:
    """
    Saves the review output to a file.
    """
    try:
        os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"✅ Review saved to: {file_path}")
    except OSError as exc:
        print(f"❌ Error saving file: {exc}")
