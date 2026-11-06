# License: Apache-2.0
# Copyright 2026 Sérgio Sereno

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
import hashlib
import os
import re
from typing import Optional

from .config import ReviewConfig
from .context_extractor import (
    ContextExtractor,
    assemble_diff_with_referenced_symbols,
    format_unified_diff_with_context,
    normalize_filter_rules,
    should_include_file,
)


class TFSError(Exception):
    """Exception for TFS/Azure DevOps communication errors."""
    pass


class TFSClient:
    """Client for Azure DevOps / TFS REST API."""

    API_VERSION = "7.0"

    # Hidden fingerprint marker embedded in AI-generated comments to detect
    # and skip duplicates on pipeline re-runs. Rendered as an HTML comment,
    # which Azure DevOps does not display in the PR UI.
    MARKER_PREFIX = "ai-review:"
    _MARKER_RE = re.compile(r"<!--\s*ai-review:([0-9a-zA-Z_]+)\s*-->")

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
        self._active_pr_files: Optional[dict[str, tuple[str, list[int]]]] = None
        self._context_extractor = ContextExtractor.from_config(config)

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

        params: dict[str, int | str] = {"$top": top}

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
            "labels": [lbl.get("name", "") for lbl in pr.get("labels", [])],  # noqa: E741
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
            # `item` can be null for deleted files; use `or {}` as a safe fallback.
            item = change.get("item") or {}
            if item.get("isFolder"):
                continue
            # `path` can be null for deleted files; fall back to `originalPath`.
            path = item.get("path") or change.get("originalPath") or ""
            files.append({
                "path": path,
                "change_type": change.get("changeType", "unknown"),
                "original_path": change.get("originalPath") or path,
            })
        return files

    def get_pull_request_diff(self, repository: str, pr_id: int,
                              excluded_paths: list[str] | None = None,
                              file_extensions_filter: list[str] | None = None) -> str:
        """
        Gets the diff of a specific Pull Request.

        Builds a standard unified diff for each changed file and appends an XML
        context block produced by :class:`~src.context_extractor.ContextExtractor`
        (``<enclosing_scopes>``, ``<adaptive_diff>``, and/or
        ``<referenced_symbols>``). The block is preserved verbatim by
        :py:meth:`GitUtils.filter_diff_additions_only` so the LLM receives
        structured scope context without consuming the entire file.

        Files are filtered **before** any HTTP content request is made, so
        excluded or extension-mismatched files incur no network cost.

        Args:
            repository: Repository name.
            pr_id: Pull Request ID.
            excluded_paths: List of path prefixes to skip (case-insensitive,
                leading slash optional). Files whose path starts with any
                prefix are excluded before content is fetched.
                Example: ``["/Libs/", "/IoT/_input/packages"]``.
            file_extensions_filter: Allowlist of file extensions
                (case-insensitive). When non-empty, only files whose path ends
                with one of the listed extensions are processed.
                Example: ``[".cs", ".ts"]``.

        Returns:
            Diff as text, ready to be passed to the LLM client.

        Raises:
            TFSError: If the PR has no iterations.
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

        _excl_prefixes, _ext_filter = normalize_filter_rules(
            excluded_paths, file_extensions_filter
        )

        # Build diff from the changes
        built_entries: list[tuple[str, list[str]]] = []
        pr_file_records: dict[str, tuple[str, list[int]]] = {}
        self._active_pr_files = pr_file_records

        try:
            for change in changes.get("changeEntries", []):
                # `item` can be null for deleted files; use `or {}` as a safe fallback.
                item = change.get("item") or {}
                change_type = change.get("changeType", "unknown")

                if item.get("isFolder"):
                    continue

                # Deleted files are skipped from the LLM diff.
                # They are still reported in the terminal summary via _get_pr_changed_files.
                if change_type == "delete":
                    continue

                # `path` can be null; fall back to `originalPath` (safe for add/edit/rename).
                file_path = item.get("path") or change.get("originalPath") or "unknown"
                original_path = change.get("originalPath") or file_path

                # --- Early-exit filters (no HTTP request made for skipped files) ---
                if not should_include_file(file_path, _excl_prefixes, _ext_filter):
                    continue
                # ------------------------------------------------------------------

                part_lines = list(
                    self._build_unified_diff_part(
                        repository=repository,
                        file_path=file_path,
                        original_path=original_path,
                        change_type=change_type,
                        source_branch=source_branch,
                        target_branch=target_branch,
                    )
                )
                built_entries.append((file_path, part_lines))
        finally:
            self._active_pr_files = None

        if not built_entries:
            return ""

        candidate_files: dict[str, str] = {
            fp: txt for fp, (txt, _) in pr_file_records.items() if txt
        }
        if (
            self._context_extractor.max_referenced_symbols > 0
            and pr_file_records
        ):
            self._populate_remote_imported_candidates(
                repository=repository,
                source_branch=source_branch,
                pr_file_records=pr_file_records,
                candidate_files=candidate_files,
            )

        return assemble_diff_with_referenced_symbols(
            built_entries=built_entries,
            file_records=pr_file_records,
            candidate_files=candidate_files,
            extractor=self._context_extractor,
        )

    def _populate_remote_imported_candidates(
        self,
        repository: str,
        source_branch: str,
        pr_file_records: dict[str, tuple[str, list[int]]],
        candidate_files: dict[str, str],
        max_fetches: int = 3,
    ) -> None:
        """Fetch up to *max_fetches* unchanged relative-imported files from TFS."""
        source_ref = source_branch.replace("refs/heads/", "")
        known_norms = {
            p.replace("\\", "/").lstrip("/").lower() for p in candidate_files
        }
        fetched = 0

        for fp, (content, _ch) in pr_file_records.items():
            if fetched >= max_fetches:
                break
            src_ext = os.path.splitext(fp)[1].lower()
            for imp_path in self._context_extractor.resolve_relative_imports(fp, content):
                if fetched >= max_fetches:
                    break
                if imp_path.endswith("/__init__.py") or imp_path.endswith("/index.ts") or imp_path.endswith("/index.js"):
                    continue
                if os.path.splitext(imp_path)[1].lower() != src_ext:
                    continue
                norm_imp = imp_path.replace("\\", "/").lstrip("/").lower()
                if norm_imp in known_norms:
                    continue
                known_norms.add(norm_imp)
                try:
                    imp_content = self._get_file_content(
                        repository,
                        imp_path,
                        version=source_ref,
                        version_type="branch",
                    )
                except Exception:
                    continue
                if imp_content:
                    candidate_files[imp_path] = imp_content
                    fetched += 1

    def _build_unified_diff_part(self, repository: str, file_path: str,
                                 original_path: str, change_type: str,
                                 source_branch: str, target_branch: str) -> list[str]:
        """Builds a unified diff for a single file.

        Fetches the old version from the target branch and the new version
        from the source branch, then generates a standard unified diff with
        5 lines of context using :py:mod:`difflib`.

        After the diff lines an XML context block is appended, produced by
        :class:`~src.context_extractor.ContextExtractor`:

        * ``<enclosing_scopes>`` — the AST scope(s) enclosing the changed
          lines when they fit the configured block and line limits
        * ``<adaptive_diff>`` — surrounding context lines when AST extraction
          is unavailable, unsupported, fails, or exceeds those limits

        The XML block is preserved verbatim by
        :py:meth:`GitUtils.filter_diff_additions_only`.

        Args:
            repository: Repository name.
            file_path: Server path of the new version of the file.
            original_path: Server path of the old version (differs on renames).
            change_type: TFS change type string (``"edit"``, ``"add"``,
                ``"rename"``). ``"delete"`` entries are filtered out
                by the caller before this method is invoked.
            source_branch: Source branch ref (feature branch — new version).
            target_branch: Target branch ref (base branch — old version).

        Returns:
            List of diff strings for this file, including the XML context block.
        """
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

        return format_unified_diff_with_context(
            file_path=file_path,
            original_path=original_path,
            old_lines=old_lines,
            new_lines=new_lines,
            extractor=self._context_extractor,
            file_records=self._active_pr_files,
        )

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
                             comment: str, status: str = "active",
                             dedup_marker: Optional[str] = None) -> dict:
        """
        Posts a general comment on a Pull Request (not associated with a file).

        Args:
            repository: Repository name.
            pr_id: Pull Request ID.
            comment: Comment text (supports Markdown).
            status: Thread status - "active", "fixed", "wontFix",
                    "closed", "pending", "byDesign"
            dedup_marker: Optional stable identifier. When provided, a hidden
                          fingerprint is appended to the comment and existing PR
                          threads are checked first; if the same marker already
                          exists, the comment is skipped instead of duplicated.

        Returns:
            Created thread data, or a ``{"skipped": True, ...}`` dict when the
            comment was skipped due to an already existing marker.
        """
        marker: Optional[str] = None
        if dedup_marker is not None:
            marker = self._general_marker(dedup_marker)
            existing = self._extract_existing_markers(
                self.list_pr_threads(repository, pr_id)
            )
            if marker in existing:
                return {"skipped": True, "marker": marker}
            comment = f"{comment}\n\n{self._marker_html(marker)}"

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

    def post_review_comments(self, repository: str, pr_id: int,
                             comments: list[dict],
                             comment_mode: str = "structured") -> list[dict]:
        """
        Posts multiple review comments on a PR.
        Maps structured LLM comments to the Azure DevOps API.
        
        Args:
            repository: Repository name.
            pr_id: Pull Request ID.
            comments: List of structured LLM comments with keys:
                     file, line, type, comment, suggestion
            
        Returns:
            List of results for each posted comment.
        """
        results = []
        comment_mode = (comment_mode or "structured").lower()
        use_inline_comments = comment_mode == "structured"

        # Load existing markers once to skip comments already posted in a
        # previous run (deduplication is always active).
        existing_markers = self._extract_existing_markers(
            self.list_pr_threads(repository, pr_id)
        )

        for c in comments:
            file_path = c.get("file", "")
            line = c.get("line", 0)

            marker = self._comment_marker(c)
            if marker in existing_markers:
                results.append({
                    "skipped": True,
                    "file": file_path,
                    "line": line,
                    "error": "duplicate (already posted)",
                })
                continue

            # Build formatted comment text with the hidden fingerprint appended.
            text = f"{self._format_review_comment(c)}\n\n{self._marker_html(marker)}"

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
                # Record the marker to avoid duplicates within the same batch.
                existing_markers.add(marker)
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

        comment_type = comment.get("type", "suggestion")
        label = type_labels.get(comment_type, comment_type.title())

        parts = [f"**{label}**"]
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

    # ==================================================================
    # Deduplication - hidden fingerprint markers
    # ==================================================================
    def list_pr_threads(self, repository: str, pr_id: int) -> list[dict]:
        """
        Lists all comment threads of a Pull Request.

        Args:
            repository: Repository name.
            pr_id: Pull Request ID.

        Returns:
            List of thread dictionaries (empty list when none exist).
        """
        path = f"git/repositories/{repository}/pullrequests/{pr_id}/threads"
        response = self._get(path)
        return response.get("value", [])

    def _comment_marker(self, comment: dict) -> str:
        """
        Builds a stable fingerprint for a structured review comment.

        The hash is derived from the file, line and type so the same finding
        location produces the same marker across runs, regardless of small
        wording changes in the comment text.

        Args:
            comment: Structured comment with ``file``, ``line`` and ``type`` keys.

        Returns:
            Hexadecimal SHA-256 digest string.
        """
        payload = "|".join([
            str(comment.get("file", "")),
            str(comment.get("line", "")),
            str(comment.get("type", "")),
        ])
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _general_marker(self, identifier: str) -> str:
        """
        Builds a stable marker for a general comment from a fixed identifier.

        Args:
            identifier: Stable identifier (e.g. ``"summary"``).

        Returns:
            Sanitized marker string usable inside the HTML comment.
        """
        return re.sub(r"[^0-9a-zA-Z_]", "_", identifier)

    def _marker_html(self, marker: str) -> str:
        """
        Wraps a marker into a hidden HTML comment.

        Args:
            marker: Fingerprint value.

        Returns:
            HTML comment string invisible in the rendered PR.
        """
        return f"<!-- {self.MARKER_PREFIX}{marker} -->"

    def _extract_existing_markers(self, threads: list[dict]) -> set[str]:
        """
        Extracts all fingerprint markers present in existing PR threads.

        Args:
            threads: Threads as returned by :meth:`list_pr_threads`.

        Returns:
            Set of marker values found across every comment.
        """
        markers: set[str] = set()
        for thread in threads:
            for comment in thread.get("comments", []) or []:
                content = comment.get("content", "") or ""
                markers.update(self._MARKER_RE.findall(content))
        return markers

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
