"""Managed local repository cache and source-branch context helpers."""

from __future__ import annotations

import base64
import datetime
import fnmatch
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .config import ReviewConfig
from .tfs_client import DEFAULT_PROJECT_CONTEXT_EXTENSIONS, DEFAULT_PROJECT_CONTEXT_FILENAMES


class LocalRepoError(Exception):
    """Raised when a managed local repository operation fails."""


@dataclass
class LocalRepoResolution:
    """Describes the local repository selected for a review."""

    path: str
    cloned: bool = False
    updated: bool = False
    managed: bool = False


class LocalRepoManager:
    """Owns managed clones used for PR diffs and repository context."""

    def __init__(self, config: ReviewConfig):
        self.config = config
        self.clone_root = os.path.abspath(
            os.path.expanduser(
                os.path.expandvars(config.tfs_local_clone_root or ".ai-review/repos")
            )
        )
        self._http_auth_header = ""
        if config.tfs_pat:
            encoded = base64.b64encode(f":{config.tfs_pat}".encode()).decode()
            self._http_auth_header = f"Authorization: Basic {encoded}"

    def ensure_repo_available(
        self,
        *,
        repository_name: str,
        clone_url: str,
        repository_id: str = "",
    ) -> LocalRepoResolution:
        """Returns a local clone path, cloning into the managed cache if needed."""
        explicit_path = (self.config.tfs_local_repo_path or "").strip()
        if explicit_path:
            path = os.path.abspath(os.path.expanduser(os.path.expandvars(explicit_path)))
            self._assert_git_repo(path)
            return LocalRepoResolution(path=path, managed=False)

        if not clone_url:
            raise LocalRepoError(
                f"Repository '{repository_name}' does not expose a clone URL."
            )

        os.makedirs(self.clone_root, exist_ok=True)
        repo_path = os.path.join(
            self.clone_root,
            self._safe_cache_name(repository_name, repository_id),
        )

        if os.path.isdir(repo_path):
            self._assert_git_repo(repo_path)
            return LocalRepoResolution(path=repo_path, updated=True, managed=True)

        self._clone_repo(clone_url, repo_path)
        return LocalRepoResolution(path=repo_path, cloned=True, managed=True)

    def _clone_repo(self, clone_url: str, repo_path: str) -> None:
        cmd = ["git"]
        if self._http_auth_header:
            cmd.extend(["-c", f"http.extraHeader={self._http_auth_header}"])
        cmd.extend(["clone", clone_url, repo_path, "--quiet"])
        self._run(cmd, cwd=self.clone_root)

    def _assert_git_repo(self, repo_path: str) -> None:
        self._run(["git", "rev-parse", "--git-dir"], cwd=repo_path)

    def _run(self, cmd: list[str], cwd: str) -> str:
        try:
            result = subprocess.run(
                cmd,
                cwd=cwd,
                capture_output=True,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
            )
        except FileNotFoundError as exc:
            raise LocalRepoError("Git not found. Make sure Git is installed.") from exc
        except subprocess.TimeoutExpired as exc:
            raise LocalRepoError(f"Timeout executing: {' '.join(cmd)}") from exc

        if result.returncode != 0:
            raise LocalRepoError(
                f"Git command failed: {' '.join(cmd)}\n"
                f"Error: {result.stderr.strip()}"
            )
        return result.stdout

    def _safe_cache_name(self, repository_name: str, repository_id: str = "") -> str:
        base = re.sub(r"[^A-Za-z0-9._-]+", "-", repository_name.strip()).strip(".-")
        if not base:
            base = "repository"
        suffix = re.sub(r"[^A-Za-z0-9._-]+", "-", repository_id.strip()).strip(".-")
        return f"{base}-{suffix[:12]}" if suffix else base


