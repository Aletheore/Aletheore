# Multi-Language Dependency Vulnerability & License Checking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend Aletheore's dependency-vulnerability/license checking from pip/npm-only to also cover Go, Rust, Java, Ruby, PHP, and C# - the six languages the dependency-graph and endpoint-mapping features already fully support but this specific check does not.

**Architecture:** Six new parser functions in `prototype/aletheore/vulnerabilities.py` (one per ecosystem, each returning `list[tuple[str, str, str]]` exactly like the existing `_parse_pip_pins`/`_parse_npm_pins`), concatenated into `check_vulnerabilities`'s existing `pins = ...` line. Six new license-fetch functions in `prototype/aletheore/licenses.py`, dispatched through a new `_LICENSE_FETCHERS` dict (replacing the current two-branch if/else, introduced in Task 1) inside `check_dependency_licenses`. OSV.dev's existing batch-query call and `categorize_license()` are untouched - both are already ecosystem-agnostic.

**Tech Stack:** Python stdlib only (`tomllib` for TOML, `xml.etree.ElementTree` for XML, `re` for the Gemfile.lock text format, `json`/`urllib.request` already in use) - no new dependencies, matching the existing codebase's own no-new-dependency discipline.

## Global Constraints

- Every new parser returns `list[tuple[str, str, str]]` of `(name, version, ecosystem)`, matching `_parse_pip_pins`/`_parse_npm_pins`'s exact existing signature - `check_vulnerabilities` and `check_dependency_licenses` need no structural change beyond concatenating more parsers and dispatching more ecosystems.
- Every new license fetcher returns `str | None`, matching `_fetch_pypi_license`/`_fetch_npm_license`'s exact existing signature, and is wrapped by the same existing `except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):` catch already in `check_dependency_licenses` - no new error-handling pattern.
- `categorize_license()` itself is never modified - every new source resolves to a plain string it already handles.
- Real, verified API/format details (do not deviate without re-verifying against a live source):
  - Go: `go.mod` `require (...)` blocks or single-line `require module version`. License via `GET https://pkg.go.dev/v1beta/package/{module}?version={v}&licenses=true` → `licenses[0].types[0]`.
  - Rust: `Cargo.lock` is TOML, `[[package]]` tables with `name`/`version` keys. License via `GET https://crates.io/api/v1/crates/{name}/{version}` → `version.license`.
  - Java: `pom.xml` is XML in the `http://maven.apache.org/POM/4.0.0` namespace (confirmed present on real POMs), direct `<dependencies>/<dependency>` entries with a literal (non-`${...}`-interpolated) `<version>`. License via fetching the real POM at `https://repo1.maven.org/maven2/{group/path}/{artifact}/{version}/{artifact}-{version}.pom` → `.//licenses/license/name`.
  - Ruby: `Gemfile.lock`'s `GEM` section's `specs:` block, 4-space-indented `name (version)` lines only (not the 6+-space sub-dependency lines). License via `GET https://rubygems.org/api/v1/gems/{name}.json` → `licenses[0]`.
  - PHP: `composer.lock`'s `packages[]` array, `name`/`version` fields (strip a leading `v` from the version if present). License via `GET https://repo.packagist.org/p2/{name}.json` → the matching version entry's `license[0]`.
  - C#: `packages.lock.json`'s `dependencies.{framework}.{name}.resolved` fields. License via `GET https://api.nuget.org/v3/registration5-semver1/{name-lowercase}/index.json` → the matching version's `catalogEntry.licenseExpression`.
- C/C++ stays explicitly out of scope (no standard, universal package manager/lockfile).

---

## Task 1: Go support

**Files:**
- Modify: `prototype/aletheore/vulnerabilities.py`
- Modify: `prototype/aletheore/licenses.py`
- Test: `prototype/tests/test_vulnerabilities.py`
- Test: `prototype/tests/test_licenses.py`

**Interfaces:**
- Produces: `_parse_go_pins(repo_path: Path) -> list[tuple[str, str, str]]` in `vulnerabilities.py`; `_fetch_go_license(name: str, version: str, timeout: int) -> str | None` in `licenses.py`; the `_LICENSE_FETCHERS` dispatch dict in `licenses.py` (used by every subsequent task).

- [ ] **Step 1: Write the failing parser test**

Append to `prototype/tests/test_vulnerabilities.py`:

