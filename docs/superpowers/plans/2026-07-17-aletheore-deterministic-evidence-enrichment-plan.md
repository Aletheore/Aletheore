# Aletheore Deterministic Evidence Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add exact symbol line bounds, dead-code detection, and git churn/co-change hotspots to
Aletheore's evidence — three deterministic extensions of infrastructure Aletheore already has
(tree-sitter ASTs, the import graph, git log parsing), with zero new dependencies.

**Architecture:** Symbol line bounds change `repository.modules[].symbols.functions`/`classes`
from `list[str]` to `list[dict]` across all 9 language extractors in
`aletheore/scanner/graph.py` (mechanical, identical transformation at every append call site).
Dead code and hotspots are new evidence blocks (`repository.dead_code`, `git.hotspots`)
following the exact same wiring every existing block already uses: a phase in
`scan_repository()`, a `QUERY_FUNCTIONS` entry (or hand-registration for multi-argument
queries), a CLI subcommand, an MCP tool.

**Tech Stack:** Python 3.11+, existing `tree-sitter` bindings (already a dependency), the
existing `_run_git` subprocess pattern in `aletheore/git_intel/analyzer.py`.

## Global Constraints

- Zero new dependencies. Every feature in this plan is achievable with what's already imported.
- `symbols.functions`/`classes`'s shape change (`list[str]` → `list[dict]`) is a deliberate
  breaking change to `evidence.json`, justified because Aletheore has no external users yet -
  every consumer of this shape inside the codebase (tests, `EVIDENCE_SCHEMA_MAP`) is updated in
  this same plan, not left inconsistent.
- Line numbers are 1-indexed and inclusive everywhere (`start_line`/`end_line`), matching how
  every editor and `git` itself display line numbers - never 0-indexed.
- No composite/blended scores anywhere in this plan. `git.hotspots` reports raw churn counts and
  raw co-occurrence counts, sorted - never a single opaque "risk score" number.
- Every new query kind is reachable identically to every existing one: `aletheore query <kind>`
  CLI subcommand and a matching MCP tool, TOON-encoded.

---

### Task 1: Symbol line bounds across all 9 language extractors

**Files:**
- Modify: `aletheore/scanner/graph.py`
- Create: `tests/conftest.py`
- Modify: `tests/test_graph.py`, `tests/test_graph_cpp.py`, `tests/test_graph_csharp.py`,
  `tests/test_graph_go.py`, `tests/test_graph_java.py`, `tests/test_graph_php.py`,
  `tests/test_graph_ruby.py`, `tests/test_graph_rust.py`

**Interfaces:**
- Produces: `repository.modules[].symbols.functions`/`.classes` become
  `list[{"name": str, "start_line": int, "end_line": int}]` (was `list[str]`).
- Produces: `tests/conftest.py`'s `symbol_names(entries: list[dict]) -> list[str]` - shared test
  helper, `return [e["name"] for e in entries]`, used everywhere a test needs to check "is this
  name present" without caring about line numbers.

The exact transformation is identical everywhere: every one of the 9 extractor functions
(`_extract_python`, `_extract_javascript`, `_extract_go`, `_extract_rust`, `_extract_java`,
`_extract_ruby`, `_extract_php`, `_extract_c_family`, `_extract_csharp`) has this shape at every
function/class append call site:

```python
functions.append(source[name_node.start_byte:name_node.end_byte].decode())
```

This becomes, using `n` (the enclosing node already being matched in the `if n.type == ...`
branch, not `name_node` which is only the identifier) for the line span:

```python
functions.append({
    "name": source[name_node.start_byte:name_node.end_byte].decode(),
    "start_line": n.start_point[0] + 1,
    "end_line": n.end_point[0] + 1,
})
```

Same transformation for every `classes.append(...)` call site. `n.start_point`/`n.end_point`
are `(row, column)` tuples tree-sitter already computes for every node - `+ 1` converts the
0-indexed row to a 1-indexed line number.

- [ ] **Step 1: Update the failing tests first - add the shared helper and migrate assertions**

```python
# tests/conftest.py (new file)
def symbol_names(entries: list[dict]) -> list[str]:
    return [entry["name"] for entry in entries]
```

Then, in every one of the 8 affected test files, change every assertion of the shape
`assert "Name" in module["symbols"]["functions"]` (or `"classes"`) to
`assert "Name" in symbol_names(module["symbols"]["functions"])`. Concretely, in
`tests/test_graph.py`:

```python
# before
assert "login" in auth["symbols"]["functions"]
assert "AuthError" in auth["symbols"]["classes"]
# after
assert "login" in symbol_names(auth["symbols"]["functions"])
assert "AuthError" in symbol_names(auth["symbols"]["classes"])
```

