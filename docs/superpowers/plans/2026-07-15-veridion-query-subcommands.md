# Veridion `scan`/`query` Subcommands Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `veridion scan` subcommand (scan-phase only, no agent call) and a `veridion
query` subcommand (answers one targeted question from an existing `evidence.json`, no
re-scanning) covering all four evidence blocks: `repository`, `git`, `security`,
`architecture`.

**Architecture:** One new module (`veridion/query.py`) with nine pure lookup functions and a
registry mapping query-kind names to `(function, requires_target)`. `cli.py` gets the scan
phase factored out of `_audit` into a shared `_scan` helper, plus two new subparsers.

**Tech Stack:** Python stdlib only — no new dependencies, no network calls, no LLM calls
anywhere in this plan.

## Global Constraints

- `query` never re-scans and never touches the network or an agent — it only reads an
  existing `.veridion/evidence.json` from disk.
- Every lookup function is shaped `(evidence: dict, target: str | None) -> Any` for
  uniformity, even the three kinds that ignore `target` (`ownership`, `vulnerabilities`,
  `layer-violations`).
- The CLI validates `requires_target` **before** calling the lookup function — a missing
  target must produce a clear, specific error, never a downstream `TypeError` or `KeyError`.
- If `.veridion/evidence.json` doesn't exist, the error must name the expected path and
  suggest `veridion scan <path>` — never a raw traceback.
- Output is always `json.dumps(result, indent=2)` to stdout — no per-kind formatting.

---

## Task 1: Query lookup functions (`query.py`)

**Files:**
- Create: `prototype/veridion/query.py`
- Test: `prototype/tests/test_query.py`

**Interfaces:**
- Consumes: nothing from other tasks — operates on a plain `evidence` dict shaped exactly
  like what `scan_repository` (in `prototype/veridion/evidence.py`) already returns:
  `{"repository": {...}, "git": {...}, "security": {...}, "architecture": {...}, ...}`.
- Produces: nine functions, each `(evidence: dict, target: str | None) -> Any`, plus
  `QUERY_FUNCTIONS: dict[str, tuple[Callable, bool]]` mapping kind name to
  `(function, requires_target)`, plus two exceptions:
  `ModuleNotFoundInEvidenceError(file_path: str)` and
  `BranchNotFoundInEvidenceError(branch_name: str)`. Task 3 imports `QUERY_FUNCTIONS` and both
  exception classes by these exact names.

- [ ] **Step 1: Write the failing tests**

Create `prototype/tests/test_query.py`:
```python
import pytest

from veridion.query import (
    QUERY_FUNCTIONS,
    BranchNotFoundInEvidenceError,
    ModuleNotFoundInEvidenceError,
    find_branch,
    find_cluster,
    find_imported_by,
    find_imports,
    find_layer_violations,
    find_ownership,
    find_secrets_for_file,
    find_symbols,
    find_vulnerabilities,
)


def make_evidence():
    return {
        "repository": {
            "modules": [
                {
                    "path": "app/auth.py",
                    "imports": ["app/config.py"],
                    "imported_by": ["app/routes.py"],
                    "symbols": {"functions": ["login"], "classes": ["AuthError"]},
                },
                {
                    "path": "app/config.py",
                    "imports": [],
                    "imported_by": ["app/auth.py"],
                    "symbols": {"functions": ["load"], "classes": []},
                },
            ]
        },
        "git": {
            "branches": [
                {"name": "main", "type": "local", "stale_days": 0, "ahead_of_main": 0, "behind_main": 0}
            ],
            "ownership": [
                {"email": "a@example.com", "names": ["Alice"], "commit_count": 5, "percent": 1.0}
            ],
        },
        "security": {
            "secrets": {
                "scanned_files": 2,
                "findings": [
                    {"path": "app/auth.py", "line": 3, "pattern": "aws_access_key_id", "match_preview": "AKIA****...WXYZ", "likely_placeholder": False}
                ],
            },
            "dependency_vulnerabilities": {"checked": True, "reason": None, "findings": []},
        },
        "architecture": {
            "clusters": [
                {"id": 0, "modules": ["app/auth.py", "app/config.py"], "internal_edges": 1}
            ],
            "layer_violations": {"convention_detected": False, "layers": [], "violations": []},
        },
    }