```python
def test_parse_go_pins_reads_require_block(tmp_path):
    from aletheore.vulnerabilities import _parse_go_pins

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "go.mod").write_text(
        "module example.com/thing\n\n"
        "go 1.21\n\n"
        "require (\n"
        "\tgithub.com/gin-gonic/gin v1.9.0\n"
        "\tgithub.com/pkg/errors v0.9.1 // indirect\n"
        ")\n"
    )

    pins = _parse_go_pins(repo)

    assert ("github.com/gin-gonic/gin", "v1.9.0", "Go") in pins
    assert ("github.com/pkg/errors", "v0.9.1", "Go") in pins


def test_parse_go_pins_reads_single_line_require(tmp_path):
    from aletheore.vulnerabilities import _parse_go_pins

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "go.mod").write_text("module example.com/thing\n\nrequire github.com/gin-gonic/gin v1.9.0\n")

    pins = _parse_go_pins(repo)

    assert pins == [("github.com/gin-gonic/gin", "v1.9.0", "Go")]


def test_parse_go_pins_empty_when_no_go_mod(tmp_path):
    from aletheore.vulnerabilities import _parse_go_pins

    repo = tmp_path / "repo"
    repo.mkdir()

    assert _parse_go_pins(repo) == []


def test_check_vulnerabilities_includes_go_pins(tmp_path):
    from unittest.mock import MagicMock, patch

    from aletheore.vulnerabilities import check_vulnerabilities

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "go.mod").write_text("module x\n\nrequire github.com/gin-gonic/gin v1.9.0\n")

    mock = MagicMock()
    mock.read.return_value = b'{"results": [{}]}'
    mock.__enter__.return_value = mock
    mock.__exit__.return_value = False

    with patch("aletheore.vulnerabilities.urllib.request.urlopen", return_value=mock) as mock_urlopen:
        result = check_vulnerabilities(repo)

    assert result == {"checked": True, "reason": None, "findings": []}
    sent_body = json.loads(mock_urlopen.call_args[0][0].data)
    assert {"package": {"name": "github.com/gin-gonic/gin", "ecosystem": "Go"}, "version": "v1.9.0"} in sent_body["queries"]
```

- [ ] **Step 2: Run to verify failure**

Run: `cd prototype && python -m pytest tests/test_vulnerabilities.py -k go_pins -v`
Expected: FAIL with `ImportError: cannot import name '_parse_go_pins'`

- [ ] **Step 3: Implement the Go parser**

Add near the top of `prototype/aletheore/vulnerabilities.py` (after the existing imports):

```python
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
```

Change `check_vulnerabilities`'s pins line from:
```python
    pins = _parse_pip_pins(repo_path) + _parse_npm_pins(repo_path)
```
to:
```python
    pins = _parse_pip_pins(repo_path) + _parse_npm_pins(repo_path) + _parse_go_pins(repo_path)
```

- [ ] **Step 4: Run to verify pass**

Run: `cd prototype && python -m pytest tests/test_vulnerabilities.py -v`
Expected: PASS (all tests, including every pre-existing test in this file)

- [ ] **Step 5: Write the failing license-fetcher test**

Append to `prototype/tests/test_licenses.py`:

```python
def test_fetch_go_license_reads_types_field():
    from aletheore.licenses import _fetch_go_license

    response = _mock_response(
        {"licenses": [{"types": ["MIT"], "filePath": "LICENSE", "contents": "MIT License..."}]}
    )

    with patch("aletheore.licenses.urllib.request.urlopen", return_value=response):
        result = _fetch_go_license("github.com/gin-gonic/gin", "v1.9.0", timeout=10)

    assert result == "MIT"


def test_check_dependency_licenses_reports_a_go_dependency(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "go.mod").write_text("module x\n\nrequire github.com/some/gplthing v1.0.0\n")

    response = _mock_response({"licenses": [{"types": ["GPL-3.0"]}]})

    with patch("aletheore.licenses.urllib.request.urlopen", return_value=response):
        result = check_dependency_licenses(repo)

    assert len(result["findings"]) == 1
    assert result["findings"][0]["ecosystem"] == "Go"
    assert result["findings"][0]["category"] == "copyleft-strong"
```

- [ ] **Step 6: Run to verify failure**

Run: `cd prototype && python -m pytest tests/test_licenses.py -k go -v`
Expected: FAIL with `ImportError: cannot import name '_fetch_go_license'`

- [ ] **Step 7: Implement the Go license fetcher and the dispatch dict**

Add to `prototype/aletheore/licenses.py`, after `_fetch_npm_license`:

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
```

Replace `check_dependency_licenses`'s existing per-pin dispatch:
```python
            if ecosystem == "PyPI":
                license_text = _fetch_pypi_license(name, version, timeout)
            else:
                license_text = _fetch_npm_license(name, version, timeout)
```
with a dispatch dict, defined once at module level (right after `DEFAULT_TIMEOUT_SECONDS`) and used inside the loop:

```python
_LICENSE_FETCHERS = {
    "PyPI": _fetch_pypi_license,
    "npm": _fetch_npm_license,
    "Go": _fetch_go_license,
}
```

```python
            license_text = _LICENSE_FETCHERS[ecosystem](name, version, timeout)
```

- [ ] **Step 8: Run to verify pass**

Run: `cd prototype && python -m pytest tests/test_licenses.py -v`
Expected: PASS (all tests, including every pre-existing test in this file)

- [ ] **Step 9: Run the full prototype suite**

Run: `cd prototype && python -m pytest -q`
Expected: PASS, no regressions

- [ ] **Step 10: Commit**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion
git add prototype/aletheore/vulnerabilities.py prototype/aletheore/licenses.py prototype/tests/test_vulnerabilities.py prototype/tests/test_licenses.py
git commit -m "feat: Go dependency vulnerability and license checking"
```

