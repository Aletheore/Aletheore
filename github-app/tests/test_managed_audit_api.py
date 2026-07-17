import hashlib
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app_server.db import create_api_token, set_installation_plan, upsert_installation
from app_server.main import app


@pytest.mark.asyncio
async def test_managed_audit_requires_bearer_token(pool):
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/v1/managed-audit", json={"evidence": "toon-text"})
    assert response.status_code == 401


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
            json={"evidence": "toon-text"},
            headers={"Authorization": "Bearer real-token"},
        )
    assert response.status_code == 402


@pytest.mark.asyncio
async def test_managed_audit_enqueues_job_for_paid_token(pool, monkeypatch):
    await upsert_installation(pool, 100, "octocat")
    await set_installation_plan(pool, 100, "pro")
    token_hash = hashlib.sha256(b"real-token").hexdigest()
    await create_api_token(pool, 100, token_hash, "laptop", "octocat")
    fake_job = MagicMock(id="job-123")
    fake_queue = MagicMock()
    fake_queue.enqueue.return_value = fake_job
    monkeypatch.setattr("app_server.managed_audit_api._get_queue", lambda redis_url: fake_queue)

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/managed-audit",
            json={"evidence": "toon-text"},
            headers={"Authorization": "Bearer real-token"},
        )

    assert response.status_code == 202
    assert response.json()["job_id"] == "job-123"
    args, kwargs = fake_queue.enqueue.call_args
    assert args[0] == "scan_worker.jobs.run_managed_audit_api_job"
    assert kwargs["evidence"] == "toon-text"


@pytest.mark.asyncio
async def test_get_job_status_returns_result_when_finished(monkeypatch):
    fake_job = MagicMock(is_finished=True, is_failed=False, result="# Report")
    monkeypatch.setattr("app_server.managed_audit_api._fetch_job", lambda job_id, redis_url: fake_job)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/managed-audit/job-123")

    assert response.status_code == 200
    assert response.json() == {"status": "finished", "result": "# Report"}
