import hashlib
import json
from pathlib import Path
from unittest.mock import Mock, AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app_server import companion_limits as module

TOKEN = "synthetic-companion-service-token-at-least-32-chars"


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv("COMPANION_LIMIT_SERVICE_TOKEN", TOKEN)
    app = FastAPI()
    app.include_router(module.companion_limits_router)
    with TestClient(app) as client:
        yield client


def command(operation="reserve", **kwargs):
    return {
        "operation": operation,
        "request_id": str(uuid4()),
        "client_hash": hashlib.sha256(b"client").hexdigest(),
        **kwargs,
    }


def post(api, body, token=TOKEN):
    return api.post(
        "/v1/internal/companion-limits",
        headers={"Authorization": f"Bearer {token}"},
        json=body,
    )


def test_script_snapshots_are_identical():
    root = Path(__file__).resolve().parents[2]
    other = json.loads((root / "site/src/lib/companion/limit-scripts.json").read_text())
    assert other == module.SCRIPTS


def test_requires_valid_separate_service_credential(api, monkeypatch):
    conn = Mock()
    conn.eval = AsyncMock()
    monkeypatch.setattr(module, "get_redis_client", lambda: conn)
    assert post(api, command(), "forged").status_code == 401
    conn.eval.assert_not_called()
    monkeypatch.delenv("COMPANION_LIMIT_SERVICE_TOKEN")
    assert post(api, command()).status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        command(cost=0),
        command("settle"),
        command(cost=True),
        command("settle", cost=-2),
        command("settle", cost=5000001),
        command("settle", cost=1.5),
        command(script="FLUSHALL"),
        command(keys=["other-account"]),
        command(request_id="../ledger"),
        command(client_hash="raw-ip"),
        {"operation": "initialize-first-use"},
        None,
    ],
)
def test_rejects_arbitrary_commands_and_budget_overrides(api, monkeypatch, body):
    conn = Mock()
    conn.eval = AsyncMock()
    monkeypatch.setattr(module, "get_redis_client", lambda: conn)
    assert post(api, body).status_code == 400
    conn.eval.assert_not_called()


def test_uses_server_defined_keys_and_full_hold(api, monkeypatch):
    conn = Mock()
    conn.eval = AsyncMock()
    conn.eval.return_value = [1, 200, 0]
    monkeypatch.setattr(module, "get_redis_client", lambda: conn)
    body = command()
    response = post(api, body)
    assert response.status_code == 200
    assert response.json() == {"result": [1, 200, 0], "policy": module.POLICY}
    args = conn.eval.call_args.args
    assert args[0] == module.SCRIPTS["RESERVE_SCRIPT"]
    assert args[1] == 6
    assert args[2] == module.LEDGER
    assert args[-3:] == (body["request_id"], "5000000", body["client_hash"])
    assert response.headers["cache-control"] == "no-store"


def test_settlement_and_disable_use_the_same_shared_reservation(api, monkeypatch):
    conn = Mock()
    conn.eval = AsyncMock()
    conn.eval.return_value = [1]
    monkeypatch.setattr(module, "get_redis_client", lambda: conn)
    body = command("settle", cost=30000)
    assert post(api, body).json() == {"result": [1], "policy": module.POLICY}
    assert conn.eval.call_args.args[-3:] == (
        body["request_id"],
        "30000",
        body["client_hash"],
    )
    body.pop("cost")
    body["operation"] = "disable"
    assert post(api, body).status_code == 200
    assert conn.eval.call_args.args[0] == module.SCRIPTS["DISABLE_SCRIPT"]


def test_redis_failure_is_closed_and_does_not_echo_diagnostics(api, monkeypatch):
    conn = Mock()
    conn.eval = AsyncMock()
    conn.eval.side_effect = RuntimeError("private Redis diagnostic and credential")
    monkeypatch.setattr(module, "get_redis_client", lambda: conn)
    response = post(api, command())
    assert response.status_code == 503
    assert "private Redis" not in response.text
    assert TOKEN not in response.text


def test_oversized_authenticated_body_is_rejected_before_redis(api, monkeypatch):
    conn = Mock()
    conn.eval = AsyncMock()
    monkeypatch.setattr(module, "get_redis_client", lambda: conn)
    response = api.post(
        "/v1/internal/companion-limits",
        headers={"Authorization": f"Bearer {TOKEN}"},
        content="x" * 3000,
    )
    assert response.status_code == 413
    conn.eval.assert_not_called()


def test_deployed_redis_has_durable_private_accounting():
    import yaml

    root = Path(__file__).resolve().parents[1]
    compose = yaml.safe_load((root / "docker-compose.yml").read_text())
    service = compose["services"]["redis"]
    assert "aletheore_redis_data:/data" in service["volumes"]
    assert "aletheore_redis_data" in compose["volumes"]
    assert "ports" not in service
    args = service["command"]
    assert args[args.index("--appendonly") + 1] == "yes"
    assert args[args.index("--appendfsync") + 1] == "always"
    assert args[args.index("--maxmemory-policy") + 1] == "noeviction"
    assert args[args.index("--aof-load-truncated") + 1] == "no"


