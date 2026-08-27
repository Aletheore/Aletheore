from unittest.mock import MagicMock

import httpx
import pytest

from app_server.db import get_installation, hide_repo, is_repo_hidden, upsert_installation
from app_server.webhooks.installation import _fetch_installation_repos_sync, handle_installation_event


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


def test_fetch_installation_repos_uses_pooled_github_client(monkeypatch):
    client = MagicMock()
    response = MagicMock()
    response.json.return_value = {"repositories": [{"full_name": "octocat/one"}]}
    client.get.return_value = response
    monkeypatch.setattr("app_server.webhooks.installation.get_installation_token", lambda *a, **k: "tok")
    factory = MagicMock(return_value=client)
    monkeypatch.setattr("app_server.webhooks.installation.get_github_api_client", factory)

    repos = _fetch_installation_repos_sync(123, "jwt")

    assert repos == ["octocat/one"]
    factory.assert_called_once_with()
    client.get.assert_called_once()
    assert client.get.call_args.args == ("/installation/repositories",)


def test_fetch_installation_repos_collects_paginated_results(monkeypatch):
    repos = [{"full_name": f"octocat/repo-{i}"} for i in range(101)]
    seen_params = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_params.append(dict(request.url.params))
        page = int(request.url.params["page"])
        assert request.url.params["per_page"] == "100"
        start = (page - 1) * 100
        return httpx.Response(
            200,
            json={"total_count": len(repos), "repositories": repos[start:start + 100]},
            request=request,
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    monkeypatch.setattr("app_server.webhooks.installation.get_installation_token", lambda *a, **k: "tok")
    monkeypatch.setattr("app_server.webhooks.installation.get_github_api_client", lambda: client)

    fetched = _fetch_installation_repos_sync(123, "jwt")

    assert fetched == [repo["full_name"] for repo in repos]
    assert seen_params == [{"per_page": "100", "page": "1"}, {"per_page": "100", "page": "2"}]


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


@pytest.mark.asyncio
async def test_installation_repositories_removed_hides_the_repo(pool, monkeypatch):
    payload = {
        "action": "removed",
        "installation": {"id": 561, "account": {"login": "someorg"}},
        "repositories_removed": [{"full_name": "someorg/gone"}],
    }
    await handle_installation_event(
        "installation_repositories", payload, pool, "redis://unused", queue=MagicMock()
    )

    assert await is_repo_hidden(pool, 561, "someorg/gone") is True


@pytest.mark.asyncio
async def test_installation_repositories_removed_creates_the_installation_row_first(pool):
    # hidden_repos.installation_id has a foreign key on installations - a
    # "removed" event must never arrive before this installation has any
    # row of its own (a bare INSERT would otherwise raise a constraint
    # violation instead of hiding the repo).
    payload = {
        "action": "removed",
        "installation": {"id": 562, "account": {"login": "someorg"}},
        "repositories_removed": [{"full_name": "someorg/gone"}],
    }
    await handle_installation_event(
        "installation_repositories", payload, pool, "redis://unused", queue=MagicMock()
    )

    assert await get_installation(pool, 562) is not None


@pytest.mark.asyncio
async def test_installation_repositories_added_unhides_a_previously_removed_repo(pool):
    await upsert_installation(pool, 563, "someorg")
    await hide_repo(pool, 563, "someorg/back-again")
    fake_queue = MagicMock()
    payload = {
        "action": "added",
        "installation": {"id": 563, "account": {"login": "someorg"}},
        "repositories_added": [{"full_name": "someorg/back-again"}],
    }
    await handle_installation_event(
        "installation_repositories", payload, pool, "redis://unused", queue=fake_queue
    )

    assert await is_repo_hidden(pool, 563, "someorg/back-again") is False
    fake_queue.enqueue.assert_called_once()
