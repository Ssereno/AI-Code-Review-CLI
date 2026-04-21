"""
Git Utilities Module - AI Code Review
========================================
Responsible for capturing Git diffs in different scenarios:
- Staged changes (before commit)
- Specific commits
- Differences between branches
- Working directory changes

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
    # Repository information
    # ------------------------------------------------------------------
    def get_current_branch(self) -> str:
        """Returns the current branch name."""
        return self._run_git("branch", "--show-current").strip()

    def get_repo_name(self) -> str:
        """Returns the repository name."""
        try:
            remote_url = self._run_git("remote", "get-url", "origin").strip()
            # Extract repo name from URL
            name = remote_url.rstrip("/").split("/")[-1]
            if name.endswith(".git"):
                name = name[:-4]
            return name
        except GitError:
            return os.path.basename(self.repo_path)

    def get_remote_url(self) -> str:
        """Returns the remote origin URL."""
        try:
            return self._run_git("remote", "get-url", "origin").strip()
        except GitError:
            return "(no remote configured)"

    def list_branches(self, remote: bool = False) -> list[str]:
        """Lists available branches."""
        args = ["branch"]
        if remote:
            args.append("-r")
        output = self._run_git(*args)
        branches = []
        for line in output.strip().split("\n"):
            branch = line.strip().lstrip("* ").strip()
            if branch and "HEAD" not in branch:
                branches.append(branch)
        return branches

    def get_recent_commits(self, count: int = 10, branch: Optional[str] = None) -> list[dict]:
        """
        Returns the most recent commits.
        
        Returns:
            List of dicts with 'hash', 'short_hash', 'author', 'date', 'message'.
        """
        args = [
            "log",
            f"-{count}",
            "--pretty=format:%H|%h|%an|%ai|%s",
        ]
        if branch:
            args.append(branch)

        output = self._run_git(*args)
        commits = []
        for line in output.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split("|", 4)
            if len(parts) == 5:
                commits.append({
                    "hash": parts[0],
                    "short_hash": parts[1],
                    "author": parts[2],
                    "date": parts[3],
                    "message": parts[4],
                })
        return commits

    # ------------------------------------------------------------------
    # Diff capture
    # ------------------------------------------------------------------
    def get_staged_diff(self) -> str:
        """
        Captures the diff of staged files (git add).
        Used for review before committing.
        """
        diff = self._run_git("diff", "--cached", "--no-color")
        if not diff.strip():
            raise GitError(
                "No staged changes found.\n"
                "Use 'git add <file>' to add files to staging."
            )
        return diff

    def get_working_diff(self) -> str:
        """
        Captures the diff of modified files in the working directory.
        (Changes not yet added to staging.)
        """
        diff = self._run_git("diff", "--no-color")
        if not diff.strip():
            raise GitError(
                "No changes in working directory.\n"
                "Files may already be staged (use --staged)."
            )
        return diff

    def get_all_changes_diff(self) -> str:
        """
        Captures the diff of ALL changes (staged + unstaged).
        """
        staged = self._run_git("diff", "--cached", "--no-color", check=False)
        unstaged = self._run_git("diff", "--no-color", check=False)

        combined = ""
        if staged.strip():
            combined += f"# === STAGED CHANGES ===\n{staged}\n"
        if unstaged.strip():
            combined += f"# === WORKING DIRECTORY CHANGES ===\n{unstaged}\n"

        if not combined.strip():
            raise GitError("No changes (staged or unstaged) in the repository.")

        return combined

    def get_commit_diff(self, commit_hash: str) -> str:
        """
        Captures the diff of a specific commit.
        
        Args:
            commit_hash: Commit hash (full or abbreviated).
        """
        diff = self._run_git("show", commit_hash, "--no-color", "--format=")
        if not diff.strip():
            raise GitError(f"Commit '{commit_hash}' contains no code changes.")
        return diff

    def get_commit_range_diff(self, from_commit: str, to_commit: str = "HEAD") -> str:
        """
        Captures the diff between two commits.
        
        Args:
            from_commit: Starting commit hash.
            to_commit: Ending commit hash (default: HEAD).
        """
        diff = self._run_git("diff", f"{from_commit}..{to_commit}", "--no-color")
        if not diff.strip():
            raise GitError(
                f"No differences between '{from_commit}' and '{to_commit}'."
            )
        return diff

    def get_branch_diff(self, source_branch: str, target_branch: Optional[str] = None) -> str:
        """
        Captures the diff between two branches.
        Useful to simulate a Pull Request diff.
        
        Args:
            source_branch: Branch with changes (feature branch).
            target_branch: Target branch (default: current branch).
        """
        if target_branch is None:
            target_branch = self.get_current_branch()

        # Use merge-base to get the correct diff (like a real PR)
        try:
            merge_base = self._run_git(
                "merge-base", target_branch, source_branch
            ).strip()
            diff = self._run_git(
                "diff", f"{merge_base}..{source_branch}", "--no-color"
            )
        except GitError:
            # Fallback: direct diff between branches
            diff = self._run_git(
                "diff", f"{target_branch}..{source_branch}", "--no-color"
            )

        if not diff.strip():
            raise GitError(
                f"No differences between '{target_branch}' and '{source_branch}'."
            )
        return diff

    def get_file_diff(self, file_path: str, staged: bool = False) -> str:
        """
        Captures the diff of a specific file.
        
        Args:
            file_path: File path.
            staged: If True, captures diff from staging.
        """
        args = ["diff", "--no-color"]
        if staged:
            args.append("--cached")
        args.append("--")
        args.append(file_path)

        diff = self._run_git(*args)
        if not diff.strip():
            raise GitError(f"No changes in file '{file_path}'.")
        return diff

    # ------------------------------------------------------------------
    # Filters and Utilities
    # ------------------------------------------------------------------
    def filter_diff_additions_only(self, diff: str) -> str:
        """
        Removes context lines and deleted lines (-) from the diff.
        Keeps only added lines (+) and structural headers needed for the LLM.

        Lines kept:
            - diff --git ...
            - --- a/...
            - +++ b/...
            - @@ ... @@
            - + <content>

        Returns:
            Filtered diff.
        """
        result = []
        for line in diff.split("\n"):
            if (
                line.startswith("diff --git")
                or line.startswith("--- ")
                or line.startswith("+++ ")
                or line.startswith("@@")
                or (line.startswith("+") and not line.startswith("+++"))
            ):
                result.append(line)
            # Context lines, deleted lines and '\ No newline' markers are discarded.
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

    def filter_diff_by_extensions(self, diff: str, extensions: list[str]) -> str:
        """
        Filters the diff to include only files with specific extensions.
        
        Args:
            diff: The full diff.
            extensions: List of extensions (e.g., ['.py', '.js', '.cs']).
        """
        if not extensions:
            return diff

        filtered_sections = []
        current_section = []
        include_section = False

        for line in diff.split("\n"):
            if line.startswith("diff --git"):
                # Save previous section if applicable
                if include_section and current_section:
                    filtered_sections.append("\n".join(current_section))
                current_section = [line]
                # Check if file has an allowed extension
                file_path = line.split(" b/")[-1] if " b/" in line else ""
                include_section = any(file_path.endswith(ext) for ext in extensions)
            else:
                current_section.append(line)

        # Last section
        if include_section and current_section:
            filtered_sections.append("\n".join(current_section))

        result = "\n".join(filtered_sections)
        if not result.strip():
            raise GitError(
                f"After filtering by extensions {extensions}, no changes remain."
            )
        return result

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