def test_find_imports_returns_the_module_imports_list():
    assert find_imports(make_evidence(), "app/auth.py") == ["app/config.py"]


def test_find_imported_by_returns_the_module_imported_by_list():
    assert find_imported_by(make_evidence(), "app/config.py") == ["app/auth.py"]


def test_find_symbols_returns_the_module_symbols_dict():
    assert find_symbols(make_evidence(), "app/auth.py") == {
        "functions": ["login"],
        "classes": ["AuthError"],
    }


def test_find_imports_raises_for_unknown_path():
    with pytest.raises(ModuleNotFoundInEvidenceError):
        find_imports(make_evidence(), "app/does_not_exist.py")


def test_find_branch_returns_the_branch_entry():
    result = find_branch(make_evidence(), "main")
    assert result["stale_days"] == 0


def test_find_branch_raises_for_unknown_branch():
    with pytest.raises(BranchNotFoundInEvidenceError):
        find_branch(make_evidence(), "does-not-exist")


def test_find_ownership_returns_the_whole_list_ignoring_target():
    result = find_ownership(make_evidence(), None)
    assert result == make_evidence()["git"]["ownership"]


def test_find_secrets_for_file_filters_by_path():
    result = find_secrets_for_file(make_evidence(), "app/auth.py")
    assert len(result) == 1
    assert result[0]["pattern"] == "aws_access_key_id"

    assert find_secrets_for_file(make_evidence(), "app/config.py") == []


def test_find_vulnerabilities_returns_the_whole_block_ignoring_target():
    result = find_vulnerabilities(make_evidence(), None)
    assert result == make_evidence()["security"]["dependency_vulnerabilities"]


def test_find_cluster_returns_the_cluster_containing_the_file():
    result = find_cluster(make_evidence(), "app/config.py")
    assert result["id"] == 0
    assert "app/auth.py" in result["modules"]


def test_find_cluster_raises_for_unknown_path():
    with pytest.raises(ModuleNotFoundInEvidenceError):
        find_cluster(make_evidence(), "app/does_not_exist.py")


def test_find_layer_violations_returns_the_whole_block_ignoring_target():
    result = find_layer_violations(make_evidence(), None)
    assert result == make_evidence()["architecture"]["layer_violations"]


def test_query_functions_registry_has_all_nine_kinds_with_correct_requires_target():
    expected = {
        "imports": True,
        "imported-by": True,
        "symbols": True,
        "branch": True,
        "ownership": False,
        "secrets": True,
        "vulnerabilities": False,
        "cluster": True,
        "layer-violations": False,
    }
    assert set(QUERY_FUNCTIONS.keys()) == set(expected.keys())
    for kind, requires_target in expected.items():
        _func, actual_requires_target = QUERY_FUNCTIONS[kind]
        assert actual_requires_target == requires_target, kind
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd prototype && pytest tests/test_query.py -v
```
Expected: FAIL (`veridion.query` doesn't exist — `ModuleNotFoundError`).

- [ ] **Step 3: Implement `query.py`**

Create `prototype/veridion/query.py`:
```python
class ModuleNotFoundInEvidenceError(Exception):
    def __init__(self, file_path: str):
        super().__init__(f"'{file_path}' is not present in evidence.repository.modules")
        self.file_path = file_path


class BranchNotFoundInEvidenceError(Exception):
    def __init__(self, branch_name: str):
        super().__init__(f"'{branch_name}' is not present in evidence.git.branches")
        self.branch_name = branch_name


def _find_module(evidence: dict, file_path: str) -> dict:
    for module in evidence["repository"]["modules"]:
        if module["path"] == file_path:
            return module
    raise ModuleNotFoundInEvidenceError(file_path)


def find_imports(evidence: dict, target: str | None) -> list[str]:
    return _find_module(evidence, target)["imports"]


def find_imported_by(evidence: dict, target: str | None) -> list[str]:
    return _find_module(evidence, target)["imported_by"]


def find_symbols(evidence: dict, target: str | None) -> dict:
    return _find_module(evidence, target)["symbols"]


def find_branch(evidence: dict, target: str | None) -> dict:
    for branch in evidence["git"]["branches"]:
        if branch["name"] == target:
            return branch
    raise BranchNotFoundInEvidenceError(target)


def find_ownership(evidence: dict, target: str | None) -> list[dict]:
    return evidence["git"]["ownership"]


