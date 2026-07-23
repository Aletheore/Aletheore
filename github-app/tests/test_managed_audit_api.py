import hashlib
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app_server.db import create_api_token, set_installation_plan, upsert_installation
from app_server.evidence_limits import MAX_EVIDENCE_BYTES
from app_server.main import app
from aletheore.toon_encoding import to_toon


def _evidence_toon(total_loc: int = 100) -> str:
    return to_toon({"repository": {"languages": [{"name": "Python", "files": 1, "lines": total_loc}]}})


@pytest.mark.asyncio
async def test_managed_audit_requires_bearer_token(pool):
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/managed-audit", json={"evidence": _evidence_toon(), "repo_full_name": "octocat/widgets"}
        )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_managed_audit_returns_422_for_oversized_evidence(pool):
    await upsert_installation(pool, 100, "octocat")
    await set_installation_plan(pool, 100, "indie")
    token_hash = hashlib.sha256(b"real-token").hexdigest()
    await create_api_token(pool, 100, token_hash, "laptop", "octocat")
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    oversized_evidence = "x" * (MAX_EVIDENCE_BYTES + 1)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/managed-audit",
            json={"evidence": oversized_evidence, "repo_full_name": "octocat/widgets"},
            headers={"Authorization": "Bearer real-token"},
        )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_managed_audit_rejects_free_plan(pool):
    await upsert_installation(pool, 100, "octocat")
    token_hash = hashlib.sha256(b"real-token").hexdigest()
    await create_api_token(pool, 100, token_hash, "laptop", "octocat")
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/managed-audit",
            json={"evidence": _evidence_toon(), "repo_full_name": "octocat/widgets"},
            headers={"Authorization": "Bearer real-token"},
        )
    assert response.status_code == 402


@pytest.mark.asyncio
async def test_managed_audit_returns_422_for_missing_evidence(pool):
    await upsert_installation(pool, 100, "octocat")
    await set_installation_plan(pool, 100, "indie")
    token_hash = hashlib.sha256(b"real-token").hexdigest()
    await create_api_token(pool, 100, token_hash, "laptop", "octocat")
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/managed-audit",
            json={"repo_full_name": "octocat/widgets"},
            headers={"Authorization": "Bearer real-token"},
        )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_managed_audit_requires_repo_full_name(pool):
    await upsert_installation(pool, 100, "octocat")
    await set_installation_plan(pool, 100, "indie")
    token_hash = hashlib.sha256(b"real-token").hexdigest()
    await create_api_token(pool, 100, token_hash, "laptop", "octocat")
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/managed-audit",
            json={"evidence": _evidence_toon()},
            headers={"Authorization": "Bearer real-token"},
        )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_managed_audit_enqueues_job_for_paid_token(pool, monkeypatch):
    await upsert_installation(pool, 100, "octocat")
    await set_installation_plan(pool, 100, "indie")
    token_hash = hashlib.sha256(b"real-token").hexdigest()
    await create_api_token(pool, 100, token_hash, "laptop", "octocat")
    fake_job = MagicMock(id="job-123")
    fake_queue = MagicMock()
    fake_queue.enqueue.return_value = fake_job
    monkeypatch.setattr("app_server.managed_audit_api._get_queue", lambda redis_url: fake_queue)

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    evidence_toon = _evidence_toon()
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/managed-audit",
            json={"evidence": evidence_toon, "repo_full_name": "octocat/widgets"},
            headers={"Authorization": "Bearer real-token"},
        )

    assert response.status_code == 202
    assert response.json()["job_id"] == "job-123"
    args, kwargs = fake_queue.enqueue.call_args
    assert args[0] == "scan_worker.jobs.run_managed_audit_api_job"
    assert kwargs["evidence"] == evidence_toon
    assert kwargs["installation_id"] == 100
    assert kwargs["job_timeout"] >= 600


@pytest.mark.asyncio
async def test_managed_audit_blocks_second_request_within_cooldown(pool, monkeypatch):
    await upsert_installation(pool, 100, "octocat")
    await set_installation_plan(pool, 100, "indie")
    token_hash = hashlib.sha256(b"real-token").hexdigest()
    await create_api_token(pool, 100, token_hash, "laptop", "octocat")
    fake_queue = MagicMock()
    fake_queue.enqueue.return_value = MagicMock(id="job-123")
    monkeypatch.setattr("app_server.managed_audit_api._get_queue", lambda redis_url: fake_queue)

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    body = {"evidence": _evidence_toon(), "repo_full_name": "octocat/widgets"}
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post(
            "/v1/managed-audit", json=body, headers={"Authorization": "Bearer real-token"}
        )
        second = await client.post(
            "/v1/managed-audit", json=body, headers={"Authorization": "Bearer real-token"}
        )

    assert first.status_code == 202
    assert second.status_code == 429