---

## Task 2: Rust support

**Files:**
- Modify: `prototype/aletheore/vulnerabilities.py`
- Modify: `prototype/aletheore/licenses.py`
- Test: `prototype/tests/test_vulnerabilities.py`
- Test: `prototype/tests/test_licenses.py`

**Interfaces:**
- Consumes: `_LICENSE_FETCHERS` dict (Task 1).
- Produces: `_parse_cargo_pins`, `_fetch_crates_license`.

- [ ] **Step 1: Write the failing parser test**

Append to `prototype/tests/test_vulnerabilities.py`:

```python
def test_parse_cargo_pins_reads_package_tables(tmp_path):
    from aletheore.vulnerabilities import _parse_cargo_pins

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "Cargo.lock").write_text(
        '# This file is automatically @generated by Cargo.\n'
        'version = 3\n\n'
        '[[package]]\n'
        'name = "serde"\n'
        'version = "1.0.219"\n'
        'source = "registry+https://github.com/rust-lang/crates.io-index"\n\n'
        '[[package]]\n'
        'name = "libc"\n'
        'version = "0.2.169"\n'
    )

    pins = _parse_cargo_pins(repo)

    assert ("serde", "1.0.219", "crates.io") in pins
    assert ("libc", "0.2.169", "crates.io") in pins


def test_parse_cargo_pins_empty_when_no_cargo_lock(tmp_path):
    from aletheore.vulnerabilities import _parse_cargo_pins

    repo = tmp_path / "repo"
    repo.mkdir()

    assert _parse_cargo_pins(repo) == []
```

- [ ] **Step 2: Run to verify failure**

Run: `cd prototype && python -m pytest tests/test_vulnerabilities.py -k cargo -v`
Expected: FAIL with `ImportError: cannot import name '_parse_cargo_pins'`

- [ ] **Step 3: Implement the Rust parser**

Add `import tomllib` to the top of `prototype/aletheore/vulnerabilities.py`'s imports (`licenses.py` already imports it - now `vulnerabilities.py` needs it too).

Add to `prototype/aletheore/vulnerabilities.py`:

```python
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
```

Extend the pins line: `+ _parse_cargo_pins(repo_path)`.

- [ ] **Step 4: Run to verify pass**

Run: `cd prototype && python -m pytest tests/test_vulnerabilities.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Write the failing license-fetcher test**

Append to `prototype/tests/test_licenses.py`:

```python
def test_fetch_crates_license_reads_version_license_field():
    from aletheore.licenses import _fetch_crates_license

    response = _mock_response({"version": {"license": "MIT OR Apache-2.0"}})

    with patch("aletheore.licenses.urllib.request.urlopen", return_value=response):
        result = _fetch_crates_license("serde", "1.0.219", timeout=10)

    assert result == "MIT OR Apache-2.0"


def test_check_dependency_licenses_reports_a_rust_dependency(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "Cargo.lock").write_text('[[package]]\nname = "somegpl"\nversion = "1.0.0"\n')

    response = _mock_response({"version": {"license": "GPL-3.0"}})

    with patch("aletheore.licenses.urllib.request.urlopen", return_value=response):
        result = check_dependency_licenses(repo)

    assert len(result["findings"]) == 1
    assert result["findings"][0]["ecosystem"] == "crates.io"
    assert result["findings"][0]["category"] == "copyleft-strong"
```

- [ ] **Step 6: Run to verify failure**

Run: `cd prototype && python -m pytest tests/test_licenses.py -k crates -v`
Expected: FAIL with `ImportError: cannot import name '_fetch_crates_license'`

- [ ] **Step 7: Implement the Rust license fetcher**

Add to `prototype/aletheore/licenses.py`, after `_fetch_go_license`:

```python
def _fetch_crates_license(name: str, version: str, timeout: int) -> str | None:
    request = urllib.request.Request(
        f"https://crates.io/api/v1/crates/{name}/{version}",
        headers={"User-Agent": "aletheore (https://github.com/Aletheore/Aletheore)"},
    )
    with urllib.request.urlopen(request, timeout=timeout, context=_SSL_CONTEXT) as response:
        data = json.loads(response.read())
    return data.get("version", {}).get("license")
```

Add `"crates.io": _fetch_crates_license,` to `_LICENSE_FETCHERS`.

- [ ] **Step 8: Run to verify pass**

Run: `cd prototype && python -m pytest tests/test_licenses.py -v`
Expected: PASS (all tests)

- [ ] **Step 9: Run the full prototype suite**

Run: `cd prototype && python -m pytest -q`
Expected: PASS, no regressions

- [ ] **Step 10: Commit**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion
git add prototype/aletheore/vulnerabilities.py prototype/aletheore/licenses.py prototype/tests/test_vulnerabilities.py prototype/tests/test_licenses.py
git commit -m "feat: Rust dependency vulnerability and license checking"
```

