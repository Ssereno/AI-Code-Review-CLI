"""
TFS/Azure DevOps Module - AI Code Review
==========================================
Integration with Team Foundation Server and Azure DevOps for:
- Listing Pull Requests (with filters by repository, author, status)
- Getting full Pull Request details (diff, files, commits)
- Posting review comments on the PR:
    - Inline comments (on specific code lines)
    - General PR comments
    - Comment thread support

Uses Azure DevOps REST API v7.0+.
Works with both on-premises TFS and Azure DevOps Services.
"""

import base64
import difflib
import fnmatch
import hashlib
import html
import os
import re
from typing import Optional

from .config import ReviewConfig


class TFSError(Exception):
    """Exception for TFS/Azure DevOps communication errors."""
    pass


DEFAULT_PROJECT_CONTEXT_EXTENSIONS = {
    ".bat",
    ".c",
    ".cfg",
    ".cmd",
    ".conf",
    ".cpp",
    ".cs",
    ".csproj",
    ".css",
    ".fs",
    ".fsproj",
    ".go",
    ".gradle",
    ".h",
    ".hpp",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".kt",
    ".kts",
    ".less",
    ".md",
    ".php",
    ".props",
    ".ps1",
    ".py",
    ".rb",
    ".rs",
    ".rst",
    ".sass",
    ".scala",
    ".scss",
    ".sh",
    ".sln",
    ".sql",
    ".svelte",
    ".swift",
    ".targets",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".vb",
    ".vue",
    ".xml",
    ".yaml",
    ".yml",
}

DEFAULT_PROJECT_CONTEXT_FILENAMES = {
    "dockerfile",
    "makefile",
    "rakefile",
    "gemfile",
    "procfile",
    "requirements",
}

WORK_ITEM_CONTEXT_FIELD_LABELS = {
    "System.Title": "Title",
    "System.WorkItemType": "Type",
    "System.State": "State",
    "System.Description": "Description",
    "Microsoft.VSTS.Common.AcceptanceCriteria": "Acceptance Criteria",
    "Microsoft.VSTS.TCM.ReproSteps": "Repro Steps",
    "Microsoft.VSTS.TCM.SystemInfo": "System Info",
}

DEFAULT_WORK_ITEM_CONTEXT_FIELDS = list(WORK_ITEM_CONTEXT_FIELD_LABELS)

TOOL_COMMENT_MARKER = "<!-- ai-code-review-cli -->"
TOOL_COMMENT_KIND_PREFIX = "<!-- ai-code-review-kind:"
TOOL_COMMENT_FINGERPRINT_PREFIX = "<!-- ai-code-review-fingerprint:"
VISIBLE_TOOL_COMMENT_MARKER = "`#AI`"

THREAD_STATUS_NAMES = {
    1: "active",
    2: "fixed",
    3: "wontfix",
    4: "closed",
    5: "bydesign",
    6: "pending",
}


