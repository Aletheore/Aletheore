import json

import pytest
from httpx import ASGITransport, AsyncClient

from app_server.ingest_limits import (
    MANAGED_AUDIT_MAX_BODY_BYTES,
    RUNTIME_EVENT_MAX_BODY_BYTES,
    BodyTooLargeError,
    MissingContentLengthError,
    check_declared_body_size,
)
from app_server.main import app


@pytest.fixture(autouse=True)
def _no_rate_limit(monkeypatch):
    """Default to rate limiting out of the way.

    Every test that isn't about the limiter needs it disabled, and the tests
    that *are* about it override this explicitly - that way a limiter bug can
    never silently make an unrelated test pass by rejecting the request
    before it reaches the thing under test.
    """
    monkeypatch.setattr("app_server.runtime_events.is_rate_limited", lambda *a, **k: False)


# --- Body-size caps ----------------------------------------------------------


def test_oversized_declared_body_is_rejected():
    with pytest.raises(BodyTooLargeError):
        check_declared_body_size("/v1/runtime-events", "POST", str(RUNTIME_EVENT_MAX_BODY_BYTES + 1))


def test_a_body_at_the_limit_is_allowed():
    check_declared_body_size("/v1/runtime-events", "POST", str(RUNTIME_EVENT_MAX_BODY_BYTES))


def test_missing_content_length_is_refused_not_waved_through():
    # Without a declared size the cap is unenforceable before reading, which
    # is the whole hole. Both real clients always send one.
    with pytest.raises(MissingContentLengthError):
        check_declared_body_size("/v1/runtime-events", "POST", None)


def test_a_non_numeric_content_length_is_refused():
    with pytest.raises(MissingContentLengthError):
        check_declared_body_size("/v1/runtime-events", "POST", "not-a-number")


def test_managed_audit_has_a_pre_routing_body_cap():
    check_declared_body_size(
        "/v1/managed-audit", "POST", str(MANAGED_AUDIT_MAX_BODY_BYTES)
    )
    with pytest.raises(BodyTooLargeError):
        check_declared_body_size(
            "/v1/managed-audit", "POST", str(MANAGED_AUDIT_MAX_BODY_BYTES + 1)
        )


def test_get_requests_are_untouched():
    check_declared_body_size("/v1/runtime-events", "GET", None)


# --- Runtime events: the queue is the resource being protected ---------------


def _sentry_event() -> dict:
    return {
        "exception": {
            "values": [
                {
                    "type": "ZeroDivisionError",
                    "value": "division by zero",
                    "stacktrace": {
                        "frames": [
                            {
                                "filename": "app/handler.py",
                                "function": "handle_request",
                                "lineno": 42,
                                "in_app": True,
                            }
                        ]
                    },
                }
            ]
        },
        "request": {"url": "https://api.example.com/v1/users", "method": "GET"},
    }


async def _paid_installation_with_token(pool, installation_id: int = 700) -> str:
    import hashlib

    from app_server.db import create_api_token, set_installation_plan, upsert_installation

    await upsert_installation(pool, installation_id, "octocat")
    await set_installation_plan(pool, installation_id, "air")
    token = f"runtime-token-{installation_id}"
    await create_api_token(
        pool, installation_id, hashlib.sha256(token.encode()).hexdigest(), "laptop", "octocat"
    )
    return token


