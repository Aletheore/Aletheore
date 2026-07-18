# Multi-Language Dependency Vulnerability & License Checking

**Status:** Draft, pending review
**Date:** 2026-07-18

## Problem

Aletheore's dependency-graph/import-resolution feature already fully supports eleven
languages (Python, JavaScript/JSX, TypeScript/TSX, Go, Rust, Java, Ruby, PHP, C, C++, C#),
and the API-endpoint-mapping feature already fully supports Go, Rust, Java, Ruby, PHP, and C#
frameworks (verified directly against `prototype/aletheore/endpoints.py` - the README's claims
for this feature are accurate, not aspirational).

But the dependency-**vulnerability**/**license** check (`prototype/aletheore/vulnerabilities.py`,
`prototype/aletheore/licenses.py`) only ever reads two files: `requirements.txt` (pip) and
`package.json` (npm). Every other supported language - Go, Rust, Java, Ruby, PHP, C# - gets zero
coverage from this specific feature, despite Aletheore already understanding their code
structurally. This is the one confirmed real gap found in a full audit of per-language coverage
across the codebase (endpoint mapping and the repo's-own-license detection were both checked and
found already complete for these languages).

## Goals

Extend both `check_vulnerabilities` and `check_dependency_licenses` to also parse real pinned
dependencies from each of these six ecosystems, verified against real lockfiles/manifests and
real registry APIs (not assumed):

| Language | Manifest read | Vulnerability source | License source |
|---|---|---|---|
| Go | `go.mod` | OSV.dev ecosystem `Go` | pkg.go.dev's new official API (`GET pkg.go.dev/v1beta/package/{module}?version={v}&licenses=true` - confirmed real 2026-06 launch, returns `licenses[].types[]`, a clean SPDX-style string) |
| Rust | `Cargo.lock` | OSV.dev ecosystem `crates.io` | crates.io API (`GET crates.io/api/v1/crates/{name}/{version}`, `version.license` field) |
| Java | `pom.xml` (direct `<dependency>` entries with explicit `<version>`) | OSV.dev ecosystem `Maven` | the real POM XML fetched from Maven Central (`repo1.maven.org/maven2/{group/path}/{artifact}/{version}/{artifact}-{version}.pom`), parsed for its `<licenses><license><name>` block |
| Ruby | `Gemfile.lock` (the `GEM`/`specs:` section's 4-space-indented `name (version)` lines) | OSV.dev ecosystem `RubyGems` | RubyGems.org API (`GET rubygems.org/api/v1/gems/{name}.json`, `licenses` array) |
| PHP | `composer.lock` (`packages[]` array) | OSV.dev ecosystem `Packagist` | Packagist API (`GET repo.packagist.org/p2/{vendor}/{name}.json`, `license` array on the matching version entry) |
| C# | `packages.lock.json` (`dependencies.{framework}.{name}.resolved`) | OSV.dev ecosystem `NuGet` | NuGet API (`GET api.nuget.org/v3/registration5-semver1/{name-lowercase}/index.json`, `licenseExpression` - empty on some older packages, same "unknown" fallback already used for pip/npm) |

All six reuse the exact same downstream pipeline already built: `check_vulnerabilities` batches
pins through OSV.dev's existing `/v1/querybatch` (already ecosystem-agnostic - it just needs the
right ecosystem string per pin, which every new parser provides), and `check_dependency_licenses`
reuses the existing `categorize_license()` unchanged, since every new license source above
resolves to a plain SPDX-style string or short license name, exactly like the PyPI/npm license
fields it already categorizes today.

## Non-Goals

- **C/C++ stays out of scope.** No standard, universal package manager (vcpkg and Conan exist but
  neither is close to universal the way the other six ecosystems' tools are) - there's no single
  lockfile format to reliably parse.
- **No new vulnerability database.** OSV.dev is already integrated and already covers all six new
  ecosystems natively (confirmed against OSV's own schema documentation) - this is "teach the
  existing pipeline to find more pins," not "add a new vulnerability source."
- **No changes to `categorize_license()`.** Every new license source resolves to a string
  (SPDX identifier or short name) that the existing categorizer already handles correctly.
- **Java**: reading only direct `pom.xml` `<dependency>` entries with an explicit `<version>` tag,
  the closest real equivalent to `requirements.txt`'s `==` pins. Gradle projects (`build.gradle`)
  and Maven property-interpolated versions (`${some.version}`) are not covered by this spec - a
  real, separate follow-up if Java's Gradle ecosystem turns out to matter as much as Maven's.
- **No lockfile generation or dependency installation.** Exactly like the existing pip/npm
  checkers, this only ever reads a lockfile already present in the repo - it never runs
  `bundle install`, `cargo generate-lockfile`, `composer install`, etc. to produce a missing one.

## Architecture

### New parser functions (mirroring `_parse_pip_pins`/`_parse_npm_pins`'s existing signature)

All added to `prototype/aletheore/vulnerabilities.py`, each returning
`list[tuple[str, str, str]]` of `(name, version, ecosystem)` exactly like the two existing
parsers, so `check_vulnerabilities`'s existing `pins = _parse_pip_pins(repo_path) + ...` pattern
just grows by concatenation - no change to its own control flow:

```python
def _parse_go_pins(repo_path: Path) -> list[tuple[str, str, str]]:
    go_mod = repo_path / "go.mod"
    if not go_mod.exists():
        return []
    text = go_mod.read_text(encoding="utf-8", errors="ignore")
    pins = []
    in_require_block = False
    for line in text.splitlines():
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
        if version.text and not version.text.strip().startswith("$"):
            pins.append((f"{group.text}:{artifact.text}", version.text.strip(), "Maven"))
    return pins


def _parse_gemfile_lock_pins(repo_path: Path) -> list[tuple[str, str, str]]:
    gemfile_lock = repo_path / "Gemfile.lock"
    if not gemfile_lock.exists():
        return []
    pins = []
    in_gem_specs = False
    for line in gemfile_lock.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith("GEM"):
            in_gem_specs = False
            continue
        if line.strip() == "specs:" and not line.startswith("    "):
            in_gem_specs = True
            continue
        if not in_gem_specs:
            continue
        if line.startswith("    ") and not line.startswith("      "):
            match = re.match(r"^\s{4}(\S+) \(([^)]+)\)$", line)
            if match:
                pins.append((match.group(1), match.group(2), "RubyGems"))
        elif not line.startswith(" "):
            in_gem_specs = False
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
```

`check_vulnerabilities` and `check_dependency_licenses` both change their one `pins = ...` line
to concatenate all eight parsers instead of two - no other change to either function's control
flow, since OSV.dev's batch query and `categorize_license()` are already ecosystem-agnostic.

### New license-fetch functions (mirroring `_fetch_pypi_license`/`_fetch_npm_license`)

Added to `prototype/aletheore/licenses.py`, each returning `str | None` exactly like the two
existing fetchers, so `check_dependency_licenses`'s existing per-pin dispatch (`if ecosystem ==
"PyPI": ... else: ...`) becomes a small ecosystem-to-fetcher dispatch table instead of a single
if/else - the only structural change needed in that function:

```python
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
```

Each new fetch function follows the existing pattern exactly: called from inside
`check_dependency_licenses`'s existing per-pin loop, wrapped by the same existing
`except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):` catch that already
degrades a single failed lookup to `license_text = None` (categorized as `"unknown"`) rather than
failing the whole check - no new error-handling pattern needed.

## Testing

- One real-repo test per new ecosystem, mirroring the existing pip/npm tests' style: a small
  fixture lockfile (`go.mod`, `Cargo.lock`, `pom.xml`, `Gemfile.lock`, `composer.lock`,
  `packages.lock.json`) with one or two real, well-known pinned packages, confirming the parser
  extracts the exact expected `(name, version, ecosystem)` tuples.
- One mocked-network test per new license fetcher (matching the existing
  `_fetch_pypi_license`/`_fetch_npm_license` test style), confirming each correctly extracts a
  license string from a realistic response shape for that specific API.
- A real, live (not mocked) end-to-end confirmation for at least one repo per new language -
  e.g., scanning a small, real Go/Rust/Java/Ruby/PHP/C# repo with known dependencies and
  confirming real findings appear, not just that the code runs without error.
- Confirm a repo with none of the six new manifest files present still returns
  `{"checked": True, "reason": None, "findings": []}` for both checks, matching the existing
  behavior when no `requirements.txt`/`package.json` exists either.

## Success Criteria

1. Scanning a real Go, Rust, Java, Ruby, PHP, or C# repository with real pinned dependencies
   produces real, correctly-categorized license and vulnerability findings - verified against
   at least one known real finding per ecosystem (a real package with a known non-permissive
   license, or a real package with a known OSV.dev advisory).
2. A repo with no dependency manifest in any of the eight now-supported formats behaves exactly
   as it does today (empty findings, `checked: true`, no error).
3. The Aletheore full-marketing-website plan's showcase-data generation (Kubernetes, Go) can now
   show real dependency-license/vulnerability findings for Kubernetes specifically, closing the
   gap flagged in that plan's Global Constraints section - a direct, concrete cross-check that
   this feature actually works end-to-end on a real, huge, real-world Go repository.