class TFSClient:
    """Client for Azure DevOps / TFS REST API."""

    API_VERSION = "7.0"

    def __init__(self, config: ReviewConfig):
        self.config = config
        self.base_url = config.tfs_base_url.rstrip("/")
        self.collection = config.tfs_collection
        self.project = config.tfs_project
        self.pat = config.tfs_pat

        if not all([self.base_url, self.project, self.pat]):
            raise TFSError(
                "Incomplete TFS configuration. Required:\n"
                "  - TFS_BASE_URL (e.g., https://dev.azure.com/org or https://tfs.company.com/tfs)\n"
                "  - TFS_PROJECT (project name)\n"
                "  - TFS_PAT (Personal Access Token)"
            )

        self._session = None

    @property
    def session(self):
        """Lazy HTTP session initialization."""
        if self._session is None:
            try:
                import requests
            except ImportError:
                raise TFSError("Module 'requests' required: pip install requests")

            self._session = requests.Session()
            auth_string = base64.b64encode(f":{self.pat}".encode()).decode()
            self._session.headers.update({
                "Authorization": f"Basic {auth_string}",
                "Content-Type": "application/json",
            })

            # SSL/TLS: prefer CA bundle for corporate environments; allow opt-out for troubleshooting.
            if self.config.tfs_ca_bundle:
                ca_path = os.path.expandvars(os.path.expanduser(self.config.tfs_ca_bundle))
                if not os.path.isfile(ca_path):
                    raise TFSError(
                        f"TFS_CA_BUNDLE configured but file does not exist: {ca_path}"
                    )
                self._session.verify = ca_path
            else:
                self._session.verify = bool(self.config.tfs_verify_ssl)
                if self._session.verify is False:
                    try:
                        import urllib3

                        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                    except Exception:
                        pass
        return self._session

    def _api_url(self, path: str, api_version: Optional[str] = None) -> str:
        """Builds the API URL."""
        version = api_version or self.API_VERSION

        if "dev.azure.com" in self.base_url or "visualstudio.com" in self.base_url:
            base = f"{self.base_url}/{self.project}/_apis"
        else:
            base = f"{self.base_url}/{self.collection}/{self.project}/_apis"

        url = f"{base}/{path}"
        separator = "&" if "?" in url else "?"
        url += f"{separator}api-version={version}"
        return url

    def _get(self, path: str, params: Optional[dict] = None,
             api_version: Optional[str] = None) -> dict:
        """Makes a GET request to the API."""
        url = self._api_url(path, api_version)
        try:
            resp = self.session.get(url, params=params, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            raise TFSError(f"Error accessing TFS API ({path}): {exc}")

    def _post(self, path: str, data: dict,
              api_version: Optional[str] = None) -> dict:
        """Makes a POST request to the API."""
        url = self._api_url(path, api_version)
        try:
            resp = self.session.post(url, json=data, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            raise TFSError(f"Error sending to TFS API ({path}): {exc}")

    def _patch(self, path: str, data: dict,
               api_version: Optional[str] = None) -> dict:
        """Makes a PATCH request to the API."""
        url = self._api_url(path, api_version)
        try:
            resp = self.session.patch(url, json=data, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            raise TFSError(f"Error updating TFS API ({path}): {exc}")

    # ==================================================================
    # Pull Requests - Listing
    # ==================================================================
    def list_pull_requests(self, status: str = "active",
                           repository: Optional[str] = None,
                           author: Optional[str] = None,
                           reviewer: Optional[str] = None,
                           source_branch: Optional[str] = None,
                           target_branch: Optional[str] = None,
                           top: int = 50) -> list[dict]:
        """
        Lists Pull Requests in the project with advanced filters.
        
        Args:
            status: "active", "completed", "abandoned", "all"
            repository: Repository name (if None, lists all).
            author: Filter by author (display name or ID).
            reviewer: Filter by reviewer.
            source_branch: Filter by source branch.
            target_branch: Filter by target branch.
            top: Maximum number of PRs to return.
            
        Returns:
            List of PRs with summary information.
        """
        if repository:
            path = f"git/repositories/{repository}/pullrequests"
        else:
            path = "git/pullrequests"

        params = {"$top": top}

        if status != "all":
            params["searchCriteria.status"] = status

        if author:
            params["searchCriteria.creatorId"] = author

        if reviewer:
            params["searchCriteria.reviewerId"] = reviewer

        if source_branch:
            if not source_branch.startswith("refs/heads/"):
                source_branch = f"refs/heads/{source_branch}"
            params["searchCriteria.sourceRefName"] = source_branch

        if target_branch:
            if not target_branch.startswith("refs/heads/"):
                target_branch = f"refs/heads/{target_branch}"
            params["searchCriteria.targetRefName"] = target_branch

        data = self._get(path, params)
        prs = []
        for pr in data.get("value", []):
            prs.append(self._parse_pr_summary(pr))
        return prs

    def _parse_pr_summary(self, pr: dict) -> dict:
        """Extracts summary information from a PR."""
        reviewers = []
        for r in pr.get("reviewers", []):
            vote_map = {10: "✅ Approved", 5: "👍 Approved w/ suggestions",
                        0: "⏳ No vote", -5: "⏸️ Waiting for author", -10: "❌ Rejected"}
            reviewers.append({
                "name": r.get("displayName", ""),
                "vote": r.get("vote", 0),
                "vote_label": vote_map.get(r.get("vote", 0), "?"),
            })

        return {
            "id": pr["pullRequestId"],
            "title": pr["title"],
            "description": pr.get("description", ""),
            "author": pr["createdBy"]["displayName"],
            "author_id": pr["createdBy"].get("id", ""),
            "source_branch": pr["sourceRefName"].replace("refs/heads/", ""),
            "target_branch": pr["targetRefName"].replace("refs/heads/", ""),
            "status": pr["status"],
            "creation_date": pr["creationDate"],
            "repository": pr["repository"]["name"],
            "repository_id": pr["repository"]["id"],
            "merge_status": pr.get("mergeStatus", ""),
            "reviewers": reviewers,
            "labels": [l.get("name", "") for l in pr.get("labels", [])],
            "is_draft": pr.get("isDraft", False),
            "url": pr.get("url", ""),
        }

    # ==================================================================
    # Pull Requests - Details
    # ==================================================================
    def get_pull_request_details(self, repository: str, pr_id: int) -> dict:
        """
        Gets full details of a Pull Request.
        
        Args:
            repository: Repository name.
            pr_id: Pull Request ID.
            
        Returns:
            Dict with PR details including diff, changed files, commits.
        """
        # Base PR details
        path = f"git/repositories/{repository}/pullrequests/{pr_id}"
        pr_data = self._get(path)
        pr_summary = self._parse_pr_summary(pr_data)

        # PR Commits
        commits_path = f"git/repositories/{repository}/pullrequests/{pr_id}/commits"
        try:
            commits_data = self._get(commits_path)
            commits = []
            for c in commits_data.get("value", []):
                commits.append({
                    "id": c.get("commitId", ""),
                    "short_id": c.get("commitId", "")[:8],
                    "message": c.get("comment", ""),
                    "author": c.get("author", {}).get("name", ""),
                    "date": c.get("author", {}).get("date", ""),
                })
        except TFSError:
            commits = []

        # Changed files
        changed_files = self._get_pr_changed_files(repository, pr_id)

        pr_summary["commits"] = commits
        pr_summary["changed_files"] = changed_files
        return pr_summary

    def _get_pr_changed_files(self, repository: str, pr_id: int) -> list[dict]:
        """Gets the list of files changed in a PR."""
        iterations_path = f"git/repositories/{repository}/pullrequests/{pr_id}/iterations"
        try:
            iterations = self._get(iterations_path)
        except TFSError:
            return []

        if not iterations.get("value"):
            return []

        last_iteration = iterations["value"][-1]["id"]
        changes_path = (
            f"git/repositories/{repository}/pullrequests/{pr_id}"
            f"/iterations/{last_iteration}/changes"
        )

        try:
            changes = self._get(changes_path)
        except TFSError:
            return []

        files = []
        for change in changes.get("changeEntries", []):
            item = change.get("item", {})
            if item.get("isFolder"):
                continue
            files.append({
                "path": item.get("path", ""),
                "change_type": change.get("changeType", "unknown"),
                "original_path": change.get("originalPath", ""),
            })
        return files

    def get_pull_request_diff(self, repository: str, pr_id: int,
                              review_scope: str = "diff_with_context") -> str:
        """
        Gets the diff of a specific Pull Request.
        
        Args:
            repository: Repository name.
            pr_id: Pull Request ID.
            
        Returns:
            Diff as text.
        """
        # Get PR details
        path = f"git/repositories/{repository}/pullrequests/{pr_id}"
        pr_data = self._get(path)

        source_branch = pr_data["sourceRefName"]
        target_branch = pr_data["targetRefName"]

        # Get PR iterations
        iterations_path = f"git/repositories/{repository}/pullrequests/{pr_id}/iterations"
        iterations = self._get(iterations_path)

        if not iterations.get("value"):
            raise TFSError(f"PR #{pr_id} has no iterations/changes.")

        # Get changes from the last iteration
        last_iteration = iterations["value"][-1]["id"]
        changes_path = (
            f"git/repositories/{repository}/pullrequests/{pr_id}"
            f"/iterations/{last_iteration}/changes"
        )
        changes = self._get(changes_path)

        review_scope = (review_scope or "diff_with_context").lower()

        # Build diff from the changes
        diff_parts = []
        for change in changes.get("changeEntries", []):
            item = change.get("item", {})
            change_type = change.get("changeType", "unknown")
            file_path = item.get("path", "unknown")
            original_path = change.get("originalPath") or file_path

            if item.get("isFolder"):
                continue

            if review_scope == "full_code":
                diff_parts.extend(
                    self._build_full_code_diff_part(
                        repository=repository,
                        file_path=file_path,
                        change_type=change_type,
                        source_branch=source_branch,
                    )
                )
                diff_parts.append("")
                continue

            diff_parts.extend(
                self._build_unified_diff_part(
                    repository=repository,
                    file_path=file_path,
                    original_path=original_path,
                    change_type=change_type,
                    source_branch=source_branch,
                    target_branch=target_branch,
                )
            )
            diff_parts.append("")

        if not diff_parts:
            raise TFSError(f"PR #{pr_id} contains no file changes.")

        return "\n".join(diff_parts)

    def _build_full_code_diff_part(self, repository: str, file_path: str,
                                   change_type: str, source_branch: str) -> list[str]:
        """Builds a full_code-style payload with the complete content of the new version."""
        parts = [
            f"diff --git a{file_path} b{file_path}",
            f"--- a{file_path}",
            f"+++ b{file_path}",
            f"@@ Change type: {change_type} @@",
        ]

        if change_type in ("edit", "add", "rename"):
            try:
                content = self._get_file_content(
                    repository,
                    file_path,
                    version=source_branch.replace("refs/heads/", ""),
                    version_type="branch",
                )
                if content:
                    for line in content.split("\n"):
                        parts.append(f"+{line}")
            except Exception:
                parts.append(f"+[Content not available for {file_path}]")

        return parts

    def _build_unified_diff_part(self, repository: str, file_path: str,
                                 original_path: str, change_type: str,
                                 source_branch: str, target_branch: str) -> list[str]:
        """Builds a unified diff with only changed lines for diff_only."""
        old_lines: list[str] = []
        new_lines: list[str] = []

        source_ref = source_branch.replace("refs/heads/", "")
        target_ref = target_branch.replace("refs/heads/", "")

        if change_type in ("edit", "rename", "delete"):
            try:
                old_content = self._get_file_content(
                    repository,
                    original_path,
                    version=target_ref,
                    version_type="branch",
                )
                old_lines = old_content.splitlines()
            except Exception:
                old_lines = []

        if change_type in ("edit", "rename", "add"):
            try:
                new_content = self._get_file_content(
                    repository,
                    file_path,
                    version=source_ref,
                    version_type="branch",
                )
                new_lines = new_content.splitlines()
            except Exception:
                new_lines = []

        diff = list(difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"a{original_path}",
            tofile=f"b{file_path}",
            lineterm="",
            n=3,
        ))

        if not diff:
            return [
                f"diff --git a{original_path} b{file_path}",
                f"--- a{original_path}",
                f"+++ b{file_path}",
                "@@ No textual differences detected (possible binary/metadata change) @@",
            ]

        # difflib does not include the "diff --git" header — always add it
        # so that filter_diff_by_extensions and _split_diff_sections work correctly.
        return [f"diff --git a{original_path} b{file_path}"] + diff

    def _get_raw(self, path: str, params: Optional[dict] = None,
               api_version: Optional[str] = None) -> str:
        """Makes a GET request to the API and returns the response as raw text."""
        url = self._api_url(path, api_version)
        try:
            resp = self.session.get(url, params=params, timeout=30)
            resp.raise_for_status()
            return resp.text
        except Exception as exc:
            raise TFSError(f"Error accessing TFS API ({path}): {exc}")

    def _get_file_content(self, repository: str, file_path: str,
                          version: str = "", version_type: str = "branch") -> str:
        """Gets the content of a file from the repository."""
        path = f"git/repositories/{repository}/items"
        params = {
            "path": file_path,
        }
        if version:
            params["versionDescriptor.version"] = version
            params["versionDescriptor.versionType"] = version_type

        return self._get_raw(path, params)

    # ==================================================================
    # Repository Context
    # ==================================================================
    def get_project_context(self, repository: str, branch: str,
                            max_files: int = 0,
                            max_chars: int = 0,
                            file_extensions: Optional[list[str]] = None,
                            exclude_patterns: Optional[list[str]] = None) -> str:
        """
        Builds a repository snapshot to use as read-only LLM context.

        The returned text is intentionally separate from the review diff. The
        LLM can use it to understand contracts and call sites, but findings
        must still be anchored to lines in the PR diff.
        """
        branch_name = (branch or "").replace("refs/heads/", "")
        if not branch_name:
            return ""

        files = self._list_repository_files(repository, branch_name)
        extensions = self._normalize_extensions(file_extensions)
        eligible_paths = [
            item.get("path", "")
            for item in files
            if self._is_project_context_file(
                item=item,
                file_extensions=extensions,
                exclude_patterns=exclude_patterns,
            )
        ]

        if not eligible_paths:
            return ""

        eligible_paths = sorted(set(eligible_paths))
        file_limit = max_files if max_files and max_files > 0 else None
        char_limit = max_chars if max_chars and max_chars > 0 else None
        selected_paths = (
            eligible_paths[:file_limit]
            if file_limit is not None
            else eligible_paths
        )
        omitted_files = max(0, len(eligible_paths) - len(selected_paths))

        parts = [
            "### Full repository context",
            f"Repository: {repository}",
            f"Source branch: {branch_name}",
            "",
            "Use this repository snapshot only to understand existing architecture, "
            "contracts, dependencies, and call sites. The review target remains "
            "the PR diff only. Do not report issues from this context unless the "
            "issue is caused by an added or modified line in the PR diff.",
            "",
        ]

        used_chars = 0
        included_files = 0
        truncated = False

        for path in selected_paths:
            remaining = char_limit - used_chars if char_limit is not None else None
            if remaining is not None and remaining <= 0:
                truncated = True
                break

            try:
                content = self._get_file_content(
                    repository,
                    path,
                    version=branch_name,
                    version_type="branch",
                )
            except Exception:
                continue

            if remaining is not None and len(content) > remaining:
                content = content[:remaining]
                truncated = True

            parts.extend([
                f"#### {path}",
                "````text",
                content,
                "````",
                "",
            ])
            used_chars += len(content)
            included_files += 1

            if truncated:
                break

        if included_files == 0:
            return ""

        if truncated or omitted_files:
            configured_chars = (
                "unlimited" if char_limit is None else str(char_limit)
            )
            parts.append(
                "[Repository context truncated: "
                f"included {included_files} file(s), omitted {omitted_files} file(s), "
                f"used {used_chars} of {configured_chars} configured characters.]"
            )

        return "\n".join(parts).strip()

    def get_project_manifest(self, repository: str, branch: str,
                             max_chars: int = 60000,
                             file_extensions: Optional[list[str]] = None,
                             exclude_patterns: Optional[list[str]] = None) -> str:
        """Builds a compact manifest of eligible repository files."""
        branch_name = (branch or "").replace("refs/heads/", "")
        if not branch_name:
            return ""

        eligible_paths = self._get_project_context_paths(
            repository,
            branch_name,
            file_extensions=file_extensions,
            exclude_patterns=exclude_patterns,
        )
        if not eligible_paths:
            return ""

        parts = [
            "### Repository file manifest",
            f"Repository: {repository}",
            f"Source branch: {branch_name}",
            "",
            "The following files are available for on-demand context. Request only "
            "files that are necessary to understand the PR changes.",
            "",
        ]

        used_chars = 0
        included = 0
        truncated = False
        for path in eligible_paths:
            line = f"- {path}"
            line_chars = len(line) + 1
            if used_chars + line_chars > max_chars:
                truncated = True
                break
            parts.append(line)
            used_chars += line_chars
            included += 1

        omitted = max(0, len(eligible_paths) - included)
        if truncated or omitted:
            parts.append(
                f"[Repository manifest truncated: included {included} file(s), "
                f"omitted {omitted} file(s).]"
            )

        return "\n".join(parts).strip()

    def get_changed_files_context(self, repository: str, branch: str,
                                  changed_files: list[dict],
                                  max_chars: int = 120000,
                                  file_max_chars: int = 30000,
                                  file_extensions: Optional[list[str]] = None,
                                  exclude_patterns: Optional[list[str]] = None) -> str:
        """Fetches complete contents for eligible files changed by the PR."""
        branch_name = (branch or "").replace("refs/heads/", "")
        if not branch_name or not changed_files:
            return ""

        paths = [
            str(item.get("path", ""))
            for item in changed_files
            if str(item.get("change_type", "")).lower() != "delete"
        ]
        return self._build_repository_files_context(
            repository=repository,
            branch_name=branch_name,
            requested_paths=paths,
            title="Source branch full files with changes applied",
            intro=(
                "These are the latest source branch contents of files changed by "
                "the PR. Use them as primary code context, but keep findings "
                "anchored to changed PR lines."
            ),
            max_files=len(paths) if paths else 1,
            max_chars=max_chars,
            file_max_chars=file_max_chars,
            file_extensions=file_extensions,
            exclude_patterns=exclude_patterns,
        )

    def get_project_files_context(self, repository: str, branch: str,
                                  requested_paths: list[str],
                                  max_files: int = 20,
                                  max_chars: int = 120000,
                                  file_max_chars: int = 30000,
                                  file_extensions: Optional[list[str]] = None,
                                  exclude_patterns: Optional[list[str]] = None) -> str:
        """Fetches selected eligible repository files for on-demand context."""
        branch_name = (branch or "").replace("refs/heads/", "")
        if not branch_name or not requested_paths:
            return ""

        return self._build_repository_files_context(
            repository=repository,
            branch_name=branch_name,
            requested_paths=requested_paths,
            title="Requested repository context",
            intro=(
                "These files were requested by the model to understand the PR. "
                "They are read-only context, not review targets."
            ),
            max_files=max_files,
            max_chars=max_chars,
            file_max_chars=file_max_chars,
            file_extensions=file_extensions,
            exclude_patterns=exclude_patterns,
        )

    def get_source_file_contents(self, repository: str, branch: str,
                                 requested_paths: list[str]) -> dict[str, str]:
        """Fetches latest source-branch file contents keyed by normalized path."""
        branch_name = (branch or "").replace("refs/heads/", "")
        if not branch_name or not requested_paths:
            return {}

        contents: dict[str, str] = {}
        seen: set[str] = set()
        for requested_path in requested_paths:
            normalized = self._normalize_context_path(requested_path)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            file_path = str(requested_path or "").replace("\\", "/").strip()
            if file_path and not file_path.startswith("/"):
                file_path = f"/{file_path}"
            try:
                content = self._get_file_content(
                    repository,
                    file_path,
                    version=branch_name,
                    version_type="branch",
                )
            except Exception:
                continue
            contents[normalized] = content
        return contents

    def _get_project_context_paths(self, repository: str, branch_name: str,
                                   file_extensions: Optional[list[str]] = None,
                                   exclude_patterns: Optional[list[str]] = None) -> list[str]:
        """Returns sorted eligible file paths for repository context."""
        files = self._list_repository_files(repository, branch_name)
        extensions = self._normalize_extensions(file_extensions)
        eligible_paths = [
            item.get("path", "")
            for item in files
            if self._is_project_context_file(
                item=item,
                file_extensions=extensions,
                exclude_patterns=exclude_patterns,
            )
        ]
        return sorted(set(path for path in eligible_paths if path))

    def _build_repository_files_context(
        self,
        *,
        repository: str,
        branch_name: str,
        requested_paths: list[str],
        title: str,
        intro: str,
        max_files: int,
        max_chars: int,
        file_max_chars: int,
        file_extensions: Optional[list[str]],
        exclude_patterns: Optional[list[str]],
    ) -> str:
        """Renders selected repository files after eligibility validation."""
        eligible_paths = self._get_project_context_paths(
            repository,
            branch_name,
            file_extensions=file_extensions,
            exclude_patterns=exclude_patterns,
        )
        eligible_by_normalized = {
            self._normalize_context_path(path): path
            for path in eligible_paths
        }

        selected_paths: list[str] = []
        skipped_paths: list[str] = []
        seen: set[str] = set()
        for requested_path in requested_paths:
            normalized = self._normalize_context_path(requested_path)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            path = eligible_by_normalized.get(normalized)
            if not path:
                skipped_paths.append(str(requested_path))
                continue
            selected_paths.append(path)
            if len(selected_paths) >= max_files:
                break

        if not selected_paths and skipped_paths:
            return "\n".join([
                f"### {title}",
                intro,
                "",
                "[No requested files were eligible for context.]",
            ])
        if not selected_paths:
            return ""

        parts = [
            f"### {title}",
            f"Repository: {repository}",
            f"Source branch: {branch_name}",
            "",
            intro,
            "",
        ]

        used_chars = 0
        included_files = 0
        truncated = False

        for path in selected_paths:
            remaining = max_chars - used_chars
            if remaining <= 0:
                truncated = True
                break

            try:
                content = self._get_file_content(
                    repository,
                    path,
                    version=branch_name,
                    version_type="branch",
                )
            except Exception:
                skipped_paths.append(path)
                continue

            file_truncated = False
            if len(content) > file_max_chars:
                content = content[:file_max_chars]
                file_truncated = True
            if len(content) > remaining:
                content = content[:remaining]
                file_truncated = True
                truncated = True

            parts.extend([
                f"#### {path}",
                "````text",
                content,
                "````",
            ])
            if file_truncated:
                parts.append("[File context truncated.]")
            parts.append("")
            used_chars += len(content)
            included_files += 1

            if truncated:
                break

        if included_files == 0:
            return ""

        if skipped_paths:
            parts.append(
                "[Skipped context files: "
                + ", ".join(str(path) for path in skipped_paths[:10])
                + (", ..." if len(skipped_paths) > 10 else "")
                + "]"
            )
        omitted = max(0, len(selected_paths) - included_files)
        if truncated or omitted:
            parts.append(
                f"[{title} truncated: included {included_files} file(s), "
                f"omitted {omitted} file(s), used {used_chars} of {max_chars} characters.]"
            )

        return "\n".join(parts).strip()

    def _normalize_context_path(self, path: object) -> str:
        """Normalizes requested repository context paths for safe matching."""
        value = str(path or "").replace("\\", "/").strip()
        if value.startswith(("a/", "b/")):
            value = value[2:]
        return value.lstrip("/").lower()

    def _list_repository_files(self, repository: str, branch: str) -> list[dict]:
        """Lists files in a repository branch recursively."""
        path = f"git/repositories/{repository}/items"
        params = {
            "scopePath": "/",
            "recursionLevel": "Full",
            "includeContentMetadata": True,
            "versionDescriptor.version": branch,
            "versionDescriptor.versionType": "branch",
        }
        data = self._get(path, params)
        return data.get("value", [])

    def _is_project_context_file(self, item: dict, file_extensions: list[str],
                                 exclude_patterns: Optional[list[str]]) -> bool:
        """Returns whether a repository item should be included as context."""
        if item.get("isFolder") or item.get("gitObjectType") == "tree":
            return False

        path = item.get("path", "")
        if not path or self._is_context_path_excluded(path, exclude_patterns):
            return False

        filename = os.path.basename(path).lower()
        extension = os.path.splitext(filename)[1].lower()

        if file_extensions:
            return extension in file_extensions

        return (
            extension in DEFAULT_PROJECT_CONTEXT_EXTENSIONS
            or filename in DEFAULT_PROJECT_CONTEXT_FILENAMES
        )

    def _is_context_path_excluded(self, path: str,
                                  exclude_patterns: Optional[list[str]]) -> bool:
        """Checks path against configured directory, file and glob exclusions."""
        patterns = (
            exclude_patterns
            if exclude_patterns is not None
            else self.config.project_context_exclude_patterns
        )
        normalized = path.replace("\\", "/").lstrip("/").lower()
        path_parts = normalized.split("/")

        for pattern in patterns or []:
            candidate = str(pattern).replace("\\", "/").strip().lower()
            if not candidate:
                continue

            glob_candidate = candidate.lstrip("/")
            if fnmatch.fnmatch(normalized, glob_candidate):
                return True
            if any(fnmatch.fnmatch(part, glob_candidate) for part in path_parts):
                return True

            plain = glob_candidate.strip("/")
            if plain in path_parts:
                return True
            if normalized == plain or normalized.startswith(f"{plain}/"):
                return True

        return False

    def _normalize_extensions(self, extensions: Optional[list[str]]) -> list[str]:
        """Normalizes extension allowlists to dot-prefixed lowercase strings."""
        normalized = []
        for ext in extensions or []:
            value = str(ext).strip().lower()
            if not value:
                continue
            if not value.startswith("."):
                value = f".{value}"
            normalized.append(value)
        return normalized

    # ==================================================================
    # Work Item Context
    # ==================================================================
    def get_work_item_context(self, repository: str, pr_id: int,
                              max_items: int = 20,
                              max_chars: int = 100000,
                              fields: Optional[list[str]] = None,
                              work_item_ids: Optional[list[int]] = None) -> str:
        """
        Builds read-only context from documentation fields on PR-linked work items.
        """
        ids = work_item_ids if work_item_ids is not None else (
            self.list_pull_request_work_item_ids(repository, pr_id)
        )
        if not ids:
            return ""

        selected_ids = ids[:max_items]
        omitted_items = max(0, len(ids) - len(selected_ids))
        requested_fields = self._resolve_work_item_fields(fields)
        work_items = self._get_work_items(selected_ids, requested_fields)

        if not work_items:
            return ""

        parts = [
            "### Linked work item documentation",
            "Use this work item context only to understand the product intent, "
            "requirements, acceptance criteria, and test notes behind the PR. "
            "Do not report issues from this context unless the issue is on a "
            "modified line in the PR diff.",
            "",
        ]

        used_chars = 0
        included_items = 0
        truncated = False

        for work_item in work_items:
            rendered = self._format_work_item_context(work_item, requested_fields)
            if not rendered:
                continue

            remaining = max_chars - used_chars
            if remaining <= 0:
                truncated = True
                break

            if len(rendered) > remaining:
                rendered = rendered[:remaining]
                truncated = True

            parts.append(rendered.rstrip())
            parts.append("")
            used_chars += len(rendered)
            included_items += 1

            if truncated:
                break

        if included_items == 0:
            return ""

        if truncated or omitted_items:
            parts.append(
                "[Work item context truncated: "
                f"included {included_items} item(s), omitted {omitted_items} item(s), "
                f"used {used_chars} of {max_chars} configured characters.]"
            )

        return "\n".join(parts).strip()

    def list_pull_request_work_item_ids(self, repository: str, pr_id: int) -> list[int]:
        """Lists numeric work item IDs associated with a pull request."""
        refs = self._list_pull_request_work_items(repository, pr_id)
        return [
            int(ref["id"])
            for ref in refs
            if str(ref.get("id", "")).isdigit()
        ]

    def _list_pull_request_work_items(self, repository: str, pr_id: int) -> list[dict]:
        """Lists work item references associated with a pull request."""
        path = f"git/repositories/{repository}/pullRequests/{pr_id}/workitems"
        data = self._get(path, api_version="7.1")
        return data.get("value", [])

    def _get_work_items(self, ids: list[int], fields: list[str]) -> list[dict]:
        """Fetches work item details, falling back if optional fields are unavailable."""
        params = {
            "ids": ",".join(str(item_id) for item_id in ids),
            "fields": ",".join(fields),
            "$expand": "relations",
            "errorPolicy": "Omit",
        }
        try:
            data = self._get("wit/workitems", params, api_version="7.1")
        except TFSError:
            required_fields = [
                "System.Title",
                "System.WorkItemType",
                "System.State",
                "System.Description",
            ]
            params["fields"] = ",".join(required_fields)
            data = self._get("wit/workitems", params, api_version="7.1")

        return data.get("value", [])

    def _resolve_work_item_fields(self, fields: Optional[list[str]]) -> list[str]:
        """Returns deduplicated work item fields, preserving required metadata."""
        requested = fields or DEFAULT_WORK_ITEM_CONTEXT_FIELDS
        required = [
            "System.Title",
            "System.WorkItemType",
            "System.State",
            "System.Description",
        ]
        resolved = []

        for field_name in [*required, *requested]:
            value = str(field_name).strip()
            if value and value not in resolved:
                resolved.append(value)

        return resolved

    def _format_work_item_context(self, work_item: dict, fields: list[str]) -> str:
        """Formats one work item as Markdown-ish read-only context."""
        values = work_item.get("fields", {})
        work_item_id = work_item.get("id", "")
        title = self._field_text(values.get("System.Title", "")).strip()
        header = f"#### Work Item {work_item_id}"
        if title:
            header += f": {title}"

        lines = [header]

        item_type = self._field_text(values.get("System.WorkItemType", "")).strip()
        state = self._field_text(values.get("System.State", "")).strip()
        if item_type:
            lines.append(f"- Type: {item_type}")
        if state:
            lines.append(f"- State: {state}")

        for field_name in fields:
            if field_name in ("System.Title", "System.WorkItemType", "System.State"):
                continue

            text = self._field_text(values.get(field_name, "")).strip()
            if not text:
                continue

            label = WORK_ITEM_CONTEXT_FIELD_LABELS.get(field_name, field_name)
            lines.extend(["", f"##### {label}", text])

        doc_links = self._extract_work_item_document_links(work_item)
        if doc_links:
            lines.extend(["", "##### Work Item Links"])
            for link in doc_links:
                lines.append(f"- {link}")

        return "\n".join(lines)

    def _extract_work_item_document_links(self, work_item: dict) -> list[str]:
        """Extracts hyperlink/attachment references from work item relations."""
        links = []
        for relation in work_item.get("relations", []) or []:
            rel = str(relation.get("rel", ""))
            if rel not in ("Hyperlink", "AttachedFile"):
                continue

            attributes = relation.get("attributes", {}) or {}
            label = (
                attributes.get("name")
                or attributes.get("comment")
                or attributes.get("resourceCreatedDate")
                or rel
            )
            url = relation.get("url", "")
            if url:
                links.append(f"{label}: {url}")

        return links

    def _field_text(self, value: object) -> str:
        """Converts Azure DevOps HTML-rich text fields to readable plain text."""
        if value is None:
            return ""

        text = str(value)
        text = re.sub(
            r"(?is)<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",
            lambda match: f"{match.group(2)} ({match.group(1)})",
            text,
        )
        text = re.sub(r"(?i)<br\s*/?>", "\n", text)
        text = re.sub(r"(?i)</(p|div|li|h[1-6])>", "\n", text)
        text = re.sub(r"(?i)<li[^>]*>", "- ", text)
        text = re.sub(r"<[^>]+>", "", text)
        text = html.unescape(text)
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    # ==================================================================
    # Pull Requests - Comments
    # ==================================================================
    def list_pull_request_comment_threads(self, repository: str, pr_id: int) -> list[dict]:
        """Lists all comment threads on a pull request."""
        path = f"git/repositories/{repository}/pullrequests/{pr_id}/threads"
        data = self._get(path)
        return data.get("value", [])

    def plan_review_comments(self, repository: str, pr_id: int,
                             comments: list[dict]) -> dict:
        """
        Compares new review comments with existing PR comments.

        Returns:
            Dict with new comments to post, duplicate comments to skip, and
            previously fixed/closed tool comments that should be reopened.
        """
        threads = self.list_pull_request_comment_threads(repository, pr_id)
        new_comments = []
        skipped_duplicates = []
        resolved_reappeared = []

        for comment in comments:
            match = self._find_matching_existing_comment(comment, threads)
            if not match:
                new_comments.append(comment)
                continue

            status_name = match["status_name"]
            if match["is_tool_comment"] and self._is_resolved_or_closed_status(status_name):
                resolved_reappeared.append({
                    "comment": comment,
                    "thread_id": match["thread_id"],
                    "status": status_name,
                    "fingerprint": self._comment_fingerprint(comment),
                })
            else:
                skipped_duplicates.append({
                    "comment": comment,
                    "thread_id": match["thread_id"],
                    "status": status_name,
                    "is_tool_comment": match["is_tool_comment"],
                })

        return {
            "new_comments": new_comments,
            "skipped_duplicates": skipped_duplicates,
            "resolved_reappeared": resolved_reappeared,
            "existing_threads": threads,
        }

    def post_general_comment(self, repository: str, pr_id: int,
                             comment: str, status: str = "active") -> dict:
        """
        Posts a general comment on a Pull Request (not associated with a file).
        
        Args:
            repository: Repository name.
            pr_id: Pull Request ID.
            comment: Comment text (supports Markdown).
            status: Thread status - "active", "fixed", "wontFix",
                    "closed", "pending", "byDesign"
            
        Returns:
            Created thread data.
        """
        path = f"git/repositories/{repository}/pullrequests/{pr_id}/threads"
        data = {
            "comments": [
                {
                    "parentCommentId": 0,
                    "content": comment,
                    "commentType": 1,  # text
                }
            ],
            "status": 1 if status == "active" else self._status_to_int(status),
        }
        return self._post(path, data)

    def post_inline_comment(self, repository: str, pr_id: int,
                            file_path: str, line: int, comment: str,
                            status: str = "active",
                            right_file: bool = True,
                            end_line: Optional[int] = None) -> dict:
        """
        Posts an inline comment on a specific line of a PR file.
        
        Args:
            repository: Repository name.
            pr_id: Pull Request ID.
            file_path: File path (e.g., "/src/auth.py").
            line: Line number where the comment will be posted.
            comment: Comment text (supports Markdown).
            status: Thread status.
            right_file: True for the new version file (right-side),
                       False for the old version file (left-side).
            
        Returns:
            Created thread data.
        """
        # Normalize path
        if not file_path.startswith("/"):
            file_path = f"/{file_path}"

        last_iteration = self._get_latest_iteration_id(repository, pr_id)
        change_tracking_id = self._get_change_tracking_id(
            repository,
            pr_id,
            last_iteration,
            file_path,
        )
        try:
            end_line = int(end_line or 0)
        except (TypeError, ValueError):
            end_line = 0
        if end_line <= line:
            end_line = line + 1

        path = f"git/repositories/{repository}/pullrequests/{pr_id}/threads"

        thread_context = {
            "filePath": file_path,
            "rightFileStart": {"line": line, "offset": 1} if right_file else None,
            "rightFileEnd": {"line": end_line, "offset": 1} if right_file else None,
            "leftFileStart": {"line": line, "offset": 1} if not right_file else None,
            "leftFileEnd": {"line": end_line, "offset": 1} if not right_file else None,
        }
        # Remover Nones
        thread_context = {k: v for k, v in thread_context.items() if v is not None}

        data = {
            "comments": [
                {
                    "parentCommentId": 0,
                    "content": comment,
                    "commentType": 1,
                }
            ],
            "status": 1 if status == "active" else self._status_to_int(status),
            "threadContext": thread_context,
            "pullRequestThreadContext": {
                "iterationContext": {
                    "firstComparingIteration": 1,
                    "secondComparingIteration": last_iteration,
                },
                "changeTrackingId": change_tracking_id,
            },
        }
        return self._post(path, data)

    def _get_latest_iteration_id(self, repository: str, pr_id: int) -> int:
        """Returns the latest PR iteration ID."""
        iterations_path = f"git/repositories/{repository}/pullrequests/{pr_id}/iterations"
        iterations = self._get(iterations_path)
        if not iterations.get("value"):
            raise TFSError(f"PR #{pr_id} has no iterations.")
        return int(iterations["value"][-1]["id"])

    def _get_change_tracking_id(
        self,
        repository: str,
        pr_id: int,
        iteration_id: int,
        file_path: str,
    ) -> int:
        """Returns the Azure DevOps changeTrackingId for a PR file."""
        changes_path = (
            f"git/repositories/{repository}/pullrequests/{pr_id}"
            f"/iterations/{iteration_id}/changes"
        )
        changes = self._get(changes_path, {"$top": 200})
        target_path = self._normalize_path(file_path)

        for change in changes.get("changeEntries", []) or []:
            item = change.get("item", {}) or {}
            candidate_paths = [
                item.get("path", ""),
                change.get("originalPath", ""),
            ]
            if any(self._normalize_path(path) == target_path for path in candidate_paths):
                change_tracking_id = change.get("changeTrackingId")
                if change_tracking_id is not None:
                    return int(change_tracking_id)

        raise TFSError(f"Could not find changeTrackingId for {file_path}.")

    def reply_to_thread(self, repository: str, pr_id: int,
                        thread_id: int, comment: str) -> dict:
        """
        Replies to an existing comment thread.
        
        Args:
            repository: Repository name.
            pr_id: Pull Request ID.
            thread_id: Thread ID to reply to.
            comment: Reply text.
            
        Returns:
            Created comment data.
        """
        path = (
            f"git/repositories/{repository}/pullrequests/{pr_id}"
            f"/threads/{thread_id}/comments"
        )
        data = {
            "parentCommentId": 1,  # Reply to the first comment
            "content": comment,
            "commentType": 1,
        }
        return self._post(path, data)

    def update_thread_status(self, repository: str, pr_id: int,
                             thread_id: int, status: str) -> dict:
        """
        Updates the status of a comment thread.
        
        Args:
            repository: Repository name.
            pr_id: Pull Request ID.
            thread_id: Thread ID.
            status: New status ("active", "fixed", "wontFix", "closed", "pending").
            
        Returns:
            Updated thread data.
        """
        path = (
            f"git/repositories/{repository}/pullrequests/{pr_id}"
            f"/threads/{thread_id}"
        )
        data = {"status": self._status_to_int(status)}
        return self._patch(path, data)

    def reopen_resolved_tool_comments(self, repository: str, pr_id: int,
                                      comments_to_reopen: list[dict]) -> list[dict]:
        """Reopens fixed/closed tool comments when the issue still appears."""
        results = []

        for item in comments_to_reopen:
            thread_id = item.get("thread_id")
            comment = item.get("comment", {})
            fingerprint = item.get("fingerprint") or self._comment_fingerprint(comment)
            body = "\n".join([
                "**AI Code Review re-check**",
                "",
                "This issue still appears in the latest review, so this previously "
                "resolved or closed thread is being reopened.",
                "",
                self._format_review_comment_body(comment),
            ])
            reply = self.tag_tool_comment(body, fingerprint, kind="recheck")

            try:
                self.update_thread_status(repository, pr_id, thread_id, "active")
                self.reply_to_thread(repository, pr_id, thread_id, reply)
                results.append({
                    "success": True,
                    "thread_id": thread_id,
                    "file": comment.get("file", ""),
                    "line": comment.get("line", 0),
                })
            except TFSError as exc:
                results.append({
                    "success": False,
                    "thread_id": thread_id,
                    "file": comment.get("file", ""),
                    "line": comment.get("line", 0),
                    "error": str(exc),
                })

        return results

    def post_review_comments(self, repository: str, pr_id: int,
                             comments: list[dict],
                             review_scope: str = "diff_with_context",
                             comment_mode: str = "structured") -> list[dict]:
        """
        Posts multiple review comments on a PR.
        Maps structured LLM comments to the Azure DevOps API.
        
        Args:
            repository: Repository name.
            pr_id: Pull Request ID.
            comments: List of structured LLM comments with keys:
                     file, line, type, severity, comment, suggestion
            
        Returns:
            List of results for each posted comment.
        """
        results = []
        review_scope = (review_scope or "diff_with_context").lower()
        comment_mode = (comment_mode or "structured").lower()
        use_inline_comments = comment_mode == "structured"

        for c in comments:
            # Build formatted comment text
            text = self._format_review_comment(c)

            file_path = c.get("file", "")
            line = c.get("line", 0)
            end_line = c.get("end_line", 0) or None

            try:
                if use_inline_comments and file_path and line > 0:
                    # Inline comment
                    result = self.post_inline_comment(
                        repository,
                        pr_id,
                        file_path,
                        line,
                        text,
                        end_line=end_line,
                    )
                else:
                    # No inline position: post as general PR comment
                    result = self.post_general_comment(
                        repository, pr_id, text
                    )
                results.append({
                    "success": True,
                    "file": file_path,
                    "line": line,
                    "thread_id": result.get("id"),
                })
            except TFSError as exc:
                results.append({
                    "success": False,
                    "file": file_path,
                    "line": line,
                    "error": str(exc),
                })

        return results

    def _format_review_comment(self, comment: dict) -> str:
        """Formats and tags a structured comment for Azure DevOps Markdown."""
        body = "\n\n".join([
            VISIBLE_TOOL_COMMENT_MARKER,
            self._format_review_comment_body(comment),
        ])
        return self.tag_tool_comment(
            body,
            self._comment_fingerprint(comment),
            kind="review-comment",
        )

    def _format_review_comment_body(self, comment: dict) -> str:
        """Formats the visible body of a structured review comment."""
        type_labels = {
            "bug": "Bug",
            "security": "Security",
            "performance": "Performance",
            "null_safety": "Bug",
            "data_integrity": "Bug",
            "api_contract": "Bug",
            "error_handling": "Bug",
            "resource": "Bug",
            "work_item": "Bug",
            "suggestion": "Suggestion",
        }

        comment_type = comment.get("type", "suggestion")
        label = type_labels.get(comment_type, comment_type.title())

        parts = [f"{label}: {comment.get('comment', '')}".strip()]

        suggestion = comment.get("suggestion", "")
        if suggestion:
            parts.append("")
            parts.append("**Suggested fix:**")
            suggestion_block = self._format_suggestion_block(comment)
            if suggestion_block:
                parts.append(suggestion_block)
            else:
                parts.append(suggestion)

                replacement = str(comment.get("suggestion_replacement", "")).strip()
                if replacement:
                    parts.append("")
                    parts.append("```")
                    parts.append(replacement)
                    parts.append("```")

        reference = comment.get("reference", "")
        if reference:
            parts.append("")
            parts.append(f"**Reference:** {reference}")

        return "\n".join(parts)

    def _format_suggestion_block(self, comment: dict) -> str:
        """Formats a TFS suggestion block when the replacement line count is valid."""
        if "suggestion_replacement" not in comment:
            return ""

        replacement = str(comment.get("suggestion_replacement", ""))
        if replacement == "":
            return ""
        try:
            line = int(comment.get("line", 0))
            end_line = int(comment.get("end_line", 0) or 0)
        except (TypeError, ValueError):
            return ""

        if line <= 0:
            return ""
        if end_line <= line:
            end_line = line + 1

        range_line_count = end_line - line
        replacement_line_count = len(replacement.splitlines())
        if replacement_line_count != range_line_count:
            return ""

        return "\n".join([
            "```suggestion",
            replacement,
            "```",
        ])

    def tag_tool_comment(self, text: str, fingerprint: str,
                         kind: str = "comment") -> str:
        """Adds stable metadata tags to comments created by this tool."""
        metadata = [
            TOOL_COMMENT_MARKER,
            f"{TOOL_COMMENT_KIND_PREFIX}{kind} -->",
            f"{TOOL_COMMENT_FINGERPRINT_PREFIX}{fingerprint} -->",
        ]
        body = text.strip()
        if "AI Code Review" not in body and VISIBLE_TOOL_COMMENT_MARKER not in body:
            body = (
                f"{body}\n\n"
                "---\n"
                "_Automated comment generated by AI Code Review CLI._"
            )
        return "\n".join([*metadata, body])

    def text_fingerprint(self, text: str) -> str:
        """Builds a stable fingerprint for arbitrary text."""
        normalized = self._normalize_for_match(text)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]

    def has_tool_comment_fingerprint(self, threads: list[dict],
                                     fingerprint: str) -> bool:
        """Returns whether any existing tool comment has a fingerprint."""
        needle = f"{TOOL_COMMENT_FINGERPRINT_PREFIX}{fingerprint}"
        for thread in threads or []:
            for comment in thread.get("comments", []) or []:
                if needle in str(comment.get("content", "")):
                    return True
        return False

    def _comment_fingerprint(self, comment: dict) -> str:
        """Builds a stable fingerprint for a structured review comment."""
        values = [
            self._normalize_path(comment.get("file", "")),
            str(comment.get("line", 0)),
            str(comment.get("type", "")),
            str(comment.get("severity", "")),
            str(comment.get("comment", "")),
            str(comment.get("suggestion", "")),
            str(comment.get("reference", "")),
        ]
        return self.text_fingerprint("|".join(values))

    def _find_matching_existing_comment(self, comment: dict,
                                        threads: list[dict]) -> Optional[dict]:
        """Finds an existing PR comment matching the generated comment."""
        fingerprint = self._comment_fingerprint(comment)
        fingerprint_needle = f"{TOOL_COMMENT_FINGERPRINT_PREFIX}{fingerprint}"

        for thread in threads or []:
            for existing_comment in thread.get("comments", []) or []:
                body = str(existing_comment.get("content", ""))
                if fingerprint_needle in body:
                    return self._build_existing_comment_match(thread, body)

        current_text = self._normalize_for_match(comment.get("comment", ""))
        if not current_text:
            return None

        for thread in threads or []:
            if not self._thread_matches_comment_location(thread, comment):
                continue

            for existing_comment in thread.get("comments", []) or []:
                raw_body = str(existing_comment.get("content", ""))
                if VISIBLE_TOOL_COMMENT_MARKER in raw_body:
                    return self._build_existing_comment_match(thread, raw_body)

                body = self._normalize_for_match(
                    self._strip_tool_metadata(raw_body)
                )
                if current_text in body:
                    return self._build_existing_comment_match(
                        thread,
                        raw_body,
                    )

        return None

    def _build_existing_comment_match(self, thread: dict, body: str) -> dict:
        """Builds duplicate match metadata from an Azure DevOps thread."""
        return {
            "thread_id": thread.get("id"),
            "status_name": self._thread_status_name(thread.get("status")),
            "is_tool_comment": (
                TOOL_COMMENT_MARKER in body
                or VISIBLE_TOOL_COMMENT_MARKER in body
            ),
        }

    def _thread_matches_comment_location(self, thread: dict, comment: dict) -> bool:
        """Checks whether a thread points to the same file/line as a comment."""
        comment_file = self._normalize_path(comment.get("file", ""))
        comment_line = self._to_int(comment.get("line", 0))
        thread_file, thread_line = self._thread_file_line(thread)

        if comment_file and thread_file and comment_file != thread_file:
            return False
        if comment_line > 0 and thread_line > 0 and comment_line != thread_line:
            return False
        return True

    def _thread_file_line(self, thread: dict) -> tuple[str, int]:
        """Returns the normalized file and line for a PR thread."""
        context = thread.get("threadContext", {}) or {}
        file_path = self._normalize_path(context.get("filePath", ""))
        line = 0
        for key in ("rightFileStart", "leftFileStart"):
            value = context.get(key) or {}
            line = self._to_int(value.get("line", 0))
            if line > 0:
                break
        return file_path, line

    def _thread_status_name(self, status: object) -> str:
        """Normalizes Azure DevOps thread statuses to lower-case names."""
        if isinstance(status, int):
            return THREAD_STATUS_NAMES.get(status, "unknown")
        if str(status).isdigit():
            return THREAD_STATUS_NAMES.get(int(str(status)), "unknown")
        return str(status or "unknown").replace(" ", "").lower()

    def _is_resolved_or_closed_status(self, status_name: str) -> bool:
        """Returns whether a thread status means resolved/closed."""
        return status_name in ("fixed", "closed", "resolved")

    def _strip_tool_metadata(self, text: object) -> str:
        """Removes hidden tool metadata tags before text comparisons."""
        value = str(text or "")
        value = re.sub(r"<!--\s*ai-code-review-[^>]*-->\s*", "", value)
        value = value.replace(VISIBLE_TOOL_COMMENT_MARKER, "")
        return value

    def _normalize_for_match(self, value: object) -> str:
        """Normalizes text for duplicate matching."""
        text = self._field_text(value)
        text = self._strip_tool_metadata(text)
        return re.sub(r"\s+", " ", text).strip().lower()

    def _normalize_path(self, path: object) -> str:
        """Normalizes repository paths for duplicate matching."""
        return str(path or "").replace("\\", "/").lstrip("/").lower()

    def _to_int(self, value: object) -> int:
        """Converts values to int with a safe fallback."""
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    def _status_to_int(self, status: str) -> int:
        """Converts status string to API integer."""
        status_map = {
            "active": 1,
            "fixed": 2,
            "wontfix": 3,
            "closed": 4,
            "bydesign": 5,
            "pending": 6,
        }
        return status_map.get(status.lower(), 1)

    # ==================================================================
    # Pull Requests - Compatibility (legacy method)
    # ==================================================================
    def add_pr_comment(self, repository: str, pr_id: int,
                       comment: str, status: str = "active") -> dict:
        """Legacy alias for post_general_comment."""
        return self.post_general_comment(repository, pr_id, comment, status)

    # ==================================================================
    # Repositories
    # ==================================================================
    def list_repositories(self) -> list[dict]:
        """Lists project repositories."""
        data = self._get("git/repositories")
        repos = []
        for repo in data.get("value", []):
            repos.append({
                "id": repo["id"],
                "name": repo["name"],
                "url": repo.get("remoteUrl", ""),
                "default_branch": repo.get("defaultBranch", "").replace(
                    "refs/heads/", ""
                ),
            })
        return repos

    def get_repository_id(self, repo_name: str) -> str:
        """Gets a repository ID by name."""
        repos = self.list_repositories()
        for repo in repos:
            if repo["name"].lower() == repo_name.lower():
                return repo["id"]
        raise TFSError(f"Repository '{repo_name}' not found in project.")
