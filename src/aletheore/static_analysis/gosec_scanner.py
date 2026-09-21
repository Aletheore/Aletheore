import json
import re
import shutil
import subprocess
from pathlib import Path

from aletheore.static_analysis._exclusions import count_real_files, excluded_dir_names, filter_findings, has_real_file

# Real bug found by an independent benchmark run the same night: a flat
# 180s (this package's original default) timed out on every real
# full-scan pass against a genuinely monorepo-scale Go repo
# (grafana/grafana) - the same gap already found and fixed for Bearer,
# then Semgrep. gosec's own real bottleneck is likely module/dependency
# graph resolution (it type-checks via go/importer), not raw file count,
# so file count is a weaker proxy here than it is for Semgrep/Bearer -
# no better one is available without more real data, so this uses the
# same conservative base+per-file+ceiling shape rather than leaving the
# flat timeout that's already confirmed insufficient.
GOSEC_BASE_TIMEOUT_SECONDS = 60
GOSEC_PER_FILE_SECONDS = 0.05
GOSEC_MAX_TIMEOUT_SECONDS = 1800

_SEVERITY_MAP = {"HIGH": "critical", "MEDIUM": "major", "LOW": "minor"}

# gosec's own "line" field is a string, and can be a "start-end" range for a
# multi-line issue (confirmed against gosec's own source: fmt.Sprintf("%d-%d",
# ...) for ranges) rather than always a bare integer - real-tested tonight
# only produced a bare "9", but the range shape is documented gosec
# behavior, not a hypothetical. Taking the first number covers both.
_FIRST_INT_RE = re.compile(r"\d+")


def _first_line_number(raw: str) -> int:
    match = _FIRST_INT_RE.search(raw or "")
    return int(match.group()) if match else 0


def _scaled_timeout(repo_path: Path) -> int:
    file_count = count_real_files(repo_path)
    return min(GOSEC_MAX_TIMEOUT_SECONDS, int(GOSEC_BASE_TIMEOUT_SECONDS + file_count * GOSEC_PER_FILE_SECONDS))


def check_gosec(repo_path: Path, timeout: int | None = None) -> dict:
    if not has_real_file(repo_path, "*.go"):
        return {"checked": True, "reason": None, "findings": []}

    binary = shutil.which("gosec")
    if binary is None:
        return {"checked": False, "reason": "gosec not installed", "findings": []}

    if timeout is None:
        timeout = _scaled_timeout(repo_path)

    cmd = [binary, "-fmt=json", "-quiet"]
    # Real-verified live: repeated -exclude-dir=<bare name> flags correctly
    # keep gosec from ever walking into (not just from reporting on) a
    # nested duplicate tree like .claude/worktrees/<id>/ - unlike bandit's
    # -x, a bare name works here without needing a "./name/*" glob form.
    for name in excluded_dir_names(repo_path):
        cmd.append(f"-exclude-dir={name}")
    cmd.append("./...")

    try:
        # gosec exits non-zero (confirmed live: exit 1) whenever it finds
        # any issue - the JSON on stdout is authoritative for 0/1; any
        # other code is treated as a real failure, not partial output.
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=repo_path)
    except subprocess.TimeoutExpired:
        return {"checked": False, "reason": f"gosec timed out after {timeout}s", "findings": []}
    except OSError as exc:
        return {"checked": False, "reason": f"gosec failed to run: {exc}", "findings": []}

    if result.returncode not in (0, 1):
        return {
            "checked": False,
            "reason": f"gosec exited {result.returncode}: {(result.stderr or result.stdout)[-500:]}",
            "findings": [],
        }

    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return {
            "checked": False,
            "reason": f"gosec produced unparseable output: {(result.stderr or '')[:300]}",
            "findings": [],
        }

    findings = []
    for item in payload.get("Issues", []):
        raw_file = item.get("file", "")
        # gosec's "file" is an absolute path (confirmed live) - relativize
        # so paths line up with every other tool's `path` field and with
        # the diff-scoping this evidence feeds elsewhere in the pipeline.
        try:
            rel_path = str(Path(raw_file).resolve().relative_to(repo_path.resolve()))
        except ValueError:
            rel_path = raw_file
        findings.append(
            {
                "tool": "gosec",
                "rule_id": item.get("rule_id", ""),
                "severity": _SEVERITY_MAP.get(item.get("severity", ""), "minor"),
                "type": "vulnerability",
                "path": rel_path,
                "line": _first_line_number(item.get("line", "")),
                "message": (item.get("details") or "").strip(),
            }
        )
    return {"checked": True, "reason": None, "findings": filter_findings(findings, repo_path)}
