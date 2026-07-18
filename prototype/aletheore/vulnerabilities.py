import json
import re
import ssl
import tomllib
import urllib.error
import urllib.request
from pathlib import Path
from xml.etree import ElementTree

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


def _parse_go_pins(repo_path: Path) -> list[tuple[str, str, str]]:
    go_mod = repo_path / "go.mod"
    if not go_mod.exists():
        return []
    pins = []
    in_require_block = False
    for line in go_mod.read_text(encoding="utf-8", errors="ignore").splitlines():
        stripped = line.strip()
        if stripped.startswith("require ("):
            in_require_block = True
            continue
        if in_require_block and stripped == ")":
            in_require_block = False
            continue
        if in_require_block:
            parts = stripped.split()
        elif stripped.startswith("require "):
            parts = stripped[len("require "):].split()
        else:
            continue
        if len(parts) >= 2 and parts[1].startswith("v"):
            pins.append((parts[0], parts[1], "Go"))
    return pins


def _parse_cargo_pins(repo_path: Path) -> list[tuple[str, str, str]]:
    cargo_lock = repo_path / "Cargo.lock"
    if not cargo_lock.exists():
        return []
    try:
        data = tomllib.loads(cargo_lock.read_text(encoding="utf-8", errors="ignore"))
    except tomllib.TOMLDecodeError:
        return []
    return [
        (pkg["name"], pkg["version"], "crates.io")
        for pkg in data.get("package", [])
        if "name" in pkg and "version" in pkg
    ]


def _parse_maven_pins(repo_path: Path) -> list[tuple[str, str, str]]:
    pom = repo_path / "pom.xml"
    if not pom.exists():
        return []
    try:
        root = ElementTree.fromstring(pom.read_text(encoding="utf-8", errors="ignore"))
    except ElementTree.ParseError:
        return []
    ns = {"m": "http://maven.apache.org/POM/4.0.0"}
    pins = []
    for dep in root.findall(".//m:dependencies/m:dependency", ns):
        group = dep.find("m:groupId", ns)
        artifact = dep.find("m:artifactId", ns)
        version = dep.find("m:version", ns)
        if group is None or artifact is None or version is None:
            continue
        if not group.text or not artifact.text or not version.text:
            continue
        version_text = version.text.strip()
        if version_text and not version_text.startswith("$"):
            pins.append((f"{group.text.strip()}:{artifact.text.strip()}", version_text, "Maven"))
    return pins


def _parse_gemfile_lock_pins(repo_path: Path) -> list[tuple[str, str, str]]:
    gemfile_lock = repo_path / "Gemfile.lock"
    if not gemfile_lock.exists():
        return []
    pins = []
    in_gem_section = False
    in_gem_specs = False
    for line in gemfile_lock.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line == "GEM":
            in_gem_section = True
            in_gem_specs = False
            continue
        if line and not line.startswith(" "):
            in_gem_section = False
            in_gem_specs = False
            continue
        if in_gem_section and line == "  specs:":
            in_gem_specs = True
            continue
        if not in_gem_specs:
            continue
        match = re.match(r"^ {4}(\S+) \(([^)]+)\)$", line)
        if match:
            pins.append((match.group(1), match.group(2), "RubyGems"))
    return pins


def _parse_composer_pins(repo_path: Path) -> list[tuple[str, str, str]]:
    composer_lock = repo_path / "composer.lock"
    if not composer_lock.exists():
        return []
    try:
        data = json.loads(composer_lock.read_text(encoding="utf-8", errors="ignore"))
    except json.JSONDecodeError:
        return []
    return [
        (pkg["name"], pkg["version"].lstrip("v"), "Packagist")
        for pkg in data.get("packages", [])
        if "name" in pkg and "version" in pkg
    ]


def _parse_nuget_pins(repo_path: Path) -> list[tuple[str, str, str]]:
    lock_file = repo_path / "packages.lock.json"
    if not lock_file.exists():
        return []
    try:
        data = json.loads(lock_file.read_text(encoding="utf-8", errors="ignore"))
    except json.JSONDecodeError:
        return []
    pins = []
    for framework_deps in data.get("dependencies", {}).values():
        for name, details in framework_deps.items():
            resolved = details.get("resolved")
            if resolved:
                pins.append((name, resolved, "NuGet"))
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
    pins = (
        _parse_pip_pins(repo_path)
        + _parse_npm_pins(repo_path)
        + _parse_go_pins(repo_path)
        + _parse_cargo_pins(repo_path)
        + _parse_maven_pins(repo_path)
        + _parse_gemfile_lock_pins(repo_path)
        + _parse_composer_pins(repo_path)
        + _parse_nuget_pins(repo_path)
    )
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
