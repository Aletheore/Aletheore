import os
import threading
import time
from datetime import datetime
from unittest.mock import patch

import pytest

from aletheore.git_intel.graph_store import CommitTouch, GraphSnapshot
from aletheore.git_intel.incremental import fold
from scan_worker.postgres_graph_store import PostgresRepoGraphStore

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


def _touch(sha, name, email, date_str, files):
    return CommitTouch(sha, name, email, datetime.fromisoformat(date_str), files)


@pytest.mark.asyncio
async def test_load_returns_empty_snapshot_for_unknown_repo(pool):
    await _insert_installation(pool, 601, "org")
    store = PostgresRepoGraphStore(TEST_DATABASE_URL, 601, "org/repo")

    snapshot = store.load("unused-repo-key", "main")

    assert snapshot.last_synced_sha is None
    assert snapshot.ownership == {}
    assert snapshot.file_churn == {}


@pytest.mark.asyncio
async def test_apply_commits_then_load_round_trips_correctly(pool):
    await _insert_installation(pool, 602, "org")
    store = PostgresRepoGraphStore(TEST_DATABASE_URL, 602, "org/repo")
    # Newest first (s2, then s1) - matching git log's real default order.
    # See src/tests/test_incremental.py's
    # test_fold_caps_recent_commits_per_file_newest_first for why this
    # matters: an oldest-first fixture here masked a real bug in fold()'s
    # recent_commits ordering, on this exact hosted-production code path
    # (PostgresRepoGraphStore also calls the same shared fold()).
    commits = [
        _touch("s2", "Bob", "b@example.com", "2026-06-08T00:00:00", ("a.txt",)),
        _touch("s1", "Alice", "a@example.com", "2026-06-01T00:00:00", ("a.txt", "b.txt")),
    ]

    store.apply_commits(
        "unused", "main", commits, new_sync_sha="s2", new_sync_at=datetime(2026, 6, 8), reset=True
    )
    snapshot = store.load("unused", "main")

    assert snapshot.last_synced_sha == "s2"
    assert snapshot.ownership["a@example.com"].commit_count == 1
    assert snapshot.ownership["a@example.com"].names == {"Alice"}
    assert snapshot.file_churn["a.txt"].churn_count == 2
    assert snapshot.file_churn["a.txt"].co_change_counts == {"b.txt": 1}
    assert len(snapshot.file_churn["a.txt"].recent_commits) == 2
    assert snapshot.file_churn["a.txt"].recent_commits[0].sha == "s2"
    # Per-file ownership must round-trip independently of the repo-wide
    # ownership dict above - a.txt was touched by both authors, b.txt by
    # only Alice, so the two files' owner sets must differ.
    assert snapshot.file_churn["a.txt"].owners["a@example.com"].commit_count == 1
    assert snapshot.file_churn["a.txt"].owners["b@example.com"].commit_count == 1
    assert snapshot.file_churn["b.txt"].owners["a@example.com"].commit_count == 1
    assert "b@example.com" not in snapshot.file_churn["b.txt"].owners


@pytest.mark.asyncio
async def test_incremental_apply_matches_a_single_full_fold(pool):
    await _insert_installation(pool, 603, "org")
    store = PostgresRepoGraphStore(TEST_DATABASE_URL, 603, "org/repo")
    baseline = [_touch("s1", "Alice", "a@example.com", "2026-06-01T00:00:00", ("a.txt",))]
    delta = [_touch("s2", "Alice", "a@example.com", "2026-06-15T00:00:00", ("b.txt",))]

    store.apply_commits("unused", "main", baseline, new_sync_sha="s1", new_sync_at=datetime(2026, 6, 1), reset=True)
    store.apply_commits("unused", "main", delta, new_sync_sha="s2", new_sync_at=datetime(2026, 6, 15), reset=False)
    incremental_result = store.load("unused", "main")

    expected = fold(GraphSnapshot.empty(), baseline + delta)

    assert incremental_result.ownership["a@example.com"].commit_count == expected.ownership["a@example.com"].commit_count
    assert incremental_result.file_churn.keys() == expected.file_churn.keys()


