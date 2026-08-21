import pytest

from scan_worker.embedding_client import CURRENT_EMBEDDER
from scan_worker.packet_cache import lookup_cached_result, store_result


def _packet(changed_files=("a.py",)):
    return {"changed_files": list(changed_files), "cache_eligible": True}


def test_lookup_returns_none_when_embedding_unavailable(monkeypatch):
    monkeypatch.setattr("scan_worker.packet_cache.embed_text", lambda text: None)

    result = lookup_cached_result("postgresql://unused", 1, "org/repo", _packet())

    assert result is None


def test_lookup_returns_none_when_no_rows_exist(monkeypatch):
    monkeypatch.setattr("scan_worker.packet_cache.embed_text", lambda text: [1.0, 0.0])
    monkeypatch.setattr("scan_worker.packet_cache.list_recent_evidence_packet_cache_rows", lambda *a, **k: [])

    result = lookup_cached_result("postgresql://unused", 1, "org/repo", _packet())

    assert result is None


def test_lookup_returns_none_below_similarity_threshold(monkeypatch):
    monkeypatch.setattr("scan_worker.packet_cache.embed_text", lambda text: [1.0, 0.0])
    monkeypatch.setattr(
        "scan_worker.packet_cache.list_recent_evidence_packet_cache_rows",
        lambda *a, **k: [
            {
                "id": 1,
                "embedding": [0.0, 1.0],
                "model_output": {"description": "unrelated"},
                "model_used": "deepseek-v4-pro",
            }
        ],
    )

    result = lookup_cached_result("postgresql://unused", 1, "org/repo", _packet())

    assert result is None


def test_lookup_returns_match_above_threshold_and_records_hit(monkeypatch):
    monkeypatch.setattr("scan_worker.packet_cache.embed_text", lambda text: [1.0, 0.0])
    monkeypatch.setattr(
        "scan_worker.packet_cache.list_recent_evidence_packet_cache_rows",
        lambda *a, **k: [
            {
                "id": 7,
                "embedding": [1.0, 0.0001],
                "model_output": {"description": "cached description"},
                "model_used": "deepseek-v4-pro",
            }
        ],
    )
    recorded = []
    monkeypatch.setattr("scan_worker.packet_cache.record_evidence_packet_cache_hit", lambda dsn, row_id: recorded.append(row_id))

    result = lookup_cached_result("postgresql://unused", 1, "org/repo", _packet())

    assert result == ({"description": "cached description"}, "deepseek-v4-pro")
    assert recorded == [7]


def test_store_result_writes_a_row(monkeypatch):
    written = {}

    def fake_insert(dsn, installation_id, repo_full_name, content_hash, embedding, packet, model_output, model_used, embedder):
        written.update(
            installation_id=installation_id,
            repo_full_name=repo_full_name,
            content_hash=content_hash,
            embedding=embedding,
            packet=packet,
            model_output=model_output,
            model_used=model_used,
            embedder=embedder,
        )

    monkeypatch.setattr("scan_worker.packet_cache.embed_text", lambda text: [0.5, 0.5])
    monkeypatch.setattr("scan_worker.packet_cache.insert_evidence_packet_cache_row", fake_insert)

    store_result("postgresql://unused", 1, "org/repo", _packet(), {"description": "fresh"}, "deepseek-v4-pro")

    assert written["installation_id"] == 1
    assert written["repo_full_name"] == "org/repo"
    assert written["embedding"] == [0.5, 0.5]
    assert written["model_output"] == {"description": "fresh"}
    assert written["model_used"] == "deepseek-v4-pro"
    # Batch 5 finding 8: every write must be tagged with the embedder that
    # produced the vector, so a future embedder switch can filter out
    # mismatched rows at lookup time instead of comparing across two
    # different embedding spaces.
    assert written["embedder"] == CURRENT_EMBEDDER


def test_lookup_filters_by_the_current_embedder(monkeypatch):
    # Batch 5 finding 8: the lookup must ask the DB layer for rows tagged
    # with the currently-configured embedder specifically, not "any row
    # for this installation/repo" - a row written under a different
    # embedder is a different embedding space, not just a worse match.
    captured = {}

    def fake_list(dsn, installation_id, repo_full_name, embedder, limit=200):
        captured["embedder"] = embedder
        return []

    monkeypatch.setattr("scan_worker.packet_cache.embed_text", lambda text: [1.0, 0.0])
    monkeypatch.setattr("scan_worker.packet_cache.list_recent_evidence_packet_cache_rows", fake_list)

    lookup_cached_result("postgresql://unused", 1, "org/repo", _packet())

    assert captured["embedder"] == CURRENT_EMBEDDER


def test_store_result_is_noop_when_embedding_unavailable(monkeypatch):
    called = []
    monkeypatch.setattr("scan_worker.packet_cache.embed_text", lambda text: None)
    monkeypatch.setattr("scan_worker.packet_cache.insert_evidence_packet_cache_row", lambda *a, **k: called.append(True))

    store_result("postgresql://unused", 1, "org/repo", _packet(), {"description": "fresh"}, "deepseek-v4-pro")

    assert called == []


@pytest.mark.asyncio
async def test_lookup_never_returns_a_different_installations_row(pool, monkeypatch):
    from conftest import TEST_DATABASE_URL

    await pool.execute(
        "INSERT INTO installations (installation_id, account_login) VALUES ($1, $2)",
        501,
        "org-a",
    )
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login) VALUES ($1, $2)",
        502,
        "org-b",
    )

    monkeypatch.setattr("scan_worker.packet_cache.embed_text", lambda text: [1.0, 0.0])

    store_result(
        TEST_DATABASE_URL,
        501,
        "org-a/repo",
        {"changed_files": ["a.py"], "cache_eligible": True},
        {"description": "org-a's cached description"},
        "deepseek-v4-pro",
    )

    result = lookup_cached_result(
        TEST_DATABASE_URL, 502, "org-b/repo", {"changed_files": ["a.py"], "cache_eligible": True}
    )

    assert result is None


@pytest.mark.asyncio
async def test_lookup_never_matches_a_row_written_under_a_different_embedder(pool, monkeypatch):
    # Batch 5 finding 8, proven against a real Postgres instance: a row
    # written under a different embedder (an old row from before a switch,
    # or one written mid-rollout) must never be served as a hit, even
    # though its vector has the identical dimension and would otherwise
    # pass _cosine_similarity's only check - comparing across two
    # different embedding spaces is meaningless, not just a worse match.
    from conftest import TEST_DATABASE_URL

    await pool.execute(
        "INSERT INTO installations (installation_id, account_login) VALUES ($1, $2)",
        503,
        "org-c",
    )

    monkeypatch.setattr("scan_worker.packet_cache.embed_text", lambda text: [1.0, 0.0])
    monkeypatch.setattr("scan_worker.packet_cache.CURRENT_EMBEDDER", "old-embedder:v1")
    store_result(
        TEST_DATABASE_URL,
        503,
        "org-c/repo",
        _packet(),
        {"description": "cached under the old embedder"},
        "deepseek-v4-pro",
    )

    # Same repo, same near-identical embedding, but the currently-configured
    # embedder has moved on - the old-embedder row must not be served.
    monkeypatch.setattr("scan_worker.packet_cache.CURRENT_EMBEDDER", "new-embedder:v2")
    result = lookup_cached_result(TEST_DATABASE_URL, 503, "org-c/repo", _packet())

    assert result is None