def test_eval_deadline_cancels_slow_storage(api, monkeypatch):
    import asyncio

    conn = Mock()
    cancelled = []

    async def stalled(*_args):
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.append(True)

    conn.eval = AsyncMock(side_effect=stalled)
    monkeypatch.setattr(module, "get_redis_client", lambda: conn)
    assert post(api, command()).status_code == 503
    assert cancelled == [True]


def test_dedicated_pool_is_bounded_and_disables_retries(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(
        module,
        "get_settings",
        lambda: SimpleNamespace(
            redis_url="redis://localhost:6379/0?socket_timeout=99&socket_connect_timeout=99"
        ),
    )
    module.get_redis_client.cache_clear()
    try:
        client = module.get_redis_client()
        pool = client.connection_pool
        assert pool.max_connections == 4
        assert pool.connection_kwargs["socket_timeout"] == 0.5
        assert pool.connection_kwargs["socket_connect_timeout"] == 0.5
        assert pool.connection_kwargs["retry"]._retries == 0
    finally:
        module.get_redis_client.cache_clear()


def test_disconnect_is_a_clean_client_error(monkeypatch):
    import asyncio
    from fastapi import HTTPException
    from starlette.requests import Request

    monkeypatch.setenv("COMPANION_LIMIT_SERVICE_TOKEN", TOKEN)

    async def disconnected():
        return {"type": "http.disconnect"}

    request = Request(
        {"type": "http", "headers": [(b"authorization", f"Bearer {TOKEN}".encode())]},
        disconnected,
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(module.companion_limits(request))
    assert exc.value.status_code == 400


@pytest.mark.parametrize("state", [{}, {"version": "1"}])
def test_operator_status_fails_for_missing_or_corrupt_ledger(
    monkeypatch, capsys, state
):
    import runpy
    import sys
    from redis import Redis

    conn = Mock()
    conn.hgetall.return_value = state
    monkeypatch.setenv("REDIS_URL", "redis://test.invalid")
    monkeypatch.setattr(Redis, "from_url", lambda *_args, **_kwargs: conn)
    monkeypatch.setattr(sys, "argv", ["companion_limits.py", "status"])
    with pytest.raises(SystemExit, match="Reconcile"):
        runpy.run_path(
            str(Path(__file__).resolve().parents[1] / "scripts/companion_limits.py"),
            run_name="__main__",
        )
    assert json.loads(capsys.readouterr().out)["status"] == "missing_or_corrupt"


def test_real_async_router_and_redis_when_available(monkeypatch):
    import asyncio
    import os
    from types import SimpleNamespace
    from starlette.requests import Request

    socket = os.environ.get("COMPANION_TEST_REDIS_SOCKET")
    url = os.environ.get("COMPANION_TEST_REDIS_URL") or os.environ.get("TEST_REDIS_URL")
    if not socket and not url:
        pytest.skip("explicit isolated Redis connection required")
    if socket:
        url = f"unix://{socket}"
    prefix = f"test:{{companion-budget}}:{uuid4()}"
    monkeypatch.setattr(module, "PREFIX", prefix)
    monkeypatch.setattr(module, "LEDGER", f"{prefix}:ledger")
    monkeypatch.setattr(module, "get_settings", lambda: SimpleNamespace(redis_url=url))
    monkeypatch.setenv("COMPANION_LIMIT_SERVICE_TOKEN", TOKEN)
    module.get_redis_client.cache_clear()

    async def run():
        client = module.get_redis_client()
        body = command()
        keys = [
            module.LEDGER,
            f"{prefix}:requests",
            f"{prefix}:active",
            f"{prefix}:client:{body['client_hash']}",
            f"{prefix}:active:{body['client_hash']}",
            f"{prefix}:reservation:{body['request_id']}",
        ]

        async def invoke(payload):
            async def receive():
                return {
                    "type": "http.request",
                    "body": json.dumps(payload).encode(),
                    "more_body": False,
                }

            request = Request(
                {
                    "type": "http",
                    "headers": [(b"authorization", f"Bearer {TOKEN}".encode())],
                },
                receive,
            )
            return json.loads((await module.companion_limits(request)).body)

        try:
            await client.hset(
                module.LEDGER,
                mapping={
                    "version": "1",
                    "enabled": "1",
                    "day": "0",
                    "month": "0",
                    "day_used": "0",
                    "month_used": "0",
                    "day_cap": "20000000",
                    "month_cap": "500000000",
                },
            )
            assert (await invoke(body))["result"] == [1, 200, 0]
            assert (await invoke({**body, "operation": "settle", "cost": 30000}))[
                "result"
            ] == [1]
            assert await client.hget(module.LEDGER, "day_used") == b"30000"
        finally:
            await client.delete(*keys)
            await client.aclose(close_connection_pool=True)

    try:
        asyncio.run(run())
    finally:
        module.get_redis_client.cache_clear()
