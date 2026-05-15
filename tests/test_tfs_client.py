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
    with pytest.raises(TFSError, match="contains no file changes"):
        client.get_pull_request_diff("repo-a", 1)


def test_build_diff_parts_and_file_content(mocker) -> None:
    """It should build full-code and unified diff payloads from file content."""
    client = TFSClient(make_tfs_config())
    get_file = mocker.patch("src.tfs_client.TFSClient._get_file_content", side_effect=["new\ncontent", "old", "new"])

    full = client._build_full_code_diff_part("repo-a", "/a.py", "edit", "refs/heads/feature")
    unified = client._build_unified_diff_part("repo-a", "/a.py", "/a.py", "edit", "refs/heads/feature", "refs/heads/main")

    assert "+new" in full[4]
    assert unified[0].startswith("diff --git")
    assert get_file.call_count == 3
    assert get_file.call_args_list[0].kwargs == {
        "version": "feature",
        "version_type": "branch",
    }
    assert get_file.call_args_list[1].kwargs == {
        "version": "main",
        "version_type": "branch",
    }
    assert get_file.call_args_list[2].kwargs == {
        "version": "feature",
        "version_type": "branch",
    }

    mocker.patch("src.tfs_client.TFSClient._get_file_content", side_effect=RuntimeError("boom"))
    fallback_full = client._build_full_code_diff_part("repo-a", "/a.py", "edit", "refs/heads/feature")
    no_text = client._build_unified_diff_part("repo-a", "/a.py", "/a.py", "edit", "refs/heads/feature", "refs/heads/main")
    assert "Content not available" in fallback_full[-1]
    assert "No textual differences detected" in no_text[-1]


def test_get_project_context_fetches_source_branch_files(mocker) -> None:
    """It should build repository context from the PR source branch."""
    client = TFSClient(make_tfs_config())
    get_mock = mocker.patch(
        "src.tfs_client.TFSClient._get",
        return_value={
            "value": [
                {"path": "/src/app.py", "gitObjectType": "blob"},
                {"path": "/README.md", "gitObjectType": "blob"},
                {"path": "/node_modules/pkg/index.js", "gitObjectType": "blob"},
                {"path": "/assets/logo.png", "gitObjectType": "blob"},
                {"path": "/src", "isFolder": True},
            ]
        },
    )
    get_file = mocker.patch(
        "src.tfs_client.TFSClient._get_file_content",
        side_effect=["# docs", "print('x')"],
    )

    context = client.get_project_context(
        "repo-a",
        "refs/heads/feature/context",
        max_files=10,
        max_chars=1000,
    )

    assert "### Full repository context" in context
    assert "Source branch: feature/context" in context
    assert "/README.md" in context
    assert "/src/app.py" in context
    assert "node_modules" not in context
    assert "logo.png" not in context
    path, params = get_mock.call_args.args[:2]
    assert path == "git/repositories/repo-a/items"
    assert params["recursionLevel"] == "Full"
    assert params["versionDescriptor.version"] == "feature/context"
    assert get_file.call_count == 2
    assert get_file.call_args_list[0].kwargs["version"] == "feature/context"


def test_get_project_context_zero_limits_include_all_eligible_files(mocker) -> None:
    """It should treat zero project context limits as unlimited."""
    client = TFSClient(make_tfs_config())
    mocker.patch(
        "src.tfs_client.TFSClient._get",
        return_value={
            "value": [
                {"path": "/README.md", "gitObjectType": "blob"},
                {"path": "/src/app.py", "gitObjectType": "blob"},
                {"path": "/src/domain.py", "gitObjectType": "blob"},
            ]
        },
    )
    get_file = mocker.patch(
        "src.tfs_client.TFSClient._get_file_content",
        side_effect=["# docs", "print('x')", "class Domain: pass"],
    )

    context = client.get_project_context(
        "repo-a",
        "feature/full-context",
        max_files=0,
        max_chars=0,
    )

    assert "/README.md" in context
    assert "/src/app.py" in context
    assert "/src/domain.py" in context
    assert "Repository context truncated" not in context
    assert get_file.call_count == 3


