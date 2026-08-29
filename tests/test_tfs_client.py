# License: Apache-2.0
# Copyright 2026 Sérgio Sereno

"""Tests for the TFS/Azure DevOps client."""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from src.config import ReviewConfig
from src.tfs_client import TFSClient, TFSError


class FakeResponse:
    """Minimal HTTP response stub for client tests."""

    def __init__(self, *, json_data: object | None = None, text: str = "", status_code: int = 200, exc: Exception | None = None) -> None:
        self._json_data = json_data if json_data is not None else {}
        self.text = text
        self.status_code = status_code
        self._exc = exc
        self.headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        """Raise the configured exception when requested."""
        if self._exc is not None:
            raise self._exc

    def json(self) -> object:
        """Return the configured JSON payload."""
        return self._json_data


class FakeSession:
    """Minimal requests.Session replacement."""

    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.verify: object = True
        self.get_response = FakeResponse()
        self.post_response = FakeResponse()
        self.patch_response = FakeResponse()
        self.calls: list[tuple[str, str, object, int]] = []

    def get(self, url: str, params: object = None, timeout: int = 30) -> FakeResponse:
        """Record GET calls and return the configured response."""
        self.calls.append(("GET", url, params, timeout))
        return self.get_response

    def post(self, url: str, json: object = None, timeout: int = 30) -> FakeResponse:
        """Record POST calls and return the configured response."""
        self.calls.append(("POST", url, json, timeout))
        return self.post_response

    def patch(self, url: str, json: object = None, timeout: int = 30) -> FakeResponse:
        """Record PATCH calls and return the configured response."""
        self.calls.append(("PATCH", url, json, timeout))
        return self.patch_response


def install_requests_module(monkeypatch: pytest.MonkeyPatch, session: FakeSession) -> None:
    """Install a fake requests module in sys.modules."""
    module = ModuleType("requests")
    module.Session = lambda: session
    monkeypatch.setitem(sys.modules, "requests", module)


def make_tfs_config(**changes: object) -> ReviewConfig:
    """Build a valid baseline TFS configuration."""
    base = ReviewConfig(
        tfs_base_url="https://dev.azure.com/org",
        tfs_collection="DefaultCollection",
        tfs_project="ProjectX",
        tfs_pat="pat-token",
        tfs_verify_ssl=True,
    )
    for key, value in changes.items():
        setattr(base, key, value)
    return base


def test_init_requires_complete_configuration() -> None:
    """It should reject incomplete TFS configurations."""
    with pytest.raises(TFSError, match="Incomplete TFS configuration"):
        TFSClient(ReviewConfig())


def test_session_configures_headers_and_ssl(monkeypatch: pytest.MonkeyPatch) -> None:
    """It should initialize the session headers and TLS verification options."""
    session = FakeSession()
    install_requests_module(monkeypatch, session)

    client = TFSClient(make_tfs_config())
    built = client.session

    assert built is session
    assert built.headers["Authorization"].startswith("Basic ")
    assert built.verify is True


def test_session_uses_ca_bundle_and_can_disable_ssl(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mocker) -> None:
    """It should use the configured CA bundle and disable warnings when SSL verify is off."""
    session = FakeSession()
    install_requests_module(monkeypatch, session)
    ca_bundle = tmp_path / "ca.pem"
    ca_bundle.write_text("pem", encoding="utf-8")

    client = TFSClient(make_tfs_config(tfs_ca_bundle=str(ca_bundle)))
    assert client.session.verify == str(ca_bundle)

    urllib3 = ModuleType("urllib3")
    urllib3.exceptions = SimpleNamespace(InsecureRequestWarning=RuntimeError)
    disable = mocker.Mock()
    urllib3.disable_warnings = disable
    monkeypatch.setitem(sys.modules, "urllib3", urllib3)
    session2 = FakeSession()
    install_requests_module(monkeypatch, session2)
    client2 = TFSClient(make_tfs_config(tfs_verify_ssl=False))

    assert client2.session.verify is False
    disable.assert_called_once_with(RuntimeError)


