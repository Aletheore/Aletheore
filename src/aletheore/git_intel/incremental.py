"""Streaming git-log reader and pure aggregation logic for the incremental
repository graph (see graph_store.py for the persisted shape).

The memory-safety property this module exists for: `git log`'s formatted
output for a commit range is read line-by-line via Popen and folded
directly into running aggregates - the raw per-commit text is never held
as a single buffered string or a full in-memory list. Confirmed directly:
the old approach (`subprocess.run(capture_output=True)`) buffering the
entire formatted output was what got OOM-killed scanning torvalds/linux's
1.46M commits under a 1GB memory limit.

A second, independent bound matters just as much for large repos: without
MAX_CO_CHANGE_PARTNERS_TRACKED, the *aggregate* itself (not the raw log
text) is what OOM-kills a cold sync of a repo like torvalds/linux under a
1GB limit - confirmed by reproducing the OOM in a container capped at the
same limit as the production scan-worker, then tracing it to "hub" files
(MAINTAINERS, top-level Kconfig) whose co_change_counts dict grows one
entry per distinct file ever touched alongside them.
"""

from __future__ import annotations

import subprocess
import tempfile
from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone
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
# "Hub" files (MAINTAINERS, top-level Kconfig/Makefile) get touched
# alongside nearly every other file over a long enough history - without a
# cap, one file's co_change_counts dict grows without bound as history
# grows. Confirmed directly: on torvalds/linux (1.46M commits), MAINTAINERS'
# co-change JSON alone was ~780KB, and the aggregate across all files was
# the dominant cost behind a cold sync OOM-killing under a 1GB limit. Far
# above CO_CHANGE_PARTNERS_RETURNED so real (non-hub) files are never
# affected - only pathological hub files get their least-frequent, least
# useful partners evicted to stay bounded.
MAX_CO_CHANGE_PARTNERS_TRACKED = 200

# A commit header line in the streamed format below always starts with this
# byte - real file paths never do, so it unambiguously marks "new commit"
# without needing a second git invocation just to know where one ends. It
# also separates the fields *within* one header line (sha/name/email/date/
# subject) - not just between commits. Real bug found via audit: an earlier
# version used `\x1f` (unit separator) for the intra-line field split, on the
# assumption that a real commit would "vanishingly unlikely" contain a raw
# control byte. That assumption is false - GIT_AUTHOR_NAME accepts arbitrary
# bytes, and a name containing a literal `\x1f` (confirmed directly) shifted
# every field after it: author_email silently became a fragment of the
# author name, the commit's real date landed in the wrong field and failed
# to parse (silently falling back to the 1970 epoch), and the subject came
# out as the real date and subject concatenated - all with no error raised.
# `\x00` doesn't have this problem: it's git's own record-boundary byte and
# genuinely cannot occur inside any of these fields (not "unlikely" - a
# commit's author/committer line and message are built from NUL-terminated
# strings internally, so this is the same guarantee the record separator
# below already depends on, just applied to every field instead of one).
# `%x00` (four literal characters) is git's own pretty-format escape for a
# NUL byte - it belongs in the --format argument. The real `\x00` byte only
# ever appears in git's *output*, never in argv (POSIX forbids embedding a
# raw NUL in an argv string - subprocess correctly rejects that outright).
_RECORD_SEP_FORMAT = "%x00"
_RECORD_SEP = "\x00"


class GitLogStreamError(RuntimeError):
    """The `git log` subprocess backing the stream failed or was killed -
    most commonly the OS OOM killer on a rev-range still too large for the
    memory available, despite streaming. Distinct from GitAnalysisError
    (analyzer.py) so this module has no import dependency on it; analyzer.py
    catches this and re-raises as GitAnalysisError for callers that already
    handle that type."""


def _scan_root_prefix(repo_path: Path) -> str:
    # git log's paths are always relative to the repo root, not cwd - when
    # repo_path is a subdirectory of the actual git root (e.g. a monorepo
    # component), files must be re-relativized to it, and anything outside
    # it excluded, matching how the rest of the scanner treats repo_path as
    # the analysis root.
    result = subprocess.run(
        ["git", "rev-parse", "--show-prefix"], cwd=repo_path, capture_output=True, text=True, errors="ignore"
    )
    return result.stdout.strip() if result.returncode == 0 else ""


