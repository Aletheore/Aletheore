"""Streaming git-log reader and pure aggregation logic for the incremental
repository graph (see graph_store.py for the persisted shape).

The memory-safety property this module exists for: `git log`'s formatted
output for a commit range is read line-by-line via Popen and folded
directly into running aggregates - the raw per-commit text is never held
as a single buffered string or a full in-memory list. Confirmed directly:
the old approach (`subprocess.run(capture_output=True)`) buffering the
entire formatted output was what got OOM-killed scanning torvalds/linux's
1.46M commits under a 1GB memory limit.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from pathlib import Path

from aletheore.git_intel.graph_store import (
    CommitTouch,
    FileChurnTotal,
    GraphSnapshot,
    OwnershipTotal,
    RecentCommit,
)

MASS_COMMIT_FILE_THRESHOLD = 50
RECENT_COMMITS_PER_FILE = 10
CO_CHANGE_PARTNERS_RETURNED = 5

# A commit header line in the streamed format below always starts with this
# byte - real file paths never do, so it unambiguously marks "new commit"
# without needing a second git invocation just to know where one ends.
# `%x00` (four literal characters) is git's own pretty-format escape for a
# NUL byte - it belongs in the --format argument. The real `\x00` byte only
# ever appears in git's *output*, never in argv (POSIX forbids embedding a
# raw NUL in an argv string - subprocess correctly rejects that outright).
_RECORD_SEP_FORMAT = "%x00"
_RECORD_SEP = "\x00"
_FIELD_SEP = "\x1f"


class GitLogStreamError(RuntimeError):
    """The `git log` subprocess backing the stream failed or was killed -
    most commonly the OS OOM killer on a rev-range still too large for the
    memory available, despite streaming. Distinct from GitAnalysisError
    (analyzer.py) so this module has no import dependency on it; analyzer.py
    catches this and re-raises as GitAnalysisError for callers that already
    handle that type."""


def stream_commit_touches(
    repo_path: Path, rev_range: str, *, max_commits: int | None = None
) -> Iterator[CommitTouch]:
    args = [
        "log",
        f"--format={_RECORD_SEP_FORMAT}%H{_FIELD_SEP}%an{_FIELD_SEP}%ae{_FIELD_SEP}%ad",
        "--date=iso-strict",
        "--name-only",
    ]
    if max_commits is not None:
        args += ["-n", str(max_commits)]
    args.append(rev_range)

    proc = subprocess.Popen(
        ["git", *args],
        cwd=repo_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="ignore",
    )
    assert proc.stdout is not None

    pending_header: tuple[str, str, str, datetime] | None = None
    pending_files: list[str] = []
    try:
        for raw_line in proc.stdout:
            line = raw_line.rstrip("\n")
            if line.startswith(_RECORD_SEP):
                if pending_header is not None:
                    yield CommitTouch(*pending_header, files=tuple(pending_files))
                sha, name, email, date_str = line[1:].split(_FIELD_SEP)
                pending_header = (sha, name, email, datetime.fromisoformat(date_str))
                pending_files = []
            elif line.strip():
                pending_files.append(line.strip())
        if pending_header is not None:
            yield CommitTouch(*pending_header, files=tuple(pending_files))
    finally:
        proc.stdout.close()
        returncode = proc.wait()

    if returncode != 0:
        stderr = proc.stderr.read() if proc.stderr else ""
        if proc.stderr:
            proc.stderr.close()
        if returncode < 0:
            raise GitLogStreamError(
                f"git log {rev_range} was killed (likely out of memory) - this repository's "
                "history may be too large to analyze with the memory available"
            )
        raise GitLogStreamError(f"git log {rev_range} failed with exit code {returncode}: {stderr}")


def _week_start(at: datetime) -> date:
    d = at.date()
    return d - timedelta(days=d.weekday())


def fold(snapshot: GraphSnapshot, commits: list[CommitTouch]) -> GraphSnapshot:
    """Pure aggregation: merges `commits` into `snapshot`, returning a new
    snapshot. Both store backends call this internally so the aggregation
    algorithm - what counts as ownership, how co-change is filtered, how
    many recent commits per file are kept - lives in exactly one place
    rather than being reimplemented per backend.
    """
    ownership = {email: OwnershipTotal(o.email, set(o.names), o.commit_count) for email, o in snapshot.ownership.items()}
    cadence = dict(snapshot.cadence_weekly_counts)
    file_churn = {
        path: FileChurnTotal(f.path, f.churn_count, list(f.recent_commits), dict(f.co_change_counts))
        for path, f in snapshot.file_churn.items()
    }

    for commit in commits:
        email_key = commit.author_email.lower()
        total = ownership.get(email_key)
        if total is None:
            total = OwnershipTotal(email=email_key, names=set(), commit_count=0)
            ownership[email_key] = total
        total.names.add(commit.author_name)
        total.commit_count += 1

        week = _week_start(commit.committed_at)
        cadence[week] = cadence.get(week, 0) + 1

        unique_files = sorted(set(commit.files))
        recent = RecentCommit(commit.sha, commit.author_name, commit.author_email, commit.committed_at)
        for path in unique_files:
            churn = file_churn.get(path)
            if churn is None:
                churn = FileChurnTotal(path=path, churn_count=0, recent_commits=[], co_change_counts={})
                file_churn[path] = churn
            churn.churn_count += 1
            churn.recent_commits.insert(0, recent)
            del churn.recent_commits[RECENT_COMMITS_PER_FILE:]

        if len(unique_files) <= MASS_COMMIT_FILE_THRESHOLD:
            for i, first in enumerate(unique_files):
                for second in unique_files[i + 1 :]:
                    file_churn[first].co_change_counts[second] = file_churn[first].co_change_counts.get(second, 0) + 1
                    file_churn[second].co_change_counts[first] = file_churn[second].co_change_counts.get(first, 0) + 1

    return GraphSnapshot(
        last_synced_sha=snapshot.last_synced_sha,
        last_synced_at=snapshot.last_synced_at,
        ownership=ownership,
        cadence_weekly_counts=cadence,
        file_churn=file_churn,
    )