class LocalRepoContext:
    """Reads repository structure and file context from a local git clone."""

    def __init__(self, repo_path: str, config: ReviewConfig):
        self.repo_path = repo_path
        self.config = config

    def checkout_target_for_managed_cache(self, target_ref: str) -> None:
        """Aligns a managed cache worktree with the PR target branch for local tools."""
        target = _branch_name(target_ref)
        if not target:
            return
        self._run_git("switch", "-C", target, f"origin/{target}", "--quiet")

    def map_repo_json(self, repository: str, ref: str) -> str:
        """Returns a JSON structure map for eligible files at a git ref."""
        branch = _branch_name(ref)
        tree_ref = f"origin/{branch}" if branch else ref
        entries = self._eligible_paths(tree_ref)
        directories = sorted({
            directory
            for item in entries
            for directory in _parent_directories(item["path"])
        })

        payload = {
            "repository": repository,
            "ref": tree_ref,
            "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "root": self.repo_path,
            "directories": directories,
            "files": entries,
            "counts": {
                "directories": len(directories),
                "files": len(entries),
            },
        }
        return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)

    def get_changed_files_context(
        self,
        branch: str,
        changed_files: list[dict],
        max_chars: int = 120000,
        file_max_chars: int = 30000,
    ) -> str:
        """Fetches source-branch contents for changed files from the local clone."""
        paths = [
            str(item.get("path", ""))
            for item in changed_files
            if str(item.get("change_type", "")).lower() != "delete"
        ]
        return self.get_files_context(
            branch,
            paths,
            title="Source branch full files with changes applied",
            intro=(
                "These are the latest source branch contents of files changed by "
                "the PR. Use them as read-only support context, but keep findings "
                "anchored to actual changed PR lines."
            ),
            max_files=len(paths) if paths else 1,
            max_chars=max_chars,
            file_max_chars=file_max_chars,
        )

    def get_files_context(
        self,
        branch: str,
        requested_paths: list[str],
        *,
        title: str = "Requested repository context",
        intro: str = "These files are read-only context, not review targets.",
        max_files: int = 20,
        max_chars: int = 120000,
        file_max_chars: int = 30000,
    ) -> str:
        """Fetches selected eligible file contents from a source branch ref."""
        branch_name = _branch_name(branch)
        if not branch_name or not requested_paths:
            return ""

        tree_ref = f"origin/{branch_name}"
        eligible = {item["path"].lower(): item["path"] for item in self._eligible_paths(tree_ref)}
        selected: list[str] = []
        seen: set[str] = set()
        for requested in requested_paths:
            key = _normalize_path(requested).lower()
            if not key or key in seen:
                continue
            path = eligible.get(key)
            if not path:
                continue
            seen.add(key)
            selected.append(path)
            if len(selected) >= max_files:
                break

        if not selected:
            return ""

        parts = [
            f"### {title}",
            f"Source branch: {branch_name}",
            "",
            intro,
            "",
        ]
        used_chars = 0
        included = 0
        truncated = False

        for path in selected:
            remaining = max_chars - used_chars
            if remaining <= 0:
                truncated = True
                break
            try:
                content = self._show_file(tree_ref, path)
            except LocalRepoError:
                continue
            if len(content) > file_max_chars:
                content = content[:file_max_chars]
                truncated = True
            if len(content) > remaining:
                content = content[:remaining]
                truncated = True

            parts.extend([f"#### /{path}", "````text", content, "````", ""])
            used_chars += len(content)
            included += 1
            if truncated:
                break

        if included == 0:
            return ""
        if truncated:
            parts.append(
                f"[Repository context truncated: included {included} file(s), "
                f"used {used_chars} of {max_chars} configured characters.]"
            )
        return "\n".join(parts).strip()

    def get_source_file_contents(self, branch: str, requested_paths: list[str]) -> dict[str, str]:
        """Returns source-branch file contents keyed by normalized path."""
        branch_name = _branch_name(branch)
        if not branch_name or not requested_paths:
            return {}

        tree_ref = f"origin/{branch_name}"
        eligible = {item["path"].lower(): item["path"] for item in self._eligible_paths(tree_ref)}
        contents: dict[str, str] = {}
        for requested in requested_paths:
            key = _normalize_path(requested).lower()
            path = eligible.get(key)
            if not path or key in contents:
                continue
            try:
                contents[path] = self._show_file(tree_ref, path)
            except LocalRepoError:
                continue
        return contents

    def _eligible_paths(self, ref: str) -> list[dict]:
        output = self._run_git("ls-tree", "-r", "-l", ref)
        extensions = _normalize_extensions(self.config.project_context_file_extensions)
        entries: list[dict] = []

        for line in output.splitlines():
            parsed = _parse_ls_tree_line(line)
            if not parsed:
                continue
            path, size = parsed
            if not self._is_eligible_path(path, extensions):
                continue
            entries.append({
                "path": path,
                "name": os.path.basename(path),
                "directory": os.path.dirname(path).replace("\\", "/"),
                "extension": os.path.splitext(path)[1].lower(),
                "size": size,
            })
        return sorted(entries, key=lambda item: item["path"].lower())

    def _show_file(self, ref: str, path: str) -> str:
        return self._run_git("show", f"{ref}:{path}")

    def _run_git(self, *args: str) -> str:
        cmd = ["git", *args]
        try:
            result = subprocess.run(
                cmd,
                cwd=self.repo_path,
                capture_output=True,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
        except FileNotFoundError as exc:
            raise LocalRepoError("Git not found. Make sure Git is installed.") from exc
        except subprocess.TimeoutExpired as exc:
            raise LocalRepoError(f"Timeout executing: {' '.join(cmd)}") from exc

        if result.returncode != 0:
            raise LocalRepoError(
                f"Git command failed: {' '.join(cmd)}\n"
                f"Error: {result.stderr.strip()}"
            )
        return result.stdout

    def _is_eligible_path(self, path: str, extensions: list[str]) -> bool:
        normalized = _normalize_path(path)
        if not normalized or normalized.startswith("../"):
            return False
        if _matches_exclude(normalized, self.config.project_context_exclude_patterns):
            return False

        name = os.path.basename(normalized).lower()
        ext = os.path.splitext(name)[1].lower()
        if extensions:
            return ext in extensions
        return ext in DEFAULT_PROJECT_CONTEXT_EXTENSIONS or name in DEFAULT_PROJECT_CONTEXT_FILENAMES


def _branch_name(ref: str) -> str:
    value = str(ref or "").strip()
    if value.startswith("refs/heads/"):
        value = value[len("refs/heads/"):]
    if value.startswith("origin/"):
        value = value[len("origin/"):]
    return value


def _normalize_path(path: str) -> str:
    value = str(path or "").replace("\\", "/").strip()
    if value.startswith(("a/", "b/")):
        value = value[2:]
    return value.lstrip("/")


def _normalize_extensions(extensions: Optional[list[str]]) -> list[str]:
    normalized = []
    for ext in extensions or []:
        value = str(ext).strip().lower()
        if not value:
            continue
        normalized.append(value if value.startswith(".") else f".{value}")
    return normalized


def _parent_directories(path: str) -> list[str]:
    parts = _normalize_path(path).split("/")[:-1]
    directories = []
    for index in range(1, len(parts) + 1):
        directories.append("/".join(parts[:index]))
    return directories


def _parse_ls_tree_line(line: str) -> tuple[str, int] | None:
    if "\t" not in line:
        return None
    metadata, path = line.split("\t", 1)
    bits = metadata.split()
    if len(bits) < 4 or bits[1] != "blob":
        return None
    try:
        size = int(bits[3]) if bits[3] != "-" else 0
    except ValueError:
        size = 0
    return _normalize_path(path), size


def _matches_exclude(path: str, patterns: list[str]) -> bool:
    normalized = _normalize_path(path)
    parts = normalized.split("/")
    for pattern in patterns or []:
        candidate = str(pattern).replace("\\", "/").strip()
        if not candidate:
            continue
        glob_candidate = candidate.strip("/")
        if fnmatch.fnmatch(normalized, glob_candidate):
            return True
        if any(fnmatch.fnmatch(part, glob_candidate) for part in parts):
            return True
        plain = glob_candidate.strip("/")
        if plain in parts or normalized == plain or normalized.startswith(f"{plain}/"):
            return True
    return False