---

## Task 3: Java support

**Files:**
- Modify: `prototype/aletheore/vulnerabilities.py`
- Modify: `prototype/aletheore/licenses.py`
- Test: `prototype/tests/test_vulnerabilities.py`
- Test: `prototype/tests/test_licenses.py`

**Interfaces:**
- Consumes: `_LICENSE_FETCHERS` dict (Task 1).
- Produces: `_parse_maven_pins`, `_fetch_maven_license`.

- [ ] **Step 1: Write the failing parser test**

Append to `prototype/tests/test_vulnerabilities.py`:

```python
def test_parse_maven_pins_reads_direct_dependencies(tmp_path):
    from aletheore.vulnerabilities import _parse_maven_pins

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pom.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<project xmlns="http://maven.apache.org/POM/4.0.0">\n'
        '  <dependencies>\n'
        '    <dependency>\n'
        '      <groupId>org.springframework</groupId>\n'
        '      <artifactId>spring-core</artifactId>\n'
        '      <version>6.1.14</version>\n'
        '    </dependency>\n'
        '    <dependency>\n'
        '      <groupId>com.example</groupId>\n'
        '      <artifactId>interpolated</artifactId>\n'
        '      <version>${some.property}</version>\n'
        '    </dependency>\n'
        '  </dependencies>\n'
        '</project>\n'
    )

    pins = _parse_maven_pins(repo)

    assert ("org.springframework:spring-core", "6.1.14", "Maven") in pins
    assert not any(p[0] == "com.example:interpolated" for p in pins)


def test_parse_maven_pins_empty_when_no_pom(tmp_path):
    from aletheore.vulnerabilities import _parse_maven_pins

    repo = tmp_path / "repo"
    repo.mkdir()

    assert _parse_maven_pins(repo) == []
```

- [ ] **Step 2: Run to verify failure**

Run: `cd prototype && python -m pytest tests/test_vulnerabilities.py -k maven -v`
Expected: FAIL with `ImportError: cannot import name '_parse_maven_pins'`

- [ ] **Step 3: Implement the Java parser**

Add `from xml.etree import ElementTree` to the top of `prototype/aletheore/vulnerabilities.py`'s imports.

Add to `prototype/aletheore/vulnerabilities.py`:

```python
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
```

Extend the pins line: `+ _parse_maven_pins(repo_path)`.

- [ ] **Step 4: Run to verify pass**

Run: `cd prototype && python -m pytest tests/test_vulnerabilities.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Write the failing license-fetcher test**

Append to `prototype/tests/test_licenses.py`:

```python
def _mock_bytes_response(body: bytes):
    mock = MagicMock()
    mock.read.return_value = body
    mock.__enter__.return_value = mock
    mock.__exit__.return_value = False
    return mock


def test_fetch_maven_license_parses_pom_xml():
    from aletheore.licenses import _fetch_maven_license

    pom_xml = (
        b'<?xml version="1.0" encoding="UTF-8"?>\n'
        b'<project xmlns="http://maven.apache.org/POM/4.0.0">\n'
        b'  <licenses>\n'
        b'    <license>\n'
        b'      <name>Apache License, Version 2.0</name>\n'
        b'    </license>\n'
        b'  </licenses>\n'
        b'</project>\n'
    )

    with patch("aletheore.licenses.urllib.request.urlopen", return_value=_mock_bytes_response(pom_xml)):
        result = _fetch_maven_license("org.springframework:spring-core", "6.1.14", timeout=10)

    assert result == "Apache License, Version 2.0"


def test_check_dependency_licenses_reports_a_maven_dependency(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pom.xml").write_text(
        '<project xmlns="http://maven.apache.org/POM/4.0.0">'
        '<dependencies><dependency>'
        '<groupId>com.example</groupId><artifactId>gplthing</artifactId><version>1.0.0</version>'
        '</dependency></dependencies></project>'
    )

    pom_xml = (
        b'<project xmlns="http://maven.apache.org/POM/4.0.0">'
        b'<licenses><license><name>GNU General Public License v3.0</name></license></licenses>'
        b'</project>'
    )

    with patch("aletheore.licenses.urllib.request.urlopen", return_value=_mock_bytes_response(pom_xml)):
        result = check_dependency_licenses(repo)

    assert len(result["findings"]) == 1
    assert result["findings"][0]["ecosystem"] == "Maven"
    assert result["findings"][0]["category"] == "copyleft-strong"
