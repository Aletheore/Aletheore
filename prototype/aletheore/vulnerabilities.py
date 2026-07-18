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


def _clean_range_version(version: str) -> str | None:
    cleaned = version.strip().lstrip("^~>=< ").strip()
    match = re.match(r"^([0-9][A-Za-z0-9_.!+*-]*)", cleaned)
    return match.group(1) if match else None


def _parse_pep508_dependency(dependency: str) -> tuple[str, str, str] | None:
    dependency = dependency.split(";", 1)[0].strip()
    match = re.match(
        r"^([A-Za-z0-9_.-]+)(?:\[[^\]]+\])?\s*(==|>=)\s*([0-9][^,\s]*)$",
        dependency,
    )
    if not match:
        return None
    return (match.group(1).lower().replace("_", "-"), match.group(3), "PyPI")


def _parse_python_dependency_value(name: str, value: object) -> tuple[str, str, str] | None:
    version = None
    if isinstance(value, str):
        version = value
    elif isinstance(value, dict) and isinstance(value.get("version"), str):
        version = value["version"]
    if version is None:
        return None
    cleaned = _clean_range_version(version)
    if cleaned is None:
        return None
    return (name.lower().replace("_", "-"), cleaned, "PyPI")


def _parse_pip_pins(repo_path: Path) -> list[tuple[str, str, str]]:
    requirements = repo_path / "requirements.txt"
    pins = []
    if requirements.exists():
        for line in requirements.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "==" not in line:
                continue
            name, _, version = line.partition("==")
            name = name.strip().lower()
            version = version.split(";")[0].split(",")[0].strip()
            if name and version:
                pins.append((name, version, "PyPI"))

    pyproject = repo_path / "pyproject.toml"
    if not pyproject.exists():
        return pins
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8", errors="ignore"))
    except tomllib.TOMLDecodeError:
        return pins

    for dependency in data.get("project", {}).get("dependencies", []):
        if not isinstance(dependency, str):
            continue
        pin = _parse_pep508_dependency(dependency)
        if pin:
            pins.append(pin)

    poetry_deps = data.get("tool", {}).get("poetry", {}).get("dependencies", {})
    for name, value in poetry_deps.items():
        if name.lower() == "python":
            continue
        pin = _parse_python_dependency_value(name, value)
        if pin:
            pins.append(pin)

    return pins


def _parse_npm_pins(repo_path: Path) -> list[tuple[str, str, str]]:
    package_lock = repo_path / "package-lock.json"
    if package_lock.exists():
        try:
            lock_data = json.loads(package_lock.read_text(encoding="utf-8", errors="ignore"))
        except json.JSONDecodeError:
            lock_data = {}
        pins = []
        for path, details in lock_data.get("packages", {}).items():
            if not path.startswith("node_modules/"):
                continue
            name = path[len("node_modules/"):]
            version = details.get("version")
            if name and version:
                pins.append((name, version, "npm"))
        if pins:
            return pins

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
        cleaned = _clean_range_version(version)
        if cleaned:
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
    if cargo_lock.exists():
        try:
            data = tomllib.loads(cargo_lock.read_text(encoding="utf-8", errors="ignore"))
        except tomllib.TOMLDecodeError:
            data = {}
        pins = [
            (pkg["name"], pkg["version"], "crates.io")
            for pkg in data.get("package", [])
            if "name" in pkg and "version" in pkg
        ]
        if pins:
            return pins

    cargo_toml = repo_path / "Cargo.toml"
    if not cargo_toml.exists():
        return []
    try:
        data = tomllib.loads(cargo_toml.read_text(encoding="utf-8", errors="ignore"))
    except tomllib.TOMLDecodeError:
        return []
    pins = []
    for section in ("dependencies", "dev-dependencies"):
        for name, value in data.get(section, {}).items():
            version = None
            if isinstance(value, str):
                version = value
            elif isinstance(value, dict) and isinstance(value.get("version"), str):
                version = value["version"]
            if version:
                cleaned = _clean_range_version(version)
                if cleaned:
                    pins.append((name, cleaned, "crates.io"))
    return pins


def _maven_text(element: ElementTree.Element | None) -> str | None:
    if element is None or element.text is None:
        return None
    text = element.text.strip()
    return text or None


def _maven_child(element: ElementTree.Element, tag: str, ns: dict[str, str]) -> ElementTree.Element | None:
    return element.find(f"m:{tag}", ns)


def _maven_resolve_property(version: str | None, properties: dict[str, str]) -> str | None:
    if not version:
        return None
    match = re.fullmatch(r"\$\{([^}]+)\}", version.strip())
    if match:
        return properties.get(match.group(1))
    return version.strip()


