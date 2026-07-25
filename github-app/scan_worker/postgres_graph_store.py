"""Hosted-service counterpart to aletheore.git_intel.sqlite_store - same
RepoGraphStore protocol, Postgres instead of a local file, keyed by
installation_id/repo_full_name/branch instead of the CLI's repo_key.

Every hosted scan clones a fresh, throwaway repo copy, so the CLI's own
local .aletheore/graph.db never persists between scans on its own - this
is what makes a repeat scan of the same installation's repo actually
incremental (see scan_worker.jobs.run_pr_scan_job, the one place this
gets wired in) rather than a from-scratch baseline walk every time.
"""

from __future__ import annotations

import json
from datetime import date, datetime

from aletheore.git_intel.graph_store import (
    CommitTouch,
    FileChurnTotal,
    GraphSnapshot,
    OwnershipTotal,
    RecentCommit,
)
from aletheore.git_intel.incremental import fold


class PostgresRepoGraphStore:
    def __init__(self, dsn: str, installation_id: int, repo_full_name: str):
        self._dsn = dsn
        self._installation_id = installation_id
        self._repo_full_name = repo_full_name

    def load(self, repo_key: str, branch: str) -> GraphSnapshot:
        # repo_key is part of the RepoGraphStore protocol (the CLI's local
        # store uses it as its identity), but the hosted store already has
        # a stronger, natural identity - installation_id + repo_full_name,
        # set at construction - so it's accepted for interface compliance
        # and otherwise ignored here.
        import psycopg

        with psycopg.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT last_synced_sha, last_synced_at FROM evidence_git_sync_state "
                    "WHERE installation_id = %s AND repo_full_name = %s AND branch = %s",
                    (self._installation_id, self._repo_full_name, branch),
                )
                sync_row = cur.fetchone()
                if sync_row is None:
                    return GraphSnapshot.empty()

                ownership: dict[str, OwnershipTotal] = {}
                cur.execute(
                    "SELECT email, names, commit_count FROM evidence_git_ownership "
                    "WHERE installation_id = %s AND repo_full_name = %s AND branch = %s",
                    (self._installation_id, self._repo_full_name, branch),
                )
                for email, names, commit_count in cur.fetchall():
                    ownership[email] = OwnershipTotal(email=email, names=set(names), commit_count=commit_count)

                cadence: dict[date, int] = {}
                cur.execute(
                    "SELECT week_start, commit_count FROM evidence_git_cadence "
                    "WHERE installation_id = %s AND repo_full_name = %s AND branch = %s",
                    (self._installation_id, self._repo_full_name, branch),
                )
                for week_start, commit_count in cur.fetchall():
                    cadence[week_start] = commit_count

                file_churn: dict[str, FileChurnTotal] = {}
                cur.execute(
                    "SELECT path, churn_count, recent_commits, co_change_counts FROM evidence_git_file_churn "
                    "WHERE installation_id = %s AND repo_full_name = %s AND branch = %s",
                    (self._installation_id, self._repo_full_name, branch),
                )
                for path, churn_count, recent_commits, co_change_counts in cur.fetchall():
                    file_churn[path] = FileChurnTotal(
                        path=path,
                        churn_count=churn_count,
                        recent_commits=[
                            RecentCommit(
                                sha=r["sha"],
                                author_name=r["author_name"],
                                author_email=r["author_email"],
                                committed_at=datetime.fromisoformat(r["committed_at"]),
                            )
                            for r in recent_commits
                        ],
                        co_change_counts=co_change_counts,
                    )

        return GraphSnapshot(
            last_synced_sha=sync_row[0],
            last_synced_at=sync_row[1],
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
        import psycopg

        current = GraphSnapshot.empty() if reset else self.load(repo_key, branch)
        merged = fold(current, commits)

        with psycopg.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM evidence_git_sync_state "
                    "WHERE installation_id = %s AND repo_full_name = %s AND branch = %s",
                    (self._installation_id, self._repo_full_name, branch),
                )
                cur.execute(
                    "DELETE FROM evidence_git_ownership "
                    "WHERE installation_id = %s AND repo_full_name = %s AND branch = %s",
                    (self._installation_id, self._repo_full_name, branch),
                )
                cur.execute(
                    "DELETE FROM evidence_git_cadence "
                    "WHERE installation_id = %s AND repo_full_name = %s AND branch = %s",
                    (self._installation_id, self._repo_full_name, branch),
                )
                cur.execute(
                    "DELETE FROM evidence_git_file_churn "
                    "WHERE installation_id = %s AND repo_full_name = %s AND branch = %s",
                    (self._installation_id, self._repo_full_name, branch),
                )

                cur.execute(
                    "INSERT INTO evidence_git_sync_state "
                    "(installation_id, repo_full_name, branch, last_synced_sha, last_synced_at) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (self._installation_id, self._repo_full_name, branch, new_sync_sha, new_sync_at),
                )

                if merged.ownership:
                    cur.executemany(
                        "INSERT INTO evidence_git_ownership "
                        "(installation_id, repo_full_name, branch, email, names, commit_count) "
                        "VALUES (%s, %s, %s, %s, %s::jsonb, %s)",
                        (
                            (
                                self._installation_id,
                                self._repo_full_name,
                                branch,
                                email,
                                json.dumps(sorted(total.names)),
                                total.commit_count,
                            )
                            for email, total in merged.ownership.items()
                        ),
                    )

                if merged.cadence_weekly_counts:
                    cur.executemany(
                        "INSERT INTO evidence_git_cadence "
                        "(installation_id, repo_full_name, branch, week_start, commit_count) "
                        "VALUES (%s, %s, %s, %s, %s)",
                        (
                            (self._installation_id, self._repo_full_name, branch, week_start, count)
                            for week_start, count in merged.cadence_weekly_counts.items()
                        ),
                    )

                if merged.file_churn:
                    cur.executemany(
                        "INSERT INTO evidence_git_file_churn "
                        "(installation_id, repo_full_name, branch, path, churn_count, recent_commits, "
                        "co_change_counts) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)",
                        (
                            (
                                self._installation_id,
                                self._repo_full_name,
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
                                        }
                                        for r in churn.recent_commits
                                    ]
                                ),
                                json.dumps(churn.co_change_counts),
                            )
                            for path, churn in merged.file_churn.items()
                        ),
                    )
            conn.commit()
