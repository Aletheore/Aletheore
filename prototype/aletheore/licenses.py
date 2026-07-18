import json
import re
import ssl
import tomllib
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from xml.etree import ElementTree

import certifi

from aletheore.vulnerabilities import (
    _parse_cargo_pins,
    _parse_composer_pins,
    _parse_gemfile_lock_pins,
    _parse_go_pins,
    _parse_maven_pins,
    _parse_npm_pins,
    _parse_nuget_pins,
    _parse_pip_pins,
)

PYPI_URL_TEMPLATE = "https://pypi.org/pypi/{name}/{version}/json"
NPM_URL_TEMPLATE = "https://registry.npmjs.org/{name}/{version}"
DEFAULT_TIMEOUT_SECONDS = 10

# Same reasoning as vulnerabilities.py: certifi's CA bundle explicitly, since a
# python.org macOS install commonly has no default CA bundle configured.
_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())

# Checked in this order deliberately: "agpl" and "lgpl" both contain "gpl" as a
# literal substring ("agpl"[1:] == "gpl", "lgpl"[1:] == "gpl"), so the specific
# variants have to be checked before the generic one or every AGPL/LGPL license
# would be miscategorized as plain (strong-copyleft) GPL.
_AGPL_MARKERS = ("agpl", "affero general public license")
_LGPL_MARKERS = ("lgpl", "lesser general public license")
_GPL_MARKERS = ("gpl", "general public license")
_WEAK_COPYLEFT_MARKERS = ("mpl", "mozilla public license", "eclipse public license", "epl")
_PERMISSIVE_MARKERS = (
    "mit", "bsd", "apache", "isc", "unlicense", "0bsd", "zlib", "boost",
    "python software foundation", "psf",
)


def _contains_marker(text: str, marker: str) -> bool:
    # A bare substring check breaks on real license *text* (as opposed to a short
    # SPDX-style string like "MPL-2.0", which is what these markers are also used
    # against): "mpl" matched literally inside the word "example" (e-x-a-mpl-e) in
    # both a real Apache LICENSE file and a hand-written MIT one, confirmed by
    # actually running this against both rather than assumed. Word-boundary regex
    # matches "MPL-2.0" (bounded by a hyphen) correctly while rejecting "example"
    # (no boundary mid-word), and costs nothing for the already-safe longer
    # markers like "general public license".
    return re.search(r"\b" + re.escape(marker) + r"\b", text) is not None


def categorize_license(license_text: str | None) -> str:
    if not license_text:
        return "unknown"
    text = license_text.lower()
    if any(_contains_marker(text, marker) for marker in _AGPL_MARKERS):
        return "copyleft-strong"
    if any(_contains_marker(text, marker) for marker in _LGPL_MARKERS):
        return "copyleft-weak"
    if any(_contains_marker(text, marker) for marker in _GPL_MARKERS):
        return "copyleft-strong"
    if any(_contains_marker(text, marker) for marker in _WEAK_COPYLEFT_MARKERS):
        return "copyleft-weak"
    if any(_contains_marker(text, marker) for marker in _PERMISSIVE_MARKERS):
        return "permissive"
    return "unknown"


def _categorize_license_file_text(text: str) -> str:
    # License file bodies are much longer than an SPDX-style string, but the
    # canonical templates for every common license open with a distinctive,
    # near-universal title line (verified against this repo's own real LICENSE
    # file, which opens with exactly "Apache License\nVersion 2.0" as asserted
    # in the test for it) - reusing the same keyword categorizer on just that
    # opening slice is simpler than a second, license-file-specific vocabulary
    # and catches the same common cases.
    return categorize_license(text[:2000])


def detect_repo_license(repo_path: Path) -> dict:
    pyproject = repo_path / "pyproject.toml"
    if pyproject.exists():
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8", errors="ignore"))
        except tomllib.TOMLDecodeError:
            data = {}
        license_field = data.get("project", {}).get("license")
        if isinstance(license_field, str):
            return {
                "category": categorize_license(license_field),
                "detected_from": f"pyproject.toml: {license_field}",
            }
        if isinstance(license_field, dict) and isinstance(license_field.get("text"), str):
            return {
                "category": categorize_license(license_field["text"]),
                "detected_from": f"pyproject.toml: {license_field['text']}",
            }

    package_json = repo_path / "package.json"
    if package_json.exists():
        try:
            data = json.loads(package_json.read_text(encoding="utf-8", errors="ignore"))
        except json.JSONDecodeError:
            data = {}
        license_field = data.get("license")
        if isinstance(license_field, str):
            return {
                "category": categorize_license(license_field),
                "detected_from": f"package.json: {license_field}",
            }

    for filename in ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING"):
        candidate = repo_path / filename
        if candidate.exists():
            text = candidate.read_text(encoding="utf-8", errors="ignore")
            category = _categorize_license_file_text(text)
            if category != "unknown":
                return {"category": category, "detected_from": f"{filename} text match"}

    return {"category": "unknown", "detected_from": None}


def _fetch_pypi_license(name: str, version: str, timeout: int) -> str | None:
    request = urllib.request.Request(PYPI_URL_TEMPLATE.format(name=name, version=version))
    with urllib.request.urlopen(request, timeout=timeout, context=_SSL_CONTEXT) as response:
        data = json.loads(response.read())
    info = data.get("info", {})
    license_field = info.get("license")
    if license_field and license_field.strip().upper() not in ("", "UNKNOWN"):
        return license_field
    for classifier in info.get("classifiers", []):
        if classifier.startswith("License ::"):
            return classifier.rsplit("::", 1)[-1].strip()
    return None


