import hashlib
import json
from pathlib import Path
from unittest.mock import Mock
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
    monkeypatch.setattr(module, "get_redis_client", lambda: conn)
    assert post(api, body).status_code == 400
    conn.eval.assert_not_called()


def test_uses_server_defined_keys_and_full_hold(api, monkeypatch):
    conn = Mock()
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
    conn.eval.side_effect = RuntimeError("private Redis diagnostic and credential")
    monkeypatch.setattr(module, "get_redis_client", lambda: conn)
    response = post(api, command())
    assert response.status_code == 503
    assert "private Redis" not in response.text
    assert TOKEN not in response.text


def test_oversized_authenticated_body_is_rejected_before_redis(api, monkeypatch):
    conn = Mock()
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
