import asyncio
from datetime import datetime, timezone
import os

import pytest

from app_server.mcp_auth import CURRENT_INSTALLATION_ID
from app_server.mcp_hosted import _hosted_search_codebase, _hosted_symbol_source
from scan_worker.db import insert_repo_history, upsert_mcp_code_embedding, upsert_mcp_git_mirror
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


def _seed_installation_sync(installation_id: int, account_login: str, repo_full_name: str, mirror_root):
    evidence = {
        "repository": {
            "modules": [
                {
                    "path": "shared_name.py",
                    "imports": ["os"],
                    "language": "python",
                    "symbols": {
                        "functions": [{"name": "process", "start_line": 1, "end_line": 2}],
                        "classes": [],
                    },
                }
            ]
        }
    }
    insert_repo_history(TEST_DATABASE_URL, installation_id, repo_full_name, datetime.now(timezone.utc), evidence)
    mirror = mirror_root / str(installation_id) / repo_full_name.replace("/", "__")
    mirror.mkdir(parents=True)
    (mirror / "shared_name.py").write_text(f"def process():\n    return '{account_login}-secret'\n")
    upsert_mcp_git_mirror(TEST_DATABASE_URL, installation_id, repo_full_name, str(mirror), "abc", 100)
    upsert_mcp_code_embedding(
        TEST_DATABASE_URL,
        installation_id,
        repo_full_name,
        "shared_name.py",
        0,
        "h",
        f"def process(): return '{account_login}-secret'",
        [1.0, float(installation_id)],
    )


async def _seed_installation(pool, installation_id: int, account_login: str, repo_full_name: str, mirror_root):
    await _insert_installation(pool, installation_id, account_login, plan="team")
    _seed_installation_sync(installation_id, account_login, repo_full_name, mirror_root)


@pytest.mark.asyncio
async def test_query_tools_never_cross_tenant(pool, tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    await _seed_installation(pool, 901, "acme", "acme/widgets", tmp_path)
    await _seed_installation(pool, 902, "other", "other/widgets", tmp_path)

    token = CURRENT_INSTALLATION_ID.set(901)
    try:
        source = _hosted_symbol_source(repo_full_name="acme/widgets", module="shared_name.py", symbol="process")
    finally:
        CURRENT_INSTALLATION_ID.reset(token)

    assert "acme-secret" in source
    assert "other-secret" not in source


@pytest.mark.asyncio
async def test_concurrent_requests_from_two_installations_never_leak(pool, tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    await _seed_installation(pool, 903, "acme", "acme/widgets", tmp_path)
    await _seed_installation(pool, 904, "other", "other/widgets", tmp_path)

    async def call_as(installation_id, repo_full_name, expected_secret, forbidden_secret):
        token = CURRENT_INSTALLATION_ID.set(installation_id)
        try:
            result = _hosted_symbol_source(repo_full_name=repo_full_name, module="shared_name.py", symbol="process")
        finally:
            CURRENT_INSTALLATION_ID.reset(token)
        assert expected_secret in result
        assert forbidden_secret not in result

    await asyncio.gather(
        *[call_as(903, "acme/widgets", "acme-secret", "other-secret") for _ in range(20)],
        *[call_as(904, "other/widgets", "other-secret", "acme-secret") for _ in range(20)],
    )


@pytest.mark.asyncio
async def test_embedding_query_path_never_returns_another_tenants_row(pool, tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    await _seed_installation(pool, 905, "acme", "acme/widgets", tmp_path)
    await _seed_installation(pool, 906, "other", "other/widgets", tmp_path)
    monkeypatch.setattr("app_server.mcp_hosted.embed_text", lambda *a, **k: [1.0, 905.0])

    token = CURRENT_INSTALLATION_ID.set(905)
    try:
        result = _hosted_search_codebase(repo_full_name="acme/widgets", query="anything", k=5)
    finally:
        CURRENT_INSTALLATION_ID.reset(token)

    assert "acme-secret" in result
    assert "other-secret" not in result
