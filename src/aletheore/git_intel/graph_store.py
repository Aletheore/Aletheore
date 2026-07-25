"""Backend-agnostic shape for the persistent, incremental repository graph.

Two goals drove this shape:
- Fix the real OOM confirmed on torvalds/linux (1.46M commits, 8.2GB): the
  old approach buffered `git log`'s full formatted output in memory before
  processing anything. The streaming engine (incremental.py) folds each
  commit directly into these aggregates as it reads them, so memory use is
  bounded by the aggregate size (unique files/authors touched), not by
  history size.
- Make repeat scans fast: once a baseline exists, only commits after the
  last-synced SHA need to be read - a normal push's worth, not the whole
  repository - and folded into the existing aggregates rather than
  recomputed from nothing.

Two concrete stores implement RepoGraphStore: SQLiteRepoGraphStore (CLI,
`.aletheore/graph.db`) and PostgresRepoGraphStore (hosted). Both must treat
`apply_commits` as all-or-nothing - a scan that fails partway through must
leave the store exactly as it was before the scan started, so a retry can
always resume cleanly from the last good sync point rather than working
from silently-corrupted aggregates.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol


@dataclass(frozen=True)
class RecentCommit:
    sha: str
    author_name: str
    author_email: str
    committed_at: datetime


@dataclass
class OwnershipTotal:
    email: str
    names: set[str]
    commit_count: int


@dataclass
class FileChurnTotal:
    path: str
    churn_count: int
    # Newest first, capped - see incremental.RECENT_COMMITS_PER_FILE. This is
    # what a runtime-correlation lookup ("recent commits touching this
    # file") reads - it must never require a fresh git log to answer.
    recent_commits: list[RecentCommit]
    # Partner path -> co-occurrence count. Only accumulated for commits
    # touching <= MASS_COMMIT_FILE_THRESHOLD files, matching the existing
    # hotspot algorithm's own noise filter (a mass rename/reformat commit
    # touching hundreds of files isn't a meaningful co-change signal).
    co_change_counts: dict[str, int]


@dataclass(frozen=True)
class CommitTouch:
    """One commit's worth of what the graph cares about - built while
    streaming `git log`, never held as part of a larger in-memory list of
    every commit. `files` is every path this commit touched, already
    normalized relative to the scan root."""

    sha: str
    author_name: str
    author_email: str
    committed_at: datetime
    files: tuple[str, ...]


@dataclass
class GraphSnapshot:
    """Everything analyze_git()/compute_hotspots() need, already aggregated
    - the whole point being that producing this never requires re-walking
    full git history once a baseline exists."""

    last_synced_sha: str | None
    last_synced_at: datetime | None
    # Keyed by lowercased email, matching the existing _ownership() behavior
    # (same person, differently-cased email, counted together).
    ownership: dict[str, OwnershipTotal]
    # Keyed by ISO week-start date.
    cadence_weekly_counts: dict[date, int]
    # Keyed by repo-relative path.
    file_churn: dict[str, FileChurnTotal]

    @staticmethod
    def empty() -> "GraphSnapshot":
        return GraphSnapshot(
            last_synced_sha=None,
            last_synced_at=None,
            ownership={},
            cadence_weekly_counts={},
            file_churn={},
        )


class RepoGraphStore(Protocol):
    def load(self, repo_key: str, branch: str) -> GraphSnapshot:
        """Returns GraphSnapshot.empty() if this repo+branch has never been
        synced before - never raises for "not found"."""
        ...

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
        """Fold `commits` into the persisted aggregates and advance the sync
        pointer, atomically. `reset=True` clears any prior state first
        (first-ever sync, or a forced rebuild after history was rewritten
        out from under a stale sync pointer) rather than merging into
        aggregates that no longer correspond to reality."""
        ...