def _parse_maven_pom(pom: Path, seen: set[Path]) -> list[tuple[str, str, str]]:
    if not pom.exists():
        return []
    pom = pom.resolve()
    if pom in seen:
        return []
    seen.add(pom)
    try:
        root = ElementTree.fromstring(pom.read_text(encoding="utf-8", errors="ignore"))
    except ElementTree.ParseError:
        return []
    ns = {"m": "http://maven.apache.org/POM/4.0.0"}
    properties = {
        child.tag.rsplit("}", 1)[-1]: child.text.strip()
        for child in root.findall("m:properties/*", ns)
        if child.text and child.text.strip()
    }
    managed_versions = {}
    management = root.find("m:dependencyManagement/m:dependencies", ns)
    if management is not None:
        for dep in management.findall("m:dependency", ns):
            group = _maven_text(_maven_child(dep, "groupId", ns))
            artifact = _maven_text(_maven_child(dep, "artifactId", ns))
            version = _maven_resolve_property(
                _maven_text(_maven_child(dep, "version", ns)),
                properties,
            )
            if group and artifact and version:
                managed_versions[(group, artifact)] = version

    pins = []
    dependencies = root.find("m:dependencies", ns)
    if dependencies is not None:
        for dep in dependencies.findall("m:dependency", ns):
            group = _maven_text(_maven_child(dep, "groupId", ns))
            artifact = _maven_text(_maven_child(dep, "artifactId", ns))
            if not group or not artifact:
                continue
            version = _maven_resolve_property(
                _maven_text(_maven_child(dep, "version", ns)),
                properties,
            ) or managed_versions.get((group, artifact))
            if version:
                pins.append((f"{group}:{artifact}", version, "Maven"))

    modules = root.find("m:modules", ns)
    if modules is not None:
        for module in modules.findall("m:module", ns):
            module_name = _maven_text(module)
            if not module_name:
                continue
            pins.extend(_parse_maven_pom(pom.parent / module_name / "pom.xml", seen))
    return pins


def _parse_maven_pins(repo_path: Path) -> list[tuple[str, str, str]]:
    return _parse_maven_pom(repo_path / "pom.xml", set())


def _parse_gemspec_pins(repo_path: Path) -> list[tuple[str, str, str]]:
    pins = []
    for gemspec in sorted(repo_path.glob("*.gemspec")):
        for line in gemspec.read_text(encoding="utf-8", errors="ignore").splitlines():
            match = re.search(
                r"\.add_(?:runtime_)?dependency\s+['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]",
                line,
            )
            if not match:
                continue
            cleaned = _clean_range_version(match.group(2))
            if cleaned:
                pins.append((match.group(1), cleaned, "RubyGems"))
    return pins


def _parse_gemfile_lock_pins(repo_path: Path) -> list[tuple[str, str, str]]:
    gemfile_lock = repo_path / "Gemfile.lock"
    if not gemfile_lock.exists():
        return _parse_gemspec_pins(repo_path)
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
    if composer_lock.exists():
        try:
            data = json.loads(composer_lock.read_text(encoding="utf-8", errors="ignore"))
        except json.JSONDecodeError:
            data = {}
        pins = [
            (pkg["name"], pkg["version"].lstrip("v"), "Packagist")
            for pkg in data.get("packages", [])
            if "name" in pkg and "version" in pkg
        ]
        if pins:
            return pins

    composer_json = repo_path / "composer.json"
    if not composer_json.exists():
        return []
    try:
        data = json.loads(composer_json.read_text(encoding="utf-8", errors="ignore"))
    except json.JSONDecodeError:
        return []
    pins = []
    for name, version in data.get("require", {}).items():
        if name.lower() == "php":
            continue
        cleaned = _clean_range_version(version)
        if cleaned:
            pins.append((name, cleaned, "Packagist"))
    return pins


def _xml_local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xml_children(element: ElementTree.Element, name: str) -> list[ElementTree.Element]:
    return [child for child in list(element) if _xml_local_name(child.tag) == name]


def _xml_descendants(element: ElementTree.Element, name: str) -> list[ElementTree.Element]:
    return [child for child in element.iter() if _xml_local_name(child.tag) == name]


def _parse_directory_package_versions(repo_path: Path) -> dict[str, str]:
    props = repo_path / "Directory.Packages.props"
    if not props.exists():
        return {}
    try:
        root = ElementTree.fromstring(props.read_text(encoding="utf-8", errors="ignore"))
    except ElementTree.ParseError:
        return {}
    versions = {}
    for package in _xml_descendants(root, "PackageVersion"):
        name = package.attrib.get("Include") or package.attrib.get("Update")
        version = package.attrib.get("Version")
        if name and version:
            versions[name] = version
    return versions


def _parse_nuget_pins(repo_path: Path) -> list[tuple[str, str, str]]:
    lock_file = repo_path / "packages.lock.json"
    if lock_file.exists():
        try:
            data = json.loads(lock_file.read_text(encoding="utf-8", errors="ignore"))
        except json.JSONDecodeError:
            data = {}
        pins = []
        for framework_deps in data.get("dependencies", {}).values():
            for name, details in framework_deps.items():
                resolved = details.get("resolved")
                if resolved:
                    pins.append((name, resolved, "NuGet"))
        if pins:
            return pins

    central_versions = _parse_directory_package_versions(repo_path)
    pins = []
    for project_file in sorted(repo_path.rglob("*.csproj")):
        try:
            root = ElementTree.fromstring(project_file.read_text(encoding="utf-8", errors="ignore"))
        except ElementTree.ParseError:
            continue
        for package in _xml_descendants(root, "PackageReference"):
            name = package.attrib.get("Include") or package.attrib.get("Update")
            version = package.attrib.get("Version") or central_versions.get(name)
            if name and version:
                pins.append((name, version, "NuGet"))
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