Apply the identical before/after transformation at all 26 existing assertion sites (confirmed
count: `grep -rn '"functions"\]\|"classes"\]' tests/*.py` from `tests/test_graph.py`,
`tests/test_graph_cpp.py`, `tests/test_graph_csharp.py`, `tests/test_graph_go.py`,
`tests/test_graph_java.py`, `tests/test_graph_php.py`, `tests/test_graph_ruby.py`,
`tests/test_graph_rust.py`). `conftest.py` fixtures/helpers are auto-discovered by pytest, so no
import statement is needed in the test files themselves.

Also add new assertions confirming the new fields exist, appended right after the existing ones
in `tests/test_graph.py`:

```python
def test_build_module_graph_records_symbol_line_bounds(tmp_path):
    repo = make_python_repo(tmp_path)
    modules, _, _ = build_module_graph(repo)
    by_path = {m["path"]: m for m in modules}
    auth = by_path["app/auth.py"]

    login_fn = next(f for f in auth["symbols"]["functions"] if f["name"] == "login")
    assert login_fn["start_line"] == 4
    assert login_fn["end_line"] == 5

    auth_error_cls = next(c for c in auth["symbols"]["classes"] if c["name"] == "AuthError")
    assert auth_error_cls["start_line"] == 7
    assert auth_error_cls["end_line"] == 8
```

