import hashlib
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app_server.db import create_api_token, set_installation_plan, upsert_installation
from app_server.main import app


def _sentry_event():
    return {
        "exception": {
            "values": [
                {
                    "type": "ZeroDivisionError",
                    "value": "division by zero",
                    "stacktrace": {
                        "frames": [
                            {"filename": "app/handler.py", "function": "handle_request", "lineno": 42, "in_app": True}
                        ]
                    },
                }
            ]
        },
        "request": {"url": "https://api.example.com/v1/users", "method": "GET"},
    }


@pytest.mark.asyncio
async def test_report_runtime_event_requires_bearer_token(pool):
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/runtime-events", json={"repo_full_name": "octocat/widgets", "event": _sentry_event()}
        )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_report_runtime_event_rejects_free_plan(pool):
    await upsert_installation(pool, 200, "octocat")
    token_hash = hashlib.sha256(b"real-token").hexdigest()
    await create_api_token(pool, 200, token_hash, "laptop", "octocat")
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/runtime-events",
            json={"repo_full_name": "octocat/widgets", "event": _sentry_event()},
            headers={"Authorization": "Bearer real-token"},
        )
    assert response.status_code == 402


@pytest.mark.asyncio
async def test_report_runtime_event_rejects_unparseable_event(pool):
    await upsert_installation(pool, 201, "octocat")
    await set_installation_plan(pool, 201, "air")
    token_hash = hashlib.sha256(b"real-token").hexdigest()
    await create_api_token(pool, 201, token_hash, "laptop", "octocat")
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/runtime-events",
            json={"repo_full_name": "octocat/widgets", "event": {"exception": {"values": []}}},
            headers={"Authorization": "Bearer real-token"},
        )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_report_runtime_event_enqueues_job_with_parsed_fields(pool, monkeypatch):
    await upsert_installation(pool, 202, "octocat")
    await set_installation_plan(pool, 202, "air")
    token_hash = hashlib.sha256(b"real-token").hexdigest()
    await create_api_token(pool, 202, token_hash, "laptop", "octocat")

    fake_queue = MagicMock()
    fake_queue.enqueue.return_value = MagicMock(id="job-77")
    monkeypatch.setattr("app_server.runtime_events._get_queue", lambda redis_url: fake_queue)

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/runtime-events",
            json={"repo_full_name": "octocat/widgets", "event": _sentry_event()},
            headers={"Authorization": "Bearer real-token"},
        )

    assert response.status_code == 200
    assert response.json() == {"job_id": "job-77"}
    _, kwargs = fake_queue.enqueue.call_args
    assert kwargs["repo_full_name"] == "octocat/widgets"
    assert kwargs["installation_id"] == 202
    assert kwargs["exception_type"] == "ZeroDivisionError"
    assert kwargs["exception_value"] == "division by zero"
    assert kwargs["source_file"] == "app/handler.py"
    assert kwargs["source_line"] == 42
    assert kwargs["method"] == "GET"
    assert kwargs["path"] == "/v1/users"
