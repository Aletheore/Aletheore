import subprocess
from datetime import datetime, timezone
from pathlib import Path

from aletheore.git_intel.graph_store import GraphSnapshot, RepoGraphStore
from aletheore.git_intel.incremental import (
    CO_CHANGE_PARTNERS_RETURNED,
    GitLogStreamError,
    _EPOCH_UTC,
    compute_repo_key,
    parse_commit_date,
    stream_commit_touches,
)
from aletheore.git_intel.sqlite_store import SQLiteRepoGraphStore, default_graph_db_path

HOTSPOT_LIMIT = 30
CADENCE_WEEKS_RETURNED = 52

# Distinct from the generic exit code 1 other scan failures use, so callers
# that only see a subprocess exit code (scan_worker/jobs.py, demo_scan.py)
# can tell "the repo is likely too large for the memory available" apart
# from "something else went wrong" without parsing stderr text.
GIT_ANALYSIS_RESOURCE_EXIT_CODE = 2


class GitAnalysisError(RuntimeError):
    """A git subprocess this module depends on failed or was killed - most
    commonly the OS OOM killer on a repository whose history is too large
    to walk in the memory available (confirmed directly: a full scan of
    torvalds/linux under a 1GB cgroup limit got OOM-killed here). Raised
    instead of letting execution continue with truncated/empty output,
    which previously surfaced many call sites downstream as a confusing,
    unrelated IndexError/ValueError instead of one clear signal.
    """


def _run_git(repo_path: Path, *args: str) -> subprocess.CompletedProcess:
    # errors="ignore" (matching secrets.py's git-log subprocess) - real commit
    # history, especially anything spanning many years/contributors, isn't
    # guaranteed to be valid UTF-8 (confirmed on Linux's own history: strict
    # decoding crashed with UnicodeDecodeError on a non-UTF-8 byte in an old
    # commit's author name). A handful of unparseable bytes in one field
    # shouldn't take down the whole git analysis.
    return subprocess.run(
        ["git", *args],
        cwd=repo_path,
        capture_output=True,
        text=True,
        errors="ignore",
    )


def _run_git_or_raise(repo_path: Path, *args: str) -> subprocess.CompletedProcess:
    # For call sites past _has_commits() that assume success and parse the
    # output unconditionally - a negative returncode means the process was
    # killed by a signal (subprocess.run's documented convention), which for
    # a `git log` over a huge history is almost always the OOM killer.
    result = _run_git(repo_path, *args)
    if result.returncode != 0:
        command = " ".join(args)
        if result.returncode < 0:
            raise GitAnalysisError(
                f"git {command} was killed (likely out of memory) - this repository's "
                "history may be too large to analyze with the memory available"
            )
        raise GitAnalysisError(f"git {command} failed with exit code {result.returncode}")
    return result


def _has_commits(repo_path: Path) -> bool:
    result = _run_git(repo_path, "rev-parse", "--git-dir")
    if result.returncode != 0:
        return False
    result = _run_git(repo_path, "rev-list", "-1", "HEAD")
    return result.returncode == 0 and result.stdout.strip() != ""


def _remote_names(repo_path: Path) -> set[str]:
    result = _run_git(repo_path, "remote")
    return set(result.stdout.strip().splitlines())


def _default_branch_ref(repo_path: Path) -> str | None:
    result = _run_git(repo_path, "symbolic-ref", "refs/remotes/origin/HEAD")
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip().removeprefix("refs/remotes/")
    result = _run_git(repo_path, "rev-parse", "--abbrev-ref", "HEAD")
    branch = result.stdout.strip()
    if result.returncode == 0 and branch and branch != "HEAD":
        return branch
    return None


def _ahead_behind(repo_path: Path, ref: str, branch: str) -> tuple[int, int]:
    result = _run_git(repo_path, "rev-list", "--left-right", "--count", f"{ref}...{branch}")
    if result.returncode != 0:
        return 0, 0
    parts = result.stdout.strip().split()
    if len(parts) != 2:
        return 0, 0
    behind, ahead = int(parts[0]), int(parts[1])
    return ahead, behind


def _parse_branches(repo_path: Path, now: datetime) -> list[dict]:
    result = _run_git(
        repo_path,
        "for-each-ref",
        "--format=%(refname:short)\t%(committerdate:iso-strict)",
        "refs/heads",
        "refs/remotes",
    )
    remotes = _remote_names(repo_path)
    default_ref = _default_branch_ref(repo_path)
    branches = []
    for line in result.stdout.strip().splitlines():
        if not line.strip():
            continue
        name, date_str = line.split("\t")
        if name in remotes or name.endswith("/HEAD"):
            continue
        branch_type = "remote" if name.startswith("origin/") or (
            "/" in name and name.split("/")[0] in remotes
        ) else "local"
        last_commit_at = parse_commit_date(date_str)
        stale_days = (now - last_commit_at).days

        ahead, behind = 0, 0
        if default_ref is not None and name != default_ref:
            ahead, behind = _ahead_behind(repo_path, default_ref, name)

        branches.append(
            {
                "name": name,
                "type": branch_type,
                "last_commit_at": last_commit_at.isoformat(),
                "stale_days": stale_days,
                "ahead_of_main": ahead,
                "behind_main": behind,
            }
        )
    return branches