def test_get_project_manifest_lists_eligible_files(mocker) -> None:
    """It should render a compact manifest without fetching file contents."""
    client = TFSClient(make_tfs_config())
    get_mock = mocker.patch(
        "src.tfs_client.TFSClient._get",
        return_value={
            "value": [
                {"path": "/src/app.py", "gitObjectType": "blob"},
                {"path": "/README.md", "gitObjectType": "blob"},
                {"path": "/dist/app.js", "gitObjectType": "blob"},
                {"path": "/assets/logo.png", "gitObjectType": "blob"},
            ]
        },
    )
    get_file = mocker.patch("src.tfs_client.TFSClient._get_file_content")

    manifest = client.get_project_manifest(
        "repo-a",
        "refs/heads/feature/context",
        max_chars=1000,
    )

    assert "Repository file manifest" in manifest
    assert "- /README.md" in manifest
    assert "- /src/app.py" in manifest
    assert "dist" not in manifest
    assert "logo.png" not in manifest
    get_mock.assert_called_once()
    get_file.assert_not_called()


def test_get_changed_and_requested_file_contexts_validate_paths(mocker) -> None:
    """It should fetch only eligible selected files for bounded context."""
    client = TFSClient(make_tfs_config())
    mocker.patch(
        "src.tfs_client.TFSClient._get",
        return_value={
            "value": [
                {"path": "/src/app.py", "gitObjectType": "blob"},
                {"path": "/src/helper.py", "gitObjectType": "blob"},
                {"path": "/assets/logo.png", "gitObjectType": "blob"},
            ]
        },
    )
    get_file = mocker.patch(
        "src.tfs_client.TFSClient._get_file_content",
        side_effect=["print('app')", "helper = True"],
    )

    changed_context = client.get_changed_files_context(
        "repo-a",
        "feature/context",
        [
            {"path": "/src/app.py", "change_type": "edit"},
            {"path": "/deleted.py", "change_type": "delete"},
        ],
        max_chars=1000,
        file_max_chars=1000,
    )
    requested_context = client.get_project_files_context(
        "repo-a",
        "feature/context",
        ["src/helper.py", "assets/logo.png", "../secret.txt"],
        max_files=3,
        max_chars=1000,
        file_max_chars=1000,
    )

    assert "Source branch full files with changes applied" in changed_context
    assert "/src/app.py" in changed_context
    assert "deleted.py" not in changed_context
    assert "Requested repository context" in requested_context
    assert "/src/helper.py" in requested_context
    assert "#### /assets/logo.png" not in requested_context
    assert "secret" in requested_context
    assert get_file.call_count == 2
    assert all(call.kwargs["version"] == "feature/context" for call in get_file.call_args_list)
    assert all(call.kwargs["version_type"] == "branch" for call in get_file.call_args_list)


def test_get_source_file_contents_fetches_latest_source_branch(mocker) -> None:
    """It should fetch comment validation content from the current source branch."""
    client = TFSClient(make_tfs_config())
    get_file = mocker.patch(
        "src.tfs_client.TFSClient._get_file_content",
        side_effect=["print('x')", RuntimeError("missing")],
    )

    contents = client.get_source_file_contents(
        "repo-a",
        "refs/heads/feature/current",
        ["src/app.py", "src/app.py", "missing.py"],
    )

    assert contents == {"src/app.py": "print('x')"}
    get_file.assert_any_call(
        "repo-a",
        "/src/app.py",
        version="feature/current",
        version_type="branch",
    )
    assert get_file.call_count == 2