@pytest.mark.asyncio
async def test_managed_audit_rate_limit_is_independent_per_repo(pool, monkeypatch):
    await upsert_installation(pool, 100, "octocat")
    await set_installation_plan(pool, 100, "indie")
    token_hash = hashlib.sha256(b"real-token").hexdigest()
    await create_api_token(pool, 100, token_hash, "laptop", "octocat")
    fake_queue = MagicMock()
    fake_queue.enqueue.return_value = MagicMock(id="job-123")
    monkeypatch.setattr("app_server.managed_audit_api._get_queue", lambda redis_url: fake_queue)

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post(
            "/v1/managed-audit",
            json={"evidence": _evidence_toon(), "repo_full_name": "octocat/widgets"},
            headers={"Authorization": "Bearer real-token"},
        )
        second = await client.post(
            "/v1/managed-audit",
            json={"evidence": _evidence_toon(), "repo_full_name": "octocat/gizmos"},
            headers={"Authorization": "Bearer real-token"},
        )

    assert first.status_code == 202
    assert second.status_code == 202


@pytest.mark.asyncio
async def test_get_job_status_requires_bearer_token(pool):
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/managed-audit/job-123")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_get_job_status_returns_result_when_finished(pool, monkeypatch):
    await upsert_installation(pool, 100, "octocat")
    await set_installation_plan(pool, 100, "indie")
    token_hash = hashlib.sha256(b"real-token").hexdigest()
    await create_api_token(pool, 100, token_hash, "laptop", "octocat")

    fake_job = MagicMock(is_finished=True, is_failed=False, result="# Report", kwargs={"installation_id": 100})
    monkeypatch.setattr("app_server.managed_audit_api._fetch_job", lambda job_id, redis_url: fake_job)

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/v1/managed-audit/job-123", headers={"Authorization": "Bearer real-token"}
        )

    assert response.status_code == 200
    assert response.json() == {"status": "finished", "result": "# Report"}


@pytest.mark.asyncio
async def test_get_job_status_rejects_job_belonging_to_another_installation(pool, monkeypatch):
    await upsert_installation(pool, 100, "octocat")
    await set_installation_plan(pool, 100, "indie")
    token_hash = hashlib.sha256(b"real-token").hexdigest()
    await create_api_token(pool, 100, token_hash, "laptop", "octocat")

    # This job was enqueued for a different installation (999) - a valid
    # token for installation 100 must not be able to read its result.
    fake_job = MagicMock(is_finished=True, is_failed=False, result="# Report", kwargs={"installation_id": 999})
    monkeypatch.setattr("app_server.managed_audit_api._fetch_job", lambda job_id, redis_url: fake_job)

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/v1/managed-audit/other-installations-job", headers={"Authorization": "Bearer real-token"}
        )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_job_status_returns_404_for_unknown_job(pool, monkeypatch):
    from rq.exceptions import NoSuchJobError

    await upsert_installation(pool, 100, "octocat")
    await set_installation_plan(pool, 100, "indie")
    token_hash = hashlib.sha256(b"real-token").hexdigest()
    await create_api_token(pool, 100, token_hash, "laptop", "octocat")

    def _raise(job_id, redis_url):
        raise NoSuchJobError(job_id)

    monkeypatch.setattr("app_server.managed_audit_api._fetch_job", _raise)

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/v1/managed-audit/no-such-job", headers={"Authorization": "Bearer real-token"}
        )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_whoami_requires_bearer_token(pool):
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/whoami")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_whoami_rejects_unknown_token(pool):
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/v1/whoami", headers={"Authorization": "Bearer no-such-token"}
        )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_whoami_returns_account_login_and_plan_for_valid_token(pool):
    await upsert_installation(pool, 100, "acme")
    await set_installation_plan(pool, 100, "indie")
    token_hash = hashlib.sha256(b"real-token").hexdigest()
    await create_api_token(pool, 100, token_hash, "laptop", "acme")

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/v1/whoami", headers={"Authorization": "Bearer real-token"}
        )

    assert response.status_code == 200
    assert response.json() == {"account_login": "acme", "plan": "indie"}