def _current_branch(repo_path: Path) -> str:
    # Keys the persisted graph - a checkout on a different branch between
    # scans must not be treated as "the same sync state", or commits unique
    # to the new branch would look like they'd already been analyzed.
    # Detached HEAD (bare "HEAD") is a known, accepted gap: multiple
    # different detached checkouts share one bucket rather than each
    # getting isolated sync state - rare in practice, since analyze_git
    # almost always runs against a real checked-out branch.
    result = _run_git(repo_path, "rev-parse", "--abbrev-ref", "HEAD")
    branch = result.stdout.strip()
    return branch if result.returncode == 0 and branch else "HEAD"


def _sha_exists(repo_path: Path, sha: str) -> bool:
    return _run_git(repo_path, "cat-file", "-e", sha).returncode == 0


def default_store(repo_path: Path) -> RepoGraphStore:
    return SQLiteRepoGraphStore(default_graph_db_path(repo_path))


def _sync_graph(
    repo_path: Path,
    store: RepoGraphStore,
    now: datetime,
    depth_cap: int | None,
    branch: str | None = None,
) -> tuple[GraphSnapshot, bool]:
    """Brings the persisted graph up to date with HEAD and returns the
    resulting snapshot, plus whether this was a full (re)baseline rather
    than an incremental delta - only a baseline can be depth-limited, so
    callers use this to decide whether to flag history as partial.

    `branch` defaults to auto-detecting the repo's current checkout, which
    is right for a normal local clone but wrong for a hosted scan clone
    checked out at a bare SHA (detached HEAD) - every such clone would
    otherwise collapse onto the same literal "HEAD" bucket regardless of
    which actual branch/PR it came from, corrupting deltas across unrelated
    scans. Hosted callers must pass an explicit, stable branch key instead.
    """
    repo_key = compute_repo_key(repo_path)
    branch = branch if branch is not None else _current_branch(repo_path)
    snapshot = store.load(repo_key, branch)

    if snapshot.last_synced_sha is None or not _sha_exists(repo_path, snapshot.last_synced_sha):
        # No prior state, or the sync pointer no longer exists in this repo
        # (history was rewritten out from under it, e.g. a force-push) -
        # either way, prior aggregates can't be trusted to merge into.
        rev_range = "HEAD"
        reset = True
        max_commits = depth_cap
    else:
        rev_range = f"{snapshot.last_synced_sha}..HEAD"
        reset = False
        max_commits = None  # a delta is already bounded by however much landed since last sync

    try:
        commits = list(stream_commit_touches(repo_path, rev_range, max_commits=max_commits))
    except GitLogStreamError as exc:
        raise GitAnalysisError(str(exc)) from exc

    new_head = _run_git_or_raise(repo_path, "rev-parse", "HEAD").stdout.strip()
    store.apply_commits(repo_key, branch, commits, new_sync_sha=new_head, new_sync_at=now, reset=reset)
    return store.load(repo_key, branch), reset


def _ownership_summary(snapshot: GraphSnapshot) -> list[dict]:
    total = sum(o.commit_count for o in snapshot.ownership.values())
    if total == 0:
        return []
    return [
        {
            "email": o.email,
            "names": sorted(o.names),
            "commit_count": o.commit_count,
            "percent": round(o.commit_count / total, 4),
        }
        for o in sorted(snapshot.ownership.values(), key=lambda o: -o.commit_count)
    ]


def _file_ownership_summary(snapshot: GraphSnapshot, modules: list[dict]) -> dict[str, list[dict]]:
    current_paths = {module["path"] for module in modules}
    result = {}
    for path, churn in snapshot.file_churn.items():
        if path not in current_paths or not churn.owners:
            continue
        total = sum(owner.commit_count for owner in churn.owners.values())
        result[path] = [
            {
                "email": owner.email,
                "names": sorted(owner.names),
                "commit_count": owner.commit_count,
                "percent": round(owner.commit_count / total, 4),
            }
            for owner in sorted(churn.owners.values(), key=lambda owner: -owner.commit_count)
        ]
    return result


def _cadence_summary(snapshot: GraphSnapshot, now: datetime) -> dict:
    if not snapshot.cadence_weekly_counts:
        return {"weekly_counts": [], "trend": "flat", "most_recent_week_partial": False}

    sorted_weeks = sorted(snapshot.cadence_weekly_counts.items())
    weekly_counts = [count for _week, count in sorted_weeks[-CADENCE_WEEKS_RETURNED:]]

    if len(weekly_counts) < 2:
        trend = "flat"
    else:
        midpoint = len(weekly_counts) // 2
        first_half = sum(weekly_counts[:midpoint]) / max(midpoint, 1)
        second_half = sum(weekly_counts[midpoint:]) / max(len(weekly_counts) - midpoint, 1)
        if second_half > first_half * 1.2:
            trend = "increasing"
        elif second_half < first_half * 0.8:
            trend = "decreasing"
        else:
            trend = "flat"

    last_week_start = sorted_weeks[-1][0]
    days_into_last_bucket = (now.date() - last_week_start).days
    most_recent_week_partial = days_into_last_bucket < 7

    return {
        "weekly_counts": weekly_counts,
        "trend": trend,
        "most_recent_week_partial": most_recent_week_partial,
    }


