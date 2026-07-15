# Veridion Part V (Security) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add secrets detection and dependency-vulnerability checking to Veridion's evidence
scanner, following the exact same deterministic-scan-then-agent-reasoning split already
proven in v1.

**Architecture:** Two new standalone modules (`veridion/secrets.py`, `veridion/vulnerabilities.py`)
that each take a `repo_path: Path` and return a plain dict, matching the style of the existing
`scanner/detect.py`/`scanner/graph.py`/`git_intel/analyzer.py`. `evidence.py` wires both into a
new top-level `security` block. `cli.py` gains a `--no-check-vulnerabilities` flag (on by
default, per an explicit product decision — most environments running `veridion` have network
access). A new `manual/part-5-security.md` teaches the agent how to read the new evidence.

**Tech Stack:** Python stdlib only — `re` for secrets, `urllib.request`/`urllib.error` for the
OSV.dev HTTP calls (no new dependency; the OSV.dev API contract below was verified live against
the real endpoint, not assumed).

## Global Constraints

- Secrets detection scans the **current working tree only** — no git history walking this round.
- Dependency-vulnerability checking covers **pip (`requirements.txt`, exact `==` pins only) and
  npm (`package.json` `dependencies`/`devDependencies`, range prefixes stripped) only** — no
  `package-lock.json` parsing this round, no other ecosystems.
- Vulnerability checking is **on by default**; `--no-check-vulnerabilities` opts out.
- Any OSV.dev request uses a **10-second timeout** and must degrade to `checked: false` with a
  real reason on any failure — it must never hang the whole scan or crash it.
- No secret's real value is ever written to `evidence.json` or any report — only a short
  redacted `match_preview`.
- Every non-code step (docs, manual content) still needs real, complete content — no
  "TBD"/"placeholder" text anywhere, per the design spec at
  `docs/superpowers/specs/2026-07-15-veridion-part-v-security-design.md`.

---

## Task 1: Secrets detection (`secrets.py`)

**Files:**
- Create: `prototype/veridion/secrets.py`
- Test: `prototype/tests/test_secrets.py`

**Interfaces:**
- Consumes: nothing from other tasks — this task is self-contained.
- Produces: `find_secrets(repo_path: Path) -> dict` returning
  `{"scanned_files": int, "findings": list[dict]}`, where each finding is
  `{"path": str, "line": int, "pattern": str, "match_preview": str, "likely_placeholder": bool}`.
  Task 3 (evidence.py wiring) calls this function by this exact name and return shape.

- [ ] **Step 1: Write the failing tests**