```

- [ ] **Step 6: Run to verify failure**

Run: `cd prototype && python -m pytest tests/test_licenses.py -k maven -v`
Expected: FAIL with `ImportError: cannot import name '_fetch_maven_license'`

- [ ] **Step 7: Implement the Java license fetcher**

Add `from xml.etree import ElementTree` to the top of `prototype/aletheore/licenses.py`'s imports.

Add to `prototype/aletheore/licenses.py`, after `_fetch_crates_license`:

```python
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
```

Add `"Maven": _fetch_maven_license,` to `_LICENSE_FETCHERS`.

- [ ] **Step 8: Run to verify pass**

Run: `cd prototype && python -m pytest tests/test_licenses.py -v`
Expected: PASS (all tests)

- [ ] **Step 9: Run the full prototype suite**

Run: `cd prototype && python -m pytest -q`
Expected: PASS, no regressions

- [ ] **Step 10: Commit**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion
git add prototype/aletheore/vulnerabilities.py prototype/aletheore/licenses.py prototype/tests/test_vulnerabilities.py prototype/tests/test_licenses.py
git commit -m "feat: Java (Maven) dependency vulnerability and license checking"
```

---

## Task 4: Ruby support

**Files:**
- Modify: `prototype/aletheore/vulnerabilities.py`
- Modify: `prototype/aletheore/licenses.py`
- Test: `prototype/tests/test_vulnerabilities.py`
- Test: `prototype/tests/test_licenses.py`

**Interfaces:**
- Consumes: `_LICENSE_FETCHERS` dict (Task 1).
- Produces: `_parse_gemfile_lock_pins`, `_fetch_rubygems_license`.

- [ ] **Step 1: Write the failing parser test**

Append to `prototype/tests/test_vulnerabilities.py`:

```python
def test_parse_gemfile_lock_pins_reads_gem_specs_only(tmp_path):
    from aletheore.vulnerabilities import _parse_gemfile_lock_pins

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "Gemfile.lock").write_text(
        "PATH\n"
        "  remote: .\n"
        "  specs:\n"
        "    mygem (1.0.0)\n"
        "      activesupport (= 8.0.0)\n\n"
        "GEM\n"
        "  remote: https://rubygems.org/\n"
        "  specs:\n"
        "    activesupport (8.0.0)\n"
        "      base64\n"
        "    nokogiri (1.16.7)\n"
        "      mini_portile2 (~> 2.8.2)\n\n"
        "PLATFORMS\n"
        "  ruby\n"
    )

    pins = _parse_gemfile_lock_pins(repo)

    assert ("activesupport", "8.0.0", "RubyGems") in pins
    assert ("nokogiri", "1.16.7", "RubyGems") in pins
    assert not any(p[0] == "mygem" for p in pins)
    assert not any(p[0] == "base64" for p in pins)
    assert not any(p[0] == "mini_portile2" for p in pins)


def test_parse_gemfile_lock_pins_empty_when_no_gemfile_lock(tmp_path):
    from aletheore.vulnerabilities import _parse_gemfile_lock_pins

    repo = tmp_path / "repo"
    repo.mkdir()

    assert _parse_gemfile_lock_pins(repo) == []
```

- [ ] **Step 2: Run to verify failure**

Run: `cd prototype && python -m pytest tests/test_vulnerabilities.py -k gemfile -v`
Expected: FAIL with `ImportError: cannot import name '_parse_gemfile_lock_pins'`

- [ ] **Step 3: Implement the Ruby parser**

Add `import re` to the top of `prototype/aletheore/vulnerabilities.py`'s imports.

Add to `prototype/aletheore/vulnerabilities.py`:

```python
def _parse_gemfile_lock_pins(repo_path: Path) -> list[tuple[str, str, str]]:
    gemfile_lock = repo_path / "Gemfile.lock"
    if not gemfile_lock.exists():
        return []
    pins = []
    in_gem_specs = False
    for line in gemfile_lock.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line == "GEM":
            in_gem_specs = False
            continue
        if line == "  specs:":
            in_gem_specs = True
            continue
        if not in_gem_specs:
            continue
        if line and not line.startswith(" "):
            in_gem_specs = False
            continue
        match = re.match(r"^ {4}(\S+) \(([^)]+)\)$", line)
        if match:
            pins.append((match.group(1), match.group(2), "RubyGems"))
    return pins
```

Extend the pins line: `+ _parse_gemfile_lock_pins(repo_path)`.

- [ ] **Step 4: Run to verify pass**

Run: `cd prototype && python -m pytest tests/test_vulnerabilities.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Write the failing license-fetcher test**

Append to `prototype/tests/test_licenses.py`:

```python
def test_fetch_rubygems_license_reads_licenses_array():
    from aletheore.licenses import _fetch_rubygems_license

    response = _mock_response({"licenses": ["MIT"]})

    with patch("aletheore.licenses.urllib.request.urlopen", return_value=response):
        result = _fetch_rubygems_license("rails", "8.0.0", timeout=10)

    assert result == "MIT"


def test_check_dependency_licenses_reports_a_ruby_dependency(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "Gemfile.lock").write_text("GEM\n  remote: https://rubygems.org/\n  specs:\n    gplgem (1.0.0)\n")

    response = _mock_response({"licenses": ["GPL-3.0"]})

    with patch("aletheore.licenses.urllib.request.urlopen", return_value=response):
        result = check_dependency_licenses(repo)

    assert len(result["findings"]) == 1
    assert result["findings"][0]["ecosystem"] == "RubyGems"
    assert result["findings"][0]["category"] == "copyleft-strong"