def _hotspots_summary(snapshot: GraphSnapshot, modules: list[dict]) -> list[dict]:
    dependents_by_path = {module["path"]: len(module.get("imported_by", [])) for module in modules}
    hotspots = []
    for path, churn in snapshot.file_churn.items():
        partners = sorted(
            churn.co_change_counts.items(), key=lambda item: (-item[1], item[0])
        )[:CO_CHANGE_PARTNERS_RETURNED]
        hotspots.append(
            {
                "path": path,
                "churn_count": churn.churn_count,
                "co_change_partners": [
                    {"path": partner, "co_occurrences": count} for partner, count in partners
                ],
                "dependents_count": dependents_by_path.get(path, 0),
            }
        )
    return sorted(hotspots, key=lambda item: (-item["churn_count"], item["path"]))[:HOTSPOT_LIMIT]


def compute_hotspots(
    repo_path: Path,
    modules: list[dict],
    *,
    store: RepoGraphStore | None = None,
    depth_cap: int | None = None,
    branch: str | None = None,
) -> list[dict]:
    owns_store = store is None
    store = store or default_store(repo_path)
    try:
        snapshot, _reset = _sync_graph(repo_path, store, datetime.now(timezone.utc), depth_cap, branch)
    finally:
        if owns_store and isinstance(store, SQLiteRepoGraphStore):
            store.close()
    return _hotspots_summary(snapshot, modules)


def _first_commit_at(repo_path: Path) -> datetime:
    # `git log --reverse HEAD` walks and formats the *entire* history just to
    # read its first line - confirmed as the exact query that got OOM-killed
    # scanning torvalds/linux's 1.46M commits under a 1GB limit. Root commits
    # are directly enumerable without touching anything in between: normally
    # there's exactly one, but a repo with merged unrelated histories can
    # have several, so take the oldest of whichever `--max-parents=0` finds -
    # still O(root commits), never O(total commits).
    roots_result = _run_git_or_raise(repo_path, "rev-list", "--max-parents=0", "HEAD")
    root_shas = [line for line in roots_result.stdout.strip().splitlines() if line]
    dates = []
    for sha in root_shas:
        date_result = _run_git_or_raise(repo_path, "log", "-1", "--format=%ad", "--date=iso-strict", sha)
        dates.append(parse_commit_date(date_result.stdout.strip()))
    # parse_commit_date's own epoch fallback is deliberately "very old" so a
    # malformed date never skews a *most-recent* ranking as if it just
    # happened (see incremental.py's _EPOCH_UTC comment) - but that same
    # "very old" value is exactly wrong to feed into this min(), where it's
    # the *oldest* value that wins. A repo with several root commits (a
    # merged unrelated history) had a single malformed one silently set the
    # whole repo's founding date to 1970-01-01, reporting it as ~56 years
    # old, regardless of what every other root commit's real date said.
    # Excluding epoch-fallback dates here means one bad commit no longer
    # poisons a min() across otherwise-good ones; only degrades back to the
    # epoch value in the genuinely-unrecoverable case where every root
    # commit's date is unparseable.
    real_dates = [d for d in dates if d != _EPOCH_UTC]
    return min(real_dates) if real_dates else min(dates)


def analyze_git(
    repo_path: Path,
    modules: list[dict] | None = None,
    now: datetime | None = None,
    *,
    store: RepoGraphStore | None = None,
    depth_cap: int | None = None,
    branch: str | None = None,
) -> dict:
    if now is None:
        now = datetime.now(timezone.utc)
    modules = modules or []

    if not _has_commits(repo_path):
        return {"available": False}

    total_commits_result = _run_git_or_raise(repo_path, "rev-list", "--count", "HEAD")
    total_commits = int(total_commits_result.stdout.strip())

    repo_age_days = (now - _first_commit_at(repo_path)).days

    owns_store = store is None
    store = store or default_store(repo_path)
    try:
        snapshot, was_full_rebuild = _sync_graph(repo_path, store, now, depth_cap, branch)
    finally:
        if owns_store and isinstance(store, SQLiteRepoGraphStore):
            store.close()

    history_depth_limited = was_full_rebuild and depth_cap is not None and total_commits > depth_cap

    return {
        "available": True,
        "branches": _parse_branches(repo_path, now),
        "commit_cadence": _cadence_summary(snapshot, now),
        "ownership": _ownership_summary(snapshot),
        "file_ownership": _file_ownership_summary(snapshot, modules),
        "repo_age_days": repo_age_days,
        "total_commits": total_commits,
        "history_depth_limited": history_depth_limited,
    }