@pytest.mark.asyncio
async def test_runtime_events_returns_429_and_enqueues_nothing_when_limited(pool, monkeypatch):
    # Each accepted event enqueues onto "scans" - the same queue as Flash
    # reviews, AIRview builds, and managed audits. A rate-limited request
    # that still enqueued would defeat the entire point.
    app.state.db_pool = pool
    token = await _paid_installation_with_token(pool, 700)
    monkeypatch.setattr("app_server.runtime_events.is_rate_limited", lambda *a, **k: True)
    enqueued = []
    monkeypatch.setattr(
        "app_server.runtime_events._get_queue",
        lambda _url: type("Q", (), {"enqueue": lambda _s, *a, **k: enqueued.append(a)})(),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/runtime-events",
            json={"repo_full_name": "octocat/widgets", "event": _sentry_event()},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 429
    assert enqueued == [], "a rate-limited request still enqueued a job"


@pytest.mark.asyncio
async def test_runtime_events_rate_limit_is_keyed_by_installation(pool, monkeypatch):
    # Installation, not IP: it is the unit that owns the queue pressure, and
    # the only identity an authenticated caller cannot change at will.
    app.state.db_pool = pool
    token = await _paid_installation_with_token(pool, 701)
    keys = []
    monkeypatch.setattr(
        "app_server.runtime_events.is_rate_limited",
        lambda _conn, key, _limit, _window: keys.append(key) or False,
    )
    monkeypatch.setattr(
        "app_server.runtime_events._get_queue",
        lambda _url: type("Q", (), {"enqueue": lambda _s, *a, **k: type("J", (), {"id": "j1"})()})(),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/runtime-events",
            json={"repo_full_name": "octocat/widgets", "event": _sentry_event()},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200, response.text
    assert keys == ["ratelimit:runtime-events:701"]


@pytest.mark.asyncio
async def test_runtime_events_rejects_unknown_top_level_fields(pool):
    app.state.db_pool = pool
    token = await _paid_installation_with_token(pool, 702)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/runtime-events",
            json={
                "repo_full_name": "octocat/widgets",
                "event": _sentry_event(),
                "smuggled": "x",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_runtime_events_rejects_an_overlong_repo_name(pool):
    app.state.db_pool = pool
    token = await _paid_installation_with_token(pool, 703)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/runtime-events",
            json={"repo_full_name": "o" * 300, "event": _sentry_event()},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_runtime_events_rate_limit_runs_after_the_paid_plan_gate(pool, monkeypatch):
    # A free-plan caller should get the 402 that explains the problem, not a
    # 429 that misattributes it - and should not consume a rate-limit slot.
    from app_server.db import create_api_token, upsert_installation

    import hashlib

    app.state.db_pool = pool
    await upsert_installation(pool, 704, "octocat")
    token = "free-plan-token"
    await create_api_token(
        pool, 704, hashlib.sha256(token.encode()).hexdigest(), "laptop", "octocat"
    )
    calls = []
    monkeypatch.setattr(
        "app_server.runtime_events.is_rate_limited", lambda *a, **k: calls.append(a) or False
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/runtime-events",
            json={"repo_full_name": "octocat/widgets", "event": _sentry_event()},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 402
    assert calls == []


@pytest.mark.asyncio
async def test_a_length_less_request_is_refused_end_to_end(pool):
    """The 411 path, exercised for real.

    A generator body makes httpx use chunked transfer encoding with no
    Content-Length - the one shape that would otherwise slip past a cap that
    can only read a declared size. Passing bytes here does not test this:
    httpx computes a Content-Length for those and the request sails through.
    """
    app.state.db_pool = pool

    async def _chunks():
        yield b'{"repo_full_name":"octocat/widgets"}'

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/runtime-events", content=_chunks(), headers={"Content-Type": "application/json"}
        )

    assert response.status_code == 411
    assert "content-length" in response.json()["detail"]


@pytest.mark.asyncio
async def test_the_oversized_and_length_less_paths_return_distinct_codes(pool):
    # 413 and 411 mean different things to a client: "shrink your payload" vs
    # "declare its size". Collapsing them into one code would mislead.
    app.state.db_pool = pool

    async def _chunks():
        yield b'{"repo_full_name":"octocat/widgets"}'

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        length_less = await client.post(
            "/v1/runtime-events", content=_chunks(), headers={"Content-Type": "application/json"}
        )
        oversized = await client.post(
            "/v1/runtime-events",
            content=json.dumps({"repo_full_name": "octocat/widgets", "pad": "p" * (RUNTIME_EVENT_MAX_BODY_BYTES + 1000)}).encode(),
            headers={"Content-Type": "application/json"},
        )

    assert (length_less.status_code, oversized.status_code) == (411, 413)