def find_secrets_for_file(evidence: dict, target: str | None) -> list[dict]:
    return [
        finding
        for finding in evidence["security"]["secrets"]["findings"]
        if finding["path"] == target
    ]


def find_vulnerabilities(evidence: dict, target: str | None) -> dict:
    return evidence["security"]["dependency_vulnerabilities"]


def find_cluster(evidence: dict, target: str | None) -> dict:
    for cluster in evidence["architecture"]["clusters"]:
        if target in cluster["modules"]:
            return cluster
    raise ModuleNotFoundInEvidenceError(target)


def find_layer_violations(evidence: dict, target: str | None) -> dict:
    return evidence["architecture"]["layer_violations"]


QUERY_FUNCTIONS = {
    "imports": (find_imports, True),
    "imported-by": (find_imported_by, True),
    "symbols": (find_symbols, True),
    "branch": (find_branch, True),
    "ownership": (find_ownership, False),
    "secrets": (find_secrets_for_file, True),
    "vulnerabilities": (find_vulnerabilities, False),
    "cluster": (find_cluster, True),
    "layer-violations": (find_layer_violations, False),
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd prototype && pytest tests/test_query.py -v
```
Expected: PASS (13 tests).

- [ ] **Step 5: Commit**

```bash
git add prototype/veridion/query.py prototype/tests/test_query.py
git commit -m "feat: add query lookup functions covering all four evidence blocks"
```

---

## Task 2: `veridion scan` subcommand

**Files:**
- Modify: `prototype/veridion/cli.py`
- Test: `prototype/tests/test_cli.py`

**Interfaces:**
- Consumes: `scan_repository`, `write_evidence` (already imported in `cli.py` from
  `veridion.evidence`) — no new imports needed for this task.
- Produces: `_scan(repo_path: str, check_vulnerabilities: bool) -> tuple[int, dict, Path]`
  returning `(exit_code, evidence, evidence_path)`. `_audit` is refactored to call this
  instead of inlining the same three lines. Task 3 does not depend on `_scan` directly (query
  reads evidence.json from disk, it doesn't call `_scan`), but this task must not change
  `_audit`'s existing behavior or its test coverage.

- [ ] **Step 1: Write the failing test**

Append to `prototype/tests/test_cli.py`:
```python
def test_main_scan_writes_evidence_without_invoking_an_agent(tmp_path, monkeypatch, capsys):
    repo = tmp_path
    (repo / "main.py").write_text("x = 1\n")
    monkeypatch.setattr(sys, "argv", ["veridion", "scan", str(repo)])

    exit_code = main()

    assert exit_code == 0
    assert (repo / ".veridion" / "evidence.json").exists()
    captured = capsys.readouterr()
    assert "audit-report.md" not in captured.out
    assert "Running audit with" not in captured.out
```

Check the top of `prototype/tests/test_cli.py` for its existing `import sys` and `from
veridion.cli import main` — reuse them, don't re-import.

- [ ] **Step 2: Run test to verify it fails**

```bash
cd prototype && pytest tests/test_cli.py -v
```
Expected: FAIL (`scan` is not a recognized subcommand yet).

- [ ] **Step 3: Refactor `_audit` and add `_scan` in `cli.py`**

In `prototype/veridion/cli.py`, replace the existing `_audit` function and add `_scan` above
it:
```python
def _scan(repo_path: str, check_vulnerabilities: bool) -> tuple[int, dict, Path]:
    repo = Path(repo_path).resolve()
    print(f"Scanning {repo}...")
    evidence = scan_repository(repo, check_vulnerabilities=check_vulnerabilities)
    evidence_path = write_evidence(evidence, repo)
    print(f"Evidence written to {evidence_path}")
    return 0, evidence, evidence_path


def _audit(repo_path: str, forced_agent: str | None, check_vulnerabilities: bool) -> int:
    exit_code, evidence, evidence_path = _scan(repo_path, check_vulnerabilities)
    repo = Path(repo_path).resolve()

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
```

Update `main()` to register the `scan` subparser and dispatch to it:
```python
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

    scan_parser = subparsers.add_parser("scan", help="run only the deterministic scan phase")
    scan_parser.add_argument("path", nargs="?", default=".")
    scan_parser.add_argument(
        "--no-check-vulnerabilities",
        dest="check_vulnerabilities",
        action="store_false",
        default=True,
        help="skip the OSV.dev dependency-vulnerability check (on by default)",
    )

    args = parser.parse_args()

    if args.command == "audit":
        return _audit(args.path, args.agent, args.check_vulnerabilities)
    if args.command == "scan":
        exit_code, _evidence, _evidence_path = _scan(args.path, args.check_vulnerabilities)
        return exit_code

    parser.print_help()
    return 1
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd prototype && pytest -v
```
Expected: PASS — full suite, including every existing `_audit`-related test (the refactor must
not change `_audit`'s observable behavior).

- [ ] **Step 5: Commit**

```bash
git add prototype/veridion/cli.py prototype/tests/test_cli.py
git commit -m "feat: add veridion scan subcommand, factor scan phase out of audit"
```

---

## Task 3: `veridion query` subcommand

**Files:**
- Modify: `prototype/veridion/cli.py`
- Test: `prototype/tests/test_cli.py`

**Interfaces:**
- Consumes: `QUERY_FUNCTIONS`, `ModuleNotFoundInEvidenceError`, `BranchNotFoundInEvidenceError`
  from `veridion.query` (Task 1) by these exact names.
- Produces: nothing consumed by later tasks — this is the final CLI wiring.

- [ ] **Step 1: Write the failing tests**

Append to `prototype/tests/test_cli.py`:
```python
def test_main_query_imports_prints_result(tmp_path, monkeypatch, capsys):
    repo = tmp_path
    (repo / "app").mkdir()
    (repo / "app" / "config.py").write_text("SETTING = 1\n")
    (repo / "app" / "auth.py").write_text("from app import config\n")
    monkeypatch.setattr(sys, "argv", ["veridion", "scan", str(repo)])
    main()

    monkeypatch.setattr(
        sys, "argv", ["veridion", "query", "imports", "app/auth.py", "--path", str(repo)]
    )
    exit_code = main()

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "app/config.py" in captured.out


def test_main_query_ownership_does_not_require_a_target(tmp_path, monkeypatch, capsys):
    repo = tmp_path
    (repo / "main.py").write_text("x = 1\n")
    monkeypatch.setattr(sys, "argv", ["veridion", "scan", str(repo)])
    main()

    monkeypatch.setattr(sys, "argv", ["veridion", "query", "ownership", "--path", str(repo)])
    exit_code = main()

    assert exit_code == 0


def test_main_query_missing_target_errors_clearly(tmp_path, monkeypatch, capsys):
    repo = tmp_path
    (repo / "main.py").write_text("x = 1\n")
    monkeypatch.setattr(sys, "argv", ["veridion", "scan", str(repo)])
    main()

    monkeypatch.setattr(sys, "argv", ["veridion", "query", "imports", "--path", str(repo)])
    exit_code = main()

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "requires a target" in captured.out


def test_main_query_without_evidence_errors_clearly(tmp_path, monkeypatch, capsys):
    repo = tmp_path
    monkeypatch.setattr(
        sys, "argv", ["veridion", "query", "imports", "app/auth.py", "--path", str(repo)]
    )

    exit_code = main()

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "veridion scan" in captured.out


def test_main_query_unknown_module_errors_clearly(tmp_path, monkeypatch, capsys):
    repo = tmp_path
    (repo / "main.py").write_text("x = 1\n")
    monkeypatch.setattr(sys, "argv", ["veridion", "scan", str(repo)])
    main()

    monkeypatch.setattr(
        sys, "argv", ["veridion", "query", "imports", "does/not/exist.py", "--path", str(repo)]
    )
    exit_code = main()

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "not present in evidence" in captured.out
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd prototype && pytest tests/test_cli.py -v
```
Expected: the 5 new tests FAIL (`query` is not a recognized subcommand yet); all earlier tests
still PASS.

- [ ] **Step 3: Wire the `query` subparser into `cli.py`**

Add the import at the top of `prototype/veridion/cli.py`:
```python
import json
```
and:
```python
from veridion.query import (
    BranchNotFoundInEvidenceError,
    ModuleNotFoundInEvidenceError,
    QUERY_FUNCTIONS,
)
```

Add the `_query` function (place it after `_audit`):
```python
def _query(kind: str, target: str | None, repo_path: str) -> int:
    repo = Path(repo_path).resolve()
    evidence_path = repo / ".veridion" / "evidence.json"
    if not evidence_path.exists():
        print(f"error: no evidence found at {evidence_path}")
        print(f"Run 'veridion scan {repo}' first.")
        return 1

    func, requires_target = QUERY_FUNCTIONS[kind]
    if requires_target and target is None:
        print(f"error: query type '{kind}' requires a target argument")
        return 1

    evidence = json.loads(evidence_path.read_text())
    try:
        result = func(evidence, target)
    except (ModuleNotFoundInEvidenceError, BranchNotFoundInEvidenceError) as exc:
        print(f"error: {exc}")
        return 1

    print(json.dumps(result, indent=2))
    return 0
```

In `main()`, register the subparser (add after the `scan_parser` block):
```python
    query_parser = subparsers.add_parser("query", help="query an existing evidence.json")
    query_parser.add_argument("kind", choices=list(QUERY_FUNCTIONS.keys()))
    query_parser.add_argument("target", nargs="?", default=None)
    query_parser.add_argument("--path", dest="repo_path", default=".")
```

And in the dispatch block, add:
```python
    if args.command == "query":
        return _query(args.kind, args.target, args.repo_path)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd prototype && pytest -v
```
Expected: PASS — full suite, no regressions in any earlier task.

- [ ] **Step 5: Commit**

```bash
git add prototype/veridion/cli.py prototype/tests/test_cli.py
git commit -m "feat: add veridion query subcommand covering all evidence blocks"
```

---

## Task 4: Live verification (not automated)

No code changes — confirms the wired-up CLI behaves correctly against a real repo. No
network calls, no agent calls anywhere in this task.

- [ ] **Step 1: Reinstall the prototype**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion/prototype
pip install -e ".[dev]"
```

- [ ] **Step 2: Scan Veridion's own repo and confirm no agent is invoked**

```bash
veridion scan /Users/arihantkaul/Documents/GitHub/Veridion
```
Confirm the output stops after "Evidence written to ..." — no "Running audit with" line, no
`audit-report.md` written or touched.

- [ ] **Step 3: Run at least one target-requiring and one non-target-requiring query, confirm against real evidence.json**

```bash
veridion query imports prototype/veridion/cli.py --path /Users/arihantkaul/Documents/GitHub/Veridion
veridion query ownership --path /Users/arihantkaul/Documents/GitHub/Veridion
```
Cross-check both outputs directly against
`/Users/arihantkaul/Documents/GitHub/Veridion/.veridion/evidence.json`'s
`repository.modules` and `git.ownership` fields respectively — they must match exactly, not
just look plausible.

- [ ] **Step 4: Confirm the pre-scan error path in a fresh directory**

```bash
mkdir -p /tmp/veridion-query-check && veridion query imports somefile.py --path /tmp/veridion-query-check
```
Expected: exit 1, an error naming the missing evidence path and suggesting `veridion scan`
— not a traceback.

- [ ] **Step 5: Record the outcome**

If all four success criteria from the design spec pass, this feature is done. If anything
fails, that's the next debugging task, not a new plan.

---

## Self-Review Notes

**Spec coverage:** all four numbered success criteria map to Task 4's steps 2-4. The 9-kind
query table maps directly to Task 1's registry and its 13 tests (9 lookup-correctness tests +
2 not-found-error tests + 1 registry-shape test + 1 ownership/vulnerabilities-ignore-target
test... actually 9 kinds tested individually across the listed tests, plus the registry-shape
test covering all 9 at once). Error handling (missing evidence, missing target, unknown
lookup key) is covered by Task 3's tests directly, matching the spec's stated error-handling
section exactly.

**Placeholder scan:** no TBD/TODO; every code block is complete, runnable code.

**Type consistency:** `find_imports`/`find_imported_by`/`find_symbols`/`find_branch`/
`find_ownership`/`find_secrets_for_file`/`find_vulnerabilities`/`find_cluster`/
`find_layer_violations` (Task 1) are consumed by `cli.py`'s `_query` (Task 3) purely through
the `QUERY_FUNCTIONS` registry, not by importing each function by name individually — this
means Task 3 never needs to match nine separate function names exactly, only the registry's
shape, which Task 1's own test (`test_query_functions_registry_has_all_nine_kinds_with_correct_requires_target`)
already locks down.