def test_get_work_item_context_fetches_linked_documentation(mocker) -> None:
    """It should build read-only context from PR-linked work item fields."""
    client = TFSClient(make_tfs_config())
    get_mock = mocker.patch(
        "src.tfs_client.TFSClient._get",
        side_effect=[
            {"value": [{"id": "101"}, {"id": "not-a-number"}]},
            {
                "value": [
                    {
                        "id": 101,
                        "fields": {
                            "System.Title": "Checkout validation",
                            "System.WorkItemType": "User Story",
                            "System.State": "Active",
                            "System.Description": "<p>Validate checkout totals</p>",
                            "Microsoft.VSTS.Common.AcceptanceCriteria": "<ul><li>Total includes tax</li></ul>",
                        },
                        "relations": [
                            {
                                "rel": "Hyperlink",
                                "url": "https://example.invalid/spec",
                                "attributes": {"comment": "Spec"},
                            }
                        ],
                    }
                ]
            },
        ],
    )

    context = client.get_work_item_context("repo-a", 42, max_items=5, max_chars=5000)

    assert "Linked work item documentation" in context
    assert "Work Item 101: Checkout validation" in context
    assert "Validate checkout totals" in context
    assert "Total includes tax" in context
    assert "Spec: https://example.invalid/spec" in context
    work_item_call = get_mock.call_args_list[0]
    assert work_item_call.args[0] == "git/repositories/repo-a/pullRequests/42/workitems"
    assert work_item_call.kwargs["api_version"] == "7.1"
    details_call = get_mock.call_args_list[1]
    assert details_call.args[0] == "wit/workitems"
    assert details_call.args[1]["ids"] == "101"
    assert details_call.args[1]["$expand"] == "relations"
    assert "System.Description" in details_call.args[1]["fields"].split(",")
    assert details_call.kwargs["api_version"] == "7.1"


def test_work_item_context_always_requests_description(mocker) -> None:
    """It should include work item descriptions even with custom field settings."""
    client = TFSClient(make_tfs_config())
    get_mock = mocker.patch(
        "src.tfs_client.TFSClient._get",
        side_effect=[
            {"value": [{"id": "101"}]},
            {
                "value": [
                    {
                        "id": 101,
                        "fields": {
                            "System.Title": "Checkout validation",
                            "System.WorkItemType": "User Story",
                            "System.State": "Active",
                            "System.Description": "<p>Use the documented tax rules.</p>",
                            "Custom.Documentation": "Regional checkout spec",
                        },
                    }
                ]
            },
        ],
    )

    context = client.get_work_item_context(
        "repo-a",
        42,
        fields=["Custom.Documentation"],
    )

    requested_fields = get_mock.call_args_list[1].args[1]["fields"].split(",")
    assert "System.Description" in requested_fields
    assert "Custom.Documentation" in requested_fields
    assert "Use the documented tax rules." in context
    assert "Regional checkout spec" in context


def test_raw_get_and_comment_endpoints(mocker) -> None:
    """It should expose raw file content and build comment payloads correctly."""
    client = TFSClient(make_tfs_config())
    mocker.patch("src.tfs_client.TFSClient._get_raw", return_value="file content")
    assert client._get_file_content("repo-a", "/a.py", version="feature") == "file content"

    post_mock = mocker.patch("src.tfs_client.TFSClient._post", return_value={"id": 10})
    patch_mock = mocker.patch("src.tfs_client.TFSClient._patch", return_value={"id": 11})
    get_mock = mocker.patch("src.tfs_client.TFSClient._get", return_value={"value": [{"id": 3}]})

    assert client.post_general_comment("repo-a", 1, "hello")["id"] == 10
    inline = client.post_inline_comment("repo-a", 1, "src/app.py", 7, "msg", right_file=False)
    assert inline["id"] == 10
    assert get_mock.called
    _, payload = post_mock.call_args.args[:2]
    assert payload["threadContext"]["filePath"] == "/src/app.py"
    assert payload["threadContext"]["leftFileStart"]["line"] == 7
    assert client.reply_to_thread("repo-a", 1, 5, "reply")["id"] == 10
    assert client.update_thread_status("repo-a", 1, 5, "fixed")["id"] == 11
    patch_mock.assert_called_once()