```

- [ ] **Step 6: Run to verify failure**

Run: `cd prototype && python -m pytest tests/test_licenses.py -k rubygems -v`
Expected: FAIL with `ImportError: cannot import name '_fetch_rubygems_license'`

- [ ] **Step 7: Implement the Ruby license fetcher**

Add to `prototype/aletheore/licenses.py`, after `_fetch_maven_license`:

```python
def _fetch_rubygems_license(name: str, version: str, timeout: int) -> str | None:
    request = urllib.request.Request(f"https://rubygems.org/api/v1/gems/{name}.json")
    with urllib.request.urlopen(request, timeout=timeout, context=_SSL_CONTEXT) as response:
        data = json.loads(response.read())
    licenses = data.get("licenses") or []
    return licenses[0] if licenses else None
```

Add `"RubyGems": _fetch_rubygems_license,` to `_LICENSE_FETCHERS`.

- [ ] **Step 8: Run to verify pass**

Run: `cd prototype && python -m pytest tests/test_licenses.py -v`
Expected: PASS (all tests)

- [ ] **Step 9: Run the full prototype suite**

Run: `cd prototype && python -m pytest -q`
Expected: PASS, no regressions

- [ ] **Step 10: Commit**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion
git add prototype/aletheore/vulnerabilities.py prototype/aletheore/licenses.py prototype/tests/test_vulnerabilities.py prototype/tests/test_licenses.py
git commit -m "feat: Ruby dependency vulnerability and license checking"
```

---

## Task 5: PHP support

**Files:**
- Modify: `prototype/aletheore/vulnerabilities.py`
- Modify: `prototype/aletheore/licenses.py`
- Test: `prototype/tests/test_vulnerabilities.py`
- Test: `prototype/tests/test_licenses.py`

**Interfaces:**
- Consumes: `_LICENSE_FETCHERS` dict (Task 1).
- Produces: `_parse_composer_pins`, `_fetch_packagist_license`.

- [ ] **Step 1: Write the failing parser test**

Append to `prototype/tests/test_vulnerabilities.py`:

```python
def test_parse_composer_pins_reads_packages_array(tmp_path):
    from aletheore.vulnerabilities import _parse_composer_pins

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "composer.lock").write_text(
        json.dumps(
            {
                "packages": [
                    {"name": "laravel/framework", "version": "v11.30.0"},
                    {"name": "symfony/console", "version": "v7.1.6"},
                ]
            }
        )
    )

    pins = _parse_composer_pins(repo)

    assert ("laravel/framework", "11.30.0", "Packagist") in pins
    assert ("symfony/console", "7.1.6", "Packagist") in pins


def test_parse_composer_pins_empty_when_no_composer_lock(tmp_path):
    from aletheore.vulnerabilities import _parse_composer_pins

    repo = tmp_path / "repo"
    repo.mkdir()

    assert _parse_composer_pins(repo) == []
```

- [ ] **Step 2: Run to verify failure**

Run: `cd prototype && python -m pytest tests/test_vulnerabilities.py -k composer -v`
Expected: FAIL with `ImportError: cannot import name '_parse_composer_pins'`

- [ ] **Step 3: Implement the PHP parser**

Add to `prototype/aletheore/vulnerabilities.py`:

```python
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
```

Extend the pins line: `+ _parse_composer_pins(repo_path)`.

- [ ] **Step 4: Run to verify pass**

Run: `cd prototype && python -m pytest tests/test_vulnerabilities.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Write the failing license-fetcher test**

Append to `prototype/tests/test_licenses.py`:

```python
def test_fetch_packagist_license_matches_exact_version():
    from aletheore.licenses import _fetch_packagist_license

    response = _mock_response(
        {
            "packages": {
                "laravel/framework": [
                    {"version": "11.30.0", "license": ["MIT"]},
                    {"version": "11.29.0", "license": ["MIT"]},
                ]
            }
        }
    )

    with patch("aletheore.licenses.urllib.request.urlopen", return_value=response):
        result = _fetch_packagist_license("laravel/framework", "11.30.0", timeout=10)

    assert result == "MIT"


def test_check_dependency_licenses_reports_a_php_dependency(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "composer.lock").write_text(
        json.dumps({"packages": [{"name": "some/gplthing", "version": "1.0.0"}]})
    )

    response = _mock_response(
        {"packages": {"some/gplthing": [{"version": "1.0.0", "license": ["GPL-3.0"]}]}}
    )

    with patch("aletheore.licenses.urllib.request.urlopen", return_value=response):
        result = check_dependency_licenses(repo)

    assert len(result["findings"]) == 1
    assert result["findings"][0]["ecosystem"] == "Packagist"
    assert result["findings"][0]["category"] == "copyleft-strong"