# A fallback for a commit whose author/committer date is real garbage, not
# just an unrecognized-but-valid timezone - confirmed on a real repo
# (requests, upstream): git's own `--date=iso-strict` faithfully reproduces
# whatever timezone offset was recorded at commit time, and at least one
# real historical commit has an offset outside any valid UTC range
# ('+518:00' - no such timezone exists; this is a corrupted local clock at
# authorship time, not a git or Aletheore bug). Ownership/hotspot analysis
# needs *some* real datetime for every commit, never a crash, and treating
# a bad date as "very old" rather than dropping the commit keeps it counted
# for ownership/authorship purposes without letting it skew recency-based
# ranking as if it just happened.
_EPOCH_UTC = datetime(1970, 1, 1, tzinfo=timezone.utc)


def parse_commit_date(date_str: str) -> datetime:
    try:
        return datetime.fromisoformat(date_str)
    except ValueError:
        pass
    # The date/time portion of `--date=iso-strict` output is always exactly
    # 19 characters (YYYY-MM-DDTHH:MM:SS) before the timezone offset - only
    # the offset itself is ever malformed in the real case this guards
    # against, so re-parsing just that prefix as UTC recovers the real
    # commit date whenever it's the offset, specifically, that's bad.
    try:
        return datetime.fromisoformat(date_str[:19]).replace(tzinfo=timezone.utc)
    except ValueError:
        return _EPOCH_UTC


def _relativize(path: str, prefix: str) -> str:
    path = path.strip()
    if not path:
        return ""
    if prefix:
        if not path.startswith(prefix):
            return ""
        path = path[len(prefix) :]
    return path


def stream_commit_touches(
    repo_path: Path, rev_range: str, *, max_commits: int | None = None
) -> Iterator[CommitTouch]:
    prefix = _scan_root_prefix(repo_path)
    args = [
        "log",
        f"--format={_RECORD_SEP_FORMAT}%H{_RECORD_SEP_FORMAT}%an{_RECORD_SEP_FORMAT}%ae"
        f"{_RECORD_SEP_FORMAT}%ad{_RECORD_SEP_FORMAT}%s",
        "--date=iso-strict",
        # -M turns a delete+add pair into an explicit "R<score>\told\tnew"
        # line (git's default 50% similarity threshold) instead of two
        # unrelated-looking touched paths - see CommitTouch.renames and
        # fold() for why: without this, a rename silently stranded a file's
        # pre-rename churn/ownership history under a name nothing queries
        # anymore (real audit finding, confirmed by direct reproduction).
        "--name-status",
        "-M",
    ]
    if max_commits is not None:
        args += ["-n", str(max_commits)]
    args.append(rev_range)

    # stderr goes to a temp file, not a PIPE: stdout is drained to completion
    # before stderr is ever read, so a chatty stderr (>~64KB pipe buffer) would
    # block git and deadlock this process against it.
    stderr_file = tempfile.TemporaryFile(mode="w+", errors="ignore")
    try:
        proc = subprocess.Popen(
            ["git", *args],
            cwd=repo_path,
            stdout=subprocess.PIPE,
            stderr=stderr_file,
            text=True,
            errors="ignore",
        )
    except BaseException:
        stderr_file.close()
        raise
    assert proc.stdout is not None

    pending_header: tuple[str, str, str, datetime] | None = None
    pending_subject: str = ""
    pending_files: list[str] = []
    pending_renames: list[tuple[str, str]] = []
    pending_departures: list[str] = []
    try:
        for raw_line in proc.stdout:
            line = raw_line.rstrip("\n")
            if line.startswith(_RECORD_SEP):
                if pending_header is not None:
                    yield CommitTouch(
                        *pending_header,
                        files=tuple(pending_files),
                        subject=pending_subject,
                        renames=tuple(pending_renames),
                        departures=tuple(pending_departures),
                    )
                # maxsplit=4 defensively, though `\x00` cannot occur inside
                # any of these fields (see the module-level comment on
                # _RECORD_SEP) - a fifth split point would only ever come
                # from something the fields themselves can never contain.
                sha, name, email, date_str, pending_subject = line[1:].split(_RECORD_SEP, 4)
                pending_header = (sha, name, email, parse_commit_date(date_str))
                pending_files = []
                pending_renames = []
                pending_departures = []
            elif line.strip():
                # --name-status's per-file lines are "<STATUS>\t<path>", or
                # for a rename, "R<score>\t<old>\t<new>" - tab-separated,
                # unlike the bare paths --name-only used to emit.
                status, _, rest = line.partition("\t")
                if status.startswith("R"):
                    old_raw, _, new_raw = rest.partition("\t")
                    new_path = _relativize(new_raw, prefix)
                    old_path = _relativize(old_raw, prefix)
                    if new_path:
                        pending_files.append(new_path)
                        if old_path:
                            pending_renames.append((old_path, new_path))
                    elif old_path:
                        # Renamed OUT of the scan root (new_path isn't in
                        # scope) - nothing in-scope to merge onto, but
                        # old_path's already-accumulated entry must not be
                        # left stale in file_churn forever either. Real gap
                        # found by Flash Review on this same change.
                        pending_departures.append(old_path)
                else:
                    path = _relativize(rest, prefix)
                    if path:
                        pending_files.append(path)
        if pending_header is not None:
            yield CommitTouch(
                *pending_header,
                files=tuple(pending_files),
                subject=pending_subject,
                renames=tuple(pending_renames),
                departures=tuple(pending_departures),
            )
    finally:
        proc.stdout.close()
        returncode = proc.wait()
        stderr = ""
        try:
            stderr_file.seek(0)
            stderr = stderr_file.read()
        finally:
            stderr_file.close()

    if returncode != 0:
        if returncode < 0:
            raise GitLogStreamError(
                f"git log {rev_range} was killed (likely out of memory) - this repository's "
                "history may be too large to analyze with the memory available"
            )
        raise GitLogStreamError(f"git log {rev_range} failed with exit code {returncode}: {stderr}")


