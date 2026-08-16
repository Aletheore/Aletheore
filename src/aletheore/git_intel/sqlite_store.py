"""Local, per-repo persistence for the incremental git graph - what makes
`aletheore scan` on a solo developer's own machine incremental too, not
just the hosted service. Lives at `.aletheore/graph.db`, gitignored like
the rest of `.aletheore/`'s scan output.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path

from aletheore.git_intel.graph_store import (
    CommitTouch,
    FileChurnTotal,
    GraphSnapshot,
    OwnershipTotal,
    RecentCommit,
)
from aletheore.git_intel.incremental import fold

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sync_state (
    repo_key TEXT NOT NULL,
    branch TEXT NOT NULL,
    last_synced_sha TEXT NOT NULL,
    last_synced_at TEXT NOT NULL,
    PRIMARY KEY (repo_key, branch)
);

CREATE TABLE IF NOT EXISTS ownership (
    repo_key TEXT NOT NULL,
    branch TEXT NOT NULL,
    email TEXT NOT NULL,
    names TEXT NOT NULL,
    commit_count INTEGER NOT NULL,
    PRIMARY KEY (repo_key, branch, email)
);

CREATE TABLE IF NOT EXISTS cadence (
    repo_key TEXT NOT NULL,
    branch TEXT NOT NULL,
    week_start TEXT NOT NULL,
    commit_count INTEGER NOT NULL,
    PRIMARY KEY (repo_key, branch, week_start)
);

CREATE TABLE IF NOT EXISTS file_churn (
    repo_key TEXT NOT NULL,
    branch TEXT NOT NULL,
    path TEXT NOT NULL,
    churn_count INTEGER NOT NULL,
    recent_commits TEXT NOT NULL,
    co_change_counts TEXT NOT NULL,
    owners TEXT NOT NULL,
    PRIMARY KEY (repo_key, branch, path)
);
"""


def default_graph_db_path(repo_path: Path) -> Path:
    return repo_path / ".aletheore" / "graph.db"


class SQLiteRepoGraphStore:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.executescript(_SCHEMA)
        try:
            self._conn.execute("ALTER TABLE file_churn ADD COLUMN owners TEXT NOT NULL DEFAULT '{}'")
        except sqlite3.OperationalError:
            pass  # column already exists
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def load(self, repo_key: str, branch: str) -> GraphSnapshot:
        cur = self._conn.cursor()

        cur.execute(
            "SELECT last_synced_sha, last_synced_at FROM sync_state WHERE repo_key = ? AND branch = ?",
            (repo_key, branch),
        )
        sync_row = cur.fetchone()
        if sync_row is None:
            return GraphSnapshot.empty()

        ownership: dict[str, OwnershipTotal] = {}
        cur.execute(
            "SELECT email, names, commit_count FROM ownership WHERE repo_key = ? AND branch = ?",
            (repo_key, branch),
        )
        for email, names_json, commit_count in cur.fetchall():
            ownership[email] = OwnershipTotal(email=email, names=set(json.loads(names_json)), commit_count=commit_count)

        cadence: dict[date, int] = {}
        cur.execute(
            "SELECT week_start, commit_count FROM cadence WHERE repo_key = ? AND branch = ?",
            (repo_key, branch),
        )
        for week_start_str, commit_count in cur.fetchall():
            cadence[date.fromisoformat(week_start_str)] = commit_count

        file_churn: dict[str, FileChurnTotal] = {}
        cur.execute(
            "SELECT path, churn_count, recent_commits, co_change_counts, owners FROM file_churn "
            "WHERE repo_key = ? AND branch = ?",
            (repo_key, branch),
        )
        for path, churn_count, recent_json, co_change_json, owners_json in cur.fetchall():
            recent_commits = [
                RecentCommit(
                    sha=r["sha"],
                    author_name=r["author_name"],
                    author_email=r["author_email"],
                    committed_at=datetime.fromisoformat(r["committed_at"]),
                    # .get(): rows written before subject-capture was added
                    # have no such key - default "" rather than KeyError.
                    subject=r.get("subject", ""),
                )
                for r in json.loads(recent_json)
            ]
            file_churn[path] = FileChurnTotal(
                path=path,
                churn_count=churn_count,
                recent_commits=recent_commits,
                co_change_counts=json.loads(co_change_json),
                owners={
                    email: OwnershipTotal(email=email, names=set(owner["names"]), commit_count=owner["commit_count"])
                    for email, owner in json.loads(owners_json).items()
                },
            )

        return GraphSnapshot(
            last_synced_sha=sync_row[0],
            last_synced_at=datetime.fromisoformat(sync_row[1]),
            ownership=ownership,
            cadence_weekly_counts=cadence,
            file_churn=file_churn,
        )

    def apply_commits(
        self,
        repo_key: str,
        branch: str,
        commits: list[CommitTouch],
        new_sync_sha: str,
        new_sync_at: datetime,
        *,
        reset: bool,
    ) -> None:
        current = GraphSnapshot.empty() if reset else self.load(repo_key, branch)
        merged = fold(current, commits)

        with self._conn:
            self._conn.execute("DELETE FROM sync_state WHERE repo_key = ? AND branch = ?", (repo_key, branch))
            self._conn.execute("DELETE FROM ownership WHERE repo_key = ? AND branch = ?", (repo_key, branch))
            self._conn.execute("DELETE FROM cadence WHERE repo_key = ? AND branch = ?", (repo_key, branch))
            self._conn.execute("DELETE FROM file_churn WHERE repo_key = ? AND branch = ?", (repo_key, branch))

            self._conn.execute(
                "INSERT INTO sync_state (repo_key, branch, last_synced_sha, last_synced_at) VALUES (?, ?, ?, ?)",
                (repo_key, branch, new_sync_sha, new_sync_at.isoformat()),
            )
            self._conn.executemany(
                "INSERT INTO ownership (repo_key, branch, email, names, commit_count) VALUES (?, ?, ?, ?, ?)",
                (
                    (repo_key, branch, email, json.dumps(sorted(total.names)), total.commit_count)
                    for email, total in merged.ownership.items()
                ),
            )
            self._conn.executemany(
                "INSERT INTO cadence (repo_key, branch, week_start, commit_count) VALUES (?, ?, ?, ?)",
                (
                    (repo_key, branch, week_start.isoformat(), count)
                    for week_start, count in merged.cadence_weekly_counts.items()
                ),
            )
            self._conn.executemany(
                "INSERT INTO file_churn (repo_key, branch, path, churn_count, recent_commits, co_change_counts, owners) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    (
                        repo_key,
                        branch,
                        path,
                        churn.churn_count,
                        json.dumps(
                            [
                                {
                                    "sha": r.sha,
                                    "author_name": r.author_name,
                                    "author_email": r.author_email,
                                    "committed_at": r.committed_at.isoformat(),
                                    "subject": r.subject,
                                }
                                for r in churn.recent_commits
                            ]
                        ),
                        json.dumps(churn.co_change_counts),
                        json.dumps(
                            {
                                email: {"names": sorted(owner.names), "commit_count": owner.commit_count}
                                for email, owner in churn.owners.items()
                            }
                        ),
                    )
                    for path, churn in merged.file_churn.items()
                ),
            )
