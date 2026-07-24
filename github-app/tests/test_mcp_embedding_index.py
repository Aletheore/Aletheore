import os

import pytest

from scan_worker.db import (
    list_mcp_code_embeddings,
    upsert_mcp_code_embedding,
)
from scan_worker.mcp_embedding_index import reindex_mcp_embeddings
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
async def test_reindex_skips_unchanged_chunks_and_reembeds_changed_ones(pool, tmp_path, monkeypatch):
    await _insert_installation(pool, 720, "acme", plan="team")
    evidence = {
        "repository": {
            "modules": [
                {
                    "path": "a.py",
                    "language": "python",
                    "symbols": {
                        "functions": [{"name": "foo", "start_line": 1, "end_line": 1}],
                        "classes": [],
                    },
                }
            ]
        }
    }
    (tmp_path / "a.py").write_text("def foo(): pass\n")

    calls = []

    def fake_embed_text(text, base_url=None, timeout_seconds=5.0):
        calls.append(text)
        return [1.0, 2.0]

    monkeypatch.setattr("scan_worker.mcp_embedding_index.embed_text", fake_embed_text)

    reindex_mcp_embeddings(TEST_DATABASE_URL, 720, "acme/widgets", evidence, tmp_path)
    assert len(calls) == 1
    assert len(list_mcp_code_embeddings(TEST_DATABASE_URL, 720, "acme/widgets")) == 1

    reindex_mcp_embeddings(TEST_DATABASE_URL, 720, "acme/widgets", evidence, tmp_path)
    assert len(calls) == 1

    (tmp_path / "a.py").write_text("def foo(): return 1\n")
    reindex_mcp_embeddings(TEST_DATABASE_URL, 720, "acme/widgets", evidence, tmp_path)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_reindex_deletes_embeddings_for_removed_files(pool, tmp_path, monkeypatch):
    await _insert_installation(pool, 721, "acme", plan="team")
    monkeypatch.setattr("scan_worker.mcp_embedding_index.embed_text", lambda *a, **k: [1.0])
    upsert_mcp_code_embedding(TEST_DATABASE_URL, 721, "acme/widgets", "gone.py", 0, "old", "old text", [0.1])

    reindex_mcp_embeddings(TEST_DATABASE_URL, 721, "acme/widgets", {"repository": {"modules": []}}, tmp_path)

    assert list_mcp_code_embeddings(TEST_DATABASE_URL, 721, "acme/widgets") == []