@pytest.mark.asyncio
async def test_reset_clears_prior_state_instead_of_merging(pool):
    await _insert_installation(pool, 604, "org")
    store = PostgresRepoGraphStore(TEST_DATABASE_URL, 604, "org/repo")
    store.apply_commits(
        "unused",
        "main",
        [_touch("s1", "Alice", "a@example.com", "2026-06-01T00:00:00", ("old.txt",))],
        new_sync_sha="s1",
        new_sync_at=datetime(2026, 6, 1),
        reset=True,
    )

    store.apply_commits(
        "unused",
        "main",
        [_touch("s2", "Bob", "b@example.com", "2026-06-02T00:00:00", ("new.txt",))],
        new_sync_sha="s2",
        new_sync_at=datetime(2026, 6, 2),
        reset=True,
    )

    snapshot = store.load("unused", "main")
    assert "a@example.com" not in snapshot.ownership
    assert "old.txt" not in snapshot.file_churn


@pytest.mark.asyncio
async def test_different_installations_are_isolated(pool):
    await _insert_installation(pool, 605, "org-a")
    await _insert_installation(pool, 606, "org-b")
    store_a = PostgresRepoGraphStore(TEST_DATABASE_URL, 605, "org-a/repo")
    store_b = PostgresRepoGraphStore(TEST_DATABASE_URL, 606, "org-b/repo")

    store_a.apply_commits(
        "unused",
        "main",
        [_touch("s1", "Alice", "a@example.com", "2026-06-01T00:00:00", ("a.txt",))],
        new_sync_sha="s1",
        new_sync_at=datetime(2026, 6, 1),
        reset=True,
    )
    store_b.apply_commits(
        "unused",
        "main",
        [_touch("s2", "Bob", "b@example.com", "2026-06-01T00:00:00", ("b.txt",))],
        new_sync_sha="s2",
        new_sync_at=datetime(2026, 6, 1),
        reset=True,
    )

    assert store_a.load("unused", "main").ownership.keys() == {"a@example.com"}
    assert store_b.load("unused", "main").ownership.keys() == {"b@example.com"}


@pytest.mark.asyncio
async def test_different_branches_are_isolated(pool):
    await _insert_installation(pool, 607, "org")
    store = PostgresRepoGraphStore(TEST_DATABASE_URL, 607, "org/repo")

    store.apply_commits(
        "unused",
        "main",
        [_touch("s1", "Alice", "a@example.com", "2026-06-01T00:00:00", ("a.txt",))],
        new_sync_sha="s1",
        new_sync_at=datetime(2026, 6, 1),
        reset=True,
    )
    store.apply_commits(
        "unused",
        "feature",
        [_touch("s2", "Bob", "b@example.com", "2026-06-01T00:00:00", ("b.txt",))],
        new_sync_sha="s2",
        new_sync_at=datetime(2026, 6, 1),
        reset=True,
    )

    assert store.load("unused", "main").ownership.keys() == {"a@example.com"}
    assert store.load("unused", "feature").ownership.keys() == {"b@example.com"}


