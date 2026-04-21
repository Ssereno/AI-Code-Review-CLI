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
import os
from typing import Optional

from .config import ReviewConfig


class TFSError(Exception):
    """Exception for TFS/Azure DevOps communication errors."""
    pass


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
                              review_scope: str = "diff_only") -> str:
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

        review_scope = (review_scope or "diff_only").lower()

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
    # Pull Requests - Comments
    # ==================================================================
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
                            right_file: bool = True) -> dict:
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

        # Get most recent iteration
        iterations_path = f"git/repositories/{repository}/pullrequests/{pr_id}/iterations"
        iterations = self._get(iterations_path)
        if not iterations.get("value"):
            raise TFSError(f"PR #{pr_id} has no iterations.")
        last_iteration = iterations["value"][-1]["id"]

        path = f"git/repositories/{repository}/pullrequests/{pr_id}/threads"

        thread_context = {
            "filePath": file_path,
            "rightFileStart": {"line": line, "offset": 1} if right_file else None,
            "rightFileEnd": {"line": line, "offset": 1} if right_file else None,
            "leftFileStart": {"line": line, "offset": 1} if not right_file else None,
            "leftFileEnd": {"line": line, "offset": 1} if not right_file else None,
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
                "changeTrackingId": 0,
            },
        }
        return self._post(path, data)

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

    def post_review_comments(self, repository: str, pr_id: int,
                             comments: list[dict],
                             review_scope: str = "diff_only",
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
        review_scope = (review_scope or "diff_only").lower()
        comment_mode = (comment_mode or "structured").lower()
        use_inline_comments = comment_mode == "structured"

        for c in comments:
            # Build formatted comment text
            text = self._format_review_comment(c)

            file_path = c.get("file", "")
            line = c.get("line", 0)
            comment_type = str(c.get("type", "")).lower()
            is_problem = comment_type not in ("praise", "")

            try:
                if use_inline_comments and file_path and line > 0:
                    # Inline comment
                    result = self.post_inline_comment(
                        repository, pr_id, file_path, line, text
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
        """Formats a structured comment for Azure DevOps Markdown."""
        type_labels = {
            "bug": "Bug",
            "security": "Security",
            "performance": "Performance",
            "style": "Code Style",
            "suggestion": "Suggestion",
            "praise": "Positive",
        }

        severity = comment.get("severity", "info")
        comment_type = comment.get("type", "suggestion")
        label = type_labels.get(comment_type, comment_type.title())

        parts = [f"**{label}** ({severity.upper()})"]
        parts.append("")
        parts.append(comment.get("comment", ""))

        suggestion = comment.get("suggestion", "")
        if suggestion:
            parts.append("")
            parts.append(f"**Suggestion:** {suggestion}")

        reference = comment.get("reference", "")
        if reference:
            parts.append("")
            parts.append(f"**Reference:** {reference}")

        return "\n".join(parts)

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
