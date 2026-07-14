import subprocess
from datetime import datetime, timezone
from pathlib import Path


def _run_git(repo_path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )


def _has_commits(repo_path: Path) -> bool:
    result = _run_git(repo_path, "rev-parse", "--git-dir")
    if result.returncode != 0:
        return False
    result = _run_git(repo_path, "rev-list", "-1", "HEAD")
    return result.returncode == 0 and result.stdout.strip() != ""


def _remote_names(repo_path: Path) -> set[str]:
    result = _run_git(repo_path, "remote")
    return set(result.stdout.strip().splitlines())


def _parse_branches(repo_path: Path, now: datetime) -> list[dict]:
    result = _run_git(
        repo_path,
        "for-each-ref",
        "--format=%(refname:short)\t%(committerdate:iso-strict)",
        "refs/heads",
        "refs/remotes",
    )
    remotes = _remote_names(repo_path)
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
        last_commit_at = datetime.fromisoformat(date_str)
        stale_days = (now - last_commit_at).days
        branches.append(
            {
                "name": name,
                "type": branch_type,
                "last_commit_at": last_commit_at.isoformat(),
                "stale_days": stale_days,
                "ahead_of_main": 0,
                "behind_main": 0,
            }
        )
    return branches


def _commit_cadence(repo_path: Path) -> dict:
    result = _run_git(repo_path, "log", "--format=%ad", "--date=iso-strict", "HEAD")
    dates = [datetime.fromisoformat(line) for line in result.stdout.strip().splitlines() if line]
    if not dates:
        return {"weekly_counts": [], "trend": "flat"}

    dates.sort()
    buckets: dict[int, int] = {}
    start = dates[0]
    for date in dates:
        week_index = (date - start).days // 7
        buckets[week_index] = buckets.get(week_index, 0) + 1
    weekly_counts = [buckets.get(i, 0) for i in range(max(buckets.keys()) + 1)]

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

    return {"weekly_counts": weekly_counts, "trend": trend}


def _ownership(repo_path: Path) -> list[dict]:
    result = _run_git(repo_path, "log", "--format=%an", "HEAD")
    authors = [line for line in result.stdout.strip().splitlines() if line]
    total = len(authors)
    counts: dict[str, int] = {}
    for author in authors:
        counts[author] = counts.get(author, 0) + 1
    return [
        {"author": author, "commit_count": count, "percent": round(count / total, 4)}
        for author, count in sorted(counts.items(), key=lambda kv: -kv[1])
    ]


def analyze_git(repo_path: Path, now: datetime | None = None) -> dict:
    if now is None:
        now = datetime.now(timezone.utc)

    if not _has_commits(repo_path):
        return {"available": False}

    total_commits_result = _run_git(repo_path, "rev-list", "--count", "HEAD")
    total_commits = int(total_commits_result.stdout.strip())

    first_commit_result = _run_git(
        repo_path, "log", "--reverse", "--format=%ad", "--date=iso-strict", "HEAD"
    )
    first_commit_line = first_commit_result.stdout.strip().splitlines()[0]
    first_commit_at = datetime.fromisoformat(first_commit_line)
    repo_age_days = (now - first_commit_at).days

    return {
        "available": True,
        "branches": _parse_branches(repo_path, now),
        "commit_cadence": _commit_cadence(repo_path),
        "ownership": _ownership(repo_path),
        "repo_age_days": repo_age_days,
        "total_commits": total_commits,
    }
