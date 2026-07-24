import hashlib
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app_server.mcp_auth import CURRENT_INSTALLATION_ID, McpAuthMiddleware
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://postgres:test@localhost:55433/aletheore_test",
)


async def _insert_installation(pool, installation_id: int, account_login: str, **values) -> None:
    columns = ["installation_id", "account_login", *values.keys()]
    params = [installation_id, account_login, *values.values()]
    placeholders = ", ".join(f"${i}" for i in range(1, len(params) + 1))
    await pool.execute(
        f"INSERT INTO installations ({', '.join(columns)}) VALUES ({placeholders})",
        *params,
    )


async def _insert_api_token(pool, installation_id: int, raw_token: str) -> None:
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    await pool.execute(
        """
        INSERT INTO api_tokens (installation_id, token_hash, label, created_by_github_login)
        VALUES ($1, $2, $3, $4)
        """,
        installation_id,
        token_hash,
        "test token",
        "test",
    )


def _test_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(McpAuthMiddleware)

    @app.get("/mcp/_debug_installation_id")
    def debug_route():
        return {"installation_id": CURRENT_INSTALLATION_ID.get()}

    return app


@pytest.mark.asyncio
async def test_missing_token_returns_401(pool, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    response = TestClient(_test_app()).get("/mcp/_debug_installation_id")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_free_plan_token_returns_402(pool, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    await _insert_installation(pool, 801, "acme", plan="free")
    await _insert_api_token(pool, 801, "free-token")

    response = TestClient(_test_app()).get(
        "/mcp/_debug_installation_id",
        headers={"Authorization": "Bearer free-token"},
    )

    assert response.status_code == 402


@pytest.mark.asyncio
async def test_valid_paid_token_sets_context_var(pool, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    await _insert_installation(pool, 802, "acme", plan="team")
    await _insert_api_token(pool, 802, "paid-token")

    response = TestClient(_test_app()).get(
        "/mcp/_debug_installation_id",
        headers={"Authorization": "Bearer paid-token"},
    )

    assert response.status_code == 200
    assert response.json()["installation_id"] == 802