@pytest.mark.asyncio
async def test_concurrent_apply_commits_does_not_lose_either_writers_commits(pool):
    # Real bug found via audit: apply_commits used to read the current
    # snapshot (self.load, its own separate connection) before opening the
    # connection that deletes and re-inserts the merged result, with
    # nothing serializing two concurrent callers for the same
    # (installation_id, repo_full_name, branch). Nothing upstream prevents
    # that in production either - no webhook handler enqueues a scan job
    # with a dedup key. Whichever writer's DELETE+INSERT committed last
    # won outright; the other writer's merged commits were not merged with
    # it, they were gone.
    #
    # Reproduced deterministically here by widening the real read-to-write
    # window with an artificial delay inside _load_with_cursor (the read
    # step) - on the pre-fix code this reliably made the second thread's
    # own read start while the first thread was still "working" (i.e.
    # before the first thread had written anything), the exact interleaving
    # that loses an update. On the fix, apply_commits holds the advisory
    # lock across this same delay, so the second thread's read cannot start
    # until the first thread's write has already committed and the lock
    # is released.
    await _insert_installation(pool, 609, "org")
    store = PostgresRepoGraphStore(TEST_DATABASE_URL, 609, "org/repo")
    store.apply_commits(
        "unused",
        "main",
        [_touch("s0", "Root", "root@example.com", "2026-06-01T00:00:00", ("root.txt",))],
        new_sync_sha="s0",
        new_sync_at=datetime(2026, 6, 1),
        reset=True,
    )

    original_load = PostgresRepoGraphStore._load_with_cursor

    def _slow_load(self, cur, branch):
        result = original_load(self, cur, branch)
        time.sleep(0.3)
        return result

    results: dict[str, Exception] = {}

    def _apply(sha, author, email, filename):
        try:
            store.apply_commits(
                "unused",
                "main",
                [_touch(sha, author, email, "2026-06-02T00:00:00", (filename,))],
                new_sync_sha=sha,
                new_sync_at=datetime(2026, 6, 2),
                reset=False,
            )
        except Exception as exc:  # noqa: BLE001 - surfaced via `results` below
            results[sha] = exc

    with patch.object(PostgresRepoGraphStore, "_load_with_cursor", _slow_load):
        thread_a = threading.Thread(target=_apply, args=("sa", "Alice", "a@example.com", "a.txt"))
        thread_b = threading.Thread(target=_apply, args=("sb", "Bob", "b@example.com", "b.txt"))
        thread_a.start()
        time.sleep(0.05)  # let thread_a acquire the lock and start its (slow) read first
        thread_b.start()
        thread_a.join(timeout=5)
        thread_b.join(timeout=5)

    assert not results, f"apply_commits raised: {results}"
    snapshot = store.load("unused", "main")
    # Both concurrent writers' commits must survive - neither is a lost update.
    assert "a@example.com" in snapshot.ownership
    assert "b@example.com" in snapshot.ownership
    assert "a.txt" in snapshot.file_churn
    assert "b.txt" in snapshot.file_churn


@pytest.mark.asyncio
async def test_apply_commits_lock_does_not_serialize_different_repos(pool):
    # The fix must not accidentally serialize every scan globally - only
    # concurrent callers for the SAME (installation_id, repo_full_name,
    # branch) should ever wait on each other.
    await _insert_installation(pool, 610, "org-x")
    await _insert_installation(pool, 611, "org-y")
    store_a = PostgresRepoGraphStore(TEST_DATABASE_URL, 610, "org-x/repo")
    store_b = PostgresRepoGraphStore(TEST_DATABASE_URL, 611, "org-y/repo")

    original_write = PostgresRepoGraphStore._write_merged

    def _slow_write(self, cur, branch, merged, new_sync_sha, new_sync_at):
        if self._repo_full_name == "org-x/repo":
            time.sleep(0.3)
        return original_write(self, cur, branch, merged, new_sync_sha, new_sync_at)

    with patch.object(PostgresRepoGraphStore, "_write_merged", _slow_write):
        thread_a = threading.Thread(
            target=lambda: store_a.apply_commits(
                "unused",
                "main",
                [_touch("s1", "Alice", "a@example.com", "2026-06-01T00:00:00", ("a.txt",))],
                new_sync_sha="s1",
                new_sync_at=datetime(2026, 6, 1),
                reset=True,
            )
        )
        thread_a.start()
        time.sleep(0.05)  # let thread_a acquire its lock and enter the slow write first

        start = time.monotonic()
        store_b.apply_commits(
            "unused",
            "main",
            [_touch("s2", "Bob", "b@example.com", "2026-06-01T00:00:00", ("b.txt",))],
            new_sync_sha="s2",
            new_sync_at=datetime(2026, 6, 1),
            reset=True,
        )
        elapsed = time.monotonic() - start
        thread_a.join(timeout=5)

    # store_b's own call was never patched to be slow, and a different repo
    # must not be blocked by store_a's in-flight lock - it should return
    # well under store_a's own artificial 0.3s delay.
    assert elapsed < 0.2


@pytest.mark.asyncio
async def test_installation_deletion_cascades_to_graph_tables(pool):
    await _insert_installation(pool, 608, "org")
    store = PostgresRepoGraphStore(TEST_DATABASE_URL, 608, "org/repo")
    store.apply_commits(
        "unused",
        "main",
        [_touch("s1", "Alice", "a@example.com", "2026-06-01T00:00:00", ("a.txt",))],
        new_sync_sha="s1",
        new_sync_at=datetime(2026, 6, 1),
        reset=True,
    )

    await pool.execute("DELETE FROM installations WHERE installation_id = 608")

    snapshot = store.load("unused", "main")
    assert snapshot.last_synced_sha is None