def _fetch_npm_license(name: str, version: str, timeout: int) -> str | None:
    request = urllib.request.Request(NPM_URL_TEMPLATE.format(name=name, version=version))
    with urllib.request.urlopen(request, timeout=timeout, context=_SSL_CONTEXT) as response:
        data = json.loads(response.read())
    license_field = data.get("license")
    if isinstance(license_field, str):
        return license_field
    if isinstance(license_field, dict):
        return license_field.get("type")
    return None


def _fetch_go_license(name: str, version: str, timeout: int) -> str | None:
    url = f"https://pkg.go.dev/v1beta/package/{name}?version={version}&licenses=true"
    request = urllib.request.Request(url)
    with urllib.request.urlopen(request, timeout=timeout, context=_SSL_CONTEXT) as response:
        data = json.loads(response.read())
    licenses = data.get("licenses", [])
    if licenses and licenses[0].get("types"):
        return licenses[0]["types"][0]
    return None


def _fetch_crates_license(name: str, version: str, timeout: int) -> str | None:
    request = urllib.request.Request(
        f"https://crates.io/api/v1/crates/{name}/{version}",
        headers={"User-Agent": "aletheore (https://github.com/Aletheore/Aletheore)"},
    )
    with urllib.request.urlopen(request, timeout=timeout, context=_SSL_CONTEXT) as response:
        data = json.loads(response.read())
    return data.get("version", {}).get("license")


def _fetch_maven_license(name: str, version: str, timeout: int) -> str | None:
    group, _, artifact = name.partition(":")
    group_path = group.replace(".", "/")
    url = (
        f"https://repo1.maven.org/maven2/{group_path}/{artifact}/{version}/"
        f"{artifact}-{version}.pom"
    )
    request = urllib.request.Request(url)
    with urllib.request.urlopen(request, timeout=timeout, context=_SSL_CONTEXT) as response:
        pom_text = response.read()
    root = ElementTree.fromstring(pom_text)
    ns = {"m": "http://maven.apache.org/POM/4.0.0"}
    license_name = root.find(".//m:licenses/m:license/m:name", ns)
    return license_name.text.strip() if license_name is not None and license_name.text else None


def _fetch_rubygems_license(name: str, version: str, timeout: int) -> str | None:
    request = urllib.request.Request(f"https://rubygems.org/api/v1/gems/{name}.json")
    with urllib.request.urlopen(request, timeout=timeout, context=_SSL_CONTEXT) as response:
        data = json.loads(response.read())
    licenses = data.get("licenses") or []
    return licenses[0] if licenses else None


def _fetch_packagist_license(name: str, version: str, timeout: int) -> str | None:
    request = urllib.request.Request(f"https://repo.packagist.org/p2/{name}.json")
    with urllib.request.urlopen(request, timeout=timeout, context=_SSL_CONTEXT) as response:
        data = json.loads(response.read())
    for entry in data.get("packages", {}).get(name, []):
        if entry.get("version") == version:
            licenses = entry.get("license") or []
            return licenses[0] if licenses else None
    return None


def _fetch_nuget_license(name: str, version: str, timeout: int) -> str | None:
    request = urllib.request.Request(
        f"https://api.nuget.org/v3/registration5-semver1/{name.lower()}/index.json"
    )
    with urllib.request.urlopen(request, timeout=timeout, context=_SSL_CONTEXT) as response:
        data = json.loads(response.read())
    for page in data.get("items", []):
        for item in page.get("items", []):
            entry = item.get("catalogEntry", {})
            if entry.get("version") == version:
                return entry.get("licenseExpression") or None
    return None


_LICENSE_FETCHERS = {
    "PyPI": _fetch_pypi_license,
    "npm": _fetch_npm_license,
    "Go": _fetch_go_license,
    "crates.io": _fetch_crates_license,
    "Maven": _fetch_maven_license,
    "RubyGems": _fetch_rubygems_license,
    "Packagist": _fetch_packagist_license,
    "NuGet": _fetch_nuget_license,
}


def check_dependency_licenses(
    repo_path: Path,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> dict:
    repo_license = detect_repo_license(repo_path)
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
        return {"checked": True, "reason": None, "repo_license": repo_license, "findings": []}

    findings = []
    for index, (name, version, ecosystem) in enumerate(pins, start=1):
        if on_progress is not None:
            on_progress(index, len(pins), name)
        try:
            license_text = _LICENSE_FETCHERS[ecosystem](name, version, timeout)
        except (
            urllib.error.URLError,
            TimeoutError,
            OSError,
            json.JSONDecodeError,
            ElementTree.ParseError,
        ):
            # A single package's registry lookup failing (network hiccup, the
            # package removed, a malformed response) isn't the same as the whole
            # check being unreachable - it's reported as an "unknown" finding
            # rather than silently dropped or failing everything else.
            license_text = None

        category = categorize_license(license_text)
        if category != "permissive":
            findings.append(
                {
                    "ecosystem": ecosystem,
                    "package": name,
                    "installed_version": version,
                    "license": license_text,
                    "category": category,
                }
            )

    return {"checked": True, "reason": None, "repo_license": repo_license, "findings": findings}