(Line numbers above are computed from `make_python_repo`'s exact fixture content already in
`tests/test_graph.py`: `"from app import config\n\n\ndef login():\n    return config.load()\n\n\nclass AuthError(Exception):\n    pass\n"`
- line 1 is the import, lines 2-3 blank, line 4 `def login():`, line 5 the return, lines 6-7
  blank... recount against the real file during implementation rather than trust this
  arithmetic blindly, since off-by-one here is exactly the kind of bug this feature exists to
  prevent.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd prototype && python -m pytest tests/test_graph.py -v`
Expected: FAIL - `TypeError` or assertion failures, since `functions`/`classes` still contain
bare strings, not dicts with `.name`, or the new line-bounds test fails outright (no
`start_line` key yet).

- [ ] **Step 3: Implement the Python extractor first (establishes the pattern)**

```python
# aletheore/scanner/graph.py - _extract_python, replace both append call sites
        elif n.type == "function_definition":
            name_node = n.child_by_field_name("name")
            if name_node is not None:
                functions.append({
                    "name": source[name_node.start_byte:name_node.end_byte].decode(),
                    "start_line": n.start_point[0] + 1,
                    "end_line": n.end_point[0] + 1,
                })
        elif n.type == "class_definition":
            name_node = n.child_by_field_name("name")
            if name_node is not None:
                classes.append({
                    "name": source[name_node.start_byte:name_node.end_byte].decode(),
                    "start_line": n.start_point[0] + 1,
                    "end_line": n.end_point[0] + 1,
                })
```

- [ ] **Step 4: Run the Python-specific tests to verify they pass**

Run: `cd prototype && python -m pytest tests/test_graph.py -v`
Expected: PASS

- [ ] **Step 5: Apply the identical transformation to the remaining 8 extractors**

For each of `_extract_javascript`, `_extract_go`, `_extract_rust`, `_extract_java`,
`_extract_ruby`, `_extract_php`, `_extract_c_family`, `_extract_csharp` in
`aletheore/scanner/graph.py`: at every `functions.append(source[...].decode())` and
`classes.append(source[...].decode())` (or the equivalent `types.append(...)` in `_extract_go`,
which evidence.py maps into the `classes` slot at the call site in `build_module_graph` - check
that mapping and preserve it, only changing what gets appended, not which accumulator it goes
into), replace the bare string append with the same three-key dict shown in Step 3, using the
enclosing matched node (`n`, or whatever the surrounding `if n.type in (...)` branch's own
matched variable is named in that function - it is not always literally named `n`, check each
function's own `walk`/`visit` closure signature) for `start_point`/`end_point`, and `name_node`
only for the byte-slice that produces `"name"`.

- [ ] **Step 6: Run each language's test file to verify it passes**

Run: `cd prototype && python -m pytest tests/test_graph_cpp.py tests/test_graph_csharp.py tests/test_graph_go.py tests/test_graph_java.py tests/test_graph_php.py tests/test_graph_ruby.py tests/test_graph_rust.py -v`
Expected: PASS for all 8 files

- [ ] **Step 7: Run the full suite to confirm no other regression**

Run: `cd prototype && python -m pytest -q`
Expected: all pass (this will also surface any other place in the codebase that assumed the old
bare-string shape that wasn't caught by the explicit file list above - fix any such place found)

- [ ] **Step 8: Commit**

```bash
git add aletheore/scanner/graph.py tests/conftest.py tests/test_graph*.py
git commit -m "feat: capture exact line bounds for every extracted function/class symbol"
```

---

### Task 2: `find_symbol_source` query - CLI, MCP, and live file read

**Files:**
- Modify: `aletheore/query.py`
- Modify: `aletheore/mcp_server.py`
- Modify: `aletheore/cli.py`
- Test: `tests/test_query.py`, `tests/test_mcp_server.py` (confirm this file's existing name via
  `ls tests/` before creating - if it doesn't exist, check how other hand-registered MCP tools
  like `aletheore_neighborhood` are tested today and match that pattern exactly)

**Interfaces:**
- Consumes: Task 1's `symbols.functions`/`.classes` shape.
- Produces: `find_symbol_source(evidence: dict, repo_path: Path, module_path: str, symbol_name: str) -> dict`
  in `aletheore/query.py`, returning
  `{"module": str, "symbol": str, "start_line": int, "end_line": int, "source": str}`.
  Raises `SymbolNotFoundInEvidenceError(module_path, symbol_name)` if `symbol_name` isn't in
  either `functions` or `classes` for that module (mirrors `ModuleNotFoundInEvidenceError`'s
  existing shape in the same file).

- [ ] **Step 1: Write the failing tests**

```python
# append to prototype/tests/test_query.py
import pytest
from pathlib import Path

from aletheore.query import SymbolNotFoundInEvidenceError, find_symbol_source


def _evidence_with_module(module_path: str, functions: list[dict]) -> dict:
    return {
        "repository": {
            "modules": [
                {"path": module_path, "symbols": {"functions": functions, "classes": []}}
            ]
        }
    }


def test_find_symbol_source_returns_exact_lines(tmp_path):
    repo = tmp_path
    (repo / "app.py").write_text("x = 1\ndef greet():\n    return 'hi'\n\n\ny = 2\n")
    evidence = _evidence_with_module(
        "app.py", [{"name": "greet", "start_line": 2, "end_line": 3}]
    )

    result = find_symbol_source(evidence, repo, "app.py", "greet")

    assert result["start_line"] == 2
    assert result["end_line"] == 3
    assert result["source"] == "def greet():\n    return 'hi'"


def test_find_symbol_source_raises_when_symbol_missing(tmp_path):
    repo = tmp_path
    (repo / "app.py").write_text("x = 1\n")
    evidence = _evidence_with_module("app.py", [])

    with pytest.raises(SymbolNotFoundInEvidenceError, match="nonexistent"):
        find_symbol_source(evidence, repo, "app.py", "nonexistent")


def test_find_symbol_source_raises_when_module_missing(tmp_path):
    evidence = _evidence_with_module("app.py", [])

    with pytest.raises(ModuleNotFoundInEvidenceError):
        find_symbol_source(evidence, tmp_path, "does_not_exist.py", "greet")
```

(Add `from aletheore.query import ModuleNotFoundInEvidenceError` to the existing import block if
not already imported in `tests/test_query.py`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd prototype && python -m pytest tests/test_query.py -k symbol_source -v`
Expected: FAIL - `ImportError: cannot import name 'find_symbol_source'`

- [ ] **Step 3: Implement in `aletheore/query.py`**

```python
class SymbolNotFoundInEvidenceError(Exception):
    def __init__(self, module_path: str, symbol_name: str):
        super().__init__(f"'{symbol_name}' is not present in {module_path}'s symbols")
        self.module_path = module_path
        self.symbol_name = symbol_name


def find_symbol_source(
    evidence: dict, repo_path: Path, module_path: str, symbol_name: str
) -> dict:
    module = _find_module(evidence, module_path)
    all_symbols = module["symbols"]["functions"] + module["symbols"]["classes"]
    entry = next((s for s in all_symbols if s["name"] == symbol_name), None)
    if entry is None:
        raise SymbolNotFoundInEvidenceError(module_path, symbol_name)

    file_path = repo_path / module_path
    lines = file_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    source = "\n".join(lines[entry["start_line"] - 1 : entry["end_line"]])

    return {
        "module": module_path,
        "symbol": symbol_name,
        "start_line": entry["start_line"],
        "end_line": entry["end_line"],
        "source": source,
    }
```

Add `from pathlib import Path` to `aletheore/query.py`'s imports if not already present.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd prototype && python -m pytest tests/test_query.py -v`
Expected: all pass

- [ ] **Step 5: Wire the CLI subcommand**

```python
# aletheore/cli.py - new query kind, since this needs two positional args
# (module path + symbol name) unlike every existing single-target query kind,
# it is NOT added to QUERY_FUNCTIONS/QUERY_KIND_CHOICES - it gets its own
# typer command instead, mirroring how "changes" already sits outside the
# QUERY_FUNCTIONS auto-dispatch as a hand-written case in _query_changes.

@app.command(name="symbol-source", help="print the exact source of one named function/class")
def symbol_source(
    module: str = typer.Argument(..., help="module path as it appears in evidence"),
    symbol: str = typer.Argument(..., help="function or class name"),
    path: str = typer.Argument(".", help="repository path"),
) -> None:
    from aletheore.query import ModuleNotFoundInEvidenceError, SymbolNotFoundInEvidenceError

    evidence_path = Path(path) / ".aletheore" / "evidence.json"
    if not evidence_path.exists():
        console.print(f"[bold red]error:[/bold red] no evidence found at {evidence_path}")
        raise typer.Exit(code=1)
    evidence = json.loads(evidence_path.read_text())
    try:
        result = find_symbol_source(evidence, Path(path), module, symbol)
    except (ModuleNotFoundInEvidenceError, SymbolNotFoundInEvidenceError) as exc:
        console.print(f"[bold red]error:[/bold red] {exc}")
        raise typer.Exit(code=1)
    console.print(f"[bold]{result['module']}:{result['start_line']}-{result['end_line']}[/bold]")
    console.print(result["source"])
```

(Match this against `cli.py`'s real existing imports for `json`, `Path`, `find_symbol_source`
before pasting - add whichever of these three isn't already imported at the top of the file.)

- [ ] **Step 6: Wire the MCP tool**

```python
# aletheore/mcp_server.py - add alongside _register_neighborhood_tool/_register_search_tool,
# which are hand-registered for the same reason (more than one argument)
def _register_symbol_source_tool(mcp_instance: FastMCP, repo_path: Path) -> None:
    @mcp_instance.tool(name="aletheore_symbol_source")
    def aletheore_symbol_source(module: str, symbol: str) -> str:
        """Exact source text for one named function/class, with resolved line bounds."""
        evidence = read_evidence(repo_path)
        result = find_symbol_source(evidence, repo_path, module, symbol)
        return _toon_result(result)
```

Add `find_symbol_source` to the existing `from aletheore.query import (...)` block at the top of
`mcp_server.py`, and add `_register_symbol_source_tool(mcp_instance, repo_path)` to
`build_server()`'s existing sequence of `_register_*` calls.

- [ ] **Step 7: Run the full suite**

Run: `cd prototype && python -m pytest -q`
Expected: all pass

- [ ] **Step 8: Commit**

```bash
git add aletheore/query.py aletheore/mcp_server.py aletheore/cli.py tests/test_query.py
git commit -m "feat: add symbol-source query, CLI command, and MCP tool"
```

---

### Task 3: Dead code detection

**Files:**
- Create: `aletheore/dead_code.py`
- Test: `tests/test_dead_code.py`

**Interfaces:**
- Consumes: `repository.modules` (path, `imports`, `imported_by`), `_parse_pip_pins`/
  `_parse_npm_pins` from `aletheore/vulnerabilities.py` (real, confirmed signatures:
  `(repo_path: Path) -> list[tuple[str, str, str]]`, tuple is `(name, version, ecosystem)`,
  `ecosystem` is `"PyPI"` or `"npm"`).
- Produces: `find_dead_code(repo_path: Path, modules: list[dict], config: dict | None) -> dict`
  returning
  `{"unreachable_modules": [...], "unused_dependencies": [...], "entry_points_detected": [...]}`.

- [ ] **Step 1: Write the failing tests**

```python
# prototype/tests/test_dead_code.py
import json
from pathlib import Path

from aletheore.dead_code import find_dead_code


def _module(path, imported_by=None):
    return {"path": path, "imports": [], "imported_by": imported_by or []}


def test_module_with_no_imported_by_is_unreachable(tmp_path):
    modules = [_module("app/orphan.py"), _module("app/used.py", imported_by=["app/main.py"])]
    result = find_dead_code(tmp_path, modules, config=None)
    paths = [m["path"] for m in result["unreachable_modules"]]
    assert "app/orphan.py" in paths
    assert "app/used.py" not in paths


def test_recognized_entry_point_is_never_unreachable(tmp_path):
    modules = [_module("main.py"), _module("app/__main__.py"), _module("index.js")]
    result = find_dead_code(tmp_path, modules, config=None)
    assert result["unreachable_modules"] == []
    assert set(result["entry_points_detected"]) == {"main.py", "app/__main__.py", "index.js"}


def test_test_files_are_never_unreachable(tmp_path):
    modules = [
        _module("tests/test_thing.py"),
        _module("src/thing_test.py"),
        _module("src/__tests__/thing.test.js"),
    ]
    result = find_dead_code(tmp_path, modules, config=None)
    assert result["unreachable_modules"] == []


def test_config_can_add_custom_entry_points(tmp_path):
    modules = [_module("app/worker.py")]
    config = {"dead_code_entry_points": ["app/worker.py"]}
    result = find_dead_code(tmp_path, modules, config=config)
    assert result["unreachable_modules"] == []
    assert "app/worker.py" in result["entry_points_detected"]


def test_unused_dependency_flagged_when_never_imported(tmp_path):
    (tmp_path / "requirements.txt").write_text("requests==2.31.0\nflask==3.0.0\n")
    modules = [_module("app/main.py")]
    modules[0]["imports"] = ["flask"]
    result = find_dead_code(tmp_path, modules, config=None)
    unused = {(d["ecosystem"], d["package"]) for d in result["unused_dependencies"]}
    assert ("PyPI", "requests") in unused
    assert ("PyPI", "flask") not in unused
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd prototype && python -m pytest tests/test_dead_code.py -v`
Expected: FAIL - `ModuleNotFoundError: No module named 'aletheore.dead_code'`

- [ ] **Step 3: Implement**

```python
# prototype/aletheore/dead_code.py
import re
from pathlib import Path

from aletheore.vulnerabilities import _parse_npm_pins, _parse_pip_pins

ENTRY_POINT_FILENAMES = {
    "main.py", "__main__.py", "app.py", "manage.py", "wsgi.py", "asgi.py",
    "index.js", "index.ts", "index.jsx", "index.tsx",
}

TEST_PATH_PATTERNS = [
    re.compile(r"(^|/)test_[^/]+\.py$"),
    re.compile(r"(^|/)[^/]+_test\.py$"),
    re.compile(r"(^|/)[^/]+\.test\.[jt]sx?$"),
    re.compile(r"(^|/)[^/]+\.spec\.[jt]sx?$"),
    re.compile(r"(^|/)(tests?|__tests__)/"),
]


def _is_entry_point(path: str, custom_entry_points: set[str]) -> bool:
    if path in custom_entry_points:
        return True
    filename = path.rsplit("/", 1)[-1]
    return filename in ENTRY_POINT_FILENAMES


def _is_test_file(path: str) -> bool:
    return any(pattern.search(path) for pattern in TEST_PATH_PATTERNS)


def find_dead_code(repo_path: Path, modules: list[dict], config: dict | None) -> dict:
    custom_entry_points = set()
    if config is not None:
        raw = config.get("dead_code_entry_points", [])
        if isinstance(raw, list):
            custom_entry_points = {p for p in raw if isinstance(p, str)}

    unreachable_modules = []
    entry_points_detected = []
    for module in modules:
        path = module["path"]
        if _is_entry_point(path, custom_entry_points):
            entry_points_detected.append(path)
            continue
        if _is_test_file(path):
            continue
        if not module["imported_by"]:
            unreachable_modules.append({"path": path, "reason": "no other module imports this file"})

    imported_names = {
        imp.split("/")[0].split(".")[0].lower()
        for module in modules
        for imp in module["imports"]
    }
    pins = _parse_pip_pins(repo_path) + _parse_npm_pins(repo_path)
    unused_dependencies = [
        {"ecosystem": ecosystem, "package": name}
        for name, _version, ecosystem in pins
        if name.lower() not in imported_names
    ]

    return {
        "unreachable_modules": unreachable_modules,
        "unused_dependencies": unused_dependencies,
        "entry_points_detected": sorted(entry_points_detected),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd prototype && python -m pytest tests/test_dead_code.py -v`
Expected: all pass. If `test_unused_dependency_flagged_when_never_imported` fails because the
real import-name normalization doesn't match `requests`/`flask` package names against however
`modules[].imports` actually represents JS/Python import strings, adjust the normalization in
`find_dead_code` (not the test) to match reality - re-run a real `aletheore scan` against a
small fixture with a genuinely unused pinned dependency to confirm the matching logic actually
works end to end before considering this step done, since string-matching package names against
import paths is exactly the kind of thing that looks right in a unit test and is wrong in
practice (scoped imports like `@org/pkg`, submodule imports like `from PIL import Image` where
the pinned name is `Pillow`, etc. - note known false-negative cases like this explicitly in a
comment rather than silently getting them wrong).

- [ ] **Step 5: Commit**

```bash
git add aletheore/dead_code.py tests/test_dead_code.py
git commit -m "feat: deterministic dead-code detection (unreachable modules, unused dependencies)"
```

---

### Task 4: Hotspots (churn + co-change)

**Files:**
- Modify: `aletheore/git_intel/analyzer.py`
- Test: `tests/test_git_intel.py`

**Interfaces:**
- Produces: `compute_hotspots(repo_path: Path, modules: list[dict]) -> list[dict]`, each entry
  `{"path": str, "churn_count": int, "co_change_partners": [{"path": str, "co_occurrences": int}], "dependents_count": int}`,
  sorted by `churn_count` descending, top 30 only.

- [ ] **Step 1: Write the failing tests**

```python
# append to prototype/tests/test_git_intel.py
import subprocess

from aletheore.git_intel.analyzer import compute_hotspots


def _git(repo_path, *args):
    subprocess.run(["git", *args], cwd=repo_path, check=True, capture_output=True)


def _init_repo_with_commits(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "a@example.com")
    _git(repo, "config", "user.name", "A")

    (repo / "a.py").write_text("1")
    (repo / "b.py").write_text("1")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "initial")

    (repo / "a.py").write_text("2")
    (repo / "b.py").write_text("2")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "touch both")

    (repo / "a.py").write_text("3")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "touch a only")

    return repo


def test_compute_hotspots_ranks_by_churn(tmp_path):
    repo = _init_repo_with_commits(tmp_path)
    modules = [
        {"path": "a.py", "imported_by": []},
        {"path": "b.py", "imported_by": ["a.py"]},
    ]
    hotspots = compute_hotspots(repo, modules)
    by_path = {h["path"]: h for h in hotspots}
    assert by_path["a.py"]["churn_count"] == 3
    assert by_path["b.py"]["churn_count"] == 2
    assert hotspots[0]["path"] == "a.py"


def test_compute_hotspots_finds_co_change_partner(tmp_path):
    repo = _init_repo_with_commits(tmp_path)
    modules = [
        {"path": "a.py", "imported_by": []},
        {"path": "b.py", "imported_by": []},
    ]
    hotspots = compute_hotspots(repo, modules)
    a = next(h for h in hotspots if h["path"] == "a.py")
    partners = {p["path"]: p["co_occurrences"] for p in a["co_change_partners"]}
    assert partners.get("b.py") == 1


def test_compute_hotspots_uses_dependents_count_from_imported_by(tmp_path):
    repo = _init_repo_with_commits(tmp_path)
    modules = [
        {"path": "a.py", "imported_by": ["b.py", "c.py"]},
        {"path": "b.py", "imported_by": []},
    ]
    hotspots = compute_hotspots(repo, modules)
    a = next(h for h in hotspots if h["path"] == "a.py")
    assert a["dependents_count"] == 2


def test_compute_hotspots_excludes_mass_commits_from_co_change(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "a@example.com")
    _git(repo, "config", "user.name", "A")

    many_files = [f"f{i}.py" for i in range(60)]
    for name in many_files:
        (repo / name).write_text("1")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "mass commit touching 60 files")

    modules = [{"path": name, "imported_by": []} for name in many_files]
    hotspots = compute_hotspots(repo, modules)
    f0 = next(h for h in hotspots if h["path"] == "f0.py")
    assert f0["co_change_partners"] == []
    assert f0["churn_count"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd prototype && python -m pytest tests/test_git_intel.py -k hotspot -v`
Expected: FAIL - `ImportError: cannot import name 'compute_hotspots'`

- [ ] **Step 3: Implement in `aletheore/git_intel/analyzer.py`**

```python
MASS_COMMIT_FILE_THRESHOLD = 50
HOTSPOT_LIMIT = 30


def _commit_file_lists(repo_path: Path) -> list[list[str]]:
    result = _run_git(repo_path, "log", "--format=%x00", "--name-only", "HEAD")
    commits: list[list[str]] = []
    current: list[str] = []
    for line in result.stdout.splitlines():
        if line == "\x00":
            if current:
                commits.append(current)
            current = []
        elif line.strip():
            current.append(line.strip())
    if current:
        commits.append(current)
    return commits


def compute_hotspots(repo_path: Path, modules: list[dict]) -> list[dict]:
    commit_file_lists = _commit_file_lists(repo_path)
    dependents_by_path = {m["path"]: len(m.get("imported_by", [])) for m in modules}

    churn: dict[str, int] = {}
    co_change: dict[str, dict[str, int]] = {}

    for files in commit_file_lists:
        for f in files:
            churn[f] = churn.get(f, 0) + 1
        if len(files) > MASS_COMMIT_FILE_THRESHOLD:
            continue
        for i, f1 in enumerate(files):
            for f2 in files[i + 1:]:
                co_change.setdefault(f1, {}).setdefault(f2, 0)
                co_change[f1][f2] += 1
                co_change.setdefault(f2, {}).setdefault(f1, 0)
                co_change[f2][f1] += 1

    hotspots = []
    for path, churn_count in churn.items():
        partners = sorted(
            co_change.get(path, {}).items(), key=lambda kv: -kv[1]
        )[:5]
        hotspots.append({
            "path": path,
            "churn_count": churn_count,
            "co_change_partners": [
                {"path": p, "co_occurrences": c} for p, c in partners
            ],
            "dependents_count": dependents_by_path.get(path, 0),
        })

    hotspots.sort(key=lambda h: -h["churn_count"])
    return hotspots[:HOTSPOT_LIMIT]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd prototype && python -m pytest tests/test_git_intel.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add aletheore/git_intel/analyzer.py tests/test_git_intel.py
git commit -m "feat: git churn and co-change hotspot detection"
```

---

### Task 5: Wire dead_code and hotspots into scan_repository, query, and MCP

**Files:**
- Modify: `aletheore/evidence.py`
- Modify: `aletheore/query.py`
- Modify: `aletheore/cli.py`
- Test: `tests/test_evidence.py` (confirm this exact filename via `ls tests/` before editing -
  if the scan orchestration is tested under a different filename, use that one instead)

**Interfaces:**
- Consumes: `find_dead_code` (Task 3), `compute_hotspots` (Task 4).
- Produces: `evidence["repository"]["dead_code"]`, `evidence["git"]["hotspots"]` (only present
  when `evidence["git"]["available"]` is `True` - mirrors how every other `git.*` field is
  already conditional on git being available).

- [ ] **Step 1: Write the failing test**

```python
# append to prototype/tests/test_evidence.py (or wherever scan_repository is already tested -
# check the real file first)
def test_scan_repository_includes_dead_code_and_hotspots(tmp_path):
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "A"], cwd=repo, check=True)
    (repo / "main.py").write_text("def run():\n    pass\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=repo, check=True)

    evidence = scan_repository(
        repo, check_vulnerabilities=False, scan_git_history=False, check_licenses=False,
        map_endpoints=False,
    )

    assert "dead_code" in evidence["repository"]
    assert "unreachable_modules" in evidence["repository"]["dead_code"]
    assert "hotspots" in evidence["git"]
    assert evidence["git"]["hotspots"][0]["path"] == "main.py"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd prototype && python -m pytest tests/test_evidence.py -k dead_code_and_hotspots -v`
Expected: FAIL - `KeyError: 'dead_code'`

- [ ] **Step 3: Wire into `scan_repository`**

```python
# aletheore/evidence.py - add imports
from aletheore.dead_code import find_dead_code
from aletheore.git_intel.analyzer import analyze_git, compute_hotspots

# after the existing "Clustering modules..." phase and before the vulnerability check:
    report("Detecting dead code")
    dead_code_data = find_dead_code(repo_path, modules, architecture_config)

    if git_data.get("available"):
        report("Computing hotspots")
        git_data["hotspots"] = compute_hotspots(repo_path, modules)
```

Add `"dead_code": dead_code_data,` to the `"repository": {...}` dict in `scan_repository`'s
return value, alongside the existing `api_endpoints` key.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd prototype && python -m pytest tests/test_evidence.py -v`
Expected: all pass

- [ ] **Step 5: Wire query/CLI/MCP for the two new evidence-lookup kinds**

```python
# aletheore/query.py
def find_dead_code_evidence(evidence: dict, target: str | None) -> dict:
    return evidence["repository"]["dead_code"]


def find_hotspots(evidence: dict, target: str | None) -> list[dict]:
    return evidence["git"].get("hotspots", [])
```

Add both to `QUERY_FUNCTIONS`:

```python
    "dead-code": (find_dead_code_evidence, False),
    "hotspots": (find_hotspots, False),
```

No `mcp_server.py` change needed beyond this - both ride the existing
`_register_query_wrapper_tools` auto-registration loop via `_TOOL_NAME_TO_QUERY_KIND`. Add:

```python
    "aletheore_dead_code": "dead-code",
    "aletheore_hotspots": "hotspots",
```

to `_TOOL_NAME_TO_QUERY_KIND` in `aletheore/mcp_server.py`.

- [ ] **Step 6: Run the full suite**

Run: `cd prototype && python -m pytest -q`
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add aletheore/evidence.py aletheore/query.py aletheore/mcp_server.py tests/test_evidence.py
git commit -m "feat: wire dead-code and hotspots into scan, query, and MCP"
```

---

### Task 6: Update `EVIDENCE_SCHEMA_MAP` and the manual for the `audit` command

**Files:**
- Modify: `aletheore/adapters/openai_compatible.py`
- Modify: relevant `aletheore/manual/*.md` files (check which part currently documents
  `repository.modules`/`git` fields via `grep -rn "symbols\|imported_by" aletheore/manual/` and
  update that file specifically, rather than guessing which numbered part it lives in)

**Interfaces:**
- Consumes: nothing new: this task only updates documentation strings the `audit` agentic loop
  reads, so the LLM-driven report doesn't cite the old (now-wrong) `functions: list[str]` shape
  or fail to mention the two new evidence blocks at all.

- [ ] **Step 1: Update `EVIDENCE_SCHEMA_MAP` in `aletheore/adapters/openai_compatible.py`**

Change the existing line:

```
repository.modules[]                - {path, imports[], imported_by[], symbols: {functions[], classes[]}}
```

to:

```
repository.modules[]                - {path, imports[], imported_by[], symbols: {functions[]: {name, start_line, end_line}, classes[]: {name, start_line, end_line}}}
repository.dead_code                 - {unreachable_modules[]: {path, reason}, unused_dependencies[]: {ecosystem, package}, entry_points_detected[]}
```

and add, in the `git.*` section:

```
git.hotspots[]                      - {path, churn_count, co_change_partners[]: {path, co_occurrences}, dependents_count}
```

- [ ] **Step 2: Update the manual's own description of the evidence shape**

Locate the exact manual file documenting evidence fields (`grep -rln "symbols" aletheore/manual/`)
and update its prose the same way - do not leave the shipped operating manual describing a
shape that no longer matches reality, since the whole `audit` design depends on the manual being
accurate.

- [ ] **Step 3: Run the full suite once more**

Run: `cd prototype && python -m pytest -q`
Expected: all pass (this task touches no executable logic, only documentation strings, so this
is a final regression check, not expected to catch anything new)

- [ ] **Step 4: Commit**

```bash
git add aletheore/adapters/openai_compatible.py aletheore/manual/*.md
git commit -m "docs: update EVIDENCE_SCHEMA_MAP and manual for new evidence shape"
```

---

### Task 7: Real verification against Aletheore's own repository

Not a TDD task - the same live-verification discipline used throughout this project.

- [ ] **Step 1: Run a real scan against Aletheore's own repo**

```bash
cd prototype && python -m aletheore.cli scan .
```

Confirm it completes without error and `.aletheore/evidence.json` contains real
`start_line`/`end_line` values.

- [ ] **Step 2: Spot-check symbol line bounds against real files**

Pick 3-4 real functions/classes from the resulting evidence across at least two different
language extractors actually exercised by this repo (Python is guaranteed; check whether any
other language appears in this specific codebase's own source - if not, use a small throwaway
fixture in another language instead), open the real file, and manually confirm the reported
`start_line`/`end_line` exactly bracket the real function/class body, not off by one in either
direction.

- [ ] **Step 3: Inspect `repository.dead_code` for false positives**

Read through `unreachable_modules` and confirm every entry is genuinely unreferenced - not a
CLI entry point or test file that slipped past the exclusion rules. If a real false positive is
found, add its pattern to `ENTRY_POINT_FILENAMES`/`TEST_PATH_PATTERNS` in
`aletheore/dead_code.py` and re-run.

- [ ] **Step 4: Inspect `git.hotspots` against real `git log --stat` output**

Cross-check the top few files by `churn_count` against `git log --oneline --name-only | sort | uniq -c | sort -rn | head` 
run manually against this same repo, confirming the numbers actually match.

- [ ] **Step 5: Verify `aletheore query symbol-source` end to end**

```bash
python -m aletheore.cli query symbol-source aletheore/query.py find_symbols .
```

Confirm the printed source exactly matches the real function body in `aletheore/query.py`.

## Success Criteria (restated for final verification)

1. `aletheore scan` on a real repo produces `start_line`/`end_line` for every function/class
   across every supported language, verified by spot-check against the real file.
2. `aletheore query symbol-source <module> <name>` returns exactly the right source text for a
   real symbol in a real repo, with no off-by-one errors.
3. `repository.dead_code.unreachable_modules` on a real repo excludes every real entry point and
   every real test file.
4. `git.hotspots` on a real repo ranks files by churn correctly and surfaces at least one real
   co-change pair, with the 50-file-commit guard verified not to blow up runtime on a synthetic
   large commit.
5. All three blocks are reachable via CLI (`aletheore query <kind>`) and MCP tool, TOON-encoded
   like every other query result.
6. Zero new dependencies added.