# `git rev-list --max-parents=0 HEAD` walks all of history (16s on the Linux
# kernel's 1.46M commits) and a scan asks for the repo key several times, so
# the root commit is memoized per (repo, HEAD): it can't change without HEAD
# changing. The remote URL below is cheap and read fresh every time.
_root_commit_cache: dict[tuple[str, str], str] = {}


def _root_commit_key(repo_path: Path) -> str:
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_path, capture_output=True, text=True, errors="ignore"
    ).stdout.strip()
    cache_key = (str(Path(repo_path).resolve()), head)
    if head and cache_key in _root_commit_cache:
        return _root_commit_cache[cache_key]
    roots_result = subprocess.run(
        ["git", "rev-list", "--max-parents=0", "HEAD"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        errors="ignore",
    )
    root_shas = sorted(line for line in roots_result.stdout.strip().splitlines() if line)
    root_key = root_shas[0] if root_shas else "no-commits"
    if head:
        _root_commit_cache[cache_key] = root_key
    return root_key


def compute_repo_key(repo_path: Path) -> str:
    """Stable identity for a repo across scans, independent of which local
    directory it happens to be cloned into or which installation is
    scanning it. Root commit SHA (see analyzer._first_commit_at for why a
    repo can have more than one - lexicographically smallest is used here
    to stay deterministic without needing commit dates) plus the origin
    remote URL where one exists; falls back to the absolute local path for
    a repo with no remote (e.g. `git init`, never pushed anywhere).
    """
    root_key = _root_commit_key(repo_path)

    remote_result = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        errors="ignore",
    )
    remote_url = remote_result.stdout.strip()
    if remote_result.returncode == 0 and remote_url:
        return f"{root_key}:{remote_url}"
    return f"{root_key}:{repo_path.resolve()}"


def _week_start(at: datetime) -> date:
    d = at.date()
    return d - timedelta(days=d.weekday())


def _bump_co_change(counts: dict[str, int], partner: str) -> None:
    if partner in counts:
        counts[partner] += 1
        return
    if len(counts) >= MAX_CO_CHANGE_PARTNERS_TRACKED:
        # Evict the current weakest partner to make room, rather than let
        # a hub file's dict grow without bound. This trades exact history
        # for a hard memory ceiling: once a file is at capacity, a rare new
        # partner may bump out a still-rarer existing one instead of both
        # being tracked - only files with more than
        # MAX_CO_CHANGE_PARTNERS_TRACKED distinct partners are affected.
        weakest = min(counts, key=counts.get)
        del counts[weakest]
    counts[partner] = 1


