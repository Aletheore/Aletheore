import json
import re
import ssl
import threading
import time
import tomllib
import urllib.error
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
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
# `timeout` (DEFAULT_TIMEOUT_SECONDS) bounds a single blocking socket
# operation (connect/read), not the total time to receive a response - a
# registry serving its body slowly (a few bytes every few seconds, no
# single read ever idle long enough to trip the socket timeout) can still
# take far longer overall. Confirmed as a real production incident, not a
# theoretical one: three real npm packages (es-errors, es6-error,
# serialize-error) reproducibly stalled dependency-license checks for
# 1+ hour before their RQ work-horse was eventually reaped, appearing as
# "work-horse terminated unexpectedly" - not a timeout error at all, since
# nothing ever actually raised one. This wall-clock cap is the real fix:
# generous enough for a legitimately slow-but-real response, firm enough
# that one bad registry endpoint can never hang the whole check.
LICENSE_FETCH_WALL_CLOCK_TIMEOUT_SECONDS = 30

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
# cddl added after finding a real, live Maven dependency (javax.annotation:
# jsr250-api, pulled in by gson's own pom.xml) whose real, correctly-fetched
# license text ("COMMON DEVELOPMENT AND DISTRIBUTION LICENSE (CDDL) Version
# 1.0") fell through every existing bucket to "unknown" - CDDL is a real,
# file-level weak-copyleft license family (same category as MPL/EPL by
# standard SPDX/OSI classification), not an unrecognizable one.
_WEAK_COPYLEFT_MARKERS = (
    "mpl", "mozilla public license", "eclipse public license", "epl",
    "cddl", "common development and distribution license",
)
_PERMISSIVE_MARKERS = (
    "mit", "bsd", "apache", "isc", "unlicense", "0bsd", "zlib", "boost",
    "python software foundation", "psf", "python-2.0", "wtfpl", "blueoak",
    # Found via ast-pattern/dead-code/licenses overnight benchmark pass,
    # real-repo stress test: neither the 2-clause nor 3-clause BSD license
    # BODY text contains the literal word "bsd" anywhere - it's a purely
    # descriptive redistribution-terms text that never names itself. The
    # "bsd" marker above only ever matches when a project's LICENSE file
    # is preceded by a header naming it, or when detect_repo_license()
    # already succeeded via pyproject.toml's/package.json's machine-
    # readable license field (which DOES say "BSD-3-Clause" etc.) - for a
    # project with no such metadata, relying purely on the LICENSE file's
    # prose body (Flask's and gorilla/mux's real situation, confirmed
    # directly: both categorized "unknown" despite being unambiguously
    # BSD-licensed, real projects, real LICENSE files), the body alone was
    # unrecognizable. This exact opening phrase is the canonical,
    # near-universal signature both BSD variants share and open with -
    # confirmed present verbatim in 4 of the real repos checked during
    # this benchmark run (click, flask, django, gorilla-mux).
    "redistribution and use in source and binary forms",
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

    # LICENSE.rst added after this benchmark's real-repo stress test:
    # Flask's own real repo uses exactly this filename (a common
    # convention for reStructuredText-docs-style Python projects) and
    # was invisible to this list entirely.
    for filename in ("LICENSE", "LICENSE.md", "LICENSE.txt", "LICENSE.rst", "COPYING"):
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


# A very common real Maven convention (Guava's own pom.xml uses it,
# confirmed directly): the <licenses> block lives on a shared <parent>
# POM, not the artifact's own - the artifact pom.xml's own comment even
# says why ("copied from the parent pom because..."). A single-level
# fetch missed this entirely: Guava, Guava-testlib, and Protobuf-java (a
# different Google project with the same parent-POM convention) all came
# back "unknown" despite being real, unambiguously Apache-2.0-licensed
# artifacts, found while stress-testing this benchmark against a real
# repo (gson, whose own pom.xml pins all three). Bounded depth (real
# parent chains are 1-2 levels; this guards against an unexpected cycle
# or unusually deep chain turning one dependency's lookup into an
# unbounded fetch loop), not unlimited recursion.
_MAVEN_POM_NS = {"m": "http://maven.apache.org/POM/4.0.0"}
_MAVEN_PARENT_LOOKUP_MAX_HOPS = 3


def _fetch_maven_pom(group: str, artifact: str, version: str, timeout: int) -> ElementTree.Element:
    group_path = group.replace(".", "/")
    url = (
        f"https://repo1.maven.org/maven2/{group_path}/{artifact}/{version}/"
        f"{artifact}-{version}.pom"
    )
    request = urllib.request.Request(url)
    with urllib.request.urlopen(request, timeout=timeout, context=_SSL_CONTEXT) as response:
        pom_text = response.read()
    return ElementTree.fromstring(pom_text)


def _fetch_maven_license(name: str, version: str, timeout: int) -> str | None:
    group, _, artifact = name.partition(":")
    coordinates = (group, artifact, version)
    for _ in range(_MAVEN_PARENT_LOOKUP_MAX_HOPS):
        group, artifact, version = coordinates
        root = _fetch_maven_pom(group, artifact, version, timeout)
        license_name = root.find(".//m:licenses/m:license/m:name", _MAVEN_POM_NS)
        if license_name is not None and license_name.text:
            return license_name.text.strip()
        parent = root.find("m:parent", _MAVEN_POM_NS)
        if parent is None:
            return None
        parent_group_el = parent.find("m:groupId", _MAVEN_POM_NS)
        parent_artifact_el = parent.find("m:artifactId", _MAVEN_POM_NS)
        parent_version_el = parent.find("m:version", _MAVEN_POM_NS)
        if parent_group_el is None or parent_artifact_el is None or parent_version_el is None:
            return None
        if not (parent_group_el.text and parent_artifact_el.text and parent_version_el.text):
            return None
        next_coordinates = (parent_group_el.text.strip(), parent_artifact_el.text.strip(), parent_version_el.text.strip())
        if next_coordinates == coordinates:
            return None
        coordinates = next_coordinates
    return None


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


_SWIFT_GITHUB_OWNER_REPO_RE = re.compile(
    r"^github\.com/([A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)/([A-Za-z0-9._-]+)$"
)


def _fetch_swift_license(name: str, version: str, timeout: int) -> str | None:
    """Swift has no centralized package-registry license API the way
    PyPI/npm/crates.io do - SwiftPM dependencies are consumed directly from
    their git host, not published to an index. GitHub's own Contents API
    ("/repos/{owner}/{repo}/license", confirmed empirically against a real
    package) is the real, free source used here instead; only GitHub-hosted
    packages are resolvable this way (the overwhelming majority of real
    Swift dependencies), matching `name`'s "github.com/owner/repo" shape
    from _parse_swift_package_resolved_pins.

    `name` traces back to Package.resolved's own "location" field - real
    content from the scanned repository, not a value this codebase
    controls. A prefix check alone (`name.startswith("github.com/")`) is
    exactly the "incomplete URL substring sanitization" class CodeQL
    flagged here: it doesn't rule out a crafted value like
    "github.com/@attacker.example/x" still passing (the classic
    trusted-domain-as-userinfo trick, though the request host here is
    always the hardcoded api.github.com regardless, not attacker-derived -
    fixing the check anyway rather than arguing the specific blast radius,
    since a security product's own dependency code is exactly where this
    should be provably correct, not just currently-not-exploitable). A
    single fullmatch against an anchored owner/repo shape closes it -
    nothing this doesn't explicitly allow can reach the URL built below.
    """
    match = _SWIFT_GITHUB_OWNER_REPO_RE.fullmatch(name)
    if match is None:
        return None
    owner_repo = f"{match.group(1)}/{match.group(2)}"
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
    pin: tuple[str, str, str], timeout: int, cache: dict[str, dict], cache_lock: threading.Lock
) -> str | None:
    name, version, ecosystem = pin
    key = _license_cache_key(ecosystem, name, version)
    cached = cache.get(key)
    if cached is not None and time.time() - cached.get("cached_at", 0) < _LICENSE_CACHE_TTL_SECONDS:
        return cached.get("license")

    license_text = _fetch_one_license(pin, timeout)
    # A plain `cache[key] = ...` here (distinct key per pin, atomic under
    # the GIL) used to be enough on its own - but check_dependency_licenses
    # abandons any worker still stuck past LICENSE_FETCH_WALL_CLOCK_TIMEOUT_
    # SECONDS (shutdown(wait=False), see there for why) rather than waiting
    # for it to exit. That abandoned thread is still running and can land
    # this same write at any point after the main thread moves on -
    # including while it's iterating `cache` whole to serialize it in
    # _save_license_cache. A dict mutating size mid-iteration raises
    # RuntimeError there, turning "degrades to license: None" into a real
    # crash of the whole check. The lock only needs to guard the two places
    # that actually touch dict *shape* (this insert, and the snapshot taken
    # before saving) - not the network fetch itself, so it costs nothing.
    with cache_lock:
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
    # Submitted individually (not executor.map, whose iterator yields
    # results in submission order - one stuck fetch at position N blocks
    # every result after it from ever being reported, even though later
    # threads already finished; see LICENSE_FETCH_WALL_CLOCK_TIMEOUT_SECONDS
    # for why "stuck" is a real, not theoretical, failure mode here).
    # future.result(timeout=...) bounds how long THIS function waits for
    # each pin without needing to interrupt whatever the worker thread is
    # actually blocked on - that thread is abandoned (Python has no way to
    # forcibly kill a blocked thread) and left to die on its own eventual
    # socket timeout; shutdown(wait=False) below means this function
    # returns promptly rather than blocking on that stray thread to exit.
    cache_lock = threading.Lock()
    executor = ThreadPoolExecutor(max_workers=LICENSE_CHECK_CONCURRENCY)
    try:
        futures = [
            executor.submit(_fetch_one_license_cached, pin, timeout, cache, cache_lock)
            for pin in pins
        ]
        for index, ((name, version, ecosystem), future) in enumerate(zip(pins, futures), start=1):
            if on_progress is not None:
                on_progress(index, len(pins), name)

            try:
                license_text = future.result(timeout=LICENSE_FETCH_WALL_CLOCK_TIMEOUT_SECONDS)
            except FutureTimeoutError:
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
    finally:
        executor.shutdown(wait=False)
    # Snapshot under the same lock rather than handing `cache` itself to
    # _save_license_cache - an abandoned worker thread can still be alive
    # past shutdown(wait=False) and land a write while this iterates it to
    # serialize (see _fetch_one_license_cached). dict(cache) here is a
    # single, fast, lock-held copy; the slower json.dumps + disk write below
    # runs outside the lock so it never blocks a worker's own brief insert.
    with cache_lock:
        cache_snapshot = dict(cache)
    _save_license_cache(cache_path, cache_snapshot)

    return {"checked": True, "reason": None, "repo_license": repo_license, "findings": findings}
