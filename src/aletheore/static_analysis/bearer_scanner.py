import json
import shutil
import subprocess
from pathlib import Path

from aletheore.static_analysis._exclusions import count_real_files, excluded_dir_names, filter_findings

# Real data points found live tonight, not assumed: 241 real (non-excluded)
# files under github-app/ finished in 18.7s; this repo's full ~3,331-file
# tree still hadn't finished at 300s, even after excluding the one known
# 51MB non-source directory (.repowise) that first looked like the cause.
# A flat timeout can't be right for both a small repo and a 165k-LOC one,
# and Bearer's own scaling looked worse than the ~0.08s/file the small
# sample implies (300s wasn't enough for 12.4x the files at that rate) -
# real data, not confirmed root-caused (the process itself showed almost
# no CPU time across those 5 minutes, pointing at an I/O-bound or
# otherwise inefficient path worth investigating separately, not a fixed
# per-file compute cost). Scaled with deliberately generous headroom over
# the measured rate rather than a tight fit to it, given that
# uncertainty - see _scaled_timeout.
BEARER_BASE_TIMEOUT_SECONDS = 120
BEARER_PER_FILE_SECONDS = 0.5
# A hard ceiling matters even for an opt-in, ask-first check: "opted in"
# should not mean "may run indefinitely." 30 minutes is well above anything
# measured tonight while still bounding a runaway/pathological case.
BEARER_MAX_TIMEOUT_SECONDS = 1800

# Real, live-verified against a throwaway git-tracked repo tonight (not
# assumed from docs): Bearer's --format=json groups findings by severity
# under top-level keys ("critical"/"high"/"medium"/"low"), each entry
# carrying `id` (rule id), `title` (short summary - `description` is a long
# markdown remediation writeup, not what belongs in a one-line finding
# message), `filename`, `line_number`, and `category_groups` (e.g.
# ["PII", "Personal Data"]). Confirmed the exit code is 1 when findings
# exist, 0 when clean - same "non-zero isn't a failure" shape as every
# other scanner here.
_SEVERITY_MAP = {"critical": "critical", "high": "major", "medium": "minor", "low": "info"}


def _scaled_timeout(repo_path: Path) -> int:
    file_count = count_real_files(repo_path)
    return min(BEARER_MAX_TIMEOUT_SECONDS, int(BEARER_BASE_TIMEOUT_SECONDS + file_count * BEARER_PER_FILE_SECONDS))


def _has_commits(repo_path: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def check_bearer(repo_path: Path, timeout: int | None = None) -> dict:
    binary = shutil.which("bearer")
    if binary is None:
        return {"checked": False, "reason": "bearer not installed", "findings": []}

    # Resolved per-call, not a fixed default - see _scaled_timeout's real
    # calibration data above. An explicit `timeout` argument (tests, a
    # caller with its own budget) always wins.
    if timeout is None:
        timeout = _scaled_timeout(repo_path)

    # Real requirement confirmed live tonight: Bearer scans git-tracked
    # files only - an untracked working tree silently returns zero
    # findings ("couldn't find any files to scan"), which would otherwise
    # read as a false "clean" rather than "didn't actually scan anything".
    if not _has_commits(repo_path):
        return {
            "checked": False,
            "reason": "bearer requires a git-tracked working tree (no commits found)",
            "findings": [],
        }

    cmd = [binary, "scan", "--format=json", "--quiet"]
    # Real-verified live: --skip-path takes a single comma-separated list
    # of ** glob patterns (bare dir names don't do the same "match
    # anywhere" a shell glob does) - "name/**" reliably keeps Bearer out of
    # a nested duplicate tree like .claude/worktrees/<id>/.
    names = excluded_dir_names(repo_path)
    if names:
        cmd.append("--skip-path=" + ",".join(f"{name}/**" for name in names))
    cmd.append(".")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=repo_path)
    except subprocess.TimeoutExpired:
        return {"checked": False, "reason": f"bearer timed out after {timeout}s", "findings": []}
    except OSError as exc:
        return {"checked": False, "reason": f"bearer failed to run: {exc}", "findings": []}

    if result.returncode not in (0, 1):
        return {
            "checked": False,
            "reason": f"bearer exited {result.returncode}: {(result.stderr or result.stdout)[-500:]}",
            "findings": [],
        }

    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return {
            "checked": False,
            "reason": f"bearer produced unparseable output: {(result.stderr or '')[:300]}",
            "findings": [],
        }

    findings = []
    for severity, entries in payload.items():
        if not isinstance(entries, list):
            continue
        for item in entries:
            findings.append(
                {
                    "tool": "bearer",
                    "rule_id": item.get("id", ""),
                    "severity": _SEVERITY_MAP.get(severity, "minor"),
                    # Bearer is exclusively a sensitive-data-flow/privacy
                    # scanner - every real finding it produces belongs to
                    # the "privacy" type by construction, not derived
                    # per-finding the way Semgrep's category is.
                    "type": "privacy",
                    "path": item.get("filename", ""),
                    "line": item.get("line_number", 0),
                    "message": (item.get("title") or "").strip(),
                }
            )
    return {"checked": True, "reason": None, "findings": filter_findings(findings, repo_path)}
