import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from aletheore.static_analysis._exclusions import count_real_files, excluded_dir_names, filter_findings

# Real, live-measured finding (2026-09-21): Trivy's secret scanner caught a
# genuine OpenAI Project API key in a real .env file that Aletheore's own
# secrets.py completely missed - confirmed why: secrets.py has zero
# OpenAI-key pattern coverage at all. Trivy's misconfig scanner also found
# real, if minor, findings (missing Dockerfile HEALTHCHECKs) that
# detect_infrastructure (pure file-inventory, no misconfiguration analysis)
# structurally cannot produce. Dependency-vulnerability scanning is
# deliberately NOT wired in here yet: a real side-by-side test against this
# repo's own requirements found 0 vulnerabilities either way, so there is
# no measured evidence yet that it beats the existing OSV.dev-based
# vulnerabilities.py - secret + misconfig only for now, vuln scanning is a
# separate, not-yet-justified addition.
#
# Deliberately secret,misconfig only, not "fs" mode's default of
# everything: `--scanners vuln` needs a real, correctly-named lockfile
# (confirmed live: this repo's own requirements.lock.txt wasn't recognized
# under its real name, only under the conventional requirements.txt) and
# has no measured value yet - see above.
_SCANNERS_ARG = "secret,misconfig"

DEFAULT_TRIVY_TIMEOUT_SECONDS = 180
TRIVY_BASE_TIMEOUT_SECONDS = 60
TRIVY_PER_FILE_SECONDS = 0.05
TRIVY_MAX_TIMEOUT_SECONDS = 1800

_SEVERITY_MAP = {
    "CRITICAL": "critical",
    "HIGH": "major",
    "MEDIUM": "major",
    "LOW": "minor",
    "UNKNOWN": "minor",
}


def _scaled_timeout(repo_path: Path) -> int:
    file_count = count_real_files(repo_path)
    scaled = TRIVY_BASE_TIMEOUT_SECONDS + int(file_count * TRIVY_PER_FILE_SECONDS)
    return min(scaled, TRIVY_MAX_TIMEOUT_SECONDS)


def _redact_secret_value(value: str, salt: str) -> str:
    # Same convention as secrets.py's own _redact - a salted one-way hash,
    # never a truncated raw preview. Real reason this matters, confirmed
    # live: Trivy's own JSON output includes the full, real secret value in
    # cleartext (both a dedicated match field and several lines of
    # surrounding file context in Code.Lines) - printing or storing that
    # raw is a real leak vector this function exists to close before a
    # finding ever leaves this module.
    digest = hashlib.sha256(f"{salt}:{value}".encode("utf-8")).hexdigest()[:12]
    return f"sha256:{digest}"


def _skip_dirs_arg(repo_path: Path) -> str | None:
    names = excluded_dir_names(repo_path)
    if not names:
        return None
    return ",".join(f"**/{name}/**" for name in names)


def check_trivy(repo_path: Path, timeout: int | None = None) -> dict:
    binary = shutil.which("trivy")
    if binary is None:
        return {"checked": False, "reason": "trivy not installed", "findings": []}

    resolved_timeout = timeout if timeout is not None else _scaled_timeout(repo_path)

    cmd = [
        binary, "fs",
        "--scanners", _SCANNERS_ARG,
        "--format", "json",
        "--timeout", f"{resolved_timeout}s",
        "--quiet",
    ]
    skip_dirs = _skip_dirs_arg(repo_path)
    if skip_dirs:
        cmd.extend(["--skip-dirs", skip_dirs])
    cmd.append(".")

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=resolved_timeout + 10, cwd=repo_path
        )
    except subprocess.TimeoutExpired:
        return {"checked": False, "reason": f"trivy timed out after {resolved_timeout}s", "findings": []}
    except OSError as exc:
        return {"checked": False, "reason": f"trivy failed to run: {exc}", "findings": []}

    if result.returncode != 0:
        return {
            "checked": False,
            "reason": f"trivy exited {result.returncode}: {(result.stderr or result.stdout)[-500:]}",
            "findings": [],
        }

    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return {
            "checked": False,
            "reason": f"trivy produced unparseable output: {(result.stderr or '')[:300]}",
            "findings": [],
        }

    findings = []
    for target_result in payload.get("Results", []):
        path = target_result.get("Target", "")
        for secret in target_result.get("Secrets") or []:
            rule_id = secret.get("RuleID", "")
            line = secret.get("StartLine", 0)
            salt = f"{path}:{rule_id}"
            # The real value never reaches this dict at all - only its
            # salted hash and its rule/title, matching every other
            # dashboard-rendered finding's shape (message text a human can
            # read safely), never the secret material itself.
            preview = _redact_secret_value(f"{path}:{line}:{rule_id}", salt)
            findings.append(
                {
                    "tool": "trivy",
                    "rule_id": rule_id,
                    "severity": _SEVERITY_MAP.get(secret.get("Severity", ""), "minor"),
                    "type": "privacy",
                    "path": path,
                    "line": line,
                    "message": f"{secret.get('Title', 'Possible secret')} ({preview})",
                }
            )
        for misconfig in target_result.get("Misconfigurations") or []:
            # Most misconfig checks (a missing directive, an absent
            # setting) have no single real line to point at - confirmed
            # live against this repo's own Dockerfiles, CauseMetadata
            # carries no StartLine at all for that shape of check. Some
            # misconfig types (a bad value ON a specific line) do populate
            # it - read it when present rather than hardcoding 0 always.
            cause = misconfig.get("CauseMetadata") or {}
            line = cause.get("StartLine") or 0
            findings.append(
                {
                    "tool": "trivy",
                    "rule_id": misconfig.get("ID", ""),
                    "severity": _SEVERITY_MAP.get(misconfig.get("Severity", ""), "minor"),
                    "type": "bug",
                    "path": path,
                    "line": line,
                    "message": (misconfig.get("Message") or misconfig.get("Title") or "").strip(),
                }
            )

    return {"checked": True, "reason": None, "findings": filter_findings(findings, repo_path)}
