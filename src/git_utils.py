# License: Apache-2.0
# Copyright 2026 Sérgio Sereno

"""
Git Utilities Module - AI Code Review
========================================
Provides diff filtering, splitting, truncation and file-summary helpers
used by the PR review pipeline.

Works with any Git repository, including TFS/Azure DevOps.
"""

import difflib
import subprocess
import os
from typing import Optional

from .context_extractor import ContextExtractor

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
            - Lines inside XML context blocks (``<enclosing_scopes>`` or ``<adaptive_diff>``)

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
        _xml_start_tags = ("<enclosing_scopes", "<adaptive_diff")
        _xml_end_tags = ("</enclosing_scopes>", "</adaptive_diff>")

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
        Truncates diff lines per file while preserving complete XML context blocks.
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
            xml_start = next(
                (
                    index
                    for index, line in enumerate(section)
                    if line.startswith(("<enclosing_scopes", "<adaptive_diff"))
                ),
                len(section),
            )
            diff_lines = section[:xml_start]
            context_lines = section[xml_start:]

            if len(diff_lines) <= max_lines:
                output_sections.append("\n".join(section))
                continue

            truncated_any = True
            omitted = len(diff_lines) - max_lines
            truncated_section = diff_lines[:max_lines]
            truncated_section.append(
                f"... [TRUNCATED IN THIS FILE: {omitted} diff lines omitted. "
                f"Original diff section: {len(diff_lines)} lines] ..."
            )
            truncated_section.extend(context_lines)
            output_sections.append("\n".join(truncated_section))

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

    # ------------------------------------------------------------------
    # Local Git Repository Inspection & Diff Generation
    # ------------------------------------------------------------------
    def get_repo_root(self) -> str:
        """Returns the absolute path to the Git repository root."""
        try:
            root = self._run_git("rev-parse", "--show-toplevel").strip()
            return root if root else self.repo_path
        except GitError:
            return self.repo_path

    def get_repo_name(self) -> str:
        """Returns the repository directory name."""
        root = self.get_repo_root().rstrip("/\\")
        return os.path.basename(root) or "local-repo"

    def get_current_branch(self) -> str:
        """Returns the current branch name (or commit SHA if detached)."""
        try:
            branch = self._run_git("rev-parse", "--abbrev-ref", "HEAD").strip()
            if branch and branch != "HEAD":
                return branch
            return self._run_git("rev-parse", "--short", "HEAD").strip()
        except GitError:
            return "HEAD"

    @staticmethod
    def _parse_name_status(output: str) -> list[dict]:
        """Parses ``git diff --name-status`` output into structured file entries."""
        files: list[dict] = []
        for raw_line in output.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                continue

            status_code = parts[0].upper()
            if status_code.startswith("R") and len(parts) >= 3:
                orig_rel = parts[1].replace("\\", "/").lstrip("/")
                new_rel = parts[2].replace("\\", "/").lstrip("/")
                files.append({
                    "path": f"/{new_rel}",
                    "change_type": "rename",
                    "original_path": f"/{orig_rel}",
                })
            else:
                rel_path = parts[1].replace("\\", "/").lstrip("/")
                change_type_map = {
                    "A": "add",
                    "M": "edit",
                    "D": "delete",
                    "T": "edit",
                    "C": "add",
                }
                change_type = change_type_map.get(status_code[0], "edit")
                files.append({
                    "path": f"/{rel_path}",
                    "change_type": change_type,
                    "original_path": f"/{rel_path}",
                })
        return files

    def _resolve_diff_sources(
        self,
        staged: bool = False,
        from_ref: Optional[str] = None,
        to_ref: Optional[str] = None,
        commit: Optional[str] = None,
    ) -> tuple[str, str]:
        """Determines the old and new content source identifiers for local diffs."""
        if commit:
            return f"{commit}~1", commit
        if from_ref:
            return from_ref, (to_ref or "HEAD")
        if staged:
            return "HEAD", "INDEX"
        return "HEAD", "WORKTREE"

    def get_local_changed_files(
        self,
        staged: bool = False,
        from_ref: Optional[str] = None,
        to_ref: Optional[str] = None,
        commit: Optional[str] = None,
    ) -> list[dict]:
        """Lists files changed in the local Git repository for the selected target.

        Args:
            staged: When True, inspects only staged changes (``--cached``).
            from_ref: Base branch or commit to compare from.
            to_ref: Target branch or commit to compare to (default ``HEAD`` when
                *from_ref* is provided).
            commit: Single commit SHA/ref to inspect (compares ``<commit>~1`` to
                ``<commit>``).

        Returns:
            List of dicts with ``path``, ``change_type`` and ``original_path``.
        """
        if commit:
            try:
                out = self._run_git("diff", "--name-status", "-M", f"{commit}~1", commit)
            except GitError:
                out = self._run_git(
                    "diff-tree", "--no-commit-id", "--name-status", "-r", "-M", commit
                )
            return self._parse_name_status(out)

        if from_ref:
            target = to_ref or "HEAD"
            out = self._run_git("diff", "--name-status", "-M", from_ref, target)
            return self._parse_name_status(out)

        if staged:
            out = self._run_git("diff", "--cached", "--name-status", "-M")
            return self._parse_name_status(out)

        # Default: uncommitted changes (working tree + index vs HEAD) plus untracked files
        try:
            out = self._run_git("diff", "--name-status", "-M", "HEAD")
        except GitError:
            out = self._run_git("diff", "--cached", "--name-status", "-M")

        files = self._parse_name_status(out)
        existing_paths = {f["path"] for f in files}

        try:
            untracked = self._run_git("ls-files", "--others", "--exclude-standard")
            for raw_line in untracked.splitlines():
                rel_path = raw_line.strip().replace("\\", "/").lstrip("/")
                if not rel_path:
                    continue
                slashed = f"/{rel_path}"
                if slashed not in existing_paths:
                    files.append({
                        "path": slashed,
                        "change_type": "add",
                        "original_path": slashed,
                    })
                    existing_paths.add(slashed)
        except GitError:
            pass

        return files

    def _get_local_file_content(self, file_path: str, source: str) -> str:
        """Reads file content from the working tree, staging index, or a Git ref."""
        rel_path = file_path.replace("\\", "/").lstrip("/")
        if source == "WORKTREE":
            full_path = os.path.join(self.get_repo_root(), rel_path)
            with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
                return fh.read()
        if source == "INDEX":
            return self._run_git("show", f":{rel_path}")
        return self._run_git("show", f"{source}:{rel_path}")

    def get_local_diff(
        self,
        review_scope: str = "diff_only",
        excluded_paths: Optional[list[str]] = None,
        file_extensions_filter: Optional[list[str]] = None,
        staged: bool = False,
        from_ref: Optional[str] = None,
        to_ref: Optional[str] = None,
        commit: Optional[str] = None,
        context_extractor: Optional[ContextExtractor] = None,
    ) -> str:
        """Builds the review diff payload for local Git changes.

        Uses the same unified-diff + AST context extraction pipeline
        (:class:`~src.context_extractor.ContextExtractor`) and ``full_code``
        mode as :meth:`~src.tfs_client.TFSClient.get_pull_request_diff`.
        """
        changes = self.get_local_changed_files(
            staged=staged,
            from_ref=from_ref,
            to_ref=to_ref,
            commit=commit,
        )
        if not changes:
            return ""

        old_source, new_source = self._resolve_diff_sources(
            staged=staged,
            from_ref=from_ref,
            to_ref=to_ref,
            commit=commit,
        )
        review_scope = (review_scope or "diff_only").lower()
        extractor = context_extractor or ContextExtractor()

        _excl_prefixes = [
            p.replace("\\", "/").lstrip("/").lower()
            for p in (excluded_paths or [])
        ]
        _ext_filter = [e.lower() for e in (file_extensions_filter or [])]

        diff_parts: list[str] = []
        for change in changes:
            change_type = change.get("change_type", "edit")
            if change_type == "delete":
                continue

            file_path = change.get("path") or "unknown"
            original_path = change.get("original_path") or file_path

            norm_path = file_path.replace("\\", "/").lstrip("/").lower()
            if _excl_prefixes and any(
                norm_path.startswith(excl) for excl in _excl_prefixes
            ):
                continue

            if _ext_filter and not any(
                norm_path.endswith(ext) for ext in _ext_filter
            ):
                continue

            if review_scope == "full_code":
                diff_parts.extend(
                    self._build_local_full_code_diff_part(
                        file_path=file_path,
                        change_type=change_type,
                        new_source=new_source,
                    )
                )
                diff_parts.append("")
                continue

            diff_parts.extend(
                self._build_local_unified_diff_part(
                    file_path=file_path,
                    original_path=original_path,
                    change_type=change_type,
                    old_source=old_source,
                    new_source=new_source,
                    extractor=extractor,
                )
            )
            diff_parts.append("")

        if not diff_parts:
            return ""

        return "\n".join(diff_parts)

    def _build_local_full_code_diff_part(
        self,
        file_path: str,
        change_type: str,
        new_source: str,
    ) -> list[str]:
        """Builds a ``full_code`` payload for a single locally changed file."""
        parts = [
            f"diff --git a{file_path} b{file_path}",
            f"--- a{file_path}",
            f"+++ b{file_path}",
            f"@@ Change type: {change_type} @@",
        ]

        if change_type in ("edit", "add", "rename"):
            try:
                content = self._get_local_file_content(file_path, new_source)
                if content:
                    for line in content.split("\n"):
                        parts.append(f"+{line}")
            except Exception:
                parts.append(f"+[Content not available for {file_path}]")

        return parts

    def _build_local_unified_diff_part(
        self,
        file_path: str,
        original_path: str,
        change_type: str,
        old_source: str,
        new_source: str,
        extractor: ContextExtractor,
    ) -> list[str]:
        """Builds a unified diff + AST context block for a single local file."""
        old_lines: list[str] = []
        new_lines: list[str] = []

        if change_type in ("edit", "rename"):
            try:
                old_content = self._get_local_file_content(original_path, old_source)
                old_lines = old_content.splitlines()
            except Exception:
                old_lines = []

        if change_type in ("edit", "rename", "add"):
            try:
                new_content = self._get_local_file_content(file_path, new_source)
                new_lines = new_content.splitlines()
            except Exception:
                new_lines = []

        diff = list(difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"a{original_path}",
            tofile=f"b{file_path}",
            lineterm="",
            n=5,
        ))

        if not diff:
            return [
                f"diff --git a{original_path} b{file_path}",
                f"--- a{original_path}",
                f"+++ b{file_path}",
                "@@ No textual differences detected (possible binary/metadata change) @@",
            ]

        result = [f"diff --git a{original_path} b{file_path}"] + list(diff)

        if new_lines:
            new_content_text = "\n".join(new_lines)
            file_diff_text = "\n".join(result)
            changed_lines = ContextExtractor.changed_lines_from_diff(file_diff_text)
            _strategy, xml_block = extractor.extract(
                file_path=file_path,
                new_content=new_content_text,
                changed_lines=changed_lines,
            )
            if xml_block:
                result.append(xml_block)

        return result