def test_post_inline_comment_requires_iterations(mocker) -> None:
    """It should fail when inline comments cannot be associated with an iteration."""
    client = TFSClient(make_tfs_config())
    mocker.patch("src.tfs_client.TFSClient._get", return_value={"value": []})

    with pytest.raises(TFSError, match="has no iterations"):
        client.post_inline_comment("repo-a", 1, "src/app.py", 1, "msg")


def test_post_review_comments_formats_and_routes_comments(mocker) -> None:
    """It should route inline and general comments and collect failures."""
    client = TFSClient(make_tfs_config())
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


def test_plan_review_comments_skips_duplicates_and_reopens_fixed_tool_comments(mocker) -> None:
    """It should skip active duplicates and reopen fixed tool comments that reappear."""
    client = TFSClient(make_tfs_config())
    comment = {
        "file": "src/app.py",
        "line": 7,
        "type": "bug",
        "severity": "high",
        "comment": "Avoid duplicate charge calculation",
        "suggestion": "Reuse the existing total",
    }
    tagged_body = client._format_review_comment(comment)
    base_thread = {
        "id": 10,
        "threadContext": {
            "filePath": "/src/app.py",
            "rightFileStart": {"line": 7},
        },
        "comments": [{"content": tagged_body}],
    }

    mocker.patch(
        "src.tfs_client.TFSClient._get",
        return_value={"value": [{**base_thread, "status": 1}]},
    )
    active_plan = client.plan_review_comments("repo-a", 1, [comment])

    assert active_plan["new_comments"] == []
    assert len(active_plan["skipped_duplicates"]) == 1
    assert active_plan["resolved_reappeared"] == []

    mocker.patch(
        "src.tfs_client.TFSClient._get",
        return_value={"value": [{**base_thread, "status": 2}]},
    )
    fixed_plan = client.plan_review_comments("repo-a", 1, [comment])

    assert fixed_plan["new_comments"] == []
    assert fixed_plan["skipped_duplicates"] == []
    assert fixed_plan["resolved_reappeared"][0]["thread_id"] == 10
    assert fixed_plan["resolved_reappeared"][0]["status"] == "fixed"


def test_reopen_resolved_tool_comments_tags_recheck_reply(mocker) -> None:
    """It should activate the old thread and add a tagged re-check reply."""
    client = TFSClient(make_tfs_config())
    update = mocker.patch("src.tfs_client.TFSClient.update_thread_status", return_value={"id": 10})
    reply = mocker.patch("src.tfs_client.TFSClient.reply_to_thread", return_value={"id": 11})

    results = client.reopen_resolved_tool_comments(
        "repo-a",
        1,
        [
            {
                "thread_id": 10,
                "comment": {
                    "file": "src/app.py",
                    "line": 7,
                    "type": "bug",
                    "severity": "high",
                    "comment": "Avoid duplicate charge calculation",
                },
            }
        ],
    )

    assert results == [{"success": True, "thread_id": 10, "file": "src/app.py", "line": 7}]
    update.assert_called_once_with("repo-a", 1, 10, "active")
    reply_body = reply.call_args.args[3]
    assert "ai-code-review-cli" in reply_body
    assert "AI Code Review re-check" in reply_body


def test_repository_helpers_and_status_formatting(mocker) -> None:
    """It should format review comments and resolve repositories by name."""
    client = TFSClient(make_tfs_config())
    comment = client._format_review_comment(
        {
            "type": "security",
            "severity": "high",
            "comment": "Avoid plain text secrets",
            "suggestion": "Use a vault",
            "reference": "OWASP",
        }
    )
    assert "Security" in comment
    assert "Use a vault" in comment
    assert "ai-code-review-cli" in comment
    assert "ai-code-review-fingerprint" in comment
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