```

- [ ] **Step 6: Run to verify failure**

Run: `cd prototype && python -m pytest tests/test_licenses.py -k packagist -v`
Expected: FAIL with `ImportError: cannot import name '_fetch_packagist_license'`

- [ ] **Step 7: Implement the PHP license fetcher**

Add to `prototype/aletheore/licenses.py`, after `_fetch_rubygems_license`:

```python
def _fetch_packagist_license(name: str, version: str, timeout: int) -> str | None:
    request = urllib.request.Request(f"https://repo.packagist.org/p2/{name}.json")
    with urllib.request.urlopen(request, timeout=timeout, context=_SSL_CONTEXT) as response:
        data = json.loads(response.read())
    for entry in data.get("packages", {}).get(name, []):
        if entry.get("version") == version:
            licenses = entry.get("license") or []
            return licenses[0] if licenses else None
    return None
```

Add `"Packagist": _fetch_packagist_license,` to `_LICENSE_FETCHERS`.

- [ ] **Step 8: Run to verify pass**

Run: `cd prototype && python -m pytest tests/test_licenses.py -v`
Expected: PASS (all tests)

- [ ] **Step 9: Run the full prototype suite**

Run: `cd prototype && python -m pytest -q`
Expected: PASS, no regressions

- [ ] **Step 10: Commit**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion
git add prototype/aletheore/vulnerabilities.py prototype/aletheore/licenses.py prototype/tests/test_vulnerabilities.py prototype/tests/test_licenses.py
git commit -m "feat: PHP (Packagist) dependency vulnerability and license checking"
```

---

## Task 6: C# support

**Files:**
- Modify: `prototype/aletheore/vulnerabilities.py`
- Modify: `prototype/aletheore/licenses.py`
- Test: `prototype/tests/test_vulnerabilities.py`
- Test: `prototype/tests/test_licenses.py`

**Interfaces:**
- Consumes: `_LICENSE_FETCHERS` dict (Task 1).
- Produces: `_parse_nuget_pins`, `_fetch_nuget_license`.

- [ ] **Step 1: Write the failing parser test**

Append to `prototype/tests/test_vulnerabilities.py`:

```python
def test_parse_nuget_pins_reads_resolved_versions(tmp_path):
    from aletheore.vulnerabilities import _parse_nuget_pins

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "packages.lock.json").write_text(
        json.dumps(
            {
                "version": 1,
                "dependencies": {
                    "net8.0": {
                        "Newtonsoft.Json": {"type": "Direct", "resolved": "13.0.3"},
                        "Serilog": {"type": "Direct", "resolved": "4.0.1"},
                    }
                },
            }
        )
    )

    pins = _parse_nuget_pins(repo)

    assert ("Newtonsoft.Json", "13.0.3", "NuGet") in pins
    assert ("Serilog", "4.0.1", "NuGet") in pins


def test_parse_nuget_pins_empty_when_no_lock_file(tmp_path):
    from aletheore.vulnerabilities import _parse_nuget_pins

    repo = tmp_path / "repo"
    repo.mkdir()

    assert _parse_nuget_pins(repo) == []
```

- [ ] **Step 2: Run to verify failure**

Run: `cd prototype && python -m pytest tests/test_vulnerabilities.py -k nuget -v`
Expected: FAIL with `ImportError: cannot import name '_parse_nuget_pins'`

- [ ] **Step 3: Implement the C# parser**

Add to `prototype/aletheore/vulnerabilities.py`:

```python
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

Change the final pins line to include every parser built across this whole plan:
```python
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
```

- [ ] **Step 4: Run to verify pass**

Run: `cd prototype && python -m pytest tests/test_vulnerabilities.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Write the failing license-fetcher test**

Append to `prototype/tests/test_licenses.py`:

```python
def test_fetch_nuget_license_matches_exact_version():
    from aletheore.licenses import _fetch_nuget_license

    response = _mock_response(
        {
            "items": [
                {
                    "items": [
                        {"catalogEntry": {"version": "13.0.2", "licenseExpression": "MIT"}},
                        {"catalogEntry": {"version": "13.0.3", "licenseExpression": "MIT"}},
                    ]
                }
            ]
        }
    )

    with patch("aletheore.licenses.urllib.request.urlopen", return_value=response):
        result = _fetch_nuget_license("Newtonsoft.Json", "13.0.3", timeout=10)

    assert result == "MIT"


def test_check_dependency_licenses_reports_a_nuget_dependency(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "packages.lock.json").write_text(
        json.dumps({"dependencies": {"net8.0": {"Some.Gpl.Thing": {"resolved": "1.0.0"}}}})
    )

    response = _mock_response(
        {"items": [{"items": [{"catalogEntry": {"version": "1.0.0", "licenseExpression": "GPL-3.0"}}]}]}
    )

    with patch("aletheore.licenses.urllib.request.urlopen", return_value=response):
        result = check_dependency_licenses(repo)

    assert len(result["findings"]) == 1
    assert result["findings"][0]["ecosystem"] == "NuGet"
    assert result["findings"][0]["category"] == "copyleft-strong"
```

- [ ] **Step 6: Run to verify failure**

