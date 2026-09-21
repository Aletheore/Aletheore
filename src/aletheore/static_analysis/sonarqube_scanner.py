import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from aletheore.static_analysis._exclusions import excluded_dir_names, filter_findings

DEFAULT_SONARQUBE_TIMEOUT_SECONDS = 600
# The scanner CLI submits analysis and returns quickly; the server then
# processes it asynchronously on its own Compute Engine queue. Polling
# beyond the scanner's own return is required before issues are queryable -
# an immediate /api/issues/search right after the scanner exits reads a
# STILL-PROCESSING or entirely absent previous analysis, not the fresh one.
_CE_TASK_POLL_INTERVAL_SECONDS = 3
_CE_TASK_POLL_TIMEOUT_SECONDS = 120
_ISSUES_PAGE_SIZE = 500

_SEVERITY_MAP = {
    "BLOCKER": "blocker",
    "CRITICAL": "critical",
    "MAJOR": "major",
    "MINOR": "minor",
    "INFO": "info",
}
_TYPE_MAP = {
    "BUG": "bug",
    "VULNERABILITY": "vulnerability",
    "CODE_SMELL": "code_smell",
}


def _project_key(repo_path: Path) -> str:
    return os.environ.get("SONARQUBE_PROJECT_KEY", repo_path.resolve().name)


def _api_get(host_url: str, path: str, token: str, timeout: int) -> dict:
    request = urllib.request.Request(f"{host_url.rstrip('/')}{path}")
    if token:
        import base64

        credentials = base64.b64encode(f"{token}:".encode()).decode()
        request.add_header("Authorization", f"Basic {credentials}")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def _report_task_info(repo_path: Path) -> dict | None:
    # The scanner writes this file on every successful analysis submission
    # (documented SonarScanner CLI behavior) - it's the only place the
    # ceTaskId/ceTaskUrl needed to poll for completion are exposed; the
    # scanner's own stdout is not a stable machine-readable source for them.
    report_path = repo_path / ".scannerwork" / "report-task.txt"
    if not report_path.exists():
        return None
    info = {}
    for line in report_path.read_text().splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            info[key.strip()] = value.strip()
    return info


def _wait_for_ce_task(host_url: str, task_id: str, token: str) -> str:
    deadline = time.monotonic() + _CE_TASK_POLL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        payload = _api_get(host_url, f"/api/ce/task?id={task_id}", token, timeout=30)
        status = payload.get("task", {}).get("status", "")
        if status in ("SUCCESS", "FAILED", "CANCELED"):
            return status
        time.sleep(_CE_TASK_POLL_INTERVAL_SECONDS)
    return "TIMEOUT"


def _fetch_issues(host_url: str, project_key: str, token: str) -> list[dict]:
    issues: list[dict] = []
    page = 1
    while True:
        payload = _api_get(
            host_url,
            f"/api/issues/search?componentKeys={project_key}"
            f"&statuses=OPEN,CONFIRMED,REOPENED&ps={_ISSUES_PAGE_SIZE}&p={page}",
            token,
            timeout=30,
        )
        page_issues = payload.get("issues", [])
        issues.extend(page_issues)
        total = payload.get("paging", {}).get("total", len(issues))
        if len(issues) >= total or not page_issues:
            break
        page += 1
    return issues


def check_sonarqube(
    repo_path: Path,
    host_url: str | None = None,
    timeout: int = DEFAULT_SONARQUBE_TIMEOUT_SECONDS,
) -> dict:
    """Opt-in/local-only per the integration scope doc's real hosting-cost
    tradeoff: no shared production server, no coverage unless the caller
    (or SONARQUBE_HOST_URL) points at one the caller controls. `checked:
    False` with no error is the expected, silent-by-design result for every
    installation that hasn't set this up - identical in spirit to
    `dependency_licenses`' checked:False on --no-check-licenses."""
    host_url = host_url or os.environ.get("SONARQUBE_HOST_URL")
    if not host_url:
        return {
            "checked": False,
            "reason": "SonarQube not configured (set SONARQUBE_HOST_URL to enable)",
            "findings": [],
        }

    binary = shutil.which("sonar-scanner")
    if binary is None:
        return {"checked": False, "reason": "sonar-scanner CLI not installed", "findings": []}

    token = os.environ.get("SONARQUBE_TOKEN", "")
    project_key = _project_key(repo_path)

    cmd = [
        binary,
        f"-Dsonar.projectKey={project_key}",
        "-Dsonar.sources=.",
        f"-Dsonar.host.url={host_url}",
    ]
    exclusions = ",".join(f"**/{name}/**" for name in excluded_dir_names(repo_path))
    if exclusions:
        cmd.append(f"-Dsonar.exclusions={exclusions}")
    if token:
        cmd.append(f"-Dsonar.token={token}")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=repo_path)
    except subprocess.TimeoutExpired:
        return {"checked": False, "reason": f"sonar-scanner timed out after {timeout}s", "findings": []}
    except OSError as exc:
        return {"checked": False, "reason": f"sonar-scanner failed to run: {exc}", "findings": []}

    if result.returncode != 0:
        return {
            "checked": False,
            "reason": f"sonar-scanner exited {result.returncode}: {(result.stderr or result.stdout)[-500:]}",
            "findings": [],
        }

    task_info = _report_task_info(repo_path)
    if task_info is None or "ceTaskId" not in task_info:
        return {
            "checked": False,
            "reason": "sonar-scanner produced no report-task.txt (analysis was not submitted)",
            "findings": [],
        }

    try:
        status = _wait_for_ce_task(host_url, task_info["ceTaskId"], token)
    except (urllib.error.URLError, OSError) as exc:
        return {"checked": False, "reason": f"SonarQube server unreachable while polling: {exc}", "findings": []}

    if status != "SUCCESS":
        return {
            "checked": False,
            "reason": f"SonarQube background analysis did not succeed (status={status})",
            "findings": [],
        }

    try:
        raw_issues = _fetch_issues(host_url, project_key, token)
    except (urllib.error.URLError, OSError) as exc:
        return {"checked": False, "reason": f"SonarQube issue fetch failed: {exc}", "findings": []}

    findings = []
    for issue in raw_issues:
        component = issue.get("component", "")
        # SonarQube's component id is "<projectKey>:<relative/path>" -
        # split on the first colon after the project key rather than
        # assuming no other colon ever appears in a path (unlikely on
        # POSIX, but Windows-style paths and some CI checkouts have used
        # colon-bearing branch-qualified keys in the wild).
        path = component.partition(f"{project_key}:")[2] or component
        findings.append(
            {
                "tool": "sonarqube",
                "rule_id": issue.get("rule", ""),
                "severity": _SEVERITY_MAP.get(issue.get("severity", ""), "minor"),
                "type": _TYPE_MAP.get(issue.get("type", ""), "bug"),
                "path": path,
                "line": issue.get("line", 0) or 0,
                "message": (issue.get("message") or "").strip(),
            }
        )
    return {"checked": True, "reason": None, "findings": filter_findings(findings, repo_path)}
