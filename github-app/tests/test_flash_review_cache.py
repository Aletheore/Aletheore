import pytest

from scan_worker.flash_review_cache import lookup_cached_result, store_result


def test_lookup_returns_none_when_embedding_unavailable(monkeypatch):
    monkeypatch.setattr("scan_worker.flash_review_cache.embed_text", lambda text: None)

    result = lookup_cached_result("postgresql://unused", 1, "org/repo", "some diff")

    assert result is None


def test_lookup_returns_none_when_no_rows_exist(monkeypatch):
    monkeypatch.setattr("scan_worker.flash_review_cache.embed_text", lambda text: [1.0, 0.0])
    monkeypatch.setattr(
        "scan_worker.flash_review_cache.list_recent_flash_review_cache_rows", lambda *a, **k: []
    )

    result = lookup_cached_result("postgresql://unused", 1, "org/repo", "some diff")

    assert result is None


def test_lookup_returns_none_below_similarity_threshold(monkeypatch):
    monkeypatch.setattr("scan_worker.flash_review_cache.embed_text", lambda text: [1.0, 0.0])
    monkeypatch.setattr(
        "scan_worker.flash_review_cache.list_recent_flash_review_cache_rows",
        lambda *a, **k: [{"id": 1, "embedding": [0.0, 1.0], "findings": [], "model_used": "deepseek-v4-flash"}],
    )

    result = lookup_cached_result("postgresql://unused", 1, "org/repo", "some diff")

    assert result is None


def test_lookup_returns_findings_above_threshold_and_records_hit(monkeypatch):
    monkeypatch.setattr("scan_worker.flash_review_cache.embed_text", lambda text: [1.0, 0.0])
    monkeypatch.setattr(
        "scan_worker.flash_review_cache.list_recent_flash_review_cache_rows",
        lambda *a, **k: [
            {
                "id": 7,
                "embedding": [1.0, 0.0001],
                "findings": [{"file": "a.py", "line": 1, "issue": "cached finding"}],
                "model_used": "deepseek-v4-flash",
            }
        ],
    )
    recorded = []
    monkeypatch.setattr(
        "scan_worker.flash_review_cache.record_flash_review_cache_hit",
        lambda dsn, row_id: recorded.append(row_id),
    )

    result = lookup_cached_result("postgresql://unused", 1, "org/repo", "some diff")

    assert result == [{"file": "a.py", "line": 1, "issue": "cached finding"}]
    assert recorded == [7]


def test_lookup_fails_open_when_db_lookup_raises(monkeypatch):
    monkeypatch.setattr("scan_worker.flash_review_cache.embed_text", lambda text: [1.0, 0.0])

    def _raise(*a, **k):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr("scan_worker.flash_review_cache.list_recent_flash_review_cache_rows", _raise)

    result = lookup_cached_result("postgresql://unused", 1, "org/repo", "some diff")

    assert result is None


def test_store_result_writes_a_row(monkeypatch):
    written = {}

    def fake_insert(dsn, installation_id, repo_full_name, content_hash, embedding, diff_text, findings, model_used):
        written.update(
            installation_id=installation_id,
            repo_full_name=repo_full_name,
            content_hash=content_hash,
            embedding=embedding,
            diff_text=diff_text,
            findings=findings,
            model_used=model_used,
        )

    monkeypatch.setattr("scan_worker.flash_review_cache.embed_text", lambda text: [0.5, 0.5])
    monkeypatch.setattr("scan_worker.flash_review_cache.insert_flash_review_cache_row", fake_insert)

    store_result(
        "postgresql://unused",
        1,
        "org/repo",
        "--- a.py ---\n@@ -1,1 +1,1 @@\n+x = 1",
        [{"file": "a.py", "line": 1, "issue": "fresh finding"}],
        "deepseek-v4-flash",
    )

    assert written["installation_id"] == 1
    assert written["repo_full_name"] == "org/repo"
    assert written["embedding"] == [0.5, 0.5]
    assert written["findings"] == [{"file": "a.py", "line": 1, "issue": "fresh finding"}]
    assert written["model_used"] == "deepseek-v4-flash"


def test_store_result_is_noop_when_embedding_unavailable(monkeypatch):
    called = []
    monkeypatch.setattr("scan_worker.flash_review_cache.embed_text", lambda text: None)
    monkeypatch.setattr(
        "scan_worker.flash_review_cache.insert_flash_review_cache_row", lambda *a, **k: called.append(True)
    )

    store_result("postgresql://unused", 1, "org/repo", "diff", [], "deepseek-v4-flash")

    assert called == []


def test_store_result_fails_open_when_insert_raises(monkeypatch):
    monkeypatch.setattr("scan_worker.flash_review_cache.embed_text", lambda text: [0.5, 0.5])

    def _raise(*a, **k):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr("scan_worker.flash_review_cache.insert_flash_review_cache_row", _raise)

    store_result("postgresql://unused", 1, "org/repo", "diff", [], "deepseek-v4-flash")


@pytest.mark.asyncio
async def test_lookup_never_returns_a_different_installations_row(pool, monkeypatch):
    from conftest import TEST_DATABASE_URL

    await pool.execute(
        "INSERT INTO installations (installation_id, account_login) VALUES ($1, $2)",
        601,
        "org-a",
    )
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login) VALUES ($1, $2)",
        602,
        "org-b",
    )

    monkeypatch.setattr("scan_worker.flash_review_cache.embed_text", lambda text: [1.0, 0.0])

    store_result(
        TEST_DATABASE_URL,
        601,
        "org-a/repo",
        "diff for org-a",
        [{"file": "a.py", "line": 1, "issue": "org-a's cached finding"}],
        "deepseek-v4-flash",
    )

    result = lookup_cached_result(TEST_DATABASE_URL, 602, "org-b/repo", "diff for org-a")

    assert result is None
