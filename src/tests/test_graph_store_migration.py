import sqlite3

from aletheore.git_intel.sqlite_store import SQLiteRepoGraphStore

_OLD_FILE_CHURN = """
CREATE TABLE sync_state (
    repo_key TEXT NOT NULL, branch TEXT NOT NULL, last_synced_sha TEXT NOT NULL, last_synced_at TEXT NOT NULL,
    PRIMARY KEY (repo_key, branch)
);
CREATE TABLE file_churn (
    repo_key TEXT NOT NULL, branch TEXT NOT NULL, path TEXT NOT NULL, churn_count INTEGER NOT NULL,
    recent_commits TEXT NOT NULL, co_change_counts TEXT NOT NULL,
    PRIMARY KEY (repo_key, branch, path)
);
"""


def test_adding_the_owners_column_forces_a_full_resync(tmp_path):
    # A graph.db from before per-file owners were stored. After the column is added its rows hold an empty owner
    # map but the sync state still says they are current, so without a resync no file ever gets an owner.
    db = tmp_path / "graph.db"
    conn = sqlite3.connect(db)
    conn.executescript(_OLD_FILE_CHURN)
    conn.execute("INSERT INTO sync_state VALUES ('r', 'main', 'abc123', '2026-09-01T00:00:00')")
    conn.execute("INSERT INTO file_churn VALUES ('r', 'main', 'a.py', 3, '[]', '{}')")
    conn.commit()
    conn.close()

    store = SQLiteRepoGraphStore(db)
    try:
        assert store.load("r", "main").last_synced_sha is None  # no recorded sync: the next one rebuilds
    finally:
        store.close()


def test_a_database_migrated_by_an_earlier_version_is_resynced_too(tmp_path):
    # The column exists already (an older version added it) but every row still holds the empty default.
    db = tmp_path / "graph.db"
    SQLiteRepoGraphStore(db).close()
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO sync_state VALUES ('r', 'main', 'abc123', '2026-09-01T00:00:00')")
    conn.execute("INSERT INTO file_churn VALUES ('r', 'main', 'a.py', 3, '[]', '{}', '{}')")
    conn.execute("INSERT INTO file_churn VALUES ('r', 'main', 'b.py', 5, '[]', '{}', '{}')")
    conn.commit()
    conn.close()

    store = SQLiteRepoGraphStore(db)
    try:
        assert store.load("r", "main").last_synced_sha is None
    finally:
        store.close()


def test_one_filled_owner_map_means_the_store_is_healthy(tmp_path):
    db = tmp_path / "graph.db"
    SQLiteRepoGraphStore(db).close()
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO sync_state VALUES ('r', 'main', 'abc123', '2026-09-01T00:00:00')")
    owner = '{"a@x": {"names": ["A"], "commit_count": 3}}'
    conn.execute("INSERT INTO file_churn VALUES ('r', 'main', 'a.py', 3, '[]', '{}', ?)", (owner,))
    conn.execute("INSERT INTO file_churn VALUES ('r', 'main', 'b.py', 5, '[]', '{}', '{}')")
    conn.commit()
    conn.close()

    store = SQLiteRepoGraphStore(db)
    try:
        assert store.load("r", "main").last_synced_sha == "abc123"
    finally:
        store.close()


def test_a_current_graph_db_keeps_its_sync_state(tmp_path):
    db = tmp_path / "graph.db"
    SQLiteRepoGraphStore(db).close()
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO sync_state VALUES ('r', 'main', 'abc123', '2026-09-01T00:00:00')")
    conn.commit()
    conn.close()

    store = SQLiteRepoGraphStore(db)  # column already there: nothing to migrate
    try:
        assert store.load("r", "main").last_synced_sha == "abc123"
    finally:
        store.close()