def test_session_raises_for_missing_requests_or_invalid_ca_bundle(monkeypatch: pytest.MonkeyPatch) -> None:
    """It should fail fast when dependencies or TLS files are missing."""
    monkeypatch.delitem(sys.modules, "requests", raising=False)
    original_import = __import__

    def fake_import(name: str, *args: object, **kwargs: object):
        if name == "requests":
            raise ImportError("missing")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    client = TFSClient(make_tfs_config())
    with pytest.raises(TFSError, match="Module 'requests' required"):
        _ = client.session
    monkeypatch.setattr("builtins.__import__", original_import)

    session = FakeSession()
    install_requests_module(monkeypatch, session)
    client = TFSClient(make_tfs_config(tfs_ca_bundle="~/missing-ca.pem"))
    with pytest.raises(TFSError, match="file does not exist"):
        _ = client.session


def test_api_url_supports_cloud_and_on_prem() -> None:
    """It should build URLs for Azure DevOps Services and on-prem TFS."""
    cloud = TFSClient(make_tfs_config(tfs_base_url="https://dev.azure.com/org"))
    on_prem = TFSClient(make_tfs_config(tfs_base_url="https://tfs.local/tfs"))

    assert "/ProjectX/_apis/git/repositories?api-version=7.0" in cloud._api_url("git/repositories")
    assert "/DefaultCollection/ProjectX/_apis/git/repositories?api-version=7.0" in on_prem._api_url("git/repositories")
    assert on_prem._api_url("git/repositories?x=1").endswith("x=1&api-version=7.0")


def test_get_post_and_patch_wrap_http_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """It should forward JSON payloads and wrap HTTP exceptions."""
    session = FakeSession()
    install_requests_module(monkeypatch, session)
    client = TFSClient(make_tfs_config())
    session.get_response = FakeResponse(json_data={"ok": True})
    session.post_response = FakeResponse(json_data={"created": True})
    session.patch_response = FakeResponse(json_data={"updated": True})

    assert client._get("path") == {"ok": True}
    assert client._post("path", {"a": 1}) == {"created": True}
    assert client._patch("path", {"a": 1}) == {"updated": True}

    session.get_response = FakeResponse(exc=RuntimeError("boom"))
    with pytest.raises(TFSError, match="Error accessing TFS API"):
        client._get("path")


def test_list_pull_requests_builds_filters_and_parses_reviewers(mocker) -> None:
    """It should normalize branch filters and parse reviewer vote labels."""
    client = TFSClient(make_tfs_config())
    get_mock = mocker.patch(
        "src.tfs_client.TFSClient._get",
        return_value={
            "value": [
                {
                    "pullRequestId": 1,
                    "title": "Improve tests",
                    "description": "desc",
                    "createdBy": {"displayName": "Alice", "id": "alice-id"},
                    "sourceRefName": "refs/heads/feature/tests",
                    "targetRefName": "refs/heads/main",
                    "status": "active",
                    "creationDate": "2024-01-01",
                    "repository": {"name": "repo-a", "id": "repo-id"},
                    "mergeStatus": "succeeded",
                    "reviewers": [{"displayName": "Bob", "vote": 10}],
                    "labels": [{"name": "backend"}],
                    "isDraft": True,
                    "url": "https://example/pr/1",
                }
            ]
        },
    )

    prs = client.list_pull_requests(
        repository="repo-a",
        author="alice-id",
        reviewer="bob-id",
        source_branch="feature/tests",
        target_branch="main",
    )

    path, params = get_mock.call_args.args[:2]
    assert path == "git/repositories/repo-a/pullrequests"
    assert params["searchCriteria.sourceRefName"] == "refs/heads/feature/tests"
    assert params["searchCriteria.targetRefName"] == "refs/heads/main"
    assert prs[0]["reviewers"][0]["vote_label"] == "✅ Approved"


def test_get_pull_request_details_handles_commit_failure_and_changed_files(mocker) -> None:
    """It should tolerate commit lookup errors and still return changed files."""
    client = TFSClient(make_tfs_config())
    mocker.patch("src.tfs_client.TFSClient._parse_pr_summary", return_value={"id": 1, "title": "t"})
    changed_files = mocker.patch("src.tfs_client.TFSClient._get_pr_changed_files", return_value=[{"path": "/a.py"}])

    def fake_get(path: str, *args: object, **kwargs: object) -> dict:
        if path.endswith("/commits"):
            raise TFSError("boom")
        return {
            "pullRequestId": 1,
            "title": "Improve tests",
            "createdBy": {"displayName": "Alice"},
            "sourceRefName": "refs/heads/feature/tests",
            "targetRefName": "refs/heads/main",
            "status": "active",
            "creationDate": "2024-01-01",
            "repository": {"name": "repo-a", "id": "repo-id"},
        }

    mocker.patch("src.tfs_client.TFSClient._get", side_effect=fake_get)
    details = client.get_pull_request_details("repo-a", 1)

    assert details["commits"] == []
    assert details["changed_files"] == [{"path": "/a.py"}]
    changed_files.assert_called_once_with("repo-a", 1)


