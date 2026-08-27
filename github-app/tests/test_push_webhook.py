from unittest.mock import MagicMock

import httpx
import pytest

from app_server.db import hide_repo, upsert_installation
from app_server.webhooks.push import handle_push_event


def _payload(ref="refs/heads/main", default_branch="main", deleted=False, after="head123", commits=None):
    return {
        "ref": ref,
        "before": "base123",
        "after": after,
        "deleted": deleted,
        "installation": {"id": 111},
        "repository": {"full_name": "octocat/hello-world", "default_branch": default_branch},
        "commits": commits
        if commits is not None
        else [
            {"added": ["new.py"], "removed": [], "modified": ["app.py"]},
            {"added": [], "removed": ["old.py"], "modified": ["app.py"]},
        ],
    }


@pytest.mark.asyncio
async def test_push_to_default_branch_enqueues_scan_job(pool):
    fake_queue = MagicMock()
    await handle_push_event(_payload(), pool, "redis://unused", queue=fake_queue)

    fake_queue.enqueue.assert_called_once()
    args, kwargs = fake_queue.enqueue.call_args
    assert args[0] == "scan_worker.jobs.run_push_scan_job"
    assert kwargs["installation_id"] == 111
    assert kwargs["repo_full_name"] == "octocat/hello-world"
    assert kwargs["head_sha"] == "head123"
    assert kwargs["changed_files"] == ["app.py", "new.py", "old.py"]
    assert kwargs["job_timeout"] > 0


@pytest.mark.asyncio
async def test_truncated_push_payload_uses_compare_api_for_complete_changed_files(pool, monkeypatch):
    payload_commits = [
        {"added": [f"payload-{i}.py"], "removed": [], "modified": []} for i in range(20)
    ]
    payload = _payload(commits=payload_commits)
    payload["size"] = 101
    compare_files = [{"filename": f"compare-{i}.py"} for i in range(101)]
    seen_params = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/octocat/hello-world/compare/base123...head123"
        assert request.headers["Authorization"] == "Bearer installation-token"
        seen_params.append(dict(request.url.params))
        page = int(request.url.params["page"])
        assert request.url.params["per_page"] == "100"
        start = (page - 1) * 100
        return httpx.Response(
            200,
            json={"files": compare_files[start:start + 100]},
            request=request,
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    monkeypatch.setattr("app_server.webhooks.push.generate_app_jwt", lambda *a, **k: "app-jwt")
    monkeypatch.setattr(
        "app_server.webhooks.push.get_installation_token",
        lambda installation_id, app_jwt: "installation-token",
    )
    monkeypatch.setattr("app_server.webhooks.push.get_github_api_client", lambda: client)

    fake_queue = MagicMock()
    await handle_push_event(payload, pool, "redis://unused", queue=fake_queue)

    fake_queue.enqueue.assert_called_once()
    _, kwargs = fake_queue.enqueue.call_args
    assert kwargs["changed_files"] == sorted(file_info["filename"] for file_info in compare_files)
    assert "payload-0.py" not in kwargs["changed_files"]
    assert seen_params == [{"per_page": "100", "page": "1"}, {"per_page": "100", "page": "2"}]


@pytest.mark.asyncio
async def test_push_to_non_default_branch_does_not_enqueue(pool):
    fake_queue = MagicMock()
    await handle_push_event(
        _payload(ref="refs/heads/feature-x", default_branch="main"), pool, "redis://unused", queue=fake_queue
    )
    fake_queue.enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_branch_deletion_does_not_enqueue(pool):
    fake_queue = MagicMock()
    await handle_push_event(_payload(deleted=True), pool, "redis://unused", queue=fake_queue)
    fake_queue.enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_push_with_no_file_changes_enqueues_empty_changed_files(pool):
    fake_queue = MagicMock()
    await handle_push_event(_payload(commits=[]), pool, "redis://unused", queue=fake_queue)

    fake_queue.enqueue.assert_called_once()
    _, kwargs = fake_queue.enqueue.call_args
    assert kwargs["changed_files"] == []


@pytest.mark.asyncio
async def test_hidden_repo_does_not_enqueue_or_call_compare_api(pool, monkeypatch):
    # A repo the customer deselected from the installation - GitHub access
    # is already revoked, so this must be a clean no-op, including
    # skipping the (would-fail-anyway) compare API call on a truncated
    # payload.
    await upsert_installation(pool, 111, "octocat")
    await hide_repo(pool, 111, "octocat/hello-world")

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("compare API should never be reached for a hidden repo")

    monkeypatch.setattr("app_server.webhooks.push.get_github_api_client", _fail_if_called)

    payload = _payload(commits=[{"added": [f"payload-{i}.py"], "removed": [], "modified": []} for i in range(20)])
    payload["size"] = 101  # forces the truncated-payload compare-API path if not short-circuited
    fake_queue = MagicMock()
    await handle_push_event(payload, pool, "redis://unused", queue=fake_queue)

    fake_queue.enqueue.assert_not_called()
