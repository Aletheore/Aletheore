import json
import re
import ssl
import time
import tomllib
import urllib.error
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from xml.etree import ElementTree

import certifi

from aletheore.vulnerabilities import (
    _parse_cargo_pins,
    _parse_composer_pins,
    _parse_gemfile_lock_pins,
    _parse_go_pins,
    _parse_gradle_pins,
    _parse_maven_pins,
    _parse_npm_pins,
    _parse_nuget_pins,
    _parse_pip_pins,
    _parse_swift_package_resolved_pins,
)

PYPI_URL_TEMPLATE = "https://pypi.org/pypi/{name}/{version}/json"
NPM_URL_TEMPLATE = "https://registry.npmjs.org/{name}/{version}"
DEFAULT_TIMEOUT_SECONDS = 10
# Each check is one blocking registry HTTP call; a repo with hundreds of
# pinned dependencies (real case: 441) took 3+ minutes fully serial, enough
# to blow the hosted worker's per-job timeout on its own. Bounded, not
# unbounded, so this stays polite to registries under a large repo.
LICENSE_CHECK_CONCURRENCY = 20

# A registry lookup for one exact (ecosystem, name, version) triple returns
# the same answer forever in the overwhelming majority of cases - a
# published package version's license doesn't change - so every scan of
# every repo on this machine re-paying the same network round-trip for the
# same dependency is pure waste. Cached across repos and across
# invocations, not per-scan. 30 days is defensive against the rare
# corrected-metadata case, not a sign this data actually changes on that
# timescale.
DEFAULT_LICENSE_CACHE_PATH = Path.home() / ".cache" / "aletheore" / "license-cache.json"
_LICENSE_CACHE_TTL_SECONDS = 30 * 24 * 60 * 60

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
    "python software foundation", "psf", "python-2.0", "wtfpl", "blueoak",
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


def _fetch_swift_license(name: str, version: str, timeout: int) -> str | None:
    """Swift has no centralized package-registry license API the way
    PyPI/npm/crates.io do - SwiftPM dependencies are consumed directly from
    their git host, not published to an index. GitHub's own Contents API
    ("/repos/{owner}/{repo}/license", confirmed empirically against a real
    package) is the real, free source used here instead; only GitHub-hosted
    packages are resolvable this way (the overwhelming majority of real
    Swift dependencies), matching `name`'s "github.com/owner/repo" shape
    from _parse_swift_package_resolved_pins.

    SwiftPM release tags are commonly the bare version ("1.36.0") but a real
    minority use a "v"-prefixed tag ("v1.36.0") - confirmed both forms exist
    in the wild. Tried in that order, falling back to the default branch's
    current license rather than reporting nothing just because the exact
    tag wasn't found - license drift between a specific release and HEAD is
    rare (same reasoning the module-level license-cache-TTL comment already
    relies on).
    """
    if not name.startswith("github.com/"):
        return None
    owner_repo = name[len("github.com/") :]
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "aletheore (https://github.com/Aletheore/Aletheore)",
    }
    for ref in (version, f"v{version}", None):
        url = f"https://api.github.com/repos/{owner_repo}/license"
        if ref:
            url += f"?ref={ref}"
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=_SSL_CONTEXT) as response:
                data = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                continue
            raise
        license_info = data.get("license") or {}
        spdx_id = license_info.get("spdx_id")
        return spdx_id if spdx_id and spdx_id != "NOASSERTION" else None
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
    "SwiftURL": _fetch_swift_license,
}


def _fetch_one_license(pin: tuple[str, str, str], timeout: int) -> str | None:
    name, version, ecosystem = pin
    try:
        return _LICENSE_FETCHERS[ecosystem](name, version, timeout)
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
        return None


def _license_cache_key(ecosystem: str, name: str, version: str) -> str:
    return f"{ecosystem}|{name}|{version}"


def _load_license_cache(cache_path: Path) -> dict[str, dict]:
    try:
        return json.loads(cache_path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _save_license_cache(cache_path: Path, cache: dict[str, dict]) -> None:
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache))
    except OSError:
        # Best-effort: a failure to persist the cache must never fail the
        # license check itself - it only costs the next scan (of this or any
        # other repo) its cache hit, not correctness.
        pass


def _fetch_one_license_cached(
    pin: tuple[str, str, str], timeout: int, cache: dict[str, dict]
) -> str | None:
    name, version, ecosystem = pin
    key = _license_cache_key(ecosystem, name, version)
    cached = cache.get(key)
    if cached is not None and time.time() - cached.get("cached_at", 0) < _LICENSE_CACHE_TTL_SECONDS:
        return cached.get("license")

    license_text = _fetch_one_license(pin, timeout)
    # Safe to mutate from multiple worker threads: dict item assignment on
    # distinct keys is atomic under the GIL, and every pin has its own
    # distinct key here.
    cache[key] = {"license": license_text, "cached_at": time.time()}
    return license_text


def check_dependency_licenses(
    repo_path: Path,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    on_progress: Callable[[int, int, str], None] | None = None,
    cache_path: Path | None = None,
) -> dict:
    # Resolved inside the function body (not as the default argument value
    # itself) so a test monkeypatching DEFAULT_LICENSE_CACHE_PATH actually
    # takes effect - a default argument's value is bound once at function
    # definition time, before any test ever runs.
    if cache_path is None:
        cache_path = DEFAULT_LICENSE_CACHE_PATH
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
        + _parse_gradle_pins(repo_path)
        + _parse_swift_package_resolved_pins(repo_path)
    )
    if not pins:
        return {"checked": True, "reason": None, "repo_license": repo_license, "findings": []}

    findings = []
    cache = _load_license_cache(cache_path)
    # Each registry lookup is an independent blocking HTTP call - a thread
    # pool overlaps their network wait time instead of paying it serially.
    # executor.map (not as_completed) preserves pins' original order in the
    # results regardless of which thread finishes first, so progress
    # reporting and finding order both stay deterministic.
    with ThreadPoolExecutor(max_workers=LICENSE_CHECK_CONCURRENCY) as executor:
        license_texts = executor.map(
            lambda pin: _fetch_one_license_cached(pin, timeout, cache), pins
        )
        for index, ((name, version, ecosystem), license_text) in enumerate(
            zip(pins, license_texts), start=1
        ):
            if on_progress is not None:
                on_progress(index, len(pins), name)

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
    _save_license_cache(cache_path, cache)

    return {"checked": True, "reason": None, "repo_license": repo_license, "findings": findings}