def test_get_pr_changed_files_handles_error_paths(mocker) -> None:
    """It should return empty lists when iterations or changes cannot be retrieved."""
    client = TFSClient(make_tfs_config())

    mocker.patch("src.tfs_client.TFSClient._get", side_effect=TFSError("boom"))
    assert client._get_pr_changed_files("repo-a", 1) == []

    get_mock = mocker.patch(
        "src.tfs_client.TFSClient._get",
        side_effect=[{"value": []}],
    )
    assert client._get_pr_changed_files("repo-a", 1) == []
    assert get_mock.call_count == 1

    mocker.patch(
        "src.tfs_client.TFSClient._get",
        side_effect=[{"value": [{"id": 2}]}, TFSError("boom")],
    )
    assert client._get_pr_changed_files("repo-a", 1) == []

    mocker.patch(
        "src.tfs_client.TFSClient._get",
        side_effect=[
            {"value": [{"id": 2}]},
            {"changeEntries": [
                {"item": {"isFolder": True, "path": "/folder"}},
                {"item": {"path": "/file.py"}, "changeType": "edit", "originalPath": "/old.py"},
            ]},
        ],
    )
    assert client._get_pr_changed_files("repo-a", 1) == [{"path": "/file.py", "change_type": "edit", "original_path": "/old.py"}]


def test_get_pull_request_diff_for_full_code_and_diff_only(mocker) -> None:
    """It should build diffs using the selected review scope."""
    client = TFSClient(make_tfs_config())
    mocker.patch(
        "src.tfs_client.TFSClient._get",
        side_effect=[
            {"sourceRefName": "refs/heads/feature", "targetRefName": "refs/heads/main"},
            {"value": [{"id": 2}]},
            {"changeEntries": [{"item": {"path": "/a.py"}, "changeType": "edit", "originalPath": "/a.py"}]},
        ],
    )
    full_code = mocker.patch("src.tfs_client.TFSClient._build_full_code_diff_part", return_value=["FULL"])
    unified = mocker.patch("src.tfs_client.TFSClient._build_unified_diff_part", return_value=["UNIFIED"])

    assert "FULL" in client.get_pull_request_diff("repo-a", 1, review_scope="full_code")
    assert full_code.called

    mocker.patch(
        "src.tfs_client.TFSClient._get",
        side_effect=[
            {"sourceRefName": "refs/heads/feature", "targetRefName": "refs/heads/main"},
            {"value": [{"id": 2}]},
            {"changeEntries": [{"item": {"path": "/a.py"}, "changeType": "edit", "originalPath": "/a.py"}]},
        ],
    )
    assert "UNIFIED" in client.get_pull_request_diff("repo-a", 1)
    assert unified.called


def test_get_pull_request_diff_raises_for_missing_iterations_or_changes(mocker) -> None:
    """It should raise when the PR has no iterations or no files."""
    client = TFSClient(make_tfs_config())
    mocker.patch(
        "src.tfs_client.TFSClient._get",
        side_effect=[
            {"sourceRefName": "refs/heads/feature", "targetRefName": "refs/heads/main"},
            {"value": []},
        ],
    )
    with pytest.raises(TFSError, match="has no iterations"):
        client.get_pull_request_diff("repo-a", 1)

    mocker.patch(
        "src.tfs_client.TFSClient._get",
        side_effect=[
            {"sourceRefName": "refs/heads/feature", "targetRefName": "refs/heads/main"},
            {"value": [{"id": 1}]},
            {"changeEntries": []},
        ],
    )
    result = client.get_pull_request_diff("repo-a", 1)
    assert result == ""


