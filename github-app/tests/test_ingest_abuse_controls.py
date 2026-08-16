import json
import os
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from app_server.ingest_limits import (
    MANAGED_AUDIT_MAX_BODY_BYTES,
    RUNTIME_EVENT_MAX_BODY_BYTES,
    TELEMETRY_MAX_BODY_BYTES,
    BodyTooLargeError,
    MissingContentLengthError,
    check_declared_body_size,
)
from app_server.main import app
from scan_worker.db import delete_expired_telemetry_events

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://postgres:test@localhost:55433/aletheore_test",
)


@pytest.fixture(autouse=True)
def _no_rate_limit(monkeypatch):
    """Default to rate limiting out of the way.

    Every test that isn't about the limiter needs it disabled, and the tests
    that *are* about it override this explicitly - that way a limiter bug can
    never silently make an unrelated test pass by rejecting the request
    before it reaches the thing under test.
    """
    monkeypatch.setattr("app_server.telemetry.is_rate_limited", lambda *a, **k: False)
    monkeypatch.setattr("app_server.runtime_events.is_rate_limited", lambda *a, **k: False)


# --- Body-size caps ----------------------------------------------------------


def test_oversized_declared_body_is_rejected():
    with pytest.raises(BodyTooLargeError):
        check_declared_body_size("/v1/telemetry", "POST", str(TELEMETRY_MAX_BODY_BYTES + 1))


def test_a_body_at_the_limit_is_allowed():
    check_declared_body_size("/v1/telemetry", "POST", str(TELEMETRY_MAX_BODY_BYTES))


def test_missing_content_length_is_refused_not_waved_through():
    # Without a declared size the cap is unenforceable before reading, which
    # is the whole hole. Both real clients always send one.
    with pytest.raises(MissingContentLengthError):
        check_declared_body_size("/v1/telemetry", "POST", None)


def test_a_non_numeric_content_length_is_refused():
    with pytest.raises(MissingContentLengthError):
        check_declared_body_size("/v1/telemetry", "POST", "not-a-number")


def test_managed_audit_has_a_pre_routing_body_cap():
    check_declared_body_size(
        "/v1/managed-audit", "POST", str(MANAGED_AUDIT_MAX_BODY_BYTES)
    )
    with pytest.raises(BodyTooLargeError):
        check_declared_body_size(
            "/v1/managed-audit", "POST", str(MANAGED_AUDIT_MAX_BODY_BYTES + 1)
        )


def test_get_requests_are_untouched():
    check_declared_body_size("/v1/telemetry", "GET", None)


def test_runtime_events_allows_a_real_stack_trace():
    # A Sentry event with frames is legitimately far bigger than a telemetry
    # ping; the caps are not interchangeable.
    assert RUNTIME_EVENT_MAX_BODY_BYTES > TELEMETRY_MAX_BODY_BYTES * 50
    check_declared_body_size("/v1/runtime-events", "POST", str(100 * 1024))


@pytest.mark.asyncio
async def test_oversized_telemetry_post_gets_413_end_to_end(pool):
    app.state.db_pool = pool
    body = json.dumps({"event": "scan", "anonymous_id": "x" * 60, "pad": "p" * 4000}).encode()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/telemetry", content=body, headers={"Content-Type": "application/json"}
        )

    assert response.status_code == 413


@pytest.mark.asyncio
async def test_oversized_body_never_reaches_the_database(pool, monkeypatch):
    """The point of enforcing in middleware rather than the handler.

    A handler-level check rejects a payload the process has already read and
    parsed - which is the cost the cap exists to avoid.
    """
    app.state.db_pool = pool
    recorded = []
    monkeypatch.setattr(
        "app_server.telemetry.record_telemetry_event",
        lambda *a, **k: recorded.append(a),
    )
    body = json.dumps({"event": "scan", "anonymous_id": "y" * 60, "pad": "p" * 8000}).encode()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/v1/telemetry", content=body, headers={"Content-Type": "application/json"}
        )

    assert recorded == []


# --- Schema narrowing --------------------------------------------------------


@pytest.mark.asyncio
async def test_telemetry_rejects_unknown_fields(pool):
    # Otherwise an unauthenticated endpoint becomes a store for whatever a
    # caller attaches.
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/telemetry",
            json={"event": "scan", "anonymous_id": "a" * 20, "extra": "smuggled"},
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_telemetry_rejects_an_overlong_event_name(pool):
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/telemetry", json={"event": "s" * 64, "anonymous_id": "a" * 20}
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_a_valid_telemetry_event_still_works(pool):
    # The controls must not break the thing they protect.
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/telemetry", json={"event": "scan", "anonymous_id": "machine-abc-123"}
        )

    assert response.status_code == 200, response.text
    stored = await pool.fetchval(
        "SELECT count(*) FROM cli_telemetry_events WHERE anonymous_id = 'machine-abc-123'"
    )
    assert stored == 1


