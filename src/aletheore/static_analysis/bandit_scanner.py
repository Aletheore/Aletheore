import json
import shutil
import subprocess
from pathlib import Path

from aletheore.static_analysis._exclusions import excluded_dir_names, filter_findings, has_real_file

DEFAULT_BANDIT_TIMEOUT_SECONDS = 180

_SEVERITY_MAP = {"HIGH": "critical", "MEDIUM": "major", "LOW": "minor"}


def _relative_path(raw_path: str, repo_path: Path) -> str:
    # Real bug found live by this module's own tests: bandit's "filename"
    # is relative to the subprocess's cwd (repo_path, since that's what
    # this module always passes) - e.g. "./app.py" - but
    # Path(raw_path).resolve() resolves a relative path against the
    # CALLING process's cwd, not repo_path. Those are only ever the same
    # by coincidence (e.g. a test happening to run from repo_path itself);
    # for a real caller (scan_worker processing an arbitrary repo, or the
    # CLI invoked from anywhere else) they're routinely different,
    # silently producing an absolute or wrongly-rooted path instead of a
    # clean repo-relative one. Joining onto repo_path explicitly first
    # anchors the resolve() to the right base regardless of the calling
    # process's own cwd.
    try:
        return str((repo_path / raw_path).resolve().relative_to(repo_path.resolve()))
    except ValueError:
        return raw_path


def _exclude_arg(repo_path: Path) -> str | None:
    # Real bug found live: bandit's -x matches literal path prefixes, not a
    # bare directory name appearing anywhere in the path - `-x .claude`
    # left every file under .claude/worktrees/<id>/... in the results
    # (confirmed: 1,376 of them, in a real run against this repo). Only an
    # explicit `./<dir>` + `./<dir>/*` pair, relative to cwd, reliably
    # excludes a nested occurrence - confirmed the fix live the same way.
    names = excluded_dir_names(repo_path)
    if not names:
        return None
    return ",".join(f"./{name}" for name in names) + "," + ",".join(f"./{name}/*" for name in names)


def check_bandit(repo_path: Path, timeout: int = DEFAULT_BANDIT_TIMEOUT_SECONDS) -> dict:
    if not has_real_file(repo_path, "*.py"):
        return {"checked": True, "reason": None, "findings": []}

    binary = shutil.which("bandit")
    if binary is None:
        return {"checked": False, "reason": "bandit not installed", "findings": []}

    cmd = [binary, "-r", ".", "-f", "json", "-q"]
    exclude_arg = _exclude_arg(repo_path)
    if exclude_arg:
        cmd.extend(["-x", exclude_arg])

    try:
        # bandit exits non-zero (confirmed live: exit 1) whenever it finds
        # any issue - the JSON on stdout is authoritative for 0/1; any
        # other code is treated as a real failure, not partial output.
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=repo_path)
    except subprocess.TimeoutExpired:
        return {"checked": False, "reason": f"bandit timed out after {timeout}s", "findings": []}
    except OSError as exc:
        return {"checked": False, "reason": f"bandit failed to run: {exc}", "findings": []}

    if result.returncode not in (0, 1):
        return {
            "checked": False,
            "reason": f"bandit exited {result.returncode}: {(result.stderr or result.stdout)[-500:]}",
            "findings": [],
        }

    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return {
            "checked": False,
            "reason": f"bandit produced unparseable output: {(result.stderr or '')[:300]}",
            "findings": [],
        }

    findings = []
    for item in payload.get("results", []):
        findings.append(
            {
                "tool": "bandit",
                "rule_id": item.get("test_id", ""),
                "severity": _SEVERITY_MAP.get(item.get("issue_severity", ""), "minor"),
                "type": "vulnerability",
                "path": _relative_path(item.get("filename", ""), repo_path),
                "line": item.get("line_number", 0),
                "message": (item.get("issue_text") or "").strip(),
            }
        )
    return {"checked": True, "reason": None, "findings": filter_findings(findings, repo_path)}