Run: `cd prototype && python -m pytest tests/test_licenses.py -k nuget -v`
Expected: FAIL with `ImportError: cannot import name '_fetch_nuget_license'`

- [ ] **Step 7: Implement the C# license fetcher**

Add to `prototype/aletheore/licenses.py`, after `_fetch_packagist_license`:

```python
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

Add `"NuGet": _fetch_nuget_license,` to `_LICENSE_FETCHERS` - this dict now has all eight entries: `PyPI`, `npm`, `Go`, `crates.io`, `Maven`, `RubyGems`, `Packagist`, `NuGet`.

- [ ] **Step 8: Run to verify pass**

Run: `cd prototype && python -m pytest tests/test_licenses.py -v`
Expected: PASS (all tests)

- [ ] **Step 9: Run the full prototype suite**

Run: `cd prototype && python -m pytest -q`
Expected: PASS, no regressions

- [ ] **Step 10: Commit**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion
git add prototype/aletheore/vulnerabilities.py prototype/aletheore/licenses.py prototype/tests/test_vulnerabilities.py prototype/tests/test_licenses.py
git commit -m "feat: C# (NuGet) dependency vulnerability and license checking"
```

---

## Task 7: Real-world validation against Kubernetes

**Files:**
- No new files - this task only validates Tasks 1-6 against a real, huge, real-world Go repository (Kubernetes), closing the gap the full-marketing-website plan's Global Constraints flagged (Kubernetes previously showed zero dependency/license findings because Go wasn't supported).

**Interfaces:**
- Consumes: everything built in Task 1 (Go support specifically).

- [ ] **Step 1: Clone Kubernetes at the same pinned commit the website plan used**

```bash
mkdir -p /tmp/aletheore-k8s-validation
cd /tmp/aletheore-k8s-validation
git clone --depth 1 https://github.com/kubernetes/kubernetes.git
cd kubernetes
git fetch --depth 1 origin bd1a1b897340ef91595c36439fed49b9072f8b1d
git checkout bd1a1b897340ef91595c36439fed49b9072f8b1d
```

- [ ] **Step 2: Confirm the real dependency count before scanning**

Run: `grep -cE "^\trequire |^\t[a-zA-Z0-9./_-]+ v[0-9]" go.mod`
Expected: a real, nonzero count, in the same ballpark as the ~206 direct requires measured during this plan's spec research (exact count may drift slightly with real upstream changes or grep-pattern differences - the point is confirming real data exists before the long scan runs, not matching a precise number).

- [ ] **Step 3: Run the real scan in the background and wait for it - this is the long-running step**

Kubernetes has ~200 direct dependencies, each triggering one OSV.dev vulnerability batch entry and (for any ecosystem hit) one pkg.go.dev license lookup - this genuinely takes real time over the network, on top of the tree-sitter parse of ~17,500 Go files. Do not run this in the foreground and assume it hung if it takes several minutes - launch it in the background and poll for completion instead of guessing when it's done:

```bash
cd /tmp/aletheore-k8s-validation/kubernetes
nohup aletheore scan . > /tmp/aletheore-k8s-validation/scan.log 2>&1 &
echo "scan started, pid $!"
```

Then wait for it explicitly rather than assuming a fixed duration:

```bash
until [ -f /tmp/aletheore-k8s-validation/kubernetes/.aletheore/evidence.json ] && ! pgrep -f "aletheore scan \." > /dev/null; do
  echo "still scanning... ($(tail -1 /tmp/aletheore-k8s-validation/scan.log))"
  sleep 30
done
echo "scan finished"
```

- [ ] **Step 4: Verify real Go findings actually appear**

Run:
```bash
python3 -c "
import json
with open('/tmp/aletheore-k8s-validation/kubernetes/.aletheore/evidence.json') as f:
    data = json.load(f)
vuln = data['security']['dependency_vulnerabilities']
lic = data['security']['dependency_licenses']
print('vulnerabilities checked:', vuln['checked'], 'reason:', vuln.get('reason'))
print('vulnerability findings:', len(vuln['findings']))
print('license findings:', len(lic['findings']))
print('sample license finding:', lic['findings'][0] if lic['findings'] else None)
"
```
Expected: `vulnerabilities checked: True`, and (unlike before this plan) real license findings now present for at least some of Kubernetes's ~200 Go dependencies - confirming this feature genuinely works end-to-end against a real, huge, real-world Go codebase, not just the fixture-scale tests from Tasks 1-6.

- [ ] **Step 5: Clean up the validation clone**

```bash
rm -rf /tmp/aletheore-k8s-validation
```

- [ ] **Step 6: Note the finding for the website plan**

If Step 4 showed real Go findings, the full-marketing-website plan's `showcase-data.js` (generated by its own Task 1, run separately) should be regenerated once this plan is merged, so the live Kubernetes showcase card can show real dependency/license numbers instead of the zero it showed before Go support existed - flag this to the user rather than silently leaving the website's showcase data stale relative to what Aletheore can now actually find.