def test_get_pull_request_diff_filters_excluded_paths(mocker) -> None:
    """It should filter change entries early based on excluded_paths prefix matching."""
    client = TFSClient(make_tfs_config())
    mocker.patch(
        "src.tfs_client.TFSClient._get",
        side_effect=[
            {"sourceRefName": "refs/heads/feature", "targetRefName": "refs/heads/main"},
            {"value": [{"id": 2}]},
            {"changeEntries": [
                {"item": {"path": "/IoT/_input/packages/pkg.json"}, "changeType": "edit"},
                {"item": {"path": "/src/app.py"}, "changeType": "edit"},
            ]},
        ],
    )
    unified = mocker.patch("src.tfs_client.TFSClient._build_unified_diff_part", return_value=["UNIFIED"])

    # Exclude IoT/_input/packages. It should skip the first file.
    result = client.get_pull_request_diff(
        "repo-a", 1, excluded_paths=["/IoT/_input/packages"]
    )
    assert "UNIFIED" in result
    unified.assert_called_once_with(
        repository="repo-a",
        file_path="/src/app.py",
        original_path="/src/app.py",
        change_type="edit",
        source_branch="refs/heads/feature",
        target_branch="refs/heads/main",
    )


def test_get_pull_request_diff_filters_extensions(mocker) -> None:
    """It should filter change entries early based on file_extensions_filter."""
    client = TFSClient(make_tfs_config())
    mocker.patch(
        "src.tfs_client.TFSClient._get",
        side_effect=[
            {"sourceRefName": "refs/heads/feature", "targetRefName": "refs/heads/main"},
            {"value": [{"id": 2}]},
            {"changeEntries": [
                {"item": {"path": "/src/app.py"}, "changeType": "edit"},
                {"item": {"path": "/docs/readme.md"}, "changeType": "edit"},
            ]},
        ],
    )
    unified = mocker.patch("src.tfs_client.TFSClient._build_unified_diff_part", return_value=["UNIFIED"])

    # Filter to only keep .py
    result = client.get_pull_request_diff(
        "repo-a", 1, file_extensions_filter=[".py"]
    )
    assert "UNIFIED" in result
    unified.assert_called_once_with(
        repository="repo-a",
        file_path="/src/app.py",
        original_path="/src/app.py",
        change_type="edit",
        source_branch="refs/heads/feature",
        target_branch="refs/heads/main",
    )


def test_get_pull_request_diff_returns_empty_if_all_filtered(mocker) -> None:
    """It should return an empty string (not raise) if all files are filtered out."""
    client = TFSClient(make_tfs_config())
    mocker.patch(
        "src.tfs_client.TFSClient._get",
        side_effect=[
            {"sourceRefName": "refs/heads/feature", "targetRefName": "refs/heads/main"},
            {"value": [{"id": 2}]},
            {"changeEntries": [
                {"item": {"path": "/IoT/_input/packages/pkg.json"}, "changeType": "edit"},
            ]},
        ],
    )
    mocker.patch("src.tfs_client.TFSClient._build_unified_diff_part", return_value=["UNIFIED"])

    # Exclude IoT. All files are filtered — returns empty string instead of raising.
    result = client.get_pull_request_diff("repo-a", 1, excluded_paths=["IoT"])
    assert result == ""


def test_get_pr_changed_files_handles_null_item(mocker) -> None:
    """It should not crash and use originalPath as fallback when item is null (deleted files)."""
    client = TFSClient(make_tfs_config())
    mocker.patch(
        "src.tfs_client.TFSClient._get",
        side_effect=[
            {"value": [{"id": 1}]},
            {"changeEntries": [
                # Deleted file: API returns item=null and provides originalPath instead.
                {"item": None, "changeType": "delete", "originalPath": "/src/deleted.py"},
                # Normal file alongside the delete.
                {"item": {"path": "/src/kept.py"}, "changeType": "edit", "originalPath": ""},
            ]},
        ],
    )
    result = client._get_pr_changed_files("repo-a", 1)

    # Both entries must be surfaced to the terminal summary without crashing.
    assert len(result) == 2
    deleted = next(r for r in result if r["change_type"] == "delete")
    assert deleted["path"] == "/src/deleted.py"
    assert deleted["original_path"] == "/src/deleted.py"


