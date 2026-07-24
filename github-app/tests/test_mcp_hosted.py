from datetime import datetime, timezone
import os

import pytest

from app_server.mcp_auth import CURRENT_INSTALLATION_ID
from app_server.mcp_hosted import (
    _hosted_imports,
    _hosted_search,
    _hosted_search_codebase,
    _hosted_symbol_source,
)
from scan_worker.db import (
    insert_repo_history,
    upsert_mcp_code_embedding,
    upsert_mcp_git_mirror,
)
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


@pytest.mark.asyncio
async def test_hosted_mcp_imports_tool_returns_only_own_installations_evidence(pool, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    await _insert_installation(pool, 811, "acme", plan="team")
    await _insert_installation(pool, 812, "other", plan="team")
    insert_repo_history(
        TEST_DATABASE_URL,
        811,
        "acme/widgets",
        datetime.now(timezone.utc),
        {"repository": {"modules": [{"path": "a.py", "imports": ["os"], "language": "python"}]}},
    )
    insert_repo_history(
        TEST_DATABASE_URL,
        812,
        "other/gizmos",
        datetime.now(timezone.utc),
        {"repository": {"modules": [{"path": "b.py", "imports": ["sys"], "language": "python"}]}},
    )

    token = CURRENT_INSTALLATION_ID.set(811)
    try:
        result = _hosted_imports(repo_full_name="acme/widgets", target="a.py")
    finally:
        CURRENT_INSTALLATION_ID.reset(token)

    assert "os" in result
    assert "sys" not in result


@pytest.mark.asyncio
async def test_hosted_mcp_tool_rejects_repo_not_owned_by_installation(pool, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    await _insert_installation(pool, 813, "acme", plan="team")
    insert_repo_history(
        TEST_DATABASE_URL,
        813,
        "acme/widgets",
        datetime.now(timezone.utc),
        {"repository": {"modules": []}},
    )

    token = CURRENT_INSTALLATION_ID.set(813)
    try:
        result = _hosted_imports(repo_full_name="someone-elses/repo", target="a.py")
    finally:
        CURRENT_INSTALLATION_ID.reset(token)

    assert "error" in result.lower()


@pytest.mark.asyncio
async def test_hosted_search_returns_resync_pending_when_mirror_missing(pool, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    await _insert_installation(pool, 814, "acme", plan="team")
    token = CURRENT_INSTALLATION_ID.set(814)
    try:
        result = _hosted_search(repo_full_name="acme/widgets", pattern="foo")
    finally:
        CURRENT_INSTALLATION_ID.reset(token)
    assert "resync pending" in result.lower()


@pytest.mark.asyncio
async def test_hosted_symbol_source_reads_from_own_mirror_only(pool, tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    await _insert_installation(pool, 815, "acme", plan="team")
    mirror = tmp_path / "815" / "acme__widgets"
    mirror.mkdir(parents=True)
    (mirror / "a.py").write_text("def foo():\n    return 1\n")
    upsert_mcp_git_mirror(TEST_DATABASE_URL, 815, "acme/widgets", str(mirror), "abc123", 100)
    insert_repo_history(
        TEST_DATABASE_URL,
        815,
        "acme/widgets",
        datetime.now(timezone.utc),
        {"repository": {"modules": [{"path": "a.py", "symbols": {"functions": [], "classes": []}}]}},
    )

    token = CURRENT_INSTALLATION_ID.set(815)
    try:
        result = _hosted_symbol_source(
            repo_full_name="acme/widgets", file_path="a.py", start_line=1, end_line=2
        )
    finally:
        CURRENT_INSTALLATION_ID.reset(token)
    assert "return 1" in result


@pytest.mark.asyncio
async def test_hosted_symbol_source_rejects_path_escaping_mirror(pool, tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    await _insert_installation(pool, 816, "acme", plan="team")
    mirror = tmp_path / "816" / "acme__widgets"
    mirror.mkdir(parents=True)
    upsert_mcp_git_mirror(TEST_DATABASE_URL, 816, "acme/widgets", str(mirror), "abc123", 100)
    insert_repo_history(
        TEST_DATABASE_URL,
        816,
        "acme/widgets",
        datetime.now(timezone.utc),
        {"repository": {"modules": []}},
    )

    token = CURRENT_INSTALLATION_ID.set(816)
    try:
        result = _hosted_symbol_source(
            repo_full_name="acme/widgets",
            file_path="../../../etc/passwd",
            start_line=1,
            end_line=1,
        )
    finally:
        CURRENT_INSTALLATION_ID.reset(token)
    assert "error" in result.lower()


@pytest.mark.asyncio
async def test_search_codebase_ranks_by_similarity_and_stays_scoped_to_own_installation(pool, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    await _insert_installation(pool, 817, "acme", plan="team")
    await _insert_installation(pool, 818, "other", plan="team")
    monkeypatch.setattr("app_server.mcp_hosted.embed_text", lambda *a, **k: [0.9, 0.1])
    upsert_mcp_code_embedding(TEST_DATABASE_URL, 817, "acme/widgets", "a.py", 0, "h1", "def foo(): pass", [1.0, 0.0])
    upsert_mcp_code_embedding(TEST_DATABASE_URL, 817, "acme/widgets", "b.py", 0, "h2", "def bar(): pass", [0.0, 1.0])
    upsert_mcp_code_embedding(TEST_DATABASE_URL, 818, "other/gizmos", "c.py", 0, "h3", "def foo(): pass", [1.0, 0.0])

    token = CURRENT_INSTALLATION_ID.set(817)
    try:
        result = _hosted_search_codebase(repo_full_name="acme/widgets", query="query about foo", k=1)
    finally:
        CURRENT_INSTALLATION_ID.reset(token)

    assert "a.py" in result
    assert "c.py" not in result