Create `prototype/tests/test_secrets.py`:
```python
from pathlib import Path

from veridion.secrets import find_secrets


def test_find_secrets_detects_aws_key(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "config.py").write_text('AWS_KEY = "AKIAABCDEFGHIJKLMNOP"\n')

    result = find_secrets(repo)

    assert result["scanned_files"] == 1
    assert len(result["findings"]) == 1
    finding = result["findings"][0]
    assert finding["path"] == "config.py"
    assert finding["line"] == 1
    assert finding["pattern"] == "aws_access_key_id"
    assert finding["likely_placeholder"] is False


def test_find_secrets_redacts_the_match(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "config.py").write_text('AWS_KEY = "AKIAABCDEFGHIJKLMNOP"\n')

    result = find_secrets(repo)

    preview = result["findings"][0]["match_preview"]
    assert "AKIAABCDEFGHIJKLMNOP" not in preview
    assert preview.startswith("AKIA")
    assert preview.endswith("MNOP")


def test_find_secrets_flags_test_fixture_paths_as_likely_placeholder(tmp_path):
    repo = tmp_path / "repo"
    (repo / "tests" / "fixtures").mkdir(parents=True)
    (repo / "tests" / "fixtures" / "sample.py").write_text(
        'STRIPE_KEY = "sk_test_00000000000000000000"\n'
    )

    result = find_secrets(repo)

    assert result["findings"][0]["likely_placeholder"] is True


def test_find_secrets_detects_github_token_and_private_key_header(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.env").write_text("TOKEN=ghp_" + "a" * 36 + "\n")
    (repo / "id_rsa").write_text("-----BEGIN RSA PRIVATE KEY-----\nMIIBogIBAAJ...\n")

    result = find_secrets(repo)

    patterns_found = {f["pattern"] for f in result["findings"]}
    assert "github_token" in patterns_found
    assert "private_key_header" in patterns_found


def test_find_secrets_ignores_ignored_dirs_and_binary_extensions(tmp_path):
    repo = tmp_path / "repo"
    (repo / "node_modules" / "pkg").mkdir(parents=True)
    (repo / "node_modules" / "pkg" / "secret.js").write_text('KEY = "AKIAABCDEFGHIJKLMNOP"\n')
    (repo / "logo.png").write_bytes(b"AKIAABCDEFGHIJKLMNOP" + b"\x89PNG")
    (repo / "clean.py").write_text("x = 1\n")

    result = find_secrets(repo)

    assert result["findings"] == []
    assert result["scanned_files"] == 1


def test_find_secrets_no_matches_in_ordinary_file(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("def add(a, b):\n    return a + b\n")

    result = find_secrets(repo)

    assert result["findings"] == []
    assert result["scanned_files"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd prototype && pytest tests/test_secrets.py -v
```
Expected: FAIL (`veridion.secrets` doesn't exist — `ModuleNotFoundError`).

- [ ] **Step 3: Implement `secrets.py`**

Create `prototype/veridion/secrets.py`:
```python
import re
from pathlib import Path

from veridion.scanner.detect import IGNORED_DIRS

BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".woff", ".woff2", ".ttf",
    ".eot", ".pdf", ".zip", ".tar", ".gz", ".mp4", ".mp3", ".wav", ".pyc",
    ".so", ".dylib", ".dll",
}

PLACEHOLDER_PATH_MARKERS = ("example", "test", "fixture", "mock")

SECRET_PATTERNS = [
    ("aws_access_key_id", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}")),
    ("stripe_key", re.compile(r"(sk|pk)_(live|test)_[A-Za-z0-9]{16,}")),
    ("private_key_header", re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("slack_token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("google_api_key", re.compile(r"AIza[0-9A-Za-z_-]{35}")),
    (
        "generic_credential_assignment",
        re.compile(r"(?i)\b(PASSWORD|SECRET|API_KEY)\s*[:=]\s*['\"]([A-Za-z0-9+/=_-]{16,})['\"]"),
    ),
]


def _iter_all_files(repo_path: Path):
    for path in repo_path.rglob("*"):
        if not path.is_file():
            continue
        if any(part in IGNORED_DIRS for part in path.parts):
            continue
        if path.suffix in BINARY_EXTENSIONS:
            continue
        yield path


def _is_likely_placeholder(rel_path: str) -> bool:
    lower = rel_path.lower()
    return any(marker in lower for marker in PLACEHOLDER_PATH_MARKERS)


def _redact(value: str) -> str:
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}{'*' * 4}...{value[-4:]}"


def find_secrets(repo_path: Path) -> dict:
    findings: list[dict] = []
    scanned_files = 0

    for path in _iter_all_files(repo_path):
        scanned_files += 1
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        rel_path = path.relative_to(repo_path).as_posix()
        for line_no, line in enumerate(text.splitlines(), start=1):
            for pattern_name, pattern in SECRET_PATTERNS:
                match = pattern.search(line)
                if match:
                    findings.append(
                        {
                            "path": rel_path,
                            "line": line_no,
                            "pattern": pattern_name,
                            "match_preview": _redact(match.group(0)),
                            "likely_placeholder": _is_likely_placeholder(rel_path),
                        }
                    )

    return {"scanned_files": scanned_files, "findings": findings}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd prototype && pytest tests/test_secrets.py -v
```
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add prototype/veridion/secrets.py prototype/tests/test_secrets.py
git commit -m "feat: add working-tree secrets detection"
```

---

## Task 2: Dependency vulnerability checking (`vulnerabilities.py`)

**Files:**
- Create: `prototype/veridion/vulnerabilities.py`
- Test: `prototype/tests/test_vulnerabilities.py`

**Interfaces:**
- Consumes: nothing from other tasks — self-contained. Reads `requirements.txt`/`package.json`
  directly (does not depend on `scanner/detect.py`'s `detect_frameworks`, which parses similar
  files for a different purpose and returns a different shape).
- Produces: `check_vulnerabilities(repo_path: Path, timeout: int = 10) -> dict` returning
  `{"checked": bool, "reason": str | None, "findings": list[dict]}`, where each finding is
  `{"ecosystem": str, "package": str, "installed_version": str, "advisory_id": str, "summary": str, "severity": list}`.
  Task 3 calls this function by this exact name and return shape.

**Verified OSV.dev API contract** (confirmed live against the real endpoint before writing this
task — do not deviate from this shape):
- `POST https://api.osv.dev/v1/querybatch` with body
  `{"queries": [{"package": {"name": "...", "ecosystem": "PyPI"}, "version": "..."}, ...]}`
  returns `{"results": [{"vulns": [{"id": "...", "modified": "..."}]}, {}, ...]}` — one entry
  per query, in the same order, `{}` (no `vulns` key) when clean.
- `GET https://api.osv.dev/v1/vulns/{id}` returns full detail including `severity` as a
  **list** of `{"type": "CVSS_V3", "score": "<CVSS vector string>"}` objects (not a plain
  string like "HIGH" — many records have this list empty), and either a short `summary` field
  or a long `details` field (not always both).

- [ ] **Step 1: Write the failing tests**

Create `prototype/tests/test_vulnerabilities.py`:
```python
import json
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

from veridion.vulnerabilities import check_vulnerabilities


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "requirements.txt").write_text("fastapi==0.100.0\nrequests>=2.0\n# comment\n")
    (repo / "package.json").write_text(
        json.dumps({"dependencies": {"left-pad": "^1.3.0"}, "devDependencies": {}})
    )
    return repo


def _mock_response(payload: dict):
    mock = MagicMock()
    mock.read.return_value = json.dumps(payload).encode("utf-8")
    mock.__enter__.return_value = mock
    mock.__exit__.return_value = False
    return mock


def test_check_vulnerabilities_parses_pinned_pip_and_npm_versions(tmp_path):
    repo = make_repo(tmp_path)
    batch_response = _mock_response({"results": [{}, {}]})

    with patch("veridion.vulnerabilities.urllib.request.urlopen", return_value=batch_response) as mock_urlopen:
        result = check_vulnerabilities(repo)

    assert result == {"checked": True, "reason": None, "findings": []}
    sent_request = mock_urlopen.call_args[0][0]
    sent_body = json.loads(sent_request.data)
    queries = sent_body["queries"]
    assert {"package": {"name": "fastapi", "ecosystem": "PyPI"}, "version": "0.100.0"} in queries
    assert {"package": {"name": "left-pad", "ecosystem": "npm"}, "version": "1.3.0"} in queries
    # requests>=2.0 has no exact pin and must not be queried
    assert not any(q["package"]["name"] == "requests" for q in queries)


def test_check_vulnerabilities_reports_a_real_finding(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "requirements.txt").write_text("fastapi==0.100.0\n")

    batch_response = _mock_response({"results": [{"vulns": [{"id": "PYSEC-2024-38"}]}]})
    detail_response = _mock_response(
        {
            "id": "PYSEC-2024-38",
            "details": "ReDoS in multipart form parsing.",
            "severity": [{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:H"}],
        }
    )

    with patch(
        "veridion.vulnerabilities.urllib.request.urlopen",
        side_effect=[batch_response, detail_response],
    ):
        result = check_vulnerabilities(repo)

    assert result["checked"] is True
    assert len(result["findings"]) == 1
    finding = result["findings"][0]
    assert finding["package"] == "fastapi"
    assert finding["advisory_id"] == "PYSEC-2024-38"
    assert finding["severity"] == [{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:H"}]


def test_check_vulnerabilities_degrades_gracefully_on_network_failure(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "requirements.txt").write_text("fastapi==0.100.0\n")

    with patch(
        "veridion.vulnerabilities.urllib.request.urlopen",
        side_effect=urllib.error.URLError("connection refused"),
    ):
        result = check_vulnerabilities(repo)

    assert result["checked"] is False
    assert "connection refused" in result["reason"]
    assert result["findings"] == []


def test_check_vulnerabilities_no_pins_short_circuits_without_network_call(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    with patch("veridion.vulnerabilities.urllib.request.urlopen") as mock_urlopen:
        result = check_vulnerabilities(repo)

    mock_urlopen.assert_not_called()
    assert result == {"checked": True, "reason": None, "findings": []}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd prototype && pytest tests/test_vulnerabilities.py -v
```
Expected: FAIL (`veridion.vulnerabilities` doesn't exist).

- [ ] **Step 3: Implement `vulnerabilities.py`**

Create `prototype/veridion/vulnerabilities.py`:
```python
import json
import urllib.error
import urllib.request
from pathlib import Path

OSV_BATCH_URL = "https://api.osv.dev/v1/querybatch"
OSV_VULN_URL_TEMPLATE = "https://api.osv.dev/v1/vulns/{vuln_id}"
DEFAULT_TIMEOUT_SECONDS = 10


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
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())["results"]


def _fetch_vuln_detail(vuln_id: str, timeout: int) -> dict:
    request = urllib.request.Request(OSV_VULN_URL_TEMPLATE.format(vuln_id=vuln_id))
    with urllib.request.urlopen(request, timeout=timeout) as response:
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd prototype && pytest tests/test_vulnerabilities.py -v
```
Expected: PASS (4 tests). No real network calls happen — every test patches `urlopen`.

- [ ] **Step 5: Commit**

```bash
git add prototype/veridion/vulnerabilities.py prototype/tests/test_vulnerabilities.py
git commit -m "feat: add OSV.dev dependency-vulnerability checking"
```

---

## Task 3: Wire `security` into `evidence.py` and add the CLI flag

**Files:**
- Modify: `prototype/veridion/evidence.py`
- Modify: `prototype/veridion/cli.py`
- Test: `prototype/tests/test_evidence.py`
- Test: `prototype/tests/test_cli.py`

**Interfaces:**
- Consumes: `find_secrets` (Task 1), `check_vulnerabilities` (Task 2) by their exact names and
  return shapes.
- Produces: `scan_repository(repo_path: Path, check_vulnerabilities: bool = True) -> dict` —
  same function, new optional parameter, adds an `evidence["security"]` key shaped
  `{"secrets": {...}, "dependency_vulnerabilities": {...}}`. Existing callers that don't pass
  the new parameter are unaffected (default is `True`, matching "on by default").

- [ ] **Step 1: Write the failing evidence test**

Append to `prototype/tests/test_evidence.py`:
```python
from unittest.mock import patch


def test_scan_repository_includes_security_block(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("x = 1\n")

    with patch("veridion.evidence.check_dependency_vulnerabilities") as mock_check:
        mock_check.return_value = {"checked": True, "reason": None, "findings": []}
        evidence = scan_repository(repo)

    assert "security" in evidence
    assert "secrets" in evidence["security"]
    assert evidence["security"]["secrets"]["scanned_files"] >= 1
    assert evidence["security"]["dependency_vulnerabilities"]["checked"] is True
    mock_check.assert_called_once()


def test_scan_repository_skips_vulnerability_check_when_disabled(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("x = 1\n")

    with patch("veridion.evidence.check_dependency_vulnerabilities") as mock_check:
        evidence = scan_repository(repo, check_vulnerabilities=False)

    mock_check.assert_not_called()
    assert evidence["security"]["dependency_vulnerabilities"] == {
        "checked": False,
        "reason": "skipped (--no-check-vulnerabilities)",
        "findings": [],
    }
```

Check the top of `prototype/tests/test_evidence.py` for its existing import of `scan_repository`
— reuse it, don't re-import.

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd prototype && pytest tests/test_evidence.py -v
```
Expected: FAIL (`evidence["security"]` doesn't exist yet).

- [ ] **Step 3: Modify `evidence.py`**

In `prototype/veridion/evidence.py`, update the imports and `scan_repository`:
```python
import json
from datetime import datetime, timezone
from pathlib import Path

from veridion.git_intel.analyzer import analyze_git
from veridion.scanner.detect import (
    detect_build_tools,
    detect_frameworks,
    detect_languages,
    detect_monorepo,
)
from veridion.scanner.graph import build_module_graph
from veridion.secrets import find_secrets
from veridion.vulnerabilities import check_vulnerabilities as check_dependency_vulnerabilities

EVIDENCE_VERSION = "0.1.0"


def scan_repository(repo_path: Path, check_vulnerabilities: bool = True) -> dict:
    repo_path = repo_path.resolve()

    languages = detect_languages(repo_path)
    frameworks = detect_frameworks(repo_path)
    build_tools = detect_build_tools(repo_path)
    monorepo = detect_monorepo(repo_path)
    modules, dependency_graph, unparseable_files = build_module_graph(repo_path)
    git_data = analyze_git(repo_path)
    secrets_data = find_secrets(repo_path)

    if check_vulnerabilities:
        vulnerabilities_data = check_dependency_vulnerabilities(repo_path)
    else:
        vulnerabilities_data = {
            "checked": False,
            "reason": "skipped (--no-check-vulnerabilities)",
            "findings": [],
        }

    return {
        "veridion_version": EVIDENCE_VERSION,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "repo_path": str(repo_path),
        "repository": {
            "languages": languages,
            "frameworks": frameworks,
            "build_tools": build_tools,
            "monorepo": monorepo,
            "modules": modules,
            "dependency_graph": dependency_graph,
            "unparseable_files": unparseable_files,
        },
        "git": git_data,
        "security": {
            "secrets": secrets_data,
            "dependency_vulnerabilities": vulnerabilities_data,
        },
    }


def write_evidence(evidence: dict, repo_path: Path) -> Path:
    veridion_dir = repo_path / ".veridion"
    veridion_dir.mkdir(parents=True, exist_ok=True)
    output_path = veridion_dir / "evidence.json"
    output_path.write_text(json.dumps(evidence, indent=2))
    return output_path
```

Note: `scan_repository`'s parameter is named `check_vulnerabilities`, the same name as the
function `veridion.vulnerabilities.check_vulnerabilities` — importing it under that same name
would make the parameter shadow it inside the function body, and the tempting fix of doing a
fresh `import` *inside* the `if` block to work around the shadowing is a real bug: a local
import re-fetches the name directly from `veridion.vulnerabilities`, bypassing whatever
`unittest.mock.patch("veridion.evidence.check_vulnerabilities")` patched at the module level —
the "mocked" test would silently make a real network call instead. The fix is the module-level
import alias above (`as check_dependency_vulnerabilities`), which avoids the shadowing
entirely and keeps `veridion.evidence.check_dependency_vulnerabilities` as a stable,
patchable, module-level attribute — which is exactly what the test in Step 1 patches.

- [ ] **Step 4: Run evidence tests to verify they pass**

```bash
cd prototype && pytest tests/test_evidence.py -v
```
Expected: PASS.

- [ ] **Step 5: Write the failing CLI test**

Append to `prototype/tests/test_cli.py`:
```python
def test_main_audit_threads_no_check_vulnerabilities_flag(tmp_path, monkeypatch):
    repo = tmp_path
    (repo / "main.py").write_text("x = 1\n")
    monkeypatch.setattr(sys, "argv", ["veridion", "audit", str(repo), "--no-check-vulnerabilities", "--agent", "nonexistent"])

    main()

    evidence = json.loads((repo / ".veridion" / "evidence.json").read_text())
    assert evidence["security"]["dependency_vulnerabilities"]["checked"] is False
    assert evidence["security"]["dependency_vulnerabilities"]["reason"] == "skipped (--no-check-vulnerabilities)"
```

Check the top of `test_cli.py` for existing `import json`/`import sys` — add whichever is
missing.

- [ ] **Step 6: Run CLI test to verify it fails**

```bash
cd prototype && pytest tests/test_cli.py -v
```
Expected: FAIL (`--no-check-vulnerabilities` is not a recognized argument).

- [ ] **Step 7: Wire the flag into `cli.py`**

In `prototype/veridion/cli.py`, update `_audit` and `main`:
```python
def _audit(repo_path: str, forced_agent: str | None, check_vulnerabilities: bool) -> int:
    repo = Path(repo_path).resolve()

    print(f"Scanning {repo}...")
    evidence = scan_repository(repo, check_vulnerabilities=check_vulnerabilities)
    evidence_path = write_evidence(evidence, repo)
    print(f"Evidence written to {evidence_path}")

    try:
        adapter = select_adapter(
            KNOWN_ADAPTERS, forced_name=forced_agent, interactive=sys.stdin.isatty()
        )
    except (NoAdapterAvailableError, AmbiguousAdapterError) as exc:
        print(f"error: {exc}")
        print(f"Evidence is still available at {evidence_path} for manual use.")
        return 1

    print(f"Running audit with {adapter.name}...")
    try:
        report_path = run_reasoning_phase(adapter, repo_path=str(repo), manual_dir=MANUAL_DIR)
    except AdapterInvocationError as exc:
        print(f"error: {exc}")
        print(f"Evidence is still available at {evidence_path} for manual use.")
        return 1

    print(f"Audit report written to {report_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="veridion")
    subparsers = parser.add_subparsers(dest="command", required=True)

    audit_parser = subparsers.add_parser("audit", help="audit a repository")
    audit_parser.add_argument("path", nargs="?", default=".")
    audit_parser.add_argument("--agent", default=None, help="force a specific agent adapter by name")
    audit_parser.add_argument(
        "--no-check-vulnerabilities",
        dest="check_vulnerabilities",
        action="store_false",
        default=True,
        help="skip the OSV.dev dependency-vulnerability check (on by default)",
    )

    args = parser.parse_args()

    if args.command == "audit":
        return _audit(args.path, args.agent, args.check_vulnerabilities)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
```

If Task 13 from `docs/superpowers/plans/2026-07-14-veridion-v1-scanner.md` (the `veridion scan`
/ `veridion query` subcommands) has already been implemented by the time this task runs, add
the same `--no-check-vulnerabilities` argument to the `scan` subparser too, and thread it into
`_scan`'s call to `scan_repository` the same way. If it hasn't landed yet, `audit` is the only
subcommand that exists, and the above is sufficient.

- [ ] **Step 8: Run tests to verify they pass**

```bash
cd prototype && pytest -v
```
Expected: PASS — full suite, no regressions in any earlier task's tests.

- [ ] **Step 9: Commit**

```bash
git add prototype/veridion/evidence.py prototype/veridion/cli.py
git add prototype/tests/test_evidence.py prototype/tests/test_cli.py
git commit -m "feat: wire security evidence into scan_repository and CLI"
```

---

## Task 4: Part V manual content

**Files:**
- Create: `prototype/manual/part-5-security.md`

**Interfaces:**
- Consumes: the exact `evidence.security` schema produced by Task 3.
- Produces: nothing consumed by other tasks — this is read by the reasoning-phase agent, not
  by any Python code.

- [ ] **Step 1: Write the manual file**

Create `prototype/manual/part-5-security.md`:
```markdown
# Part V — Security

This section governs how to read `evidence.security`. Follow the mandatory verification
rules in Part I for everything below.

## What's in `evidence.security`

- `secrets.scanned_files`: how many files were scanned for secret patterns.
- `secrets.findings`: each with `path`, `line`, `pattern` (which rule matched),
  `match_preview` (redacted — never the real value), and `likely_placeholder` (a heuristic
  based on the file path containing "test"/"example"/"fixture"/"mock").
- `dependency_vulnerabilities.checked`: whether the OSV.dev check actually ran.
- `dependency_vulnerabilities.reason`: why it didn't run or failed, when `checked` is `false`.
- `dependency_vulnerabilities.findings`: each with `ecosystem`, `package`,
  `installed_version`, `advisory_id`, `summary`, and `severity` (a list of CVSS entries —
  may be empty even for a real finding, since not every advisory has a computed CVSS score).

## Mandatory rules

- **Never state a secret's real value.** Cite only `match_preview`. If asked to reveal more,
  refuse — `evidence.json` itself never contains the real value, so there is nothing more to
  reveal.
- **Never claim "no vulnerabilities" when `dependency_vulnerabilities.checked` is `false`.**
  State plainly that the check did not run, and cite `reason`.
- **Treat `likely_placeholder: true` as a hint to weigh, not an automatic dismissal.** A real
  secret could coincidentally live at a path that matches a test-naming convention. Note the
  flag, don't silently drop the finding.

## What counts as noteworthy

- **A secret finding with `likely_placeholder: false`** is high severity by default — name
  the exact `path` and `line`, and state the `pattern` matched.
- **A secret finding with `likely_placeholder: true`** is still worth reporting, at lower
  confidence — say plainly that the path suggests (but does not guarantee) a placeholder
  value.
- **A dependency-vulnerability finding on a package that is actually imported somewhere**,
  per `repository.dependency_graph` or any module's `imports` list, outranks a finding on a
  package that only appears in the manifest with no confirmed import in the scanned modules —
  this cross-reference is now possible because the module graph already exists. State
  explicitly which case applies; if you can't confirm either way from evidence, say so.
- **Severity**: `severity` entries carry a raw CVSS vector string (e.g.
  `CVSS:3.1/AV:N/AC:L/.../A:H`), not a plain label. Do not translate this into "HIGH"/"LOW"
  yourself unless you can point to the specific vector components driving that judgment —
  otherwise, quote the vector and the advisory `summary` as the evidence, and let the human
  reader assess severity.

## What this section does not produce

Do not attempt OWASP/CWE/MITRE ATT&CK framework mapping, container/Kubernetes/cloud/IaC
findings, or authentication/authorization review (RBAC, JWT, OAuth, OIDC, SAML). Those are
not covered by any evidence this scanner produces. Do not scan or claim anything about git
history for secrets — only the current working tree was checked.
```

- [ ] **Step 2: Commit**

```bash
git add prototype/manual/part-5-security.md
git commit -m "docs: add Part V security manual"
```

---

## Task 5: Live dogfood acceptance gate (not automated)

This task has no code changes — it's the go/no-go check from the design spec's Success
Criteria section, run manually against Procta.

- [ ] **Step 1: Reinstall the prototype (new files need to be picked up)**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion/prototype
pip install -e ".[dev]"
```

- [ ] **Step 2: Run with vulnerability checking on (the default)**

```bash
veridion audit /Users/arihantkaul/proctored-browser --agent nonexistent-placeholder
```
(Forcing a nonexistent adapter makes the command stop after the scan phase, so this step
never triggers a live agent call — matches the same safe-verification pattern used for v1's
Task 9.)

Confirm in `.veridion/evidence.json`:
- `security.dependency_vulnerabilities.checked` is `true` (the live OSV.dev call succeeded),
  with either real findings or a clean empty list.
- `security.secrets.findings` — spot-check a few against the actual file/line referenced, and
  confirm no full secret value appears anywhere in `evidence.json` (grep the file for a couple
  of known real-looking strings from the flagged files' surrounding context and confirm only
  the redacted preview appears, not the full value).
- At least one `tests/`-path finding (if any exist) shows `likely_placeholder: true`.

- [ ] **Step 3: Run with vulnerability checking off**

```bash
veridion audit /Users/arihantkaul/proctored-browser --agent nonexistent-placeholder --no-check-vulnerabilities
```
Confirm `security.dependency_vulnerabilities.checked` is `false` with
`reason: "skipped (--no-check-vulnerabilities)"`, and that the command completed quickly
(no network call attempted).

- [ ] **Step 4: Confirm network-failure handling**

Temporarily point at an unreachable host to confirm graceful degradation (rather than actually
disconnecting the network, which would also break other tools):
```bash
python3 -c "
from pathlib import Path
from veridion.vulnerabilities import check_vulnerabilities
result = check_vulnerabilities(Path('/Users/arihantkaul/proctored-browser'), timeout=1)
print(result['checked'], result.get('reason'))
" 
```
This uses a 1-second timeout against the real API, which may legitimately succeed if OSV.dev
responds fast enough — if so, rerun with an obviously invalid host to force the failure path
and confirm `checked: False` with a real reason is returned rather than a raised exception.

- [ ] **Step 5: Full reasoning-phase run (requires explicit go-ahead, same as v1's Task 9)**

Only after checking with the user first — this is a live `claude` call against Procta's
private source, same authorization boundary as v1:
```bash
veridion audit /Users/arihantkaul/proctored-browser
```
Confirm the resulting `audit-report.md` has a Security section, cites `evidence.security`
fields by exact path (same citation discipline as every other section), and does not state
any secret's real value or claim vulnerabilities were checked when they weren't.

- [ ] **Step 6: Record the outcome**

If all criteria pass, Part V is done — report back with any surprises (real findings on
Procta, false positives that slipped past the placeholder heuristic, OSV.dev behavior that
didn't match what Task 2 verified). If any criterion fails, that's the next debugging task,
not a new plan.

---

## Self-Review Notes

**Spec coverage:** every section of the Part V design spec maps to a task — evidence schema
(Task 3), secrets detection with redaction/placeholder heuristic (Task 1), dependency
vulnerabilities with on-by-default/opt-out/timeout (Task 2 + Task 3's CLI wiring), manual
content (Task 4), testing strategy (unit tests throughout, live gate in Task 5), success
criteria (Task 5 steps map 1:1 to the spec's numbered criteria).

**Placeholder scan:** no TBD/TODO in any step; every code block is complete, runnable code,
not a description of what to write.

**Type consistency:** `find_secrets` (Task 1) → consumed by `evidence.py` (Task 3) with the
same `{"scanned_files", "findings"}` shape throughout. `check_vulnerabilities` (Task 2) →
same `{"checked", "reason", "findings"}` shape in both its own tests and `evidence.py`'s usage.
The `check_vulnerabilities` naming collision between the module-level function and
`scan_repository`'s parameter — caught during self-review, since the naive fix (a local
re-import inside the `if` block) silently breaks the mock-patch target in Task 3's own test —
is resolved via a module-level import alias (`check_dependency_vulnerabilities`) instead, and
called out explicitly rather than left as a subtle
bug for the implementer to trip over.
