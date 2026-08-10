from unittest.mock import MagicMock

import pytest

from app_server.db import get_installation, upsert_installation
from app_server.webhooks.installation import handle_installation_event


@pytest.mark.asyncio
async def test_installation_created_upserts_row(pool, monkeypatch):
    async def _fake_fetch(installation_id):
        return []

    monkeypatch.setattr(
        "app_server.webhooks.installation._fetch_all_installation_repo_full_names",
        _fake_fetch,
    )
    payload = {
        "action": "created",
        "installation": {"id": 555, "account": {"login": "octocat"}},
    }
    await handle_installation_event("installation", payload, pool, "redis://unused")
    row = await get_installation(pool, 555)
    assert row["account_login"] == "octocat"


@pytest.mark.asyncio
async def test_installation_deleted_removes_row(pool):
    await upsert_installation(pool, 555, "octocat")
    payload = {
        "action": "deleted",
        "installation": {"id": 555, "account": {"login": "octocat"}},
    }
    await handle_installation_event("installation", payload, pool, "redis://unused", queue=MagicMock())
    assert await get_installation(pool, 555) is None


@pytest.mark.asyncio
async def test_installation_repositories_added_upserts_row(pool):
    payload = {
        "action": "added",
        "installation": {"id": 556, "account": {"login": "someorg"}},
        "repositories_added": [],
    }
    await handle_installation_event("installation_repositories", payload, pool, "redis://unused")
    row = await get_installation(pool, 556)
    assert row["account_login"] == "someorg"


@pytest.mark.asyncio
async def test_installation_created_enqueues_initial_scan_for_every_repo(pool, monkeypatch):
    async def _fake_fetch(installation_id):
        return ["octocat/one", "octocat/two"]

    monkeypatch.setattr(
        "app_server.webhooks.installation._fetch_all_installation_repo_full_names",
        _fake_fetch,
    )
    fake_queue = MagicMock()
    payload = {
        "action": "created",
        "installation": {"id": 557, "account": {"login": "octocat"}},
    }
    await handle_installation_event("installation", payload, pool, "redis://unused", queue=fake_queue)

    assert fake_queue.enqueue.call_count == 2
    enqueued_repos = {call.kwargs["repo_full_name"] for call in fake_queue.enqueue.call_args_list}
    assert enqueued_repos == {"octocat/one", "octocat/two"}
    for call in fake_queue.enqueue.call_args_list:
        assert call.args[0] == "scan_worker.jobs.run_initial_scan_job"
        assert call.kwargs["installation_id"] == 557


@pytest.mark.asyncio
async def test_installation_created_does_not_crash_when_repo_enumeration_fails(pool, monkeypatch):
    async def _raise(installation_id):
        raise RuntimeError("GitHub API unavailable")

    monkeypatch.setattr(
        "app_server.webhooks.installation._fetch_all_installation_repo_full_names", _raise
    )
    fake_queue = MagicMock()
    payload = {
        "action": "created",
        "installation": {"id": 558, "account": {"login": "octocat"}},
    }
    await handle_installation_event("installation", payload, pool, "redis://unused", queue=fake_queue)

    row = await get_installation(pool, 558)
    assert row["account_login"] == "octocat"
    fake_queue.enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_installation_repositories_added_enqueues_initial_scan_for_new_repos_only(pool, monkeypatch):
    fake_queue = MagicMock()
    payload = {
        "action": "added",
        "installation": {"id": 559, "account": {"login": "someorg"}},
        "repositories_added": [{"full_name": "someorg/newly-added"}],
    }
    await handle_installation_event(
        "installation_repositories", payload, pool, "redis://unused", queue=fake_queue
    )

    fake_queue.enqueue.assert_called_once()
    args, kwargs = fake_queue.enqueue.call_args
    assert args[0] == "scan_worker.jobs.run_initial_scan_job"
    assert kwargs["installation_id"] == 559
    assert kwargs["repo_full_name"] == "someorg/newly-added"


@pytest.mark.asyncio
async def test_installation_repositories_removed_does_not_enqueue_a_scan(pool, monkeypatch):
    fake_queue = MagicMock()
    payload = {
        "action": "removed",
        "installation": {"id": 560, "account": {"login": "someorg"}},
        "repositories_removed": [{"full_name": "someorg/gone"}],
    }
    await handle_installation_event(
        "installation_repositories", payload, pool, "redis://unused", queue=fake_queue
    )

    fake_queue.enqueue.assert_not_called()