def test_get_pull_request_diff_skips_deleted_files(mocker) -> None:
    """Deleted files must be excluded from the LLM diff without raising an error."""
    client = TFSClient(make_tfs_config())
    mocker.patch(
        "src.tfs_client.TFSClient._get",
        side_effect=[
            {"sourceRefName": "refs/heads/feature", "targetRefName": "refs/heads/main"},
            {"value": [{"id": 1}]},
            {"changeEntries": [
                # Deleted file — item is null, API provides only originalPath.
                {"item": None, "changeType": "delete", "originalPath": "/src/removed.py"},
                # Edited file that should still appear in the diff.
                {"item": {"path": "/src/app.py"}, "changeType": "edit", "originalPath": "/src/app.py"},
            ]},
        ],
    )
    unified = mocker.patch(
        "src.tfs_client.TFSClient._build_unified_diff_part", return_value=["UNIFIED"]
    )

    result = client.get_pull_request_diff("repo-a", 1)

    # The diff builder must only be called for the edited file, not the deleted one.
    unified.assert_called_once_with(
        repository="repo-a",
        file_path="/src/app.py",
        original_path="/src/app.py",
        change_type="edit",
        source_branch="refs/heads/feature",
        target_branch="refs/heads/main",
    )
    assert "UNIFIED" in result


def test_get_pull_request_diff_handles_null_item_in_non_delete(mocker) -> None:
    """A null item for a non-delete change must not crash; originalPath is used as path fallback."""
    client = TFSClient(make_tfs_config())
    mocker.patch(
        "src.tfs_client.TFSClient._get",
        side_effect=[
            {"sourceRefName": "refs/heads/feature", "targetRefName": "refs/heads/main"},
            {"value": [{"id": 1}]},
            {"changeEntries": [
                # Unexpected null item for an edit — defensive case.
                {"item": None, "changeType": "edit", "originalPath": "/src/fallback.py"},
            ]},
        ],
    )
    unified = mocker.patch(
        "src.tfs_client.TFSClient._build_unified_diff_part", return_value=["UNIFIED"]
    )

    result = client.get_pull_request_diff("repo-a", 1)

    unified.assert_called_once_with(
        repository="repo-a",
        file_path="/src/fallback.py",
        original_path="/src/fallback.py",
        change_type="edit",
        source_branch="refs/heads/feature",
        target_branch="refs/heads/main",
    )
    assert "UNIFIED" in result


def test_get_pull_request_diff_returns_empty_when_only_deletes_present(mocker) -> None:
    """It should return an empty string (not raise) when the PR contains only deleted files."""
    client = TFSClient(make_tfs_config())
    mocker.patch(
        "src.tfs_client.TFSClient._get",
        side_effect=[
            {"sourceRefName": "refs/heads/feature", "targetRefName": "refs/heads/main"},
            {"value": [{"id": 1}]},
            {"changeEntries": [
                {"item": None, "changeType": "delete", "originalPath": "/src/a.py"},
                {"item": None, "changeType": "delete", "originalPath": "/src/b.py"},
            ]},
        ],
    )

    result = client.get_pull_request_diff("repo-a", 1)
    assert result == ""


def test_build_diff_parts_and_file_content(mocker) -> None:
    """It should build full-code and unified diff payloads from file content."""
    client = TFSClient(make_tfs_config())
    get_file = mocker.patch("src.tfs_client.TFSClient._get_file_content", side_effect=["new\ncontent", "old", "new"])

    full = client._build_full_code_diff_part("repo-a", "/a.py", "edit", "refs/heads/feature")
    unified = client._build_unified_diff_part("repo-a", "/a.py", "/a.py", "edit", "refs/heads/feature", "refs/heads/main")

    assert "+new" in full[4]
    assert unified[0].startswith("diff --git")
    assert get_file.call_count == 3

    mocker.patch("src.tfs_client.TFSClient._get_file_content", side_effect=RuntimeError("boom"))
    fallback_full = client._build_full_code_diff_part("repo-a", "/a.py", "edit", "refs/heads/feature")
    no_text = client._build_unified_diff_part("repo-a", "/a.py", "/a.py", "edit", "refs/heads/feature", "refs/heads/main")
    assert "Content not available" in fallback_full[-1]
    assert "No textual differences detected" in no_text[-1]


# ---------------------------------------------------------------------------
# _build_unified_diff_part — XML context block
# ---------------------------------------------------------------------------

