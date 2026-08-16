from datetime import date, datetime, timedelta
import sqlite3
from pathlib import Path

from aletheore.git_intel.graph_store import CommitTouch, GraphSnapshot
from aletheore.git_intel.incremental import fold
from aletheore.git_intel.sqlite_store import SQLiteRepoGraphStore, default_graph_db_path


def _touch(sha, name, email, date_str, files):
    return CommitTouch(sha, name, email, datetime.fromisoformat(date_str), files)


def _store(tmp_path: Path) -> SQLiteRepoGraphStore:
    return SQLiteRepoGraphStore(tmp_path / "graph.db")


def test_load_returns_empty_snapshot_for_unknown_repo(tmp_path):
    store = _store(tmp_path)
    snapshot = store.load("repo-1", "main")
    assert snapshot.last_synced_sha is None
    assert snapshot.ownership == {}
    assert snapshot.file_churn == {}


def test_apply_commits_then_load_round_trips_correctly(tmp_path):
    store = _store(tmp_path)
    commits = [
        _touch("s1", "Alice", "a@example.com", "2026-06-01T00:00:00+00:00", ("a.txt", "b.txt")),
        _touch("s2", "Bob", "b@example.com", "2026-06-08T00:00:00+00:00", ("a.txt",)),
    ]
    store.apply_commits(
        "repo-1", "main", commits, new_sync_sha="s2", new_sync_at=datetime(2026, 6, 8), reset=True
    )

    snapshot = store.load("repo-1", "main")
    assert snapshot.last_synced_sha == "s2"
    assert snapshot.last_synced_at == datetime(2026, 6, 8)
    assert snapshot.ownership["a@example.com"].commit_count == 1
    assert snapshot.ownership["a@example.com"].names == {"Alice"}
    assert snapshot.ownership["b@example.com"].commit_count == 1
    assert snapshot.file_churn["a.txt"].churn_count == 2
    assert snapshot.file_churn["a.txt"].co_change_counts == {"b.txt": 1}
    assert len(snapshot.file_churn["a.txt"].recent_commits) == 2
    assert snapshot.file_churn["a.txt"].recent_commits[0].sha == "s2"  # newest first
    assert snapshot.file_churn["a.txt"].owners["a@example.com"].commit_count == 1
    assert snapshot.file_churn["a.txt"].owners["b@example.com"].commit_count == 1


