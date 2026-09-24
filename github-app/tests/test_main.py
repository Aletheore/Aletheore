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
async def test_webhook_rejects_non_ascii_signature_without_500():
    app.state.db_pool = object()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/webhook",
            content=b"{}",
            headers=[
                (b"X-Hub-Signature-256", b"sha256=caf\xe9"),
                (b"X-GitHub-Event", b"installation"),
            ],
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

    async def fake_handle(payload_arg, pool_arg, redis_url):
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

    async def fake_handle(payload_arg, pool_arg, redis_url):
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
async def test_unexpected_exception_alert_key_uses_route_template_not_instantiated_url(monkeypatch):
    # Real gap found in a backward-audit of #734: the dedup key passed to
    # send_error_alert used request.url.path (the fully-instantiated URL,
    # e.g. "/dashboard/acme/widgets"), not the matched route's template
    # (e.g. "/dashboard/{org}/{repo}"). error_alerts.py's dedup store is a
    # plain, never-evicted, process-lifetime dict keyed by this exact
    # string - several real routes here take path params (org/repo,
    # job_id, verification_token, {file_path:path}), so keying by the
    # instantiated URL would mint one new permanent dict entry per
    # distinct org/repo/job/file that ever errors: an unbounded leak for
    # the life of the process, not the single bounded entry per route the
    # fix intended. Two requests to the SAME route template with
    # DIFFERENT instantiated URLs must collapse to the same alert key.
    from starlette.requests import Request
    from starlette.routing import Route

    from app_server.main import handle_unexpected_exception

    alerts = []
    monkeypatch.setattr("app_server.main.send_error_alert", lambda *a, **k: alerts.append(a[0]))

    route = Route("/dashboard/{org}/{repo}", endpoint=lambda request: None)
    for org, repo in [("acme", "widgets"), ("other-org", "other-repo")]:
        scope = {
            "type": "http",
            "method": "GET",
            "path": f"/dashboard/{org}/{repo}",
            "headers": [],
            "route": route,
        }
        request = Request(scope)
        await handle_unexpected_exception(request, RuntimeError("boom"))

    assert alerts == ["app_server:/dashboard/{org}/{repo}"] * 2


@pytest.mark.asyncio
async def test_unexpected_exception_alert_key_falls_back_to_url_path_with_no_matched_route(monkeypatch):
    # Defensive fallback: a request that somehow reaches this handler with
    # no route on the scope still gets a usable (if unbounded) key rather
    # than crashing on a None-valued f-string segment.
    from starlette.requests import Request

    from app_server.main import handle_unexpected_exception

    alerts = []
    monkeypatch.setattr("app_server.main.send_error_alert", lambda *a, **k: alerts.append(a[0]))

    scope = {"type": "http", "method": "GET", "path": "/no-matched-route", "headers": []}
    request = Request(scope)
    await handle_unexpected_exception(request, RuntimeError("boom"))

    assert alerts == ["app_server:/no-matched-route"]


@pytest.mark.asyncio
async def test_webhook_crash_records_durable_5xx_counter_and_scopes_alert_source(monkeypatch, pool):
    """Real incident (2026-09-18): a /webhook crash produced zero visible
    signal anywhere. Two things must now happen when webhook handling
    raises: the durable Redis counter ops_monitor reads gets incremented
    (record_webhook_5xx), and the crash-alert source is scoped by path so
    an unrelated exception elsewhere in app_server can't share its dedup
    cooldown and suppress this one (see error_alerts.py's (source,
    exception type) keying)."""
    app.state.db_pool = pool
    payload = {
        "action": "opened",
        "number": 9,
        "installation": {"id": 123},
        "repository": {"full_name": "octocat/hello-world"},
        "pull_request": {"base": {"sha": "aaa"}, "head": {"sha": "bbb"}},
    }
    body = json.dumps(payload).encode()

    async def fake_handle(payload_arg, pool_arg, redis_url):
        raise RuntimeError("boom")

    monkeypatch.setattr("app_server.webhooks.pull_request.handle_pull_request_event", fake_handle)

    recorded = []
    monkeypatch.setattr(
        "app_server.redis_client.record_webhook_5xx", lambda redis_conn: recorded.append(redis_conn)
    )
    alerts = []
    monkeypatch.setattr("app_server.main.send_error_alert", lambda *a, **k: alerts.append((a, k)))

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/webhook",
            content=body,
            headers={
                "X-Hub-Signature-256": _signature(body, settings.github_webhook_secret),
                "X-GitHub-Event": "pull_request",
                "X-GitHub-Delivery": "delivery-crash-1",
            },
        )

    assert response.status_code == 500
    assert len(recorded) == 1
    assert len(alerts) == 1
    assert alerts[0][0][0] == "app_server:/webhook"


