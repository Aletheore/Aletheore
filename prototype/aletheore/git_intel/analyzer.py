import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

MASS_COMMIT_FILE_THRESHOLD = 50
HOTSPOT_LIMIT = 30


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
        last_commit_at = datetime.fromisoformat(date_str)
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


def _commit_cadence(repo_path: Path, now: datetime) -> dict:
    result = _run_git(repo_path, "log", "--format=%ad", "--date=iso-strict", "HEAD")
    dates = [datetime.fromisoformat(line) for line in result.stdout.strip().splitlines() if line]
    if not dates:
        return {"weekly_counts": [], "trend": "flat", "most_recent_week_partial": False}

    dates.sort()
    buckets: dict[int, int] = {}
    start = dates[0]
    for date in dates:
        week_index = (date - start).days // 7
        buckets[week_index] = buckets.get(week_index, 0) + 1
    last_bucket = max(buckets.keys())
    weekly_counts = [buckets.get(i, 0) for i in range(last_bucket + 1)]

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

    last_bucket_start = start + timedelta(days=last_bucket * 7)
    days_into_last_bucket = (now - last_bucket_start).days
    most_recent_week_partial = days_into_last_bucket < 7

    return {
        "weekly_counts": weekly_counts,
        "trend": trend,
        "most_recent_week_partial": most_recent_week_partial,
    }


def _ownership(repo_path: Path) -> list[dict]:
    result = _run_git(repo_path, "log", "--format=%an\x1f%ae", "HEAD")
    entries = [
        line.split("\x1f") for line in result.stdout.strip().splitlines() if line
    ]
    total = len(entries)
    counts: dict[str, int] = {}
    names_by_email: dict[str, set[str]] = {}
    for name, email in entries:
        key = email.lower()
        counts[key] = counts.get(key, 0) + 1
        names_by_email.setdefault(key, set()).add(name)
    return [
        {
            "email": email,
            "names": sorted(names_by_email[email]),
            "commit_count": count,
            "percent": round(count / total, 4),
        }
        for email, count in sorted(counts.items(), key=lambda kv: -kv[1])
    ]


def _commit_file_lists(repo_path: Path) -> list[list[str]]:
    result = _run_git(repo_path, "log", "--format=%x00", "--name-only", "HEAD")
    prefix_result = _run_git(repo_path, "rev-parse", "--show-prefix")
    prefix = prefix_result.stdout.strip()
    commits: list[list[str]] = []
    current: list[str] = []
    for line in result.stdout.splitlines():
        if line == "\x00":
            if current:
                commits.append(current)
            current = []
        elif line.strip():
            path = line.strip()
            if prefix:
                if not path.startswith(prefix):
                    continue
                path = path[len(prefix) :]
            if path:
                current.append(path)
    if current:
        commits.append(current)
    return commits


def compute_hotspots(repo_path: Path, modules: list[dict]) -> list[dict]:
    dependents_by_path = {module["path"]: len(module.get("imported_by", [])) for module in modules}
    churn: dict[str, int] = {}
    co_change: dict[str, dict[str, int]] = {}

    for files in _commit_file_lists(repo_path):
        unique_files = sorted(set(files))
        for path in unique_files:
            churn[path] = churn.get(path, 0) + 1
        if len(unique_files) > MASS_COMMIT_FILE_THRESHOLD:
            continue
        for index, first in enumerate(unique_files):
            for second in unique_files[index + 1 :]:
                first_partners = co_change.setdefault(first, {})
                second_partners = co_change.setdefault(second, {})
                first_partners[second] = first_partners.get(second, 0) + 1
                second_partners[first] = second_partners.get(first, 0) + 1

    hotspots = []
    for path, churn_count in churn.items():
        partners = sorted(co_change.get(path, {}).items(), key=lambda item: (-item[1], item[0]))[:5]
        hotspots.append(
            {
                "path": path,
                "churn_count": churn_count,
                "co_change_partners": [
                    {"path": partner, "co_occurrences": count} for partner, count in partners
                ],
                "dependents_count": dependents_by_path.get(path, 0),
            }
        )

    return sorted(hotspots, key=lambda item: (-item["churn_count"], item["path"]))[:HOTSPOT_LIMIT]


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
        "commit_cadence": _commit_cadence(repo_path, now),
        "ownership": _ownership(repo_path),
        "repo_age_days": repo_age_days,
        "total_commits": total_commits,
    }
