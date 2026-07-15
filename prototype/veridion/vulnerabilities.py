import json
import ssl
import urllib.error
import urllib.request
from pathlib import Path

import certifi

OSV_BATCH_URL = "https://api.osv.dev/v1/querybatch"
OSV_VULN_URL_TEMPLATE = "https://api.osv.dev/v1/vulns/{vuln_id}"
DEFAULT_TIMEOUT_SECONDS = 10

# Use certifi's CA bundle explicitly rather than the system default SSL context.
# On macOS, Python installed from python.org commonly has no default CA bundle
# configured (the "Install Certificates.command" step is easy to skip), which
# would otherwise make every OSV.dev call fail with CERTIFICATE_VERIFY_FAILED
# even though certifi itself is installed and correct - discovered by actually
# running this against a real repo, not by inspection.
_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())


def _parse_pip_pins(repo_path: Path) -> list[tuple[str, str, str]]:
    requirements = repo_path / "requirements.txt"
    if not requirements.exists():
        return []
    pins = []
    for line in requirements.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "==" not in line:
            continue
        name, _, version = line.partition("==")
        name = name.strip().lower()
        version = version.split(";")[0].split(",")[0].strip()
        if name and version:
            pins.append((name, version, "PyPI"))
    return pins


def _parse_npm_pins(repo_path: Path) -> list[tuple[str, str, str]]:
    package_json = repo_path / "package.json"
    if not package_json.exists():
        return []
    try:
        data = json.loads(package_json.read_text(encoding="utf-8", errors="ignore"))
    except json.JSONDecodeError:
        return []
    deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
    pins = []
    for name, version in deps.items():
        cleaned = version.lstrip("^~>=< ").strip()
        if cleaned and cleaned[0].isdigit():
            pins.append((name, cleaned, "npm"))
    return pins


def _query_batch(pins: list[tuple[str, str, str]], timeout: int) -> list[dict]:
    queries = [
        {"package": {"name": name, "ecosystem": ecosystem}, "version": version}
        for name, version, ecosystem in pins
    ]
    body = json.dumps({"queries": queries}).encode("utf-8")
    request = urllib.request.Request(
        OSV_BATCH_URL, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(request, timeout=timeout, context=_SSL_CONTEXT) as response:
        return json.loads(response.read())["results"]


def _fetch_vuln_detail(vuln_id: str, timeout: int) -> dict:
    request = urllib.request.Request(OSV_VULN_URL_TEMPLATE.format(vuln_id=vuln_id))
    with urllib.request.urlopen(request, timeout=timeout, context=_SSL_CONTEXT) as response:
        return json.loads(response.read())


def check_vulnerabilities(repo_path: Path, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> dict:
    pins = _parse_pip_pins(repo_path) + _parse_npm_pins(repo_path)
    if not pins:
        return {"checked": True, "reason": None, "findings": []}

    try:
        results = _query_batch(pins, timeout)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {
            "checked": False,
            "reason": f"OSV.dev unreachable or timed out: {exc}",
            "findings": [],
        }

    findings = []
    for (name, version, ecosystem), result in zip(pins, results):
        for vuln in result.get("vulns", []):
            try:
                detail = _fetch_vuln_detail(vuln["id"], timeout)
            except (urllib.error.URLError, TimeoutError, OSError):
                detail = {}
            summary = detail.get("summary") or (detail.get("details") or "")[:200]
            findings.append(
                {
                    "ecosystem": ecosystem,
                    "package": name,
                    "installed_version": version,
                    "advisory_id": vuln["id"],
                    "summary": summary,
                    "severity": detail.get("severity", []),
                }
            )

    return {"checked": True, "reason": None, "findings": findings}