def test_build_unified_diff_part_appends_xml_context_block(mocker) -> None:
    """_build_unified_diff_part must append an XML context block (enclosing_scopes/adaptive_diff) after the diff."""
    client = TFSClient(make_tfs_config())
    mocker.patch(
        "src.tfs_client.TFSClient._get_file_content",
        side_effect=["old line", "new line one\nnew line two"],
    )

    result = client._build_unified_diff_part(
        "repo-a", "/src/app.py", "/src/app.py", "edit",
        "refs/heads/feature", "refs/heads/main",
    )
    joined = "\n".join(result)

    # Must contain an XML context block (adaptive_diff for short files without Tree-sitter)
    _xml_tags = ("<enclosing_scopes", "<file_skeleton", "<adaptive_diff", "<sql_statement")
    assert any(tag in joined for tag in _xml_tags), "Expected an XML context block"
    assert "new line one" in joined
    assert "new line two" in joined
    assert "old line" not in joined.split("<")[1]  # old content not in context block
    # XML context block must appear after the diff headers
    first_xml_pos = min(
        (joined.find(tag) for tag in _xml_tags if joined.find(tag) >= 0),
        default=-1,
    )
    assert first_xml_pos > joined.index("diff --git")
    # Must NOT use old FULL_FILE_CONTEXT markers
    assert "FULL_FILE_CONTEXT_START" not in joined
    assert "FULL_FILE_CONTEXT_END" not in joined


def test_build_unified_diff_part_context_block_present_on_add(mocker) -> None:
    """_build_unified_diff_part must append an XML context block for 'add' change type."""
    client = TFSClient(make_tfs_config())
    mocker.patch(
        "src.tfs_client.TFSClient._get_file_content",
        return_value="new content",
    )

    result = client._build_unified_diff_part(
        "repo-a", "/src/new_file.py", "/src/new_file.py", "add",
        "refs/heads/feature", "refs/heads/main",
    )
    joined = "\n".join(result)

    _xml_tags = ("<enclosing_scopes", "<file_skeleton", "<adaptive_diff", "<sql_statement")
    assert any(tag in joined for tag in _xml_tags), "Expected XML context block"
    assert "new content" in joined
    assert "FULL_FILE_CONTEXT_START" not in joined


def test_build_unified_diff_part_no_context_block_on_delete(mocker) -> None:
    """_build_unified_diff_part must NOT append any context block when change_type is delete.

    Note: in production this code path is no longer reached via get_pull_request_diff —
    deleted entries are filtered out before _build_unified_diff_part is ever called.
    This test is kept as a defensive contract on the method's own behaviour.
    """
    client = TFSClient(make_tfs_config())
    mocker.patch(
        "src.tfs_client.TFSClient._get_file_content",
        return_value="old content",
    )

    result = client._build_unified_diff_part(
        "repo-a", "/src/app.py", "/src/app.py", "delete",
        "refs/heads/feature", "refs/heads/main",
    )
    joined = "\n".join(result)

    assert "FULL_FILE_CONTEXT_START" not in joined
    assert "FULL_FILE_CONTEXT_END" not in joined
    _xml_tags = ("<enclosing_scopes", "<file_skeleton", "<adaptive_diff", "<sql_statement")
    assert not any(tag in joined for tag in _xml_tags), "No context block expected for delete"


def test_build_unified_diff_part_context_block_uses_new_file_path(mocker) -> None:
    """The XML context block must reference the new file path (file_path), not original_path."""
    client = TFSClient(make_tfs_config())
    mocker.patch(
        "src.tfs_client.TFSClient._get_file_content",
        side_effect=["old content", "new content"],
    )

    result = client._build_unified_diff_part(
        "repo-a", "/src/renamed.py", "/src/original.py", "rename",
        "refs/heads/feature", "refs/heads/main",
    )
    joined = "\n".join(result)

    # XML context block must mention the new (renamed) path
    assert "/src/renamed.py" in joined
    assert "new content" in joined
    # Must NOT use old FULL_FILE_CONTEXT markers
    assert "FULL_FILE_CONTEXT_START" not in joined
    assert "FULL_FILE_CONTEXT_END" not in joined