def test_existing_database_without_file_owners_column_upgrades_in_place(tmp_path):
    db_path = tmp_path / "graph.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE sync_state (repo_key TEXT NOT NULL, branch TEXT NOT NULL,
            last_synced_sha TEXT NOT NULL, last_synced_at TEXT NOT NULL,
            PRIMARY KEY (repo_key, branch));
        CREATE TABLE ownership (repo_key TEXT NOT NULL, branch TEXT NOT NULL,
            email TEXT NOT NULL, names TEXT NOT NULL, commit_count INTEGER NOT NULL,
            PRIMARY KEY (repo_key, branch, email));
        CREATE TABLE cadence (repo_key TEXT NOT NULL, branch TEXT NOT NULL,
            week_start TEXT NOT NULL, commit_count INTEGER NOT NULL,
            PRIMARY KEY (repo_key, branch, week_start));
        CREATE TABLE file_churn (repo_key TEXT NOT NULL, branch TEXT NOT NULL,
            path TEXT NOT NULL, churn_count INTEGER NOT NULL,
            recent_commits TEXT NOT NULL, co_change_counts TEXT NOT NULL,
            PRIMARY KEY (repo_key, branch, path));
        INSERT INTO sync_state VALUES ('repo-1', 'main', 's1', '2026-06-01T00:00:00');
        INSERT INTO file_churn VALUES ('repo-1', 'main', 'a.txt', 1, '[]', '{}');
        """
    )
    conn.commit()
    conn.close()

    store = SQLiteRepoGraphStore(db_path)
    snapshot = store.load("repo-1", "main")
    assert snapshot.file_churn["a.txt"].owners == {}


def test_incremental_apply_matches_a_single_full_fold(tmp_path):
    # The real correctness property: applying a baseline then a later delta
    # through the store must produce the exact same persisted state as
    # applying everything in one pass - proving the store's own merge logic
    # (load current -> fold -> overwrite) doesn't drift from the pure fold().
    baseline = [
        _touch("s1", "Alice", "a@example.com", "2026-06-01T00:00:00+00:00", ("a.txt", "b.txt")),
        _touch("s2", "Bob", "b@example.com", "2026-06-02T00:00:00+00:00", ("a.txt",)),
    ]
    delta = [_touch("s3", "Alice", "a@example.com", "2026-06-15T00:00:00+00:00", ("c.txt",))]

    incremental_store = _store(tmp_path)
    incremental_store.apply_commits(
        "repo-1", "main", baseline, new_sync_sha="s2", new_sync_at=datetime(2026, 6, 2), reset=True
    )
    incremental_store.apply_commits(
        "repo-1", "main", delta, new_sync_sha="s3", new_sync_at=datetime(2026, 6, 15), reset=False
    )
    incremental_result = incremental_store.load("repo-1", "main")

    expected = fold(GraphSnapshot.empty(), baseline + delta)

    assert incremental_result.ownership.keys() == expected.ownership.keys()
    for email in expected.ownership:
        assert incremental_result.ownership[email].commit_count == expected.ownership[email].commit_count
    assert incremental_result.file_churn.keys() == expected.file_churn.keys()
    for path in expected.file_churn:
        assert incremental_result.file_churn[path].churn_count == expected.file_churn[path].churn_count
        assert incremental_result.file_churn[path].co_change_counts == expected.file_churn[path].co_change_counts
    assert incremental_result.cadence_weekly_counts == expected.cadence_weekly_counts


def test_reset_clears_prior_state_instead_of_merging(tmp_path):
    store = _store(tmp_path)
    store.apply_commits(
        "repo-1",
        "main",
        [_touch("s1", "Alice", "a@example.com", "2026-06-01T00:00:00+00:00", ("old.txt",))],
        new_sync_sha="s1",
        new_sync_at=datetime(2026, 6, 1),
        reset=True,
    )
    store.apply_commits(
        "repo-1",
        "main",
        [_touch("s2", "Bob", "b@example.com", "2026-06-02T00:00:00+00:00", ("new.txt",))],
        new_sync_sha="s2",
        new_sync_at=datetime(2026, 6, 2),
        reset=True,  # force rebuild - e.g. a stale sync SHA no longer in history
    )

    snapshot = store.load("repo-1", "main")
    assert "a@example.com" not in snapshot.ownership
    assert "old.txt" not in snapshot.file_churn
    assert snapshot.ownership["b@example.com"].commit_count == 1


def test_different_repo_keys_are_isolated(tmp_path):
    store = _store(tmp_path)
    store.apply_commits(
        "repo-a",
        "main",
        [_touch("s1", "Alice", "a@example.com", "2026-06-01T00:00:00+00:00", ("a.txt",))],
        new_sync_sha="s1",
        new_sync_at=datetime(2026, 6, 1),
        reset=True,
    )
    store.apply_commits(
        "repo-b",
        "main",
        [_touch("s2", "Bob", "b@example.com", "2026-06-01T00:00:00+00:00", ("b.txt",))],
        new_sync_sha="s2",
        new_sync_at=datetime(2026, 6, 1),
        reset=True,
    )

    assert store.load("repo-a", "main").ownership.keys() == {"a@example.com"}
    assert store.load("repo-b", "main").ownership.keys() == {"b@example.com"}


def test_different_branches_of_the_same_repo_are_isolated(tmp_path):
    store = _store(tmp_path)
    store.apply_commits(
        "repo-1",
        "main",
        [_touch("s1", "Alice", "a@example.com", "2026-06-01T00:00:00+00:00", ("a.txt",))],
        new_sync_sha="s1",
        new_sync_at=datetime(2026, 6, 1),
        reset=True,
    )
    store.apply_commits(
        "repo-1",
        "feature",
        [_touch("s2", "Bob", "b@example.com", "2026-06-01T00:00:00+00:00", ("b.txt",))],
        new_sync_sha="s2",
        new_sync_at=datetime(2026, 6, 1),
        reset=True,
    )

    assert store.load("repo-1", "main").ownership.keys() == {"a@example.com"}
    assert store.load("repo-1", "feature").ownership.keys() == {"b@example.com"}


def test_state_persists_across_store_instances(tmp_path):
    # Confirms this is real disk persistence, not just an in-process cache -
    # a later `aletheore scan` run is a fresh process, so a fresh
    # SQLiteRepoGraphStore instance must see what an earlier one wrote.
    db_path = tmp_path / "graph.db"
    SQLiteRepoGraphStore(db_path).apply_commits(
        "repo-1",
        "main",
        [_touch("s1", "Alice", "a@example.com", "2026-06-01T00:00:00+00:00", ("a.txt",))],
        new_sync_sha="s1",
        new_sync_at=datetime(2026, 6, 1),
        reset=True,
    )

    reopened = SQLiteRepoGraphStore(db_path)
    snapshot = reopened.load("repo-1", "main")
    assert snapshot.last_synced_sha == "s1"
    assert snapshot.ownership["a@example.com"].commit_count == 1


def test_default_graph_db_path_lives_under_aletheore_dir(tmp_path):
    assert default_graph_db_path(tmp_path) == tmp_path / ".aletheore" / "graph.db"