@pytest.mark.asyncio
async def test_successful_webhook_does_not_touch_5xx_counter(monkeypatch, pool):
    app.state.db_pool = pool
    payload = {
        "action": "opened",
        "number": 9,
        "installation": {"id": 123},
        "repository": {"full_name": "octocat/hello-world"},
        "pull_request": {"base": {"sha": "aaa"}, "head": {"sha": "bbb"}},
    }
    body = json.dumps(payload).encode()

    async def fake_handle(payload_arg, pool_arg, redis_url):
        pass

    monkeypatch.setattr("app_server.webhooks.pull_request.handle_pull_request_event", fake_handle)
    recorded = []
    monkeypatch.setattr(
        "app_server.redis_client.record_webhook_5xx", lambda redis_conn: recorded.append(redis_conn)
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/webhook",
            content=body,
            headers={
                "X-Hub-Signature-256": _signature(body, settings.github_webhook_secret),
                "X-GitHub-Event": "pull_request",
                "X-GitHub-Delivery": "delivery-ok-1",
            },
        )

    assert response.status_code == 200
    assert recorded == []


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


@pytest.mark.asyncio
async def test_client_disconnect_is_not_a_bug_alert_or_a_webhook_5xx(monkeypatch):
    """Seen in production 2026-09-24: GitHub hung up mid-delivery, which was
    emailed as an app_server bug and counted as a /webhook 5xx, and that one
    event kept ops_monitor.webhook_5xx alerting 15 minutes later. A caller
    hanging up is neither."""
    from starlette.requests import ClientDisconnect, Request

    from app_server.main import handle_unexpected_exception

    alerts = []
    counted = []
    monkeypatch.setattr("app_server.main.send_error_alert", lambda *a, **k: alerts.append(a))
    monkeypatch.setattr("app_server.redis_client.record_webhook_5xx", lambda conn: counted.append(1))
    monkeypatch.setattr("app_server.redis_client.get_redis_client", lambda: object())

    scope = {"type": "http", "method": "POST", "path": "/webhook", "headers": []}
    response = await handle_unexpected_exception(Request(scope), ClientDisconnect())

    assert alerts == []
    assert counted == []
    assert response.status_code == 499


@pytest.mark.asyncio
async def test_other_webhook_exceptions_still_alert_and_count(monkeypatch):
    from starlette.requests import Request

    from app_server.main import handle_unexpected_exception

    alerts = []
    counted = []
    monkeypatch.setattr("app_server.main.send_error_alert", lambda *a, **k: alerts.append(a))
    monkeypatch.setattr("app_server.redis_client.record_webhook_5xx", lambda conn: counted.append(1))
    monkeypatch.setattr("app_server.redis_client.get_redis_client", lambda: object())

    scope = {"type": "http", "method": "POST", "path": "/webhook", "headers": []}
    response = await handle_unexpected_exception(Request(scope), RuntimeError("boom"))

    assert len(alerts) == 1
    assert counted == [1]
    assert response.status_code == 500
