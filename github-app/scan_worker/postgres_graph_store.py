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

    def _load_with_cursor(self, cur, branch: str) -> GraphSnapshot:
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
            "SELECT path, churn_count, recent_commits, co_change_counts, owners FROM evidence_git_file_churn "
            "WHERE installation_id = %s AND repo_full_name = %s AND branch = %s",
            (self._installation_id, self._repo_full_name, branch),
        )
        for path, churn_count, recent_commits, co_change_counts, owners in cur.fetchall():
            file_churn[path] = FileChurnTotal(
                path=path,
                churn_count=churn_count,
                recent_commits=[
                    RecentCommit(
                        sha=r["sha"],
                        author_name=r["author_name"],
                        author_email=r["author_email"],
                        committed_at=datetime.fromisoformat(r["committed_at"]),
                        # .get(): rows written before subject-capture
                        # was added have no such key - default ""
                        # rather than KeyError.
                        subject=r.get("subject", ""),
                    )
                    for r in recent_commits
                ],
                co_change_counts=co_change_counts,
                owners={
                    email: OwnershipTotal(
                        email=email,
                        names=set(owner["names"]),
                        commit_count=owner["commit_count"],
                    )
                    for email, owner in (owners or {}).items()
                },
            )

        return GraphSnapshot(
            last_synced_sha=sync_row[0],
            last_synced_at=sync_row[1],
            ownership=ownership,
            cadence_weekly_counts=cadence,
            file_churn=file_churn,
        )

    def load(self, repo_key: str, branch: str) -> GraphSnapshot:
        # repo_key is part of the RepoGraphStore protocol (the CLI's local
        # store uses it as its identity), but the hosted store already has
        # a stronger, natural identity - installation_id + repo_full_name,
        # set at construction - so it's accepted for interface compliance
        # and otherwise ignored here.
        import psycopg

        with psycopg.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                return self._load_with_cursor(cur, branch)

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
        """Real bug found via audit: this used to read the current snapshot
        (self.load, its own separate connection) before opening the
        connection that deletes and re-inserts the merged result - a plain
        read-modify-write with nothing serializing two concurrent callers
        for the same (installation_id, repo_full_name, branch). Nothing
        upstream prevents that: no webhook handler enqueues a scan job with
        a dedup key (confirmed directly - grepped every `Queue(...).enqueue`
        call site), so a second push landing while the first push's scan is
        still running, or a push and a PR sync event racing each other,
        both call this. Whichever writer's DELETE+INSERT commits last wins
        outright - the other writer's merged commits are not merged with
        it, they are gone, since each independently read the same stale
        `current` snapshot before either had written anything.

        Fixed with a session-scoped Postgres advisory lock
        (pg_advisory_lock, not pg_advisory_xact_lock - the read and the
        write need to be serialized together as one critical section, and
        xact-level locks release at COMMIT, before this method is done)
        keyed on the same (installation_id, repo_full_name, branch) the
        rows themselves are keyed on, held for this whole method on one
        connection spanning both the read and the write - unlike before,
        where the read used a separate connection/session entirely and so
        could never have been serialized by a lock scoped to the write's
        own transaction alone. A concurrent caller for a DIFFERENT
        installation/repo/branch hashes to a different lock key and is not
        blocked by this at all.

        No explicit pg_advisory_unlock: a session-scoped lock is released
        when the session (this connection) ends regardless of how it ends,
        and `with psycopg.connect(...)` below already guarantees that on
        every exit path, success or exception. An explicit unlock in a
        `finally` was tried and rejected - if _write_merged raises mid-
        transaction, the transaction is left in Postgres's own "aborted"
        state, and the unlock statement would itself fail in that state
        (InFailedSqlTransaction), replacing the real error with a
        confusing one about the failed cleanup attempt instead. Letting
        connection teardown release the lock avoids that entirely and
        still releases it correctly either way.
        """
        import psycopg

        lock_key = f"{self._installation_id}:{self._repo_full_name}:{branch}"

        with psycopg.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                # hashtextextended(text, seed) -> bigint: a stable, built-in
                # way to turn an arbitrary string key into the single bigint
                # pg_advisory_lock takes, without hand-rolling a hash in
                # Python that would need to match Postgres's own hashing to
                # be useful for anything (it doesn't need to - only this
                # process ever calls pg_advisory_lock with this key shape,
                # so any stable hash function works; hashtextextended just
                # avoids writing one).
                cur.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (lock_key,))
                current = GraphSnapshot.empty() if reset else self._load_with_cursor(cur, branch)
                merged = fold(current, commits)
                self._write_merged(cur, branch, merged, new_sync_sha, new_sync_at)
                conn.commit()

    def _write_merged(
        self,
        cur,
        branch: str,
        merged: GraphSnapshot,
        new_sync_sha: str,
        new_sync_at: datetime,
    ) -> None:
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
                "co_change_counts, owners) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb)",
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
                                    "subject": r.subject,
                                }
                                for r in churn.recent_commits
                            ]
                        ),
                        json.dumps(churn.co_change_counts),
                        json.dumps(
                            {
                                email: {
                                    "names": sorted(owner.names),
                                    "commit_count": owner.commit_count,
                                }
                                for email, owner in churn.owners.items()
                            }
                        ),
                    )
                    for path, churn in merged.file_churn.items()
                ),
            )