# --- Rate limiting -----------------------------------------------------------


@pytest.mark.asyncio
async def test_telemetry_returns_429_when_rate_limited(pool, monkeypatch):
    app.state.db_pool = pool
    monkeypatch.setattr("app_server.telemetry.is_rate_limited", lambda *a, **k: True)
    recorded = []
    monkeypatch.setattr(
        "app_server.telemetry.record_telemetry_event", lambda *a, **k: recorded.append(a)
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/telemetry", json={"event": "scan", "anonymous_id": "b" * 20}
        )

    assert response.status_code == 429
    assert response.headers["Retry-After"]
    assert recorded == [], "a rate-limited request still wrote to the database"


@pytest.mark.asyncio
async def test_telemetry_fails_open_when_redis_is_down(pool, monkeypatch):
    # A Redis outage should cost abuse protection on a best-effort stats
    # endpoint, not turn into a hard failure for every CLI user.
    app.state.db_pool = pool

    def _boom(*_args, **_kwargs):
        raise ConnectionError("redis unavailable")

    monkeypatch.setattr("app_server.telemetry.is_rate_limited", _boom)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/telemetry", json={"event": "scan", "anonymous_id": "c" * 20}
        )

    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_telemetry_rate_limit_key_cannot_be_spoofed_via_forwarded_for(pool, monkeypatch):
    """The limit is only worth having if a caller can't rotate its own key.

    Caddy appends the real connecting peer as the LAST X-Forwarded-For entry;
    anything earlier arrived with the request and is attacker-controlled. So
    the key must derive from the last entry - keying on the first would let
    one client mint unlimited buckets just by varying a header.
    """
    app.state.db_pool = pool
    keys = []
    monkeypatch.setattr(
        "app_server.telemetry.is_rate_limited",
        lambda _conn, key, _limit, _window: keys.append(key) or False,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/v1/telemetry",
            json={"event": "scan", "anonymous_id": "d" * 20},
            headers={"x-forwarded-for": "203.0.113.9, 10.0.0.1"},
        )

    assert keys == ["ratelimit:telemetry:10.0.0.1"]
    assert "203.0.113.9" not in keys[0], "attacker-supplied hop was used as the key"


@pytest.mark.asyncio
async def test_unknown_event_type_is_rejected_before_the_rate_limiter(pool, monkeypatch):
    # A junk event must not consume a caller's budget - otherwise malformed
    # traffic could exhaust the allowance for a legitimate client on the
    # same NAT address.
    app.state.db_pool = pool
    calls = []
    monkeypatch.setattr(
        "app_server.telemetry.is_rate_limited", lambda *a, **k: calls.append(a) or False
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/telemetry", json={"event": "not-a-real-event", "anonymous_id": "e" * 20}
        )

    assert response.status_code == 400
    assert calls == []


# --- Retention ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_retention_sweep_drops_old_events_and_keeps_recent_ones(pool):
    now = datetime.now(timezone.utc)
    await pool.execute(
        "INSERT INTO cli_telemetry_events (event_type, anonymous_id, occurred_at) "
        "VALUES ($1, $2, $3)",
        "scan",
        "old-machine",
        now - timedelta(days=366),
    )
    await pool.execute(
        "INSERT INTO cli_telemetry_events (event_type, anonymous_id, occurred_at) "
        "VALUES ($1, $2, $3)",
        "scan",
        "recent-machine",
        now - timedelta(days=364),
    )

    deleted = delete_expired_telemetry_events(TEST_DATABASE_URL, 365)

    assert deleted == 1
    remaining = await pool.fetch("SELECT anonymous_id FROM cli_telemetry_events")
    assert {row["anonymous_id"] for row in remaining} == {"recent-machine"}


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
        yield b'{"event":"scan","anonymous_id":"abcdefghij"}'

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/telemetry", content=_chunks(), headers={"Content-Type": "application/json"}
        )

    assert response.status_code == 411
    assert "content-length" in response.json()["detail"]


@pytest.mark.asyncio
async def test_the_oversized_and_length_less_paths_return_distinct_codes(pool):
    # 413 and 411 mean different things to a client: "shrink your payload" vs
    # "declare its size". Collapsing them into one code would mislead.
    app.state.db_pool = pool

    async def _chunks():
        yield b'{"event":"scan","anonymous_id":"abcdefghij"}'

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        length_less = await client.post(
            "/v1/telemetry", content=_chunks(), headers={"Content-Type": "application/json"}
        )
        oversized = await client.post(
            "/v1/telemetry",
            content=json.dumps({"event": "scan", "anonymous_id": "z" * 60, "pad": "p" * 5000}).encode(),
            headers={"Content-Type": "application/json"},
        )

    assert (length_less.status_code, oversized.status_code) == (411, 413)