def _merge_renamed_file_churn(file_churn: dict[str, FileChurnTotal], old_path: str, new_path: str) -> None:
    """Carries old_path's already-accumulated churn/recent-commits/ownership
    forward onto new_path, so a renamed file's pre-rename history keeps
    counting under its current name instead of staying stranded under a
    name nothing queries anymore (the real audit gap this closes).
    Deliberately does NOT chase old_path through other files'
    co_change_counts - that's a separate, lower-value staleness (a
    co-change partner shown under a retired name) the audit itself flagged
    as a lesser, pre-existing limitation, not the churn/hotspot-ranking
    defect this fix targets.
    """
    old_entry = file_churn.pop(old_path, None)
    if old_entry is None:
        return  # old_path never touched within this fold's view - nothing to carry forward
    new_entry = file_churn.get(new_path)
    if new_entry is None:
        new_entry = FileChurnTotal(path=new_path, churn_count=0, recent_commits=[], co_change_counts={}, owners={})
        file_churn[new_path] = new_entry
    new_entry.churn_count += old_entry.churn_count
    new_entry.recent_commits = sorted(
        new_entry.recent_commits + old_entry.recent_commits, key=lambda rc: rc.committed_at, reverse=True
    )[:RECENT_COMMITS_PER_FILE]
    for email, old_owner in old_entry.owners.items():
        owner = new_entry.owners.get(email)
        if owner is None:
            new_entry.owners[email] = OwnershipTotal(
                email=email, names=set(old_owner.names), commit_count=old_owner.commit_count
            )
        else:
            owner.names |= old_owner.names
            owner.commit_count += old_owner.commit_count


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
        path: FileChurnTotal(
            f.path,
            f.churn_count,
            list(f.recent_commits),
            dict(f.co_change_counts),
            {email: OwnershipTotal(o.email, set(o.names), o.commit_count) for email, o in f.owners.items()},
        )
        for path, f in snapshot.file_churn.items()
    }

    # Reversed, not `commits` in its natural order: every caller feeds this
    # `git log`'s own default order, newest commit first (stream_commit_touches
    # issues no --reverse, and analyzer.py's list(stream_commit_touches(...))
    # doesn't add one either). Every OTHER aggregate below is order-independent
    # (a running sum, or a set union) - recent_commits is the one exception:
    # insert(0, recent) puts each processed commit at the front, so processing
    # newest-first ends with the OLDEST commit at index 0 and the newest
    # buried at the back, exactly backwards from what "recent" means. Once a
    # file's touches in one batch exceed RECENT_COMMITS_PER_FILE, the
    # truncation below then keeps the oldest ones and drops the genuinely
    # recent ones. Confirmed directly against fold() itself, not just traced:
    # three commits newest-to-oldest in, recent_commits[0] came back as the
    # oldest of the three. Iterating oldest-of-this-batch-first instead makes
    # the last insert(0, ...) - the real newest commit - land at index 0,
    # matching every reader of recent_commits[0] (jobs.py's
    # _commit_attachment_from_graph/_owner_attachment_from_graph, both of
    # which treat it as "the latest commit" for health-check-failure
    # correlation and likely-owner inference).
    for commit in reversed(commits):
        # Processed oldest-of-this-batch-first, same as everything else in
        # this loop - a rename's old path can only ever have accumulated
        # history from commits already folded in by the time we reach it.
        for old_path, new_path in commit.renames:
            _merge_renamed_file_churn(file_churn, old_path, new_path)
        # departures: the old path left the scan root (see CommitTouch's
        # docstring) - nothing in-scope to merge onto, so drop its entry
        # outright rather than leaving it stale forever.
        for departed_path in commit.departures:
            file_churn.pop(departed_path, None)

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
        recent = RecentCommit(
            commit.sha, commit.author_name, commit.author_email, commit.committed_at, commit.subject
        )
        for path in unique_files:
            churn = file_churn.get(path)
            if churn is None:
                churn = FileChurnTotal(path=path, churn_count=0, recent_commits=[], co_change_counts={}, owners={})
                file_churn[path] = churn
            churn.churn_count += 1
            churn.recent_commits.insert(0, recent)
            del churn.recent_commits[RECENT_COMMITS_PER_FILE:]
            file_owner = churn.owners.get(email_key)
            if file_owner is None:
                file_owner = OwnershipTotal(email=email_key, names=set(), commit_count=0)
                churn.owners[email_key] = file_owner
            file_owner.names.add(commit.author_name)
            file_owner.commit_count += 1

        if len(unique_files) <= MASS_COMMIT_FILE_THRESHOLD:
            for i, first in enumerate(unique_files):
                for second in unique_files[i + 1 :]:
                    _bump_co_change(file_churn[first].co_change_counts, second)
                    _bump_co_change(file_churn[second].co_change_counts, first)

    return GraphSnapshot(
        last_synced_sha=snapshot.last_synced_sha,
        last_synced_at=snapshot.last_synced_at,
        ownership=ownership,
        cadence_weekly_counts=cadence,
        file_churn=file_churn,
    )