def test_build_unified_diff_part_no_context_block_when_content_unavailable(mocker) -> None:
    """When both old and new file content fetches fail, no context block must appear."""
    client = TFSClient(make_tfs_config())
    mocker.patch(
        "src.tfs_client.TFSClient._get_file_content",
        side_effect=RuntimeError("unreachable"),
    )

    result = client._build_unified_diff_part(
        "repo-a", "/src/app.py", "/src/app.py", "edit",
        "refs/heads/feature", "refs/heads/main",
    )
    joined = "\n".join(result)

    assert "FULL_FILE_CONTEXT_START" not in joined
    assert "FULL_FILE_CONTEXT_END" not in joined
    _xml_tags = ("<enclosing_scopes", "<file_skeleton", "<adaptive_diff", "<sql_statement")
    assert not any(tag in joined for tag in _xml_tags), "No context block expected when content is unavailable"


def test_raw_get_and_comment_endpoints(mocker) -> None:
    """It should expose raw file content and build comment payloads correctly."""
    client = TFSClient(make_tfs_config())
    mocker.patch("src.tfs_client.TFSClient._get_raw", return_value="file content")
    assert client._get_file_content("repo-a", "/a.py", version="feature") == "file content"

    post_mock = mocker.patch("src.tfs_client.TFSClient._post", return_value={"id": 10})
    mocker.patch("src.tfs_client.TFSClient._get", return_value={"value": [{"id": 3}]})

    assert client.post_general_comment("repo-a", 1, "hello")["id"] == 10
    inline = client.post_inline_comment("repo-a", 1, "src/app.py", 7, "msg", right_file=False)
    assert inline["id"] == 10
    _, payload = post_mock.call_args.args[:2]
    assert payload["threadContext"]["filePath"] == "/src/app.py"
    assert payload["threadContext"]["leftFileStart"]["line"] == 7


def test_post_inline_comment_requires_iterations(mocker) -> None:
    """It should fail when inline comments cannot be associated with an iteration."""
    client = TFSClient(make_tfs_config())
    mocker.patch("src.tfs_client.TFSClient._get", return_value={"value": []})

    with pytest.raises(TFSError, match="has no iterations"):
        client.post_inline_comment("repo-a", 1, "src/app.py", 1, "msg")


def test_post_review_comments_formats_and_routes_comments(mocker) -> None:
    """It should route inline and general comments and collect failures."""
    client = TFSClient(make_tfs_config())
    mocker.patch("src.tfs_client.TFSClient.list_pr_threads", return_value=[])
    formatter = mocker.patch("src.tfs_client.TFSClient._format_review_comment", side_effect=lambda comment: f"TEXT:{comment['comment']}")
    inline = mocker.patch("src.tfs_client.TFSClient.post_inline_comment", return_value={"id": 1})
    general = mocker.patch("src.tfs_client.TFSClient.post_general_comment", side_effect=[{"id": 2}, TFSError("boom")])

    results = client.post_review_comments(
        "repo-a",
        1,
        [
            {"file": "src/app.py", "line": 3, "type": "bug", "comment": "inline"},
            {"file": "", "line": 0, "type": "praise", "comment": "general"},
            {"file": "", "line": 0, "type": "suggestion", "comment": "fail"},
        ],
    )

    assert formatter.call_count == 3
    inline.assert_called_once()
    assert general.call_count == 2
    assert results[0]["success"] is True
    assert results[1]["success"] is True
    assert results[2]["success"] is False


def test_list_pr_threads_returns_value_list(mocker) -> None:
    """It should GET the PR threads endpoint and return the value list."""
    client = TFSClient(make_tfs_config())
    get_mock = mocker.patch(
        "src.tfs_client.TFSClient._get",
        return_value={"value": [{"id": 1}, {"id": 2}]},
    )

    threads = client.list_pr_threads("repo-a", 7)

    assert threads == [{"id": 1}, {"id": 2}]
    path = get_mock.call_args.args[0]
    assert path == "git/repositories/repo-a/pullrequests/7/threads"


def test_comment_marker_is_stable_and_content_sensitive() -> None:
    """It should produce a stable marker per location, ignoring comment text."""
    client = TFSClient(make_tfs_config())
    base = {"file": "src/app.py", "line": 3, "type": "bug", "comment": "x"}

    assert client._comment_marker(base) == client._comment_marker(dict(base))
    # Comment text is not part of the fingerprint.
    assert client._comment_marker(base) == client._comment_marker(
        {**base, "comment": "y"}
    )
    assert client._comment_marker(base) != client._comment_marker(
        {**base, "line": 4}
    )
    assert client._comment_marker(base) != client._comment_marker(
        {**base, "type": "security"}
    )


