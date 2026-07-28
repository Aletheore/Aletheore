import hashlib
import hmac
import json
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app_server.main import app, settings


def _signature(payload: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


@pytest.mark.asyncio
async def test_webhook_rejects_invalid_signature():
    app.state.db_pool = object()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/webhook",
            content=b"{}",
            headers={
                "X-Hub-Signature-256": "sha256=wrong",
                "X-GitHub-Event": "installation",
            },
        )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_webhook_dispatches_pull_request_enqueue(monkeypatch):
    app.state.db_pool = object()
    payload = {
        "action": "opened",
        "number": 9,
        "installation": {"id": 123},
        "repository": {"full_name": "octocat/hello-world"},
        "pull_request": {"base": {"sha": "aaa"}, "head": {"sha": "bbb"}},
    }
    body = json.dumps(payload).encode()
    called = {}

    async def fake_handle(payload_arg, redis_url):
        called["payload"] = payload_arg
        called["redis_url"] = redis_url

    monkeypatch.setattr("app_server.webhooks.pull_request.handle_pull_request_event", fake_handle)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/webhook",
            content=body,
            headers={
                "X-Hub-Signature-256": _signature(body, settings.github_webhook_secret),
                "X-GitHub-Event": "pull_request",
            },
        )

    assert response.status_code == 200
    assert called["payload"]["number"] == 9
    assert called["redis_url"] == settings.redis_url


@pytest.mark.asyncio
async def test_webhook_dispatches_push_enqueue(monkeypatch):
    app.state.db_pool = object()
    payload = {
        "ref": "refs/heads/main",
        "after": "def456",
        "installation": {"id": 123},
        "repository": {"full_name": "octocat/hello-world", "default_branch": "main"},
        "commits": [],
    }
    body = json.dumps(payload).encode()
    called = {}

    async def fake_handle(payload_arg, redis_url):
        called["payload"] = payload_arg
        called["redis_url"] = redis_url

    monkeypatch.setattr("app_server.webhooks.push.handle_push_event", fake_handle)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/webhook",
            content=body,
            headers={
                "X-Hub-Signature-256": _signature(body, settings.github_webhook_secret),
                "X-GitHub-Event": "push",
            },
        )

    assert response.status_code == 200
    assert called["payload"]["after"] == "def456"
    assert called["redis_url"] == settings.redis_url


@pytest.mark.asyncio
async def test_healthz_returns_200_when_dependencies_are_healthy(monkeypatch):
    app.state.db_pool = MagicMock()
    app.state.db_pool.fetchval = AsyncMock(return_value=1)
    fake_redis = MagicMock()
    monkeypatch.setattr("redis.Redis.from_url", lambda url: fake_redis)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": "ok", "redis": "ok"}}


@pytest.mark.asyncio
async def test_healthz_returns_503_when_database_is_unreachable(monkeypatch):
    app.state.db_pool = MagicMock()
    app.state.db_pool.fetchval = AsyncMock(side_effect=Exception("connection refused"))
    fake_redis = MagicMock()
    monkeypatch.setattr("redis.Redis.from_url", lambda url: fake_redis)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/healthz")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "error"
    assert body["checks"]["database"] == "error"
    assert body["checks"]["redis"] == "ok"


@pytest.mark.asyncio
async def test_healthz_returns_503_when_redis_is_unreachable(monkeypatch):
    app.state.db_pool = MagicMock()
    app.state.db_pool.fetchval = AsyncMock(return_value=1)

    def _raise(url):
        raise ConnectionError("redis unreachable")

    monkeypatch.setattr("redis.Redis.from_url", _raise)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/healthz")

    assert response.status_code == 503
    body = response.json()
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["redis"] == "error"


@pytest.mark.asyncio
async def test_request_logging_middleware_adds_request_id_header():
    app.state.db_pool = object()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/whoami")
    assert "X-Request-ID" in response.headers
    assert len(response.headers["X-Request-ID"]) > 10


@pytest.mark.asyncio
async def test_request_logging_middleware_logs_structured_fields(caplog):
    app.state.db_pool = object()
    transport = ASGITransport(app=app)
    with caplog.at_level(logging.INFO, logger="app_server.access"):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/v1/whoami")

    record = next(r for r in caplog.records if r.message == "request completed")
    assert record.method == "GET"
    assert record.path == "/v1/whoami"
    assert record.status_code == response.status_code
    assert record.duration_ms >= 0
    assert record.request_id == response.headers["X-Request-ID"]
