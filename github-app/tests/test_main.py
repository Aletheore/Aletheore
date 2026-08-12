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
async def test_webhook_dispatches_pull_request_enqueue(monkeypatch, pool):
    app.state.db_pool = pool
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
                "X-GitHub-Delivery": "delivery-pr-1",
            },
        )

    assert response.status_code == 200
    assert called["payload"]["number"] == 9
    assert called["redis_url"] == settings.redis_url


@pytest.mark.asyncio
async def test_webhook_dispatches_push_enqueue(monkeypatch, pool):
    app.state.db_pool = pool
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
                "X-GitHub-Delivery": "delivery-push-1",
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
    monkeypatch.setattr("app_server.redis_client.get_redis_client", lambda: fake_redis)

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
    monkeypatch.setattr("app_server.redis_client.get_redis_client", lambda: fake_redis)

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

    def _raise():
        raise ConnectionError("redis unreachable")

    monkeypatch.setattr("app_server.redis_client.get_redis_client", _raise)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/healthz")

    assert response.status_code == 503
    body = response.json()
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["redis"] == "error"


@pytest.mark.asyncio
async def test_malformed_json_body_with_valid_signature_returns_401_not_500(monkeypatch):
    app.state.db_pool = object()
    body = b"not valid json"

    calls = []
    monkeypatch.setattr("app_server.main.send_error_alert", lambda *a, **k: calls.append((a, k)))

    # raise_app_exceptions=False: httpx's ASGITransport otherwise re-raises
    # any exception the app handled internally, defeating the point of
    # this test (verifying a caught exception still produces a real HTTP
    # response, not that it propagates - that's what the other tests are
    # for).
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/webhook",
            content=body,
            headers={
                "X-Hub-Signature-256": _signature(body, settings.github_webhook_secret),
                "X-GitHub-Event": "installation",
                # Present so the request gets past the delivery-header check
                # and fails where this test intends: parsing the body. The
                # pool stub is never touched, since parsing precedes the
                # delivery claim.
                "X-GitHub-Delivery": "delivery-malformed-1",
            },
        )

    assert response.status_code == 401
    assert calls == []


@pytest.mark.asyncio
async def test_non_object_json_body_with_valid_signature_returns_401_not_500(monkeypatch):
    app.state.db_pool = object()
    body = b"[1, 2, 3]"

    calls = []
    monkeypatch.setattr("app_server.main.send_error_alert", lambda *a, **k: calls.append((a, k)))

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/webhook",
            content=body,
            headers={
                "X-Hub-Signature-256": _signature(body, settings.github_webhook_secret),
                "X-GitHub-Event": "installation",
                "X-GitHub-Delivery": "delivery-non-object-1",
            },
        )

    assert response.status_code == 401
    assert calls == []


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
