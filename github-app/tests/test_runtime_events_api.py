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


@pytest.mark.asyncio
async def test_runtime_event_rate_limit_check_is_offloaded_to_thread(pool, monkeypatch):
    # Real regression this guards: is_rate_limited uses the synchronous
    # redis-py client and blocks on pipe.execute() - called directly inside
    # an async def handler, each check stalls the whole event loop (every
    # other concurrent request on this worker) for its full duration. See
    # embeddings_api.py's identical test for the pattern this was copied
    # from (#328's own original fix).
    from unittest.mock import AsyncMock, patch

    import app_server.runtime_events as runtime_events_module

    await upsert_installation(pool, 203, "octocat")
    await set_installation_plan(pool, 203, "air")
    token_hash = hashlib.sha256(b"real-token").hexdigest()
    await create_api_token(pool, 203, token_hash, "laptop", "octocat")

    fake_queue = MagicMock()
    fake_queue.enqueue.return_value = MagicMock(id="job-78")
    monkeypatch.setattr("app_server.runtime_events._get_queue", lambda redis_url: fake_queue)

    offloaded_funcs = []

    async def _dispatch(func, *args, **kwargs):
        offloaded_funcs.append(func)
        return func(*args, **kwargs)

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    with patch.object(runtime_events_module.asyncio, "to_thread", AsyncMock(side_effect=_dispatch)):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/runtime-events",
                json={"repo_full_name": "octocat/widgets", "event": _sentry_event()},
                headers={"Authorization": "Bearer real-token"},
            )

    assert response.status_code == 200
    assert runtime_events_module.is_rate_limited in offloaded_funcs
