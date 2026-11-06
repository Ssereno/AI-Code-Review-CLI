# License: Apache-2.0
# Copyright 2026 Sérgio Sereno

"""
Git Utilities Module - AI Code Review
========================================
Provides diff filtering, splitting, truncation and file-summary helpers
used by the PR review pipeline.

Works with any Git repository, including TFS/Azure DevOps.
"""

import os
import re
import subprocess
from typing import Optional

from .context_extractor import (
    ContextExtractor,
    XML_CONTEXT_END_TAGS,
    XML_CONTEXT_START_TAGS,
    format_full_code_diff,
    format_unified_diff_with_context,
    normalize_filter_rules,
    should_include_file,
)

class GitError(Exception):
    """Exception for Git-related errors."""
    pass


class GitUtils:
    """Utility class for Git operations."""

    def __init__(self, repo_path: Optional[str] = None, validate: bool = True):
        """
        Initializes the Git utility.

        Args:
            repo_path: Path to the repository. If None, uses the current directory.
            validate: If True, verifies that *repo_path* is inside a Git repository.
                Set to False when only pure diff-manipulation helpers are needed.
        """
        self.repo_path = repo_path or os.getcwd()
        if validate:
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
            - Lines inside XML context blocks (``<enclosing_scopes>``, ``<adaptive_diff>``, or ``<referenced_symbols>``)

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

        for line in diff.split("\n"):
            if any(line.startswith(tag) for tag in XML_CONTEXT_START_TAGS):
                in_xml_block = True
                result.append(line)
                continue

            if any(line.startswith(tag) for tag in XML_CONTEXT_END_TAGS):
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

    @staticmethod
    def _extract_bundle_keys(file_path: str) -> tuple[str, str]:
        """Extracts ``(module_key, stem_key)`` for Smart File Bundling.

        * ``stem_key`` groups tightly coupled files across directories — such as
          C#/TS interfaces and implementations (``IUserService.cs`` +
          ``UserService.cs``), unit tests (``UserServiceTests.cs``), multi-part
          components (``login.component.ts`` + ``login.component.html``), and
          localization pairs (``message_en.properties`` + ``message_zh.properties``).
        * ``module_key`` groups files belonging to the same domain/directory
          (stripping top-level roots like ``src/`` or ``tests/``).
        """
        norm = file_path.replace("\\", "/").lstrip("/")
        parts = [p for p in norm.split("/") if p]
        basename = parts[-1] if parts else norm
        dir_parts = parts[:-1]

        # Strip common structural top-level roots so src/Auth and tests/Auth match
        _root_Containers = {
            "src", "lib", "app", "source", "sources",
            "test", "tests", "spec", "specs", "__tests__",
            "interfaces", "services", "controllers", "models",
        }
        filtered_dirs = [
            d.lower() for d in dir_parts if d.lower() not in _root_Containers
        ]
        module_key = "/".join(filtered_dirs) if filtered_dirs else (
            dir_parts[-1].lower() if dir_parts else ""
        )

        # Stem extraction: take segment before the first dot (unless dotfile)
        if basename.startswith("."):
            raw_stem = os.path.splitext(basename)[0]
        else:
            raw_stem = basename.split(".")[0]

        # Strip C#/TS interface prefix 'I' followed by an uppercase letter (e.g. IUserService -> UserService)
        if len(raw_stem) >= 3 and raw_stem[0] == "I" and raw_stem[1].isupper() and (
            raw_stem[2].islower() or raw_stem[2].isupper()
        ):
            candidate = raw_stem[1:]
            if any(ch.islower() for ch in candidate):
                raw_stem = candidate

        # Strip localization suffixes (e.g. message_en, message_zh_CN, strings-pt-BR)
        raw_stem = re.sub(
            r"[_-][a-z]{2}(?:[_-][a-zA-Z]{2,4})?$",
            "",
            raw_stem,
        )

        # Strip common test/companion suffixes iteratively
        _suffix_re = re.compile(
            r"(?:[_-]?(?:tests|test|specs|spec|impl|mock|mocks|stub|stubs))$",
            re.IGNORECASE,
        )
        while True:
            cleaned = _suffix_re.sub("", raw_stem)
            if cleaned and cleaned != raw_stem:
                raw_stem = cleaned
            else:
                break

        stem_key = raw_stem.lower() or basename.lower()
        return module_key, stem_key

    def bundle_diff_files(self, diff: str, max_files_per_bundle: int = 5) -> list[dict]:
        """Groups changed files in *diff* into cohesive review bundles (Smart File Bundling).

        Inspired by Alibaba's ``open-code-review`` deterministic bundling pipeline:
        1. Tightly coupled files sharing the same normalized ``stem_key`` (e.g.,
           ``IUserService.cs``, ``UserService.cs``, ``UserServiceTests.cs``, or
           ``message_en.properties`` + ``message_zh.properties``) are grouped
           first into atomic stem units.
        2. Stem units belonging to the same ``module_key`` (domain/directory) are
           packed together into module chunks up to *max_files_per_bundle*.
        3. Module chunks are packed into bounded bundles of at most
           *max_files_per_bundle* files without splitting cohesive units unless a
           single unit exceeds *max_files_per_bundle*.

        Args:
            diff: Unified diff string (with ``diff --git`` file sections).
            max_files_per_bundle: Maximum number of files per bundle (must be >= 1).

        Returns:
            List of bundle dicts, each containing:
            - ``"diff"``: Combined diff string for the files in that bundle.
            - ``"files_summary"``: List of ``{"file", "additions", "deletions"}``
              dicts for the files in that bundle.
        """
        if not diff or not diff.strip():
            return []

        max_files = max(1, int(max_files_per_bundle))
        sections, has_file_sections = self._split_diff_sections(diff)
        if not has_file_sections:
            return [{
                "diff": diff,
                "files_summary": self.get_changed_files_summary(diff),
            }]

        items: list[dict] = []
        for section in sections:
            section_text = "\n".join(section)
            summary_list = self.get_changed_files_summary(section_text)
            summary = summary_list[0] if summary_list else {
                "file": "unknown",
                "additions": 0,
                "deletions": 0,
            }
            file_path = summary["file"]
            module_key, stem_key = self._extract_bundle_keys(file_path)
            items.append({
                "section_text": section_text,
                "summary": summary,
                "file_path": file_path,
                "module_key": module_key,
                "stem_key": stem_key,
            })

        if len(items) <= max_files:
            return [{
                "diff": diff,
                "files_summary": [item["summary"] for item in items],
            }]

        # Step 1: Group by stem_key to keep interface + implementation + test + i18n together
        stem_groups: dict[str, list[dict]] = {}
        stem_order: list[str] = []
        for item in items:
            s_key = item["stem_key"]
            if s_key not in stem_groups:
                stem_groups[s_key] = []
                stem_order.append(s_key)
            stem_groups[s_key].append(item)

        # Split any oversized stem group into chunks of max_files and assign primary module_key
        stem_units: list[tuple[str, list[dict]]] = []
        for s_key in stem_order:
            group = stem_groups[s_key]
            primary_module = group[0]["module_key"]
            for idx in range(0, len(group), max_files):
                stem_units.append((primary_module, group[idx:idx + max_files]))

        # Step 2: Group stem_units by module_key and pack within each module
        module_buckets: dict[str, list[list[dict]]] = {}
        module_order: list[str] = []
        for mod_key, unit in stem_units:
            if mod_key not in module_buckets:
                module_buckets[mod_key] = []
                module_order.append(mod_key)
            module_buckets[mod_key].append(unit)

        module_chunks: list[list[dict]] = []
        for mod_key in module_order:
            current_chunk: list[dict] = []
            for unit in module_buckets[mod_key]:
                if current_chunk and len(current_chunk) + len(unit) > max_files:
                    module_chunks.append(current_chunk)
                    current_chunk = list(unit)
                else:
                    current_chunk.extend(unit)
            if current_chunk:
                module_chunks.append(current_chunk)

        # Step 3: Pack module_chunks into final bundles without breaking module chunks
        final_bundles_items: list[list[dict]] = []
        current_bundle: list[dict] = []
        for chunk in module_chunks:
            if current_bundle and len(current_bundle) + len(chunk) > max_files:
                final_bundles_items.append(current_bundle)
                current_bundle = list(chunk)
            else:
                current_bundle.extend(chunk)
        if current_bundle:
            final_bundles_items.append(current_bundle)

        return [
            {
                "diff": "\n".join(it["section_text"] for it in bundle_items),
                "files_summary": [it["summary"] for it in bundle_items],
            }
            for bundle_items in final_bundles_items
        ]

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
                    if line.startswith(XML_CONTEXT_START_TAGS)
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
        _excl_prefixes, _ext_filter = normalize_filter_rules(
            excluded_paths, file_extensions_filter
        )

        diff_parts: list[str] = []
        built_entries: list[tuple[str, list[str]]] = []
        file_records: dict[str, tuple[str, list[int]]] = {}

        for change in changes:
            change_type = change.get("change_type", "edit")
            if change_type == "delete":
                continue

            file_path = change.get("path") or "unknown"
            original_path = change.get("original_path") or file_path

            if not should_include_file(file_path, _excl_prefixes, _ext_filter):
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

            part_lines = self._build_local_unified_diff_part(
                file_path=file_path,
                original_path=original_path,
                change_type=change_type,
                old_source=old_source,
                new_source=new_source,
                extractor=extractor,
                file_records=file_records,
            )
            built_entries.append((file_path, part_lines))

        if review_scope != "full_code" and built_entries:
            candidate_files: dict[str, str] = {
                fp: txt for fp, (txt, _) in file_records.items() if txt
            }
            if extractor.max_referenced_symbols > 0 and file_records:
                self._populate_local_imported_candidates(
                    file_records=file_records,
                    candidate_files=candidate_files,
                    new_source=new_source,
                    extractor=extractor,
                )

            for entry_path, entry_lines in built_entries:
                if (
                    extractor.max_referenced_symbols > 0
                    and entry_path in file_records
                ):
                    new_txt, ch_lines = file_records[entry_path]
                    ref_xml = extractor.extract_referenced_symbols(
                        file_path=entry_path,
                        new_content=new_txt,
                        changed_lines=ch_lines,
                        candidate_files=candidate_files,
                    )
                    if ref_xml:
                        entry_lines.append(ref_xml)
                diff_parts.extend(entry_lines)
                diff_parts.append("")

        if not diff_parts:
            return ""

        return "\n".join(diff_parts)

    def _populate_local_imported_candidates(
        self,
        file_records: dict[str, tuple[str, list[int]]],
        candidate_files: dict[str, str],
        new_source: str,
        extractor: ContextExtractor,
        max_extra_files: int = 10,
    ) -> None:
        """Load unchanged local relative/sibling imported files into *candidate_files*."""
        known_norms = {
            p.replace("\\", "/").lstrip("/").lower() for p in candidate_files
        }
        repo_root = self.get_repo_root() if new_source == "WORKTREE" else ""
        added = 0

        for fp, (content, ch_lines) in file_records.items():
            if added >= max_extra_files:
                break
            import_paths = list(extractor.resolve_relative_imports(fp, content))
            if new_source == "WORKTREE":
                import_paths.extend(
                    extractor.resolve_sibling_type_files(fp, content, ch_lines)
                )

            for imp_path in import_paths:
                if added >= max_extra_files:
                    break
                norm_imp = imp_path.replace("\\", "/").lstrip("/").lower()
                if norm_imp in known_norms:
                    continue

                if new_source == "WORKTREE":
                    disk_path = os.path.join(
                        repo_root, imp_path.replace("\\", "/").lstrip("/")
                    )
                    if not os.path.isfile(disk_path):
                        continue

                try:
                    imp_content = self._get_local_file_content(imp_path, new_source)
                except Exception:
                    continue

                if imp_content:
                    candidate_files[imp_path] = imp_content
                    known_norms.add(norm_imp)
                    added += 1

    def _build_local_full_code_diff_part(
        self,
        file_path: str,
        change_type: str,
        new_source: str,
    ) -> list[str]:
        """Builds a ``full_code`` payload for a single locally changed file."""
        content: Optional[str] = None
        unavailable_error = False

        if change_type in ("edit", "add", "rename"):
            try:
                content = self._get_local_file_content(file_path, new_source)
            except Exception:
                unavailable_error = True

        return format_full_code_diff(
            file_path=file_path,
            change_type=change_type,
            content=content,
            unavailable_error=unavailable_error,
        )

    def _build_local_unified_diff_part(
        self,
        file_path: str,
        original_path: str,
        change_type: str,
        old_source: str,
        new_source: str,
        extractor: ContextExtractor,
        file_records: Optional[dict[str, tuple[str, list[int]]]] = None,
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

        return format_unified_diff_with_context(
            file_path=file_path,
            original_path=original_path,
            old_lines=old_lines,
            new_lines=new_lines,
            extractor=extractor,
            file_records=file_records,
        )