def test_extract_existing_markers_reads_all_threads() -> None:
    """It should extract every fingerprint present in thread comments."""
    client = TFSClient(make_tfs_config())
    threads = [
        {"comments": [{"content": "hi <!-- ai-review:abc123 -->"}]},
        {"comments": [
            {"content": "no marker here"},
            {"content": "<!-- ai-review:summary -->"},
        ]},
    ]

    assert client._extract_existing_markers(threads) == {"abc123", "summary"}


def test_post_review_comments_skips_existing_and_dedupes_batch(mocker) -> None:
    """It should skip already posted markers and dedupe within one batch."""
    client = TFSClient(make_tfs_config())
    duplicate = {"file": "src/app.py", "line": 3, "type": "bug", "comment": "dup"}
    existing_marker = client._comment_marker(duplicate)
    mocker.patch(
        "src.tfs_client.TFSClient.list_pr_threads",
        return_value=[{"comments": [
            {"content": client._marker_html(existing_marker)}
        ]}],
    )
    inline = mocker.patch(
        "src.tfs_client.TFSClient.post_inline_comment", return_value={"id": 9}
    )

    new_comment = {"file": "src/app.py", "line": 5, "type": "bug", "comment": "new"}
    results = client.post_review_comments(
        "repo-a",
        1,
        [duplicate, new_comment, dict(new_comment)],
    )

    # First is skipped (already exists), second posted, third skipped (batch dedup).
    assert results[0]["skipped"] is True
    assert results[1]["success"] is True
    assert results[2]["skipped"] is True
    inline.assert_called_once()
    posted_text = inline.call_args.args[4]
    assert client._marker_html(client._comment_marker(new_comment)) in posted_text


def test_post_general_comment_dedup_marker_skips_when_present(mocker) -> None:
    """It should skip general comments whose dedup marker already exists."""
    client = TFSClient(make_tfs_config())
    post_mock = mocker.patch("src.tfs_client.TFSClient._post", return_value={"id": 5})
    mocker.patch(
        "src.tfs_client.TFSClient.list_pr_threads",
        return_value=[{"comments": [{"content": "<!-- ai-review:summary -->"}]}],
    )

    result = client.post_general_comment(
        "repo-a", 1, "summary body", dedup_marker="summary"
    )

    assert result["skipped"] is True
    post_mock.assert_not_called()


def test_post_general_comment_dedup_marker_posts_when_absent(mocker) -> None:
    """It should append the marker and post when it is not already present."""
    client = TFSClient(make_tfs_config())
    post_mock = mocker.patch("src.tfs_client.TFSClient._post", return_value={"id": 5})
    mocker.patch("src.tfs_client.TFSClient.list_pr_threads", return_value=[])

    result = client.post_general_comment(
        "repo-a", 1, "summary body", dedup_marker="summary"
    )

    assert result == {"id": 5}
    _, payload = post_mock.call_args.args[:2]
    assert "<!-- ai-review:summary -->" in payload["comments"][0]["content"]


def test_repository_helpers_and_status_formatting(mocker) -> None:
    """It should format review comments and resolve repositories by name."""
    client = TFSClient(make_tfs_config())
    comment = client._format_review_comment(
        {
            "type": "security",
            "comment": "Avoid plain text secrets",
            "suggestion": "Use a vault",
            "reference": "OWASP",
        }
    )
    assert "Security" in comment
    assert "Use a vault" in comment
    assert client._status_to_int("wontfix") == 3
    assert client._status_to_int("unknown") == 1

    mocker.patch(
        "src.tfs_client.TFSClient._get",
        return_value={
            "value": [
                {"id": "1", "name": "Repo-A", "remoteUrl": "https://example/repo-a", "defaultBranch": "refs/heads/main"}
            ]
        },
    )
    assert client.list_repositories() == [{"id": "1", "name": "Repo-A", "url": "https://example/repo-a", "default_branch": "main"}]
    assert client.get_repository_id("repo-a") == "1"
    with pytest.raises(TFSError, match="not found"):
        client.get_repository_id("missing")