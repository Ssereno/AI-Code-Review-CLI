# License: Apache-2.0
# Copyright 2026 Sérgio Sereno

"""
Git Utilities Module - AI Code Review
========================================
Provides diff filtering, splitting, truncation and file-summary helpers
used by the PR review pipeline.

Works with any Git repository, including TFS/Azure DevOps.
"""

import subprocess
import os
from typing import Optional

class GitError(Exception):
    """Exception for Git-related errors."""
    pass


class GitUtils:
    """Utility class for Git operations."""

    def __init__(self, repo_path: Optional[str] = None):
        """
        Initializes the Git utility.
        
        Args:
            repo_path: Path to the repository. If None, uses the current directory.
        """
        self.repo_path = repo_path or os.getcwd()
        self._validate_repo()

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    def _validate_repo(self) -> None:
        """Checks whether we are inside a valid Git repository."""
        try:
            self._run_git("rev-parse", "--git-dir")
        except GitError:
            raise GitError(
                f"Directory '{self.repo_path}' is not a valid Git repository.\n"
                "Make sure you are inside a Git repository."
            )

    # ------------------------------------------------------------------
    # Internal Git commands
    # ------------------------------------------------------------------
    def _run_git(self, *args: str, check: bool = True) -> str:
        """
        Runs a git command and returns the output.
        
        Args:
            *args: Git command arguments.
            check: If True, raises an exception on error.
            
        Returns:
            Command output as string.
        """
        cmd = ["git"] + list(args)
        try:
            result = subprocess.run(
                cmd,
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
            if check and result.returncode != 0:
                raise GitError(
                    f"Git command failed: {' '.join(cmd)}\n"
                    f"Error: {result.stderr.strip()}"
                )
            return result.stdout
        except FileNotFoundError:
            raise GitError(
                "Git not found. Make sure Git is installed "
                "and available in PATH."
            )
        except subprocess.TimeoutExpired:
            raise GitError(f"Timeout executing: {' '.join(cmd)}")


    # ------------------------------------------------------------------
    # Filters and Utilities
    # ------------------------------------------------------------------
    def filter_diff_additions_only(self, diff: str) -> str:
        """
        Removes deleted lines (-) from the diff, keeping context lines and added lines.
        Structural headers and XML context blocks are always preserved.

        Lines kept:
            - ``diff --git ...``
            - ``--- a/...``
            - ``+++ b/...``
            - ``@@ ... @@``
            - ``+ <content>`` (added lines)
            - `` <content>`` (context lines — unchanged surrounding code)
            - Lines inside XML context blocks (``<enclosing_scopes>``, ``<file_skeleton>``, etc.)

        Lines removed:
            - ``- <content>`` (deleted lines)
            - ``\\ No newline ...`` markers

        Args:
            diff: Raw unified diff string.

        Returns:
            Filtered diff string with deleted lines stripped but context lines intact.
        """
        result = []
        in_xml_block = False
        _xml_start_tags = ("<enclosing_scopes", "<file_skeleton", "<adaptive_diff", "<sql_statement")
        _xml_end_tags = ("</enclosing_scopes>", "</file_skeleton>", "</adaptive_diff>", "</sql_statement>")

        for line in diff.split("\n"):
            if any(line.startswith(tag) for tag in _xml_start_tags):
                in_xml_block = True
                result.append(line)
                continue
            
            if any(line.startswith(tag) for tag in _xml_end_tags):
                in_xml_block = False
                result.append(line)
                continue

            if in_xml_block:
                result.append(line)
                continue

            if (
                line.startswith("diff --git")
                or line.startswith("--- ")
                or line.startswith("+++ ")
                or line.startswith("@@")
                or (line.startswith("+") and not line.startswith("+++"))
                or line.startswith(" ")  # context lines — unchanged surrounding code
            ):
                result.append(line)
            # Deleted lines (-) and '\\ No newline' markers are discarded.
        return "\n".join(result)

    def _split_diff_sections(self, diff: str) -> tuple[list[list[str]], bool]:
        """
        Splits the diff into sections per file ("diff --git ...").

        Returns:
            Tuple (sections, has_file_separators).
        """
        lines = diff.split("\n")
        has_sections = any(line.startswith("diff --git") for line in lines)
        if not has_sections:
            return [lines], False

        sections: list[list[str]] = []
        current: list[str] = []
        for line in lines:
            if line.startswith("diff --git") and current:
                sections.append(current)
                current = [line]
            else:
                current.append(line)
        if current:
            sections.append(current)
        return sections, True

    def limit_diff_files(self, diff: str, max_files: int = 50) -> tuple[str, bool, int]:
        """
        Limits the number of files in the diff ("diff --git" sections).

        Returns:
            Tuple (limited_diff, was_limited, omitted_files).
        """
        sections, has_file_sections = self._split_diff_sections(diff)
        if not has_file_sections:
            return diff, False, 0

        total_files = len(sections)
        if total_files <= max_files:
            return diff, False, 0

        kept_sections = sections[:max_files]
        omitted_files = total_files - max_files
        limited = "\n".join("\n".join(section) for section in kept_sections)
        limited += (
            f"\n\n... [TRUNCATED: {omitted_files} file(s) omitted. "
            f"Total files in diff: {total_files}] ..."
        )
        return limited, True, omitted_files

    def truncate_diff(self, diff: str, max_lines: int = 2000) -> tuple[str, bool]:
        """
        Kept for compatibility: applies per-file truncation when
        the diff contains sections in 'diff --git' format.

        Returns:
            Tuple (truncated_diff, was_truncated).
        """
        return self.truncate_diff_per_file(diff, max_lines)

    def truncate_diff_per_file(self, diff: str, max_lines: int = 2000) -> tuple[str, bool]:
        """
        Truncates the diff per file if it exceeds the maximum lines per section.
        Falls back to global truncation if no file sections are present.
        
        Returns:
            Tuple (truncated_diff, was_truncated).
        """
        sections, has_file_sections = self._split_diff_sections(diff)

        # Fallback for diffs without file separators
        if not has_file_sections:
            lines = sections[0]
            if len(lines) <= max_lines:
                return diff, False
            truncated = "\n".join(lines[:max_lines])
            truncated += (
                f"\n\n... [TRUNCATED: {len(lines) - max_lines} lines omitted. "
                f"Total: {len(lines)} lines] ..."
            )
            return truncated, True

        truncated_any = False
        output_sections: list[str] = []
        for section in sections:
            if len(section) <= max_lines:
                output_sections.append("\n".join(section))
                continue

            truncated_any = True
            omitted = len(section) - max_lines
            part = "\n".join(section[:max_lines])
            part += (
                f"\n... [TRUNCATED IN THIS FILE: {omitted} lines omitted. "
                f"Original section: {len(section)} lines] ..."
            )
            output_sections.append(part)

        return "\n".join(output_sections), truncated_any

    def get_changed_files_summary(self, diff: str) -> list[dict]:
        """
        Extracts a summary of changed files from the diff.
        
        Returns:
            List of dicts with 'file', 'additions', 'deletions'.
        """
        files = []
        current_file = None
        additions = 0
        deletions = 0

        for line in diff.split("\n"):
            if line.startswith("diff --git"):
                if current_file:
                    files.append({
                        "file": current_file,
                        "additions": additions,
                        "deletions": deletions,
                    })
                # Extract file name
                parts = line.split(" b/")
                current_file = parts[-1] if len(parts) > 1 else "unknown"
                additions = 0
                deletions = 0
            elif line.startswith("+") and not line.startswith("+++"):
                additions += 1
            elif line.startswith("-") and not line.startswith("---"):
                deletions += 1

        if current_file:
            files.append({
                "file": current_file,
                "additions": additions,
                "deletions": deletions,
            })

        return files
