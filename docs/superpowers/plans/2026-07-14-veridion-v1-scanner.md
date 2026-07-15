# Veridion v1 Scanner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `veridion audit [path]`, a single CLI command that produces a grounded, evidence-based audit report of a repository by combining deterministic static analysis (tree-sitter + git log) with a shell-out to an already-installed coding agent CLI.

**Architecture:** Two phases in one command. Phase 1 (pure Python, no LLM): scan the repo for language/framework/build-tool signals, build a module/dependency graph via tree-sitter, and analyze git history for branch staleness/cadence/ownership — all written to `.veridion/evidence.json`. Phase 2 (agent shell-out): detect an installed agent CLI, point it at the manual files and `evidence.json` already on disk, and let the agent's own filesystem tools produce `.veridion/audit-report.md`.

**Tech Stack:** Python 3.11+, `tree-sitter` + `tree-sitter-python` + `tree-sitter-javascript` + `tree-sitter-typescript` (import/symbol parsing), stdlib `subprocess` (git and agent CLI shell-out), stdlib `argparse` (CLI), `pytest` (tests).

## Global Constraints

- **This is an unratified prototype under VDP-0000-REQ-009** ("Prototypes and experiments MAY precede specifications, but they MUST NOT become normative without the VDP process"). It does NOT go through VDP Draft/Discussion/independent-review/Accept. Do not create a VDP proposal for this work.
- **Do not touch** `constitution/`, `docs/governance/`, `docs/reviews/`, `docs/proposal-lifecycle.md`, `docs/specification-process.md`, `GOVERNANCE.md`, `templates/VDP_TEMPLATE.md`, or any existing VDP file. This work is fully out-of-band from that apparatus.
- **Do not add code to** `cli/`, `engines/`, or `modules/` — each of those directories' own README explicitly reserves itself for post-acceptance code ("Add only after framework architecture is approved" / "Add modules only through accepted proposals"). All prototype code lives under a new top-level `prototype/` directory instead.
- **No hosted inference, no API keys.** Phase 2 always shells out to an already-installed, already-authenticated agent CLI. Veridion itself never calls an LLM API directly.
- **Every claim the agent produces in the final report must trace to a specific `evidence.json` field.** This is enforced by the Part I manual instructions (Task 2), not by code — but every deterministic component (Tasks 3-6) must be correct and honest about what it couldn't determine (`unparseable_files`, `git.available: false`) rather than silently omitting gaps.
- All file paths below are relative to the repository root (`/Users/arihantkaul/Documents/GitHub/Veridion`) unless stated otherwise.

---

## File Structure

```
Veridion/
  prototype/
    README.md                          # Task 1 — explains REQ-009 status, out-of-band from constitution/
    pyproject.toml                     # Task 1
    manual/
      part-1-operating-instructions.md # Task 2
      part-2-repository-intelligence.md# Task 2
      part-3-git-intelligence.md       # Task 2
    veridion/
      __init__.py                      # Task 1
      cli.py                           # Task 8
      scanner/
        __init__.py                    # Task 1
        detect.py                      # Task 3
        graph.py                       # Task 4
      git_intel/
        __init__.py                    # Task 1
        analyzer.py                    # Task 5
      evidence.py                      # Task 6
      adapters/
        __init__.py                    # Task 1
        base.py                        # Task 7
        claude_code.py                 # Task 7
      report.py                        # Task 8
    tests/
      __init__.py                      # Task 1
      fixtures/
        sample_repo/                   # Task 3 (built by test setup, not committed as static files)
      test_detect.py                   # Task 3
      test_graph.py                    # Task 4
      test_git_intel.py                # Task 5
      test_evidence.py                 # Task 6
      test_adapters.py                 # Task 7
      test_cli.py                      # Task 8
```

---

## Task 1: Prototype scaffolding

**Files:**
- Create: `prototype/README.md`
- Create: `prototype/pyproject.toml`
- Create: `prototype/veridion/__init__.py`
- Create: `prototype/veridion/scanner/__init__.py`
- Create: `prototype/veridion/git_intel/__init__.py`
- Create: `prototype/veridion/adapters/__init__.py`
- Create: `prototype/tests/__init__.py`
- Test: `prototype/tests/test_scaffold.py`

**Interfaces:**
- Produces: an installable package `veridion` (via `pip install -e .` from `prototype/`), importable as `import veridion`. All later tasks assume this package layout exists and that `pytest` runs from inside `prototype/`.

- [ ] **Step 1: Create the prototype README**

```markdown
# Veridion Prototype

**Status:** Unratified prototype under VDP-0000-REQ-009 ("Prototypes and experiments MAY
precede specifications, but they MUST NOT become normative without the VDP process").

This directory is deliberately out-of-band from the constitutional apparatus in
`constitution/`, `docs/governance/`, and `docs/reviews/`. It does not modify, supersede, or
depend on any VDP. It is not a proposal and should not be treated as one.

Design spec: `../docs/superpowers/specs/2026-07-14-veridion-v1-design.md`
Implementation plan: `../docs/superpowers/plans/2026-07-14-veridion-v1-scanner.md`

## What this is

A working CLI, `veridion audit [path]`, that produces a grounded audit report of a
repository using deterministic static analysis (tree-sitter + git log) plus a shell-out to
an already-installed coding agent CLI (Claude Code in v1).

## Setup

```bash
cd prototype
pip install -e ".[dev]"
pytest
```
```

- [ ] **Step 2: Create `pyproject.toml`**

```toml
[project]
name = "veridion"
version = "0.1.0"
description = "Evidence-grounded repository audit prototype (unratified, VDP-0000-REQ-009)"
requires-python = ">=3.11"
dependencies = [
    "tree-sitter>=0.21,<0.22",
    "tree-sitter-python>=0.21,<0.22",
    "tree-sitter-javascript>=0.21,<0.22",
    "tree-sitter-typescript>=0.21,<0.22",
]

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[project.scripts]
veridion = "veridion.cli:main"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["veridion*"]
```

- [ ] **Step 3: Create empty package `__init__.py` files**

`prototype/veridion/__init__.py`:
```python
__version__ = "0.1.0"
```

`prototype/veridion/scanner/__init__.py`:
```python
```

`prototype/veridion/git_intel/__init__.py`:
```python
```

`prototype/veridion/adapters/__init__.py`:
```python
```

`prototype/tests/__init__.py`:
```python
```

- [ ] **Step 4: Write the scaffold smoke test**

`prototype/tests/test_scaffold.py`:
```python
import veridion


def test_package_importable():
    assert veridion.__version__ == "0.1.0"
```

- [ ] **Step 5: Install and run**

Run:
```bash
cd prototype
pip install -e ".[dev]"
pytest tests/test_scaffold.py -v
```
Expected: PASS (1 test).

- [ ] **Step 6: Commit**

```bash
git add prototype/
git commit -m "chore: scaffold veridion prototype package (VDP-0000-REQ-009)"
```

---

## Task 2: Manual content (Parts I-III)

**Files:**
- Create: `prototype/manual/part-1-operating-instructions.md`
- Create: `prototype/manual/part-2-repository-intelligence.md`
- Create: `prototype/manual/part-3-git-intelligence.md`

**Interfaces:**
- Consumes: the `evidence.json` schema defined in the design spec (repeated below for each file's own reference — schema is fixed by Task 6, but the field names are already final per the spec, so this task can be written before Task 6's code).
- Produces: static markdown files that Task 8's `report.py` points the agent adapter at. No code interface — this task has no tests.

This task has no test cycle (it's content, not code). Write the three files in one step each.

- [ ] **Step 1: Write Part I — Operating Instructions**

`prototype/manual/part-1-operating-instructions.md`:
```markdown
# Part I — Operating Instructions

You are auditing a repository using Veridion. You have been given two things: this manual,
and `.veridion/evidence.json`, a deterministic, machine-generated file describing the
repository's languages, frameworks, module/dependency graph, and git history. Read
`evidence.json` in full before writing anything.

## Mandatory verification rules (primary — these override everything else in this manual)

1. **Every factual claim you make must cite a specific field in `evidence.json`.** If you
   say a file exists, a function is defined, a branch is stale, or an author owns a module,
   name the exact `evidence.json` path (e.g. `repository.modules[3].path`,
   `git.branches[1].stale_days`) that supports it.
2. **If evidence does not support a claim you want to make, write "not determinable from
   available evidence" instead of guessing.** Do not infer facts about files, languages, or
   history that are not present in `evidence.json`. Do not use general knowledge about what a
   framework "usually" does in place of specific evidence from this repository.
3. **Never reference a file, function, class, or branch that is not present in
   `evidence.json`.** If `evidence.json` doesn't mention it, you have no evidence it exists.
4. **State a confidence level (High / Medium / Low) for every major finding.** High confidence
   means the finding is a direct, unambiguous read of one or more evidence fields. Medium
   means it requires combining multiple evidence fields with reasonable inference. Low means
   it is a plausible interpretation that evidence is consistent with but does not prove.
5. **If `evidence.repository.unparseable_files` is non-empty, state explicitly that those
   files were not analyzed and your findings do not cover them.**
6. **If `evidence.git.available` is `false`, state that git intelligence is unavailable for
   this repository. Do not fabricate branch names, commit counts, or contributor history.**

## Output contract

Structure your report with these sections, in this order:

1. **Summary** — 3-5 sentences, no unsupported claims, citing the highest-confidence findings.
2. **Repository Intelligence** — findings from `evidence.repository`, per Part II below.
3. **Git Intelligence** — findings from `evidence.git`, per Part III below.
4. **Evidence Gaps** — an explicit list of what `evidence.json` could not tell you
   (unparseable files, unavailable git data, anything you were tempted to claim but couldn't
   support).

## Review stance (secondary — stylistic framing, subordinate to the rules above)

Bias toward maintainability over cleverness. Favor plain, falsifiable statements over
impressive-sounding but unverifiable ones. When a finding could be read two ways, present
both and say which the evidence favors, rather than picking the more dramatic one.
```

- [ ] **Step 2: Write Part II — Repository Intelligence**

`prototype/manual/part-2-repository-intelligence.md`:
```markdown
# Part II — Repository Intelligence

This section governs how to read `evidence.repository`. Follow the mandatory verification
rules in Part I for everything below.

## What's in `evidence.repository`

- `languages`: detected languages with file counts and rough line counts.
- `frameworks`: detected frameworks, each with an `evidence` string naming the manifest line
  that proves it (e.g. `"requirements.txt:fastapi==0.110.0"`).
- `build_tools`: detected build tooling, same evidence-string pattern.
- `monorepo`: whether workspace/monorepo tooling was detected, and the workspace list if so.
- `modules`: one entry per parsed source file, with `imports` (what it imports),
  `imported_by` (what imports it), and `symbols` (top-level functions/classes found).
- `dependency_graph`: `nodes` and `edges` derived from `modules`.
- `unparseable_files`: files that could not be parsed, with a `reason` per file.

## Do not speculate rule

**Do not speculate about languages or frameworks absent from the `languages` and
`frameworks` arrays.** If a language or framework isn't listed, evidence does not confirm
its presence — say so rather than guessing from file names or conventions.

## What counts as noteworthy

- **High fan-in modules**: a module with a long `imported_by` list. Worth flagging if it also
  has no obviously corresponding test file among the other `modules` entries (a module named
  `test_x.py` or `x.test.js` importing it) — state this as Medium confidence unless you can
  point to the specific absence.
- **Circular import chains**: any path in `dependency_graph.edges` that returns to its
  starting node. State the exact node sequence you found (High confidence — this is a direct
  graph read, not an inference).
- **Single-file god-modules**: a module whose `symbols.functions` + `symbols.classes` count is
  far larger than the repository's average for its language. State the actual counts you
  compared.
- **Evidence coverage gaps**: always report `unparseable_files` count and list, even if empty
  (say "none" explicitly rather than omitting the section).
```

- [ ] **Step 3: Write Part III — Git Intelligence**

`prototype/manual/part-3-git-intelligence.md`:
```markdown
# Part III — Git Intelligence

This section governs how to read `evidence.git`. Follow the mandatory verification rules in
Part I for everything below.

## Availability check (do this first)

**If `evidence.git.available` is `false`, stop here for this section.** State plainly that
git intelligence is unavailable for this repository (e.g. no commits yet), and do not proceed
to describe branches, cadence, or ownership. Do not fabricate any git history.

## What's in `evidence.git` (when available)

- `branches`: each with `name`, `type` (local/remote), `last_commit_at`, `stale_days`,
  `ahead_of_main`/`behind_main`.
- `commit_cadence`: `weekly_counts` (commits per week, most recent last) and a `trend`
  classification.
- `ownership`: per-author commit counts and the percentage of total commits each represents.
- `repo_age_days` and `total_commits`.

## What counts as noteworthy

- **Long-stale branches**: any branch with a large `stale_days` relative to the others. Name
  the branch and its exact `stale_days` value.
- **Ownership concentration**: if one author's `percent` in `ownership` is much higher than
  the rest combined, name the author and the exact percentage — this is a bus-factor signal,
  not a judgment about the author.
- **Cadence drop-offs**: a sharp decline in `commit_cadence.weekly_counts` toward the most
  recent weeks. Cite the actual numbers you're comparing.

## What this section does not produce

Do not attempt to score "branching strategy quality," "commit message quality," or produce a
Merge Order Matrix, Conflict Prediction, or Cherry-Pick Suggestions. Those require judgment
this manual does not yet define rules for. Report the raw facts above; leave scoring for a
future part of the manual.
```

- [ ] **Step 4: Commit**

```bash
git add prototype/manual/
git commit -m "docs: write Veridion v1 manual (Parts I-III)"
```

---

## Task 3: Repository detection (`detect.py`)

**Files:**
- Create: `prototype/veridion/scanner/detect.py`
- Test: `prototype/tests/test_detect.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (pure filesystem walk).
- Produces (used by Task 6):
  - `detect_languages(repo_path: pathlib.Path) -> list[dict]` — each dict has keys `name: str`, `file_count: int`, `loc: int`.
  - `detect_frameworks(repo_path: pathlib.Path) -> list[dict]` — each dict has keys `name: str`, `evidence: str`.
  - `detect_build_tools(repo_path: pathlib.Path) -> list[dict]` — each dict has keys `name: str`, `evidence: str`.
  - `detect_monorepo(repo_path: pathlib.Path) -> dict` — keys `detected: bool`, `workspaces: list[str]`.

- [ ] **Step 1: Write failing tests**

`prototype/tests/test_detect.py`:
```python
import json
import textwrap
from pathlib import Path

from veridion.scanner.detect import (
    detect_build_tools,
    detect_frameworks,
    detect_languages,
    detect_monorepo,
)


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "app").mkdir(parents=True)
    (repo / "frontend").mkdir()
    (repo / "app" / "main.py").write_text("import os\n\ndef hello():\n    return 1\n")
    (repo / "app" / "other.py").write_text("x = 1\ny = 2\n")
    (repo / "frontend" / "index.js").write_text("console.log('hi')\n")
    (repo / "requirements.txt").write_text("fastapi==0.110.0\nuvicorn==0.29.0\n")
    (repo / "package.json").write_text(
        json.dumps({"name": "frontend", "dependencies": {"react": "^18.2.0"}})
    )
    return repo


def test_detect_languages_counts_files_and_loc(tmp_path):
    repo = make_repo(tmp_path)
    languages = detect_languages(repo)
    by_name = {entry["name"]: entry for entry in languages}
    assert by_name["python"]["file_count"] == 2
    assert by_name["python"]["loc"] == 6
    assert by_name["javascript"]["file_count"] == 1


def test_detect_frameworks_reads_requirements_txt(tmp_path):
    repo = make_repo(tmp_path)
    frameworks = detect_frameworks(repo)
    names = {f["name"] for f in frameworks}
    assert "fastapi" in names
    fastapi_entry = next(f for f in frameworks if f["name"] == "fastapi")
    assert fastapi_entry["evidence"] == "requirements.txt:fastapi==0.110.0"


def test_detect_frameworks_reads_package_json(tmp_path):
    repo = make_repo(tmp_path)
    frameworks = detect_frameworks(repo)
    names = {f["name"] for f in frameworks}
    assert "react" in names


def test_detect_build_tools_finds_dockerfile(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "Dockerfile").write_text("FROM python:3.11\n")
    tools = detect_build_tools(repo)
    names = {t["name"] for t in tools}
    assert "docker" in names


def test_detect_monorepo_detects_npm_workspaces(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "package.json").write_text(
        json.dumps({"name": "root", "workspaces": ["packages/*"]})
    )
    result = detect_monorepo(repo)
    assert result["detected"] is True
    assert result["workspaces"] == ["packages/*"]


def test_detect_monorepo_false_when_absent(tmp_path):
    repo = make_repo(tmp_path)
    result = detect_monorepo(repo)
    assert result["detected"] is False
    assert result["workspaces"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd prototype
pytest tests/test_detect.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'veridion.scanner.detect'`.

- [ ] **Step 3: Implement `detect.py`**

`prototype/veridion/scanner/detect.py`:
```python
import json
from pathlib import Path

IGNORED_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".veridion"}

EXTENSION_TO_LANGUAGE = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
}

FRAMEWORK_MARKERS_PY = {
    "fastapi": "fastapi",
    "flask": "flask",
    "django": "django",
    "uvicorn": "uvicorn",
}

FRAMEWORK_MARKERS_JS = {
    "react": "react",
    "vue": "vue",
    "express": "express",
    "next": "next",
}

BUILD_TOOL_MARKERS = {
    "Dockerfile": "docker",
    "docker-compose.yml": "docker-compose",
    "Makefile": "make",
    "webpack.config.js": "webpack",
    "vite.config.ts": "vite",
    "vite.config.js": "vite",
}


def _iter_source_files(repo_path: Path):
    for path in repo_path.rglob("*"):
        if not path.is_file():
            continue
        if any(part in IGNORED_DIRS for part in path.parts):
            continue
        yield path


def detect_languages(repo_path: Path) -> list[dict]:
    counts: dict[str, dict] = {}
    for path in _iter_source_files(repo_path):
        language = EXTENSION_TO_LANGUAGE.get(path.suffix)
        if language is None:
            continue
        entry = counts.setdefault(language, {"name": language, "file_count": 0, "loc": 0})
        entry["file_count"] += 1
        try:
            entry["loc"] += sum(1 for _ in path.open("r", encoding="utf-8", errors="ignore"))
        except OSError:
            continue
    return list(counts.values())


def detect_frameworks(repo_path: Path) -> list[dict]:
    frameworks: list[dict] = []

    requirements = repo_path / "requirements.txt"
    if requirements.exists():
        for line in requirements.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            package_name = line.split("==")[0].split(">=")[0].split("<=")[0].strip().lower()
            if package_name in FRAMEWORK_MARKERS_PY:
                frameworks.append(
                    {
                        "name": FRAMEWORK_MARKERS_PY[package_name],
                        "evidence": f"requirements.txt:{line}",
                    }
                )

    package_json = repo_path / "package.json"
    if package_json.exists():
        try:
            data = json.loads(package_json.read_text(encoding="utf-8", errors="ignore"))
        except json.JSONDecodeError:
            data = {}
        deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
        for name, version in deps.items():
            key = name.lower()
            if key in FRAMEWORK_MARKERS_JS:
                frameworks.append(
                    {
                        "name": FRAMEWORK_MARKERS_JS[key],
                        "evidence": f"package.json:{name}@{version}",
                    }
                )

    return frameworks


def detect_build_tools(repo_path: Path) -> list[dict]:
    tools = []
    for filename, tool_name in BUILD_TOOL_MARKERS.items():
        marker = repo_path / filename
        if marker.exists():
            tools.append({"name": tool_name, "evidence": filename})
    return tools


def detect_monorepo(repo_path: Path) -> dict:
    package_json = repo_path / "package.json"
    if package_json.exists():
        try:
            data = json.loads(package_json.read_text(encoding="utf-8", errors="ignore"))
        except json.JSONDecodeError:
            data = {}
        workspaces = data.get("workspaces")
        if workspaces:
            return {"detected": True, "workspaces": list(workspaces)}

    for marker in ("pnpm-workspace.yaml", "lerna.json", "nx.json"):
        if (repo_path / marker).exists():
            return {"detected": True, "workspaces": []}

    return {"detected": False, "workspaces": []}
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
pytest tests/test_detect.py -v
```
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add prototype/veridion/scanner/detect.py prototype/tests/test_detect.py
git commit -m "feat: add repository language/framework/build-tool detection"
```

---

## Task 4: Module and dependency graph (`graph.py`)

**Files:**
- Create: `prototype/veridion/scanner/graph.py`
- Test: `prototype/tests/test_graph.py`

**Interfaces:**
- Consumes: nothing from earlier tasks except `IGNORED_DIRS` pattern (redefine locally, do not import private details across modules).
- Produces (used by Task 6):
  - `build_module_graph(repo_path: pathlib.Path) -> tuple[list[dict], dict, list[dict]]` returning `(modules, dependency_graph, unparseable_files)`.
    - `modules`: list of `{"path": str, "language": str, "imports": list[str], "imported_by": list[str], "symbols": {"functions": list[str], "classes": list[str]}}`. `path` is repo-relative with forward slashes.
    - `dependency_graph`: `{"nodes": list[str], "edges": list[list[str]]}` where edges are `[from_path, to_path]` pairs, only for internal (resolved) imports.
    - `unparseable_files`: list of `{"path": str, "reason": str}`.

- [ ] **Step 1: Write failing tests**

`prototype/tests/test_graph.py`:
```python
from pathlib import Path

from veridion.scanner.graph import build_module_graph


def make_python_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    app = repo / "app"
    app.mkdir(parents=True)
    (app / "__init__.py").write_text("")
    (app / "config.py").write_text("SETTING = 1\n\ndef load():\n    return SETTING\n")
    (app / "auth.py").write_text(
        "from app import config\n\n\ndef login():\n    return config.load()\n\n\nclass AuthError(Exception):\n    pass\n"
    )
    (app / "routes.py").write_text("from app.auth import login\n\ndef handle():\n    return login()\n")
    return repo


def test_build_module_graph_extracts_python_imports_and_symbols(tmp_path):
    repo = make_python_repo(tmp_path)
    modules, dependency_graph, unparseable = build_module_graph(repo)

    by_path = {m["path"]: m for m in modules}
    assert "app/auth.py" in by_path
    auth = by_path["app/auth.py"]
    assert "app/config.py" in auth["imports"]
    assert "login" in auth["symbols"]["functions"]
    assert "AuthError" in auth["symbols"]["classes"]

    config = by_path["app/config.py"]
    assert "app/auth.py" in config["imported_by"]

    assert unparseable == []


def test_build_module_graph_dependency_edges(tmp_path):
    repo = make_python_repo(tmp_path)
    _, dependency_graph, _ = build_module_graph(repo)
    edges = {tuple(edge) for edge in dependency_graph["edges"]}
    assert ("app/auth.py", "app/config.py") in edges
    assert ("app/routes.py", "app/auth.py") in edges


def test_build_module_graph_records_unparseable_files(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "helper.swift").write_text("func hi() {}\n")
    modules, _, unparseable = build_module_graph(repo)
    assert modules == []
    assert unparseable == [{"path": "helper.swift", "reason": "no grammar registered for .swift"}]


def test_build_module_graph_extracts_javascript_imports(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "utils.js").write_text("export function add(a, b) { return a + b; }\n")
    (repo / "index.js").write_text("import { add } from './utils';\n\nfunction main() { return add(1, 2); }\n")
    modules, dependency_graph, unparseable = build_module_graph(repo)
    by_path = {m["path"]: m for m in modules}
    assert "index.js" in by_path
    assert "utils.js" in by_path["index.js"]["imports"]
    assert unparseable == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
pytest tests/test_graph.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'veridion.scanner.graph'`.

- [ ] **Step 3: Implement `graph.py`**

`prototype/veridion/scanner/graph.py`:
```python
from pathlib import Path

import tree_sitter_javascript as tsjavascript
import tree_sitter_python as tspython
from tree_sitter import Language, Node, Parser

IGNORED_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".veridion"}

PY_LANGUAGE = Language(tspython.language())
JS_LANGUAGE = Language(tsjavascript.language())

LANGUAGE_BY_EXTENSION = {
    ".py": ("python", PY_LANGUAGE),
    ".js": ("javascript", JS_LANGUAGE),
    ".jsx": ("javascript", JS_LANGUAGE),
}


def _iter_source_files(repo_path: Path):
    for path in sorted(repo_path.rglob("*")):
        if not path.is_file():
            continue
        if any(part in IGNORED_DIRS for part in path.parts):
            continue
        yield path


def _rel(repo_path: Path, path: Path) -> str:
    return path.relative_to(repo_path).as_posix()


def _extract_python(
    node: Node, source: bytes
) -> tuple[list[str], list[tuple[str, list[str]]], list[str], list[str]]:
    """Returns (plain_imports, from_imports, functions, classes).

    plain_imports: dotted module names from `import a.b.c` statements.
    from_imports: (module_name, [imported_names]) pairs from `from a.b import c, d`
    statements. The imported names must be kept separate from module_name because
    `from app import config` needs to resolve to the submodule `app/config.py`, not
    just the package `app/__init__.py` — module_name alone is not enough.
    """
    plain_imports: list[str] = []
    from_imports: list[tuple[str, list[str]]] = []
    functions: list[str] = []
    classes: list[str] = []

    def walk(n: Node):
        if n.type == "import_from_statement":
            module_node = n.child_by_field_name("module_name")
            module_name = (
                source[module_node.start_byte:module_node.end_byte].decode()
                if module_node is not None
                else ""
            )
            names: list[str] = []
            for child in n.named_children:
                if child == module_node:
                    continue
                if child.type in ("dotted_name", "identifier"):
                    names.append(source[child.start_byte:child.end_byte].decode())
                elif child.type == "aliased_import":
                    name_node = child.child_by_field_name("name")
                    if name_node is not None:
                        names.append(source[name_node.start_byte:name_node.end_byte].decode())
            from_imports.append((module_name, names))
        elif n.type == "import_statement":
            for child in n.named_children:
                if child.type == "dotted_name":
                    plain_imports.append(source[child.start_byte:child.end_byte].decode())
                elif child.type == "aliased_import":
                    name_node = child.child_by_field_name("name")
                    if name_node is not None:
                        plain_imports.append(
                            source[name_node.start_byte:name_node.end_byte].decode()
                        )
        elif n.type == "function_definition":
            name_node = n.child_by_field_name("name")
            if name_node is not None:
                functions.append(source[name_node.start_byte:name_node.end_byte].decode())
        elif n.type == "class_definition":
            name_node = n.child_by_field_name("name")
            if name_node is not None:
                classes.append(source[name_node.start_byte:name_node.end_byte].decode())
        for child in n.children:
            walk(child)

    walk(node)
    return plain_imports, from_imports, functions, classes


def _extract_javascript(node: Node, source: bytes) -> tuple[list[str], list[str], list[str]]:
    imports: list[str] = []
    functions: list[str] = []
    classes: list[str] = []

    def walk(n: Node):
        if n.type == "import_statement":
            source_node = n.child_by_field_name("source")
            if source_node is not None:
                raw = source[source_node.start_byte:source_node.end_byte].decode()
                imports.append(raw.strip("'\""))
        elif n.type == "function_declaration":
            name_node = n.child_by_field_name("name")
            if name_node is not None:
                functions.append(source[name_node.start_byte:name_node.end_byte].decode())
        elif n.type == "class_declaration":
            name_node = n.child_by_field_name("name")
            if name_node is not None:
                classes.append(source[name_node.start_byte:name_node.end_byte].decode())
        for child in n.children:
            walk(child)

    walk(node)
    return imports, functions, classes


def _resolve_python_module(repo_path: Path, dotted: str) -> str | None:
    if not dotted:
        return None
    as_path = Path(*dotted.split("."))
    candidate_module = repo_path / (as_path.as_posix() + ".py")
    candidate_package = repo_path / as_path / "__init__.py"
    if candidate_module.exists():
        return _rel(repo_path, candidate_module)
    if candidate_package.exists():
        return _rel(repo_path, candidate_package)
    return None


def _resolve_python_from_import(repo_path: Path, module_name: str, imported_name: str) -> str | None:
    # `from a.b import c` most often imports a submodule `a/b/c.py` - try that first.
    submodule_dotted = f"{module_name}.{imported_name}" if module_name else imported_name
    target = _resolve_python_module(repo_path, submodule_dotted)
    if target is not None:
        return target
    # Otherwise it's importing a symbol from within module `a.b` itself.
    return _resolve_python_module(repo_path, module_name)


def _resolve_js_import(repo_path: Path, from_file: Path, spec: str) -> str | None:
    if not spec.startswith("."):
        return None
    base = (from_file.parent / spec).resolve()
    for candidate in (base, base.with_suffix(".js"), base / "index.js"):
        if candidate.exists() and candidate.is_file():
            try:
                return _rel(repo_path, candidate)
            except ValueError:
                return None
    return None


def build_module_graph(repo_path: Path) -> tuple[list[dict], dict, list[dict]]:
    modules: list[dict] = []
    unparseable: list[dict] = []
    imported_by_map: dict[str, list[str]] = {}
    edges: list[list[str]] = []

    parser = Parser()

    for path in _iter_source_files(repo_path):
        rel_path = _rel(repo_path, path)
        language_info = LANGUAGE_BY_EXTENSION.get(path.suffix)
        if language_info is None:
            unparseable.append(
                {"path": rel_path, "reason": f"no grammar registered for {path.suffix}"}
            )
            continue

        language_name, ts_language = language_info
        parser.language = ts_language
        source = path.read_bytes()
        tree = parser.parse(source)

        if language_name == "python":
            plain_imports, from_imports, functions, classes = _extract_python(
                tree.root_node, source
            )
            resolved_imports: list[str] = []

            for dotted in plain_imports:
                target = _resolve_python_module(repo_path, dotted)
                if target is not None:
                    resolved_imports.append(target)
                    edges.append([rel_path, target])
                    imported_by_map.setdefault(target, []).append(rel_path)

            for module_name, names in from_imports:
                targets: set[str] = set()
                if names:
                    for name in names:
                        target = _resolve_python_from_import(repo_path, module_name, name)
                        if target is not None:
                            targets.add(target)
                else:
                    target = _resolve_python_module(repo_path, module_name)
                    if target is not None:
                        targets.add(target)
                for target in sorted(targets):
                    resolved_imports.append(target)
                    edges.append([rel_path, target])
                    imported_by_map.setdefault(target, []).append(rel_path)
        else:
            raw_imports, functions, classes = _extract_javascript(tree.root_node, source)
            resolved_imports = []
            for spec in raw_imports:
                target = _resolve_js_import(repo_path, path, spec)
                if target is not None:
                    resolved_imports.append(target)
                    edges.append([rel_path, target])
                    imported_by_map.setdefault(target, []).append(rel_path)

        modules.append(
            {
                "path": rel_path,
                "language": language_name,
                "imports": resolved_imports,
                "imported_by": [],
                "symbols": {"functions": functions, "classes": classes},
            }
        )

    for module in modules:
        module["imported_by"] = sorted(imported_by_map.get(module["path"], []))

    nodes = sorted({m["path"] for m in modules})
    dependency_graph = {"nodes": nodes, "edges": edges}

    return modules, dependency_graph, unparseable
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
pytest tests/test_graph.py -v
```
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add prototype/veridion/scanner/graph.py prototype/tests/test_graph.py
git commit -m "feat: add tree-sitter module and dependency graph builder"
```

---

## Task 5: Git intelligence analyzer (`git_intel/analyzer.py`)

**Files:**
- Create: `prototype/veridion/git_intel/analyzer.py`
- Test: `prototype/tests/test_git_intel.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces (used by Task 6):
  - `analyze_git(repo_path: pathlib.Path, now: datetime.datetime | None = None) -> dict` returning the full `git` block matching the spec schema (`available`, and when true: `branches`, `commit_cadence`, `ownership`, `repo_age_days`, `total_commits`). `now` is injectable for deterministic tests; defaults to `datetime.now(timezone.utc)`.

- [ ] **Step 1: Write failing tests**

`prototype/tests/test_git_intel.py`:
```python
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from veridion.git_intel.analyzer import analyze_git


def run(repo: Path, *args: str):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def commit(repo: Path, message: str, date: str):
    # `git commit --date` only sets the AUTHOR date; committer date defaults to the
    # real wall-clock time unless GIT_COMMITTER_DATE is also set. Both must be pinned
    # for the fixture to be deterministic (branch staleness reads committer date,
    # cadence/ownership read author date). Dates include an explicit +00:00 offset so
    # the result doesn't depend on the machine's local timezone.
    env = os.environ.copy()
    env["GIT_AUTHOR_DATE"] = date
    env["GIT_COMMITTER_DATE"] = date
    subprocess.run(
        ["git", "commit", "-m", message], cwd=repo, check=True, capture_output=True, env=env
    )


def make_git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    run(repo, "init", "-b", "main")
    run(repo, "config", "user.email", "a@example.com")
    run(repo, "config", "user.name", "Alice")

    (repo / "a.txt").write_text("1")
    run(repo, "add", "a.txt")
    commit(repo, "first", "2026-06-01T00:00:00+00:00")

    (repo / "a.txt").write_text("2")
    run(repo, "add", "a.txt")
    commit(repo, "second", "2026-06-15T00:00:00+00:00")

    run(repo, "checkout", "-b", "feature/old")
    (repo / "b.txt").write_text("1")
    run(repo, "add", "b.txt")
    commit(repo, "feature work", "2026-06-16T00:00:00+00:00")
    run(repo, "checkout", "main")

    run(repo, "config", "user.name", "Bob")
    run(repo, "config", "user.email", "b@example.com")
    (repo / "a.txt").write_text("3")
    run(repo, "add", "a.txt")
    commit(repo, "third", "2026-07-01T00:00:00+00:00")

    return repo


def test_analyze_git_no_history_returns_unavailable(tmp_path):
    repo = tmp_path / "empty"
    repo.mkdir()
    run(repo, "init", "-b", "main")
    result = analyze_git(repo)
    assert result == {"available": False}


def test_analyze_git_not_a_repo_returns_unavailable(tmp_path):
    repo = tmp_path / "not_a_repo"
    repo.mkdir()
    result = analyze_git(repo)
    assert result == {"available": False}


def test_analyze_git_branches_and_staleness(tmp_path):
    repo = make_git_repo(tmp_path)
    now = datetime(2026, 7, 14, tzinfo=timezone.utc)
    result = analyze_git(repo, now=now)
    assert result["available"] is True

    by_name = {b["name"]: b for b in result["branches"]}
    assert "main" in by_name
    assert by_name["main"]["type"] == "local"
    assert by_name["main"]["stale_days"] == 13

    assert "feature/old" in by_name
    assert by_name["feature/old"]["stale_days"] == 28


def test_analyze_git_ownership(tmp_path):
    repo = make_git_repo(tmp_path)
    result = analyze_git(repo, now=datetime(2026, 7, 14, tzinfo=timezone.utc))
    by_author = {o["author"]: o for o in result["ownership"]}
    # Ownership is computed from `git log HEAD` (main's history only). The
    # "feature work" commit lives only on feature/old and is never merged into
    # main in this fixture, so it is correctly excluded: Alice has "first" and
    # "second", Bob has "third".
    assert by_author["Alice"]["commit_count"] == 2
    assert by_author["Bob"]["commit_count"] == 1
    assert by_author["Alice"]["percent"] == 0.6667


def test_analyze_git_totals(tmp_path):
    repo = make_git_repo(tmp_path)
    result = analyze_git(repo, now=datetime(2026, 7, 14, tzinfo=timezone.utc))
    # 3, not 4: total_commits counts commits reachable from HEAD (main), and
    # "feature work" is only reachable from feature/old.
    assert result["total_commits"] == 3
    assert result["repo_age_days"] == 43
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
pytest tests/test_git_intel.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'veridion.git_intel.analyzer'`.

- [ ] **Step 3: Implement `analyzer.py`**

`prototype/veridion/git_intel/analyzer.py`:
```python
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def _run_git(repo_path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )


def _has_commits(repo_path: Path) -> bool:
    result = _run_git(repo_path, "rev-parse", "--git-dir")
    if result.returncode != 0:
        return False
    result = _run_git(repo_path, "rev-list", "-1", "HEAD")
    return result.returncode == 0 and result.stdout.strip() != ""


def _parse_branches(repo_path: Path, now: datetime) -> list[dict]:
    result = _run_git(
        repo_path,
        "for-each-ref",
        "--format=%(refname:short)\t%(committerdate:iso-strict)",
        "refs/heads",
        "refs/remotes",
    )
    branches = []
    for line in result.stdout.strip().splitlines():
        if not line.strip():
            continue
        name, date_str = line.split("\t")
        branch_type = "remote" if name.startswith("origin/") or "/" in name and name.split("/")[0] in _remote_names(repo_path) else "local"
        last_commit_at = datetime.fromisoformat(date_str)
        stale_days = (now - last_commit_at).days
        branches.append(
            {
                "name": name,
                "type": branch_type,
                "last_commit_at": last_commit_at.isoformat(),
                "stale_days": stale_days,
                "ahead_of_main": 0,
                "behind_main": 0,
            }
        )
    return branches


def _remote_names(repo_path: Path) -> set[str]:
    result = _run_git(repo_path, "remote")
    return set(result.stdout.strip().splitlines())


def _commit_cadence(repo_path: Path) -> dict:
    result = _run_git(repo_path, "log", "--format=%ad", "--date=iso-strict", "HEAD")
    dates = [datetime.fromisoformat(line) for line in result.stdout.strip().splitlines() if line]
    if not dates:
        return {"weekly_counts": [], "trend": "flat"}

    dates.sort()
    buckets: dict[int, int] = {}
    start = dates[0]
    for date in dates:
        week_index = (date - start).days // 7
        buckets[week_index] = buckets.get(week_index, 0) + 1
    weekly_counts = [buckets.get(i, 0) for i in range(max(buckets.keys()) + 1)]

    if len(weekly_counts) < 2:
        trend = "flat"
    else:
        midpoint = len(weekly_counts) // 2
        first_half = sum(weekly_counts[:midpoint]) / max(midpoint, 1)
        second_half = sum(weekly_counts[midpoint:]) / max(len(weekly_counts) - midpoint, 1)
        if second_half > first_half * 1.2:
            trend = "increasing"
        elif second_half < first_half * 0.8:
            trend = "decreasing"
        else:
            trend = "flat"

    return {"weekly_counts": weekly_counts, "trend": trend}


def _ownership(repo_path: Path) -> list[dict]:
    result = _run_git(repo_path, "log", "--format=%an", "HEAD")
    authors = [line for line in result.stdout.strip().splitlines() if line]
    total = len(authors)
    counts: dict[str, int] = {}
    for author in authors:
        counts[author] = counts.get(author, 0) + 1
    return [
        {"author": author, "commit_count": count, "percent": round(count / total, 4)}
        for author, count in sorted(counts.items(), key=lambda kv: -kv[1])
    ]


def analyze_git(repo_path: Path, now: datetime | None = None) -> dict:
    if now is None:
        now = datetime.now(timezone.utc)

    if not _has_commits(repo_path):
        return {"available": False}

    total_commits_result = _run_git(repo_path, "rev-list", "--count", "HEAD")
    total_commits = int(total_commits_result.stdout.strip())

    first_commit_result = _run_git(repo_path, "log", "--reverse", "--format=%ad", "--date=iso-strict", "HEAD")
    first_commit_line = first_commit_result.stdout.strip().splitlines()[0]
    first_commit_at = datetime.fromisoformat(first_commit_line)
    repo_age_days = (now - first_commit_at).days

    return {
        "available": True,
        "branches": _parse_branches(repo_path, now),
        "commit_cadence": _commit_cadence(repo_path),
        "ownership": _ownership(repo_path),
        "repo_age_days": repo_age_days,
        "total_commits": total_commits,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
pytest tests/test_git_intel.py -v
```
Expected: PASS (5 tests). If `test_analyze_git_branches_and_staleness` fails on the `feature/old` `stale_days` value, check the fixture's committer dates match the `git commit --date` flag interpretation (author date vs committer date) — use `GIT_COMMITTER_DATE` env var alongside `--date` if `%(committerdate)` doesn't reflect the `--date` flag on your git version.

- [ ] **Step 5: Commit**

```bash
git add prototype/veridion/git_intel/analyzer.py prototype/tests/test_git_intel.py
git commit -m "feat: add git branch staleness, cadence, and ownership analyzer"
```

---

## Task 6: Evidence schema assembly (`evidence.py`)

**Files:**
- Create: `prototype/veridion/evidence.py`
- Test: `prototype/tests/test_evidence.py`

**Interfaces:**
- Consumes: `detect_languages`, `detect_frameworks`, `detect_build_tools`, `detect_monorepo` from `veridion.scanner.detect` (Task 3); `build_module_graph` from `veridion.scanner.graph` (Task 4); `analyze_git` from `veridion.git_intel.analyzer` (Task 5).
- Produces (used by Task 8):
  - `scan_repository(repo_path: pathlib.Path) -> dict` — runs all scanner functions and returns the full `evidence.json` dict (including `veridion_version`, `scanned_at`, `repo_path`, `repository`, `git`).
  - `write_evidence(evidence: dict, repo_path: pathlib.Path) -> pathlib.Path` — writes to `repo_path / ".veridion" / "evidence.json"`, creating the directory if needed, and returns the written path.

- [ ] **Step 1: Write failing tests**

`prototype/tests/test_evidence.py`:
```python
import json
import subprocess
from pathlib import Path

from veridion.evidence import scan_repository, write_evidence


def run(repo: Path, *args: str):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("def hello():\n    return 1\n")
    (repo / "requirements.txt").write_text("fastapi==0.110.0\n")
    run(repo, "init", "-b", "main")
    run(repo, "config", "user.email", "a@example.com")
    run(repo, "config", "user.name", "Alice")
    run(repo, "add", ".")
    run(repo, "commit", "-m", "init")
    return repo


def test_scan_repository_produces_full_schema(tmp_path):
    repo = make_repo(tmp_path)
    evidence = scan_repository(repo)

    assert evidence["veridion_version"] == "0.1.0"
    assert "scanned_at" in evidence
    assert evidence["repo_path"] == str(repo)

    assert any(entry["name"] == "python" for entry in evidence["repository"]["languages"])
    assert any(entry["name"] == "fastapi" for entry in evidence["repository"]["frameworks"])
    assert evidence["repository"]["modules"][0]["path"] == "main.py"

    assert evidence["git"]["available"] is True
    assert evidence["git"]["total_commits"] == 1


def test_scan_repository_handles_no_git_history(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("x = 1\n")
    evidence = scan_repository(repo)
    assert evidence["git"] == {"available": False}


def test_write_evidence_creates_veridion_dir(tmp_path):
    repo = make_repo(tmp_path)
    evidence = scan_repository(repo)
    written_path = write_evidence(evidence, repo)

    assert written_path == repo / ".veridion" / "evidence.json"
    assert written_path.exists()
    loaded = json.loads(written_path.read_text())
    assert loaded["veridion_version"] == "0.1.0"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
pytest tests/test_evidence.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'veridion.evidence'`.

- [ ] **Step 3: Implement `evidence.py`**

`prototype/veridion/evidence.py`:
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

EVIDENCE_VERSION = "0.1.0"


def scan_repository(repo_path: Path) -> dict:
    repo_path = repo_path.resolve()

    languages = detect_languages(repo_path)
    frameworks = detect_frameworks(repo_path)
    build_tools = detect_build_tools(repo_path)
    monorepo = detect_monorepo(repo_path)
    modules, dependency_graph, unparseable_files = build_module_graph(repo_path)
    git_data = analyze_git(repo_path)

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
    }


def write_evidence(evidence: dict, repo_path: Path) -> Path:
    veridion_dir = repo_path / ".veridion"
    veridion_dir.mkdir(parents=True, exist_ok=True)
    output_path = veridion_dir / "evidence.json"
    output_path.write_text(json.dumps(evidence, indent=2))
    return output_path
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
pytest tests/test_evidence.py -v
```
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add prototype/veridion/evidence.py prototype/tests/test_evidence.py
git commit -m "feat: assemble and write evidence.json from scanner and git_intel"
```

---

## Task 7: Agent adapter interface and Claude Code adapter

**Files:**
- Create: `prototype/veridion/adapters/base.py`
- Create: `prototype/veridion/adapters/claude_code.py`
- Test: `prototype/tests/test_adapters.py`

**Interfaces:**
- Produces (used by Task 8):
  - `AgentAdapter` (abstract base class in `base.py`) with `name: str` class attribute, `is_available(self) -> bool`, `invoke(self, instruction: str, cwd: str) -> str`.
  - `ClaudeCodeAdapter` (in `claude_code.py`, subclass of `AgentAdapter`), `name = "claude"`.
  - `AdapterInvocationError(Exception)` raised by `invoke` on non-zero exit or timeout.

- [ ] **Step 1: Write failing tests**

`prototype/tests/test_adapters.py`:
```python
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from veridion.adapters.base import AgentAdapter
from veridion.adapters.claude_code import AdapterInvocationError, ClaudeCodeAdapter


def test_agent_adapter_is_abstract():
    with pytest.raises(TypeError):
        AgentAdapter()


def test_claude_code_adapter_name():
    assert ClaudeCodeAdapter().name == "claude"


@patch("veridion.adapters.claude_code.shutil.which")
def test_is_available_true_when_binary_found(mock_which):
    mock_which.return_value = "/usr/local/bin/claude"
    assert ClaudeCodeAdapter().is_available() is True
    mock_which.assert_called_once_with("claude")


@patch("veridion.adapters.claude_code.shutil.which")
def test_is_available_false_when_binary_missing(mock_which):
    mock_which.return_value = None
    assert ClaudeCodeAdapter().is_available() is False


@patch("veridion.adapters.claude_code.subprocess.run")
def test_invoke_returns_stdout_on_success(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout="report text", stderr="")
    result = ClaudeCodeAdapter().invoke("do the audit", cwd="/some/repo")
    assert result == "report text"
    args, kwargs = mock_run.call_args
    assert args[0][0] == "claude"
    assert kwargs["cwd"] == "/some/repo"


@patch("veridion.adapters.claude_code.subprocess.run")
def test_invoke_raises_on_nonzero_exit(mock_run):
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="boom")
    with pytest.raises(AdapterInvocationError, match="boom"):
        ClaudeCodeAdapter().invoke("do the audit", cwd="/some/repo")


@patch("veridion.adapters.claude_code.subprocess.run")
def test_invoke_raises_on_timeout(mock_run):
    mock_run.side_effect = subprocess.TimeoutExpired(cmd="claude", timeout=600)
    with pytest.raises(AdapterInvocationError, match="timed out"):
        ClaudeCodeAdapter().invoke("do the audit", cwd="/some/repo")
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
pytest tests/test_adapters.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'veridion.adapters.base'`.

- [ ] **Step 3: Implement `base.py`**

`prototype/veridion/adapters/base.py`:
```python
from abc import ABC, abstractmethod


class AgentAdapter(ABC):
    name: str = "unnamed"

    @abstractmethod
    def is_available(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def invoke(self, instruction: str, cwd: str) -> str:
        raise NotImplementedError
```

- [ ] **Step 4: Implement `claude_code.py`**

`prototype/veridion/adapters/claude_code.py`:
```python
import shutil
import subprocess

from veridion.adapters.base import AgentAdapter

INVOCATION_TIMEOUT_SECONDS = 600


class AdapterInvocationError(Exception):
    pass


class ClaudeCodeAdapter(AgentAdapter):
    name = "claude"

    def is_available(self) -> bool:
        return shutil.which("claude") is not None

    def invoke(self, instruction: str, cwd: str) -> str:
        try:
            result = subprocess.run(
                ["claude", "-p", instruction],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=INVOCATION_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise AdapterInvocationError(
                f"claude invocation timed out after {INVOCATION_TIMEOUT_SECONDS}s"
            ) from exc

        if result.returncode != 0:
            raise AdapterInvocationError(
                f"claude invocation failed (exit {result.returncode}): {result.stderr}"
            )

        return result.stdout
```

- [ ] **Step 5: Run tests to verify they pass**

Run:
```bash
pytest tests/test_adapters.py -v
```
Expected: PASS (7 tests).

- [ ] **Step 6: Commit**

```bash
git add prototype/veridion/adapters/
git add prototype/tests/test_adapters.py
git commit -m "feat: add agent adapter interface and Claude Code adapter"
```

---

## Task 8: CLI entrypoint and reasoning-phase orchestration

**Files:**
- Create: `prototype/veridion/report.py`
- Create: `prototype/veridion/cli.py`
- Test: `prototype/tests/test_cli.py`

**Interfaces:**
- Consumes: `scan_repository`, `write_evidence` (Task 6); `AgentAdapter`, `ClaudeCodeAdapter`, `AdapterInvocationError` (Task 7).
- Produces:
  - `report.py`: `select_adapter(adapters: list[AgentAdapter], forced_name: str | None, interactive: bool) -> AgentAdapter` (raises `NoAdapterAvailableError` or `AmbiguousAdapterError`); `build_instruction(manual_dir: str) -> str`; `run_reasoning_phase(adapter: AgentAdapter, repo_path: str, manual_dir: str) -> str` (writes `.veridion/audit-report.md`, returns its path as a string).
  - `cli.py`: `main() -> int`, the `veridion` console-script entrypoint. Reads `sys.argv`, supports `veridion audit [path] [--agent NAME]`.

- [ ] **Step 1: Write failing tests**

`prototype/tests/test_cli.py`:
```python
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from veridion.report import (
    AmbiguousAdapterError,
    NoAdapterAvailableError,
    build_instruction,
    run_reasoning_phase,
    select_adapter,
)


def make_adapter(name: str, available: bool):
    adapter = MagicMock()
    adapter.name = name
    adapter.is_available.return_value = available
    return adapter


def test_select_adapter_returns_only_available_one():
    a = make_adapter("claude", True)
    b = make_adapter("cursor", False)
    result = select_adapter([a, b], forced_name=None, interactive=False)
    assert result is a


def test_select_adapter_raises_when_none_available():
    a = make_adapter("claude", False)
    with pytest.raises(NoAdapterAvailableError):
        select_adapter([a], forced_name=None, interactive=False)


def test_select_adapter_raises_when_multiple_and_not_interactive_and_no_flag():
    a = make_adapter("claude", True)
    b = make_adapter("cursor", True)
    with pytest.raises(AmbiguousAdapterError):
        select_adapter([a, b], forced_name=None, interactive=False)


def test_select_adapter_honors_forced_name():
    a = make_adapter("claude", True)
    b = make_adapter("cursor", True)
    result = select_adapter([a, b], forced_name="cursor", interactive=False)
    assert result is b


def test_build_instruction_references_manual_and_evidence():
    instruction = build_instruction(manual_dir="manual")
    assert "manual" in instruction
    assert ".veridion/evidence.json" in instruction


def test_run_reasoning_phase_writes_report(tmp_path):
    repo = tmp_path
    (repo / ".veridion").mkdir()
    (repo / ".veridion" / "evidence.json").write_text("{}")

    adapter = MagicMock()
    adapter.invoke.return_value = "# Audit Report\n\nfindings here\n"

    report_path = run_reasoning_phase(adapter, repo_path=str(repo), manual_dir="manual")

    written = Path(report_path)
    assert written == repo / ".veridion" / "audit-report.md"
    assert written.read_text() == "# Audit Report\n\nfindings here\n"
    adapter.invoke.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
pytest tests/test_cli.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'veridion.report'`.

- [ ] **Step 3: Implement `report.py`**

`prototype/veridion/report.py`:
```python
from pathlib import Path

from veridion.adapters.base import AgentAdapter


class NoAdapterAvailableError(Exception):
    pass


class AmbiguousAdapterError(Exception):
    pass


def select_adapter(
    adapters: list[AgentAdapter], forced_name: str | None, interactive: bool
) -> AgentAdapter:
    available = [a for a in adapters if a.is_available()]

    if forced_name is not None:
        for adapter in available:
            if adapter.name == forced_name:
                return adapter
        raise NoAdapterAvailableError(
            f"requested adapter '{forced_name}' is not available on PATH"
        )

    if not available:
        names = ", ".join(a.name for a in adapters)
        raise NoAdapterAvailableError(
            f"no supported agent CLI found on PATH (checked: {names})"
        )

    if len(available) == 1:
        return available[0]

    if interactive:
        names = [a.name for a in available]
        print("Multiple agent CLIs found:")
        for i, name in enumerate(names, start=1):
            print(f"  {i}. {name}")
        choice = input(f"Which one? [1-{len(names)}]: ").strip()
        index = int(choice) - 1
        return available[index]

    names = ", ".join(a.name for a in available)
    raise AmbiguousAdapterError(
        f"multiple agent CLIs available ({names}) and not running interactively; "
        "pass --agent NAME to choose one"
    )


def build_instruction(manual_dir: str) -> str:
    return (
        f"Read every markdown file in the '{manual_dir}' directory and "
        f"'.veridion/evidence.json' in the current directory. Follow the manual's "
        f"Part I operating instructions exactly, including its output contract, and "
        f"write the resulting audit report to '.veridion/audit-report.md'."
    )


def run_reasoning_phase(adapter: AgentAdapter, repo_path: str, manual_dir: str) -> str:
    instruction = build_instruction(manual_dir)
    output = adapter.invoke(instruction, cwd=repo_path)

    report_path = Path(repo_path) / ".veridion" / "audit-report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(output)
    return str(report_path)
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
pytest tests/test_cli.py -v
```
Expected: PASS (6 tests).

- [ ] **Step 5: Implement `cli.py`**

`prototype/veridion/cli.py`:
```python
import argparse
import sys
from pathlib import Path

from veridion.adapters.claude_code import AdapterInvocationError, ClaudeCodeAdapter
from veridion.evidence import scan_repository, write_evidence
from veridion.report import (
    AmbiguousAdapterError,
    NoAdapterAvailableError,
    run_reasoning_phase,
    select_adapter,
)

KNOWN_ADAPTERS = [ClaudeCodeAdapter()]

MANUAL_DIR = str(Path(__file__).resolve().parent.parent / "manual")


def _audit(repo_path: str, forced_agent: str | None) -> int:
    repo = Path(repo_path).resolve()

    print(f"Scanning {repo}...")
    evidence = scan_repository(repo)
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

    args = parser.parse_args()

    if args.command == "audit":
        return _audit(args.path, args.agent)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 6: Add a smoke test for `main()` argument parsing**

Append to `prototype/tests/test_cli.py`:
```python
from unittest.mock import patch

from veridion.cli import main


def test_main_requires_a_command(capsys):
    with patch("sys.argv", ["veridion"]):
        with pytest.raises(SystemExit):
            main()


def test_main_audit_invokes_audit_flow(tmp_path):
    with patch("sys.argv", ["veridion", "audit", str(tmp_path), "--agent", "claude"]):
        with patch("veridion.cli._audit", return_value=0) as mock_audit:
            exit_code = main()
    assert exit_code == 0
    mock_audit.assert_called_once_with(str(tmp_path), "claude")
```

- [ ] **Step 7: Run the full test suite**

Run:
```bash
pytest -v
```
Expected: PASS (all tests across every task).

- [ ] **Step 8: Commit**

```bash
git add prototype/veridion/report.py prototype/veridion/cli.py prototype/tests/test_cli.py
git commit -m "feat: wire up veridion audit CLI (scan phase + reasoning phase)"
```

---

## Task 9: Dogfood run against Procta (acceptance gate, not automated)

This task has no code changes. It is the go/no-go check from the design spec's Success Criteria section, run manually by whoever has both Veridion and Procta checked out.

- [ ] **Step 1: Install the prototype**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion/prototype
pip install -e ".[dev]"
```

- [ ] **Step 2: Run against Procta**

```bash
veridion audit /Users/arihantkaul/proctored-browser
```

- [ ] **Step 3: Check each success criterion from the design spec**

1. Command completes without crashing.
2. `.veridion/evidence.json` inside the Procta repo contains a non-empty `repository.modules` covering both the FastAPI backend and Electron frontend files.
3. Every branch currently in the Procta repo appears in `evidence.json` with staleness/cadence numbers — spot-check a few against `git -C /Users/arihantkaul/proctored-browser branch -a` and `git -C /Users/arihantkaul/proctored-browser log`.
4. `.veridion/audit-report.md` contains zero claims about files/functions/branches absent from `evidence.json` — spot-check 3-5 claims manually.
5. Re-run against the Veridion repo itself (`veridion audit /Users/arihantkaul/Documents/GitHub/Veridion`) — since `master` has commits but this check also covers the zero-commit case already tested in Task 5's `test_analyze_git_no_history_returns_unavailable`.

- [ ] **Step 4: Record the outcome**

If all five criteria pass, v1 is done — report back with any surprises (spot-checks that failed, evidence gaps that were larger than expected, sections of the manual the agent ignored). If any criterion fails, that's the next debugging task, not a new plan.

---

## Task 10: Fix ignored-dir coverage, `unparseable_files` semantics, and TypeScript/TSX support

**Context (found during Task 9's real dogfood run):** scanning Procta produced 666 modules
but **23,647** `unparseable_files` — almost all `.mypy_cache` JSON cache files, because
`IGNORED_DIRS` didn't cover common tool-cache/build-artifact directories, and because
`unparseable_files` treated *every* non-`.py`/`.js`/`.jsx` file as a coverage gap, including
files that were never source code (assets, docs, configs, lock files) rather than only files
that look like source in a language we don't support yet. Left as-is, the Part I/II manual
instructs the agent to always report `unparseable_files` — it would have produced a report
confidently claiming "23,647 files could not be analyzed," which is grounded-sounding but
false: those files were never meant to be analyzed. Separately, `.ts`/`.tsx` were never
registered in `graph.py`'s `LANGUAGE_BY_EXTENSION` despite `tree-sitter-typescript` already
being a declared dependency and the design spec calling for Python/JavaScript/TypeScript
coverage — it didn't affect Procta (which turned out to be plain JS, not TS) but would silently
under-cover any TypeScript codebase.

This task has been implemented and verified against both the synthetic fixtures below and a
live run against a real ~450k-line repository (numbers confirmed: `unparseable_files` dropped
from 23,647 to 3, and all 3 remaining are genuine C++/Swift header files with no grammar
available — an accurate signal instead of noise).

**Files:**
- Modify: `prototype/veridion/scanner/detect.py` (`IGNORED_DIRS`)
- Modify: `prototype/veridion/scanner/graph.py` (`IGNORED_DIRS` now imported from `detect.py`
  instead of duplicated; `LANGUAGE_BY_EXTENSION` gains `.ts`/`.tsx`; new
  `KNOWN_SOURCE_EXTENSIONS_WITHOUT_GRAMMAR` allowlist gates `unparseable_files`; import
  resolution now checks `.js`/`.jsx`/`.ts`/`.tsx` candidates instead of only `.js`)
- Test: `prototype/tests/test_detect.py` (append)
- Test: `prototype/tests/test_graph.py` (append)

**Interfaces:**
- Consumes: existing `detect_languages` (Task 3), `build_module_graph` (Task 4) — same function
  signatures, no callers elsewhere need to change.
- Produces: `IGNORED_DIRS` now lives only in `veridion.scanner.detect`; `veridion.scanner.graph`
  imports it from there rather than redefining it.

- [ ] **Step 1: Write failing tests**

Append to `prototype/tests/test_detect.py`:
```python
def test_detect_languages_ignores_cache_dirs(tmp_path):
    repo = tmp_path / "repo"
    cache = repo / ".mypy_cache" / "3.12"
    cache.mkdir(parents=True)
    for i in range(50):
        (cache / f"mod{i}.json").write_text("{}")
    (repo / "main.py").write_text("x = 1\n")
    languages = detect_languages(repo)
    by_name = {entry["name"]: entry for entry in languages}
    assert by_name["python"]["file_count"] == 1
```

Append to `prototype/tests/test_graph.py`:
```python
def test_build_module_graph_skips_non_source_files_silently(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("x = 1\n")
    (repo / "data.json").write_text("{}")
    (repo / "logo.png").write_bytes(b"\x89PNG")
    (repo / "notes.md").write_text("# hi")
    modules, _, unparseable = build_module_graph(repo)
    assert unparseable == []
    assert {m["path"] for m in modules} == {"main.py"}


def test_build_module_graph_ignores_cache_and_build_dirs(tmp_path):
    repo = tmp_path / "repo"
    cache = repo / ".mypy_cache" / "3.12"
    cache.mkdir(parents=True)
    (cache / "module.data.json").write_text("{}")
    (repo / "dist").mkdir()
    (repo / "dist" / "bundle.js").write_text("console.log(1)")
    (repo / "main.py").write_text("x = 1\n")
    modules, _, unparseable = build_module_graph(repo)
    assert unparseable == []
    assert {m["path"] for m in modules} == {"main.py"}


def test_build_module_graph_extracts_typescript_imports(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "utils.ts").write_text(
        "export function add(a: number, b: number): number { return a + b; }\n"
    )
    (repo / "index.ts").write_text(
        "import { add } from './utils';\n\nfunction main(): number { return add(1, 2); }\n"
    )
    modules, dependency_graph, unparseable = build_module_graph(repo)
    by_path = {m["path"]: m for m in modules}
    assert "index.ts" in by_path
    assert "utils.ts" in by_path["index.ts"]["imports"]
    assert "add" in by_path["utils.ts"]["symbols"]["functions"]
    assert unparseable == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
pytest tests/test_detect.py::test_detect_languages_ignores_cache_dirs tests/test_graph.py::test_build_module_graph_skips_non_source_files_silently tests/test_graph.py::test_build_module_graph_ignores_cache_and_build_dirs tests/test_graph.py::test_build_module_graph_extracts_typescript_imports -v
```
Expected: FAIL (`.mypy_cache` files counted, `.json`/`.png`/`.md` files show up in
`unparseable_files`, `.ts` files show up in `unparseable_files` instead of `modules`).

- [ ] **Step 3: Widen `IGNORED_DIRS` in `detect.py`**

In `prototype/veridion/scanner/detect.py`, replace:
```python
IGNORED_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".veridion"}
```
with:
```python
IGNORED_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", ".veridion",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".cache",
    "dist", "build", "out", "release", ".next", "coverage", "htmlcov",
}
```

- [ ] **Step 4: Update `graph.py` — shared `IGNORED_DIRS`, TypeScript/TSX, allowlisted `unparseable_files`**

In `prototype/veridion/scanner/graph.py`, replace the imports and constants block:
```python
from pathlib import Path

import tree_sitter_javascript as tsjavascript
import tree_sitter_python as tspython
from tree_sitter import Language, Node, Parser

IGNORED_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".veridion"}

PY_LANGUAGE = Language(tspython.language())
JS_LANGUAGE = Language(tsjavascript.language())

LANGUAGE_BY_EXTENSION = {
    ".py": ("python", PY_LANGUAGE),
    ".js": ("javascript", JS_LANGUAGE),
    ".jsx": ("javascript", JS_LANGUAGE),
}
```
with:
```python
from pathlib import Path

import tree_sitter_javascript as tsjavascript
import tree_sitter_python as tspython
import tree_sitter_typescript as tstypescript
from tree_sitter import Language, Node, Parser

from veridion.scanner.detect import IGNORED_DIRS

PY_LANGUAGE = Language(tspython.language())
JS_LANGUAGE = Language(tsjavascript.language())
TS_LANGUAGE = Language(tstypescript.language_typescript())
TSX_LANGUAGE = Language(tstypescript.language_tsx())

LANGUAGE_BY_EXTENSION = {
    ".py": ("python", PY_LANGUAGE),
    ".js": ("javascript", JS_LANGUAGE),
    ".jsx": ("javascript", JS_LANGUAGE),
    ".ts": ("typescript", TS_LANGUAGE),
    ".tsx": ("typescript", TSX_LANGUAGE),
}

# Extensions that are recognizable programming languages we don't yet have a grammar
# for. Only these count as "unparseable" coverage gaps. Everything else (assets, docs,
# configs, lock files, tool caches not already excluded by IGNORED_DIRS) was never
# source code and is skipped silently rather than reported as a gap - otherwise
# unparseable_files balloons with noise (a real repo scan turned up 19k+ .json files
# from an untracked cache directory before IGNORED_DIRS was widened, none of which
# were ever "unparseable source").
KNOWN_SOURCE_EXTENSIONS_WITHOUT_GRAMMAR = {
    ".swift", ".go", ".rs", ".java", ".rb", ".c", ".cpp", ".cc", ".h", ".hpp",
    ".cs", ".kt", ".kts", ".m", ".mm", ".scala", ".php",
}
```

Replace the `_resolve_js_import` function:
```python
def _resolve_js_import(repo_path: Path, from_file: Path, spec: str) -> str | None:
    if not spec.startswith("."):
        return None
    base = (from_file.parent / spec).resolve()
    for candidate in (base, base.with_suffix(".js"), base / "index.js"):
        if candidate.exists() and candidate.is_file():
            try:
                return _rel(repo_path, candidate)
            except ValueError:
                return None
    return None
```
with:
```python
JS_FAMILY_EXTENSIONS = (".js", ".jsx", ".ts", ".tsx")


def _resolve_js_import(repo_path: Path, from_file: Path, spec: str) -> str | None:
    if not spec.startswith("."):
        return None
    base = (from_file.parent / spec).resolve()
    candidates = [base]
    for ext in JS_FAMILY_EXTENSIONS:
        candidates.append(base.with_suffix(ext))
        candidates.append(base / f"index{ext}")
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            try:
                return _rel(repo_path, candidate)
            except ValueError:
                return None
    return None
```

In `build_module_graph`, replace:
```python
        language_info = LANGUAGE_BY_EXTENSION.get(path.suffix)
        if language_info is None:
            unparseable.append(
                {"path": rel_path, "reason": f"no grammar registered for {path.suffix}"}
            )
            continue
```
with:
```python
        language_info = LANGUAGE_BY_EXTENSION.get(path.suffix)
        if language_info is None:
            if path.suffix in KNOWN_SOURCE_EXTENSIONS_WITHOUT_GRAMMAR:
                unparseable.append(
                    {"path": rel_path, "reason": f"no grammar registered for {path.suffix}"}
                )
            continue
```

- [ ] **Step 5: Run tests to verify they pass**

Run:
```bash
pytest -v
```
Expected: PASS (39 tests total — the original 35 plus the 4 added in this task). The existing
`test_build_module_graph_records_unparseable_files` test (using a `.swift` file) still passes
unchanged, since `.swift` is in `KNOWN_SOURCE_EXTENSIONS_WITHOUT_GRAMMAR`.

- [ ] **Step 6: Re-run the deterministic scan against Procta and confirm the fix**

```bash
veridion audit /Users/arihantkaul/proctored-browser --agent nonexistent-placeholder
```
(This intentionally forces `NoAdapterAvailableError` so only the scan phase runs — no live
agent call. `.veridion/evidence.json` is still written before the error.) Confirm
`repository.unparseable_files` is now small (single digits to low tens, not thousands) and
every remaining entry has a `.reason` naming a real, unsupported programming-language
extension — not a config/asset/cache file.

- [ ] **Step 7: Commit**

```bash
git add prototype/veridion/scanner/detect.py prototype/veridion/scanner/graph.py
git add prototype/tests/test_detect.py prototype/tests/test_graph.py
git commit -m "fix: widen ignored dirs, gate unparseable_files to real source extensions, add TS/TSX support"
```

## Task 11: Fix reasoning-phase report clobbering (completed, retrospective)

Found by actually running Task 9's dogfood check end-to-end against Veridion's own repo: the
first live `veridion audit .` run produced a 3-line `audit-report.md` instead of a real report.
Root cause: `build_instruction` tells the agent to write the report file itself, which Claude
Code correctly did with its own file tools — but `run_reasoning_phase` then unconditionally
overwrote that same path with `adapter.invoke()`'s stdout return value (the agent's short
wrap-up message, not the report), destroying the real report immediately after it was written.
No mocked-adapter unit test could have caught this, since a mock never exercises "the agent
both writes a file and returns separate stdout text."

Fix: track the report file's mtime across the `invoke()` call; only fall back to writing
stdout as the report if the agent didn't write the file itself during that call. Verified with
a second live run against Veridion's own repo: 100-line report, every claim citing a specific
`evidence.json` field, and every spot-checked claim (function counts, commit counts, branch
counts) exactly correct.

Files changed: `prototype/veridion/report.py`, `prototype/tests/test_cli.py`.
Commit: `f8f2431` ("fix: stop clobbering agent-written audit reports with adapter stdout").

## Task 12: Real git ahead/behind, email-based ownership, partial-week flag (completed, retrospective)

Found by reading the actual audit report generated against Procta: it explicitly hedged on
three things instead of stating them as facts. Investigation confirmed all three were real,
fixable gaps rather than genuine evidence limits:

- `ahead_of_main`/`behind_main` were hardcoded to `0` for every branch — never computed. Fixed
  via `git rev-list --left-right --count <default-branch>...<branch>`, with the default branch
  detected from `refs/remotes/origin/HEAD` (falling back to the current checked-out branch).
- `ownership` grouped by raw author display name (`%an`), so the same person under two git
  configs ("Arihant" vs "Arihant Kaul", same email, different capitalization on Procta) showed
  as two separate identities. Fixed by grouping on lowercased author email instead, retaining
  the observed display names per email as a `names` list.
- `commit_cadence` gave no way to tell whether its most recent weekly bucket was a complete
  week, so a low final-week count was unfalsifiable as a "slowdown" vs. "week still in
  progress." Added `most_recent_week_partial`, computed from days elapsed since that bucket
  started relative to `now`.

Verified against both real repos: Procta's ownership collapsed to a directly-computed 98.18%
(previously a hedged "if these are the same person" guess), and 162/163 branches showed real
ahead/behind divergence, surfacing 12 branches with genuine unmerged work and one concrete,
independently-verified finding (local `main` was 7 commits ahead of `origin/main` — real
unpushed work, confirmed via `git rev-list --count origin/main..main`).

Files changed: `prototype/veridion/git_intel/analyzer.py`, `prototype/tests/test_git_intel.py`,
`prototype/manual/part-3-git-intelligence.md` (updated to tell the agent these fields are now
reliable signals, not hedges).
Commits: `bd25632` ("feat: compute real ahead/behind, merge ownership by email, flag partial
weeks"), `59e6663` ("docs: update Part III manual...").

## Task 13: `veridion scan` and `veridion query` subcommands

**Motivation:** `evidence.json`'s `repository.modules` (with `imports`/`imported_by`) and
`repository.dependency_graph` already contain a full whole-repo connectivity graph — this was
built in Task 4 for the audit report, but nothing lets an agent query it directly. Right now,
answering "what does `auth.py` import" or "what imports `auth.py`" requires either grepping the
repo or reading the entire `evidence.json`. Two commands close this gap:

- `veridion scan [path]` — runs only the deterministic scan phase (no agent invocation) and
  writes `evidence.json`. This already exists as *behavior* (`_audit` does it as step one) but
  has no standalone entrypoint — today, getting a fresh `evidence.json` without triggering a
  live agent call requires the `--agent nonexistent-placeholder` workaround used during Task 9's
  dogfooding. `query` depends on this having its own real command.
- `veridion query <imports|imported-by|symbols> <file> [--path REPO]` — reads the existing
  `evidence.json` (does not re-scan) and looks up one module's data directly. Errors clearly if
  no `evidence.json` exists yet, pointing at `veridion scan`.

This is deliberately **not** a live/always-fresh index (no MCP server, no file-watching, no
re-indexing on edit) — it answers from whatever `evidence.json` was last written, which is
exact as of that scan and stale after. That tradeoff is fine for session-start context-loading
(an agent runs `veridion scan` once, then queries cheaply many times) but would be wrong to
rely on for verifying a change made *during* the same session — the manual and any tooling
built on top of this must not claim otherwise.

**Files:**
- Create: `prototype/veridion/query.py`
- Modify: `prototype/veridion/cli.py` (add `scan` and `query` subcommands)
- Test: `prototype/tests/test_query.py`
- Test: `prototype/tests/test_cli.py` (append — new subcommand wiring)

**Interfaces:**
- Consumes: `evidence.json`'s existing schema (`repository.modules[].path/imports/imported_by/symbols`)
  from Task 6 — no schema changes.
- Produces: `find_imports(evidence: dict, file_path: str) -> list[str]`,
  `find_imported_by(evidence: dict, file_path: str) -> list[str]`,
  `find_symbols(evidence: dict, file_path: str) -> dict` in `veridion/query.py`, each raising
  `ModuleNotFoundInEvidenceError(file_path)` (defined in the same module) if `file_path` isn't
  a key in `repository.modules`.

- [ ] **Step 1: Write the failing tests for `query.py`**

Create `prototype/tests/test_query.py`:
```python
import pytest

from veridion.query import (
    ModuleNotFoundInEvidenceError,
    find_imported_by,
    find_imports,
    find_symbols,
)


def make_evidence():
    return {
        "repository": {
            "modules": [
                {
                    "path": "app/auth.py",
                    "language": "python",
                    "imports": ["app/config.py"],
                    "imported_by": ["app/routes.py"],
                    "symbols": {"functions": ["login"], "classes": ["AuthError"]},
                },
                {
                    "path": "app/config.py",
                    "language": "python",
                    "imports": [],
                    "imported_by": ["app/auth.py"],
                    "symbols": {"functions": ["load"], "classes": []},
                },
            ]
        }
    }


def test_find_imports_returns_the_module_imports_list():
    evidence = make_evidence()
    assert find_imports(evidence, "app/auth.py") == ["app/config.py"]


def test_find_imported_by_returns_the_module_imported_by_list():
    evidence = make_evidence()
    assert find_imported_by(evidence, "app/config.py") == ["app/auth.py"]


def test_find_symbols_returns_the_module_symbols_dict():
    evidence = make_evidence()
    assert find_symbols(evidence, "app/auth.py") == {
        "functions": ["login"],
        "classes": ["AuthError"],
    }


def test_find_imports_raises_for_unknown_path():
    evidence = make_evidence()
    with pytest.raises(ModuleNotFoundInEvidenceError):
        find_imports(evidence, "app/does_not_exist.py")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_query.py -v
```
Expected: FAIL (`veridion.query` doesn't exist yet — `ModuleNotFoundError`).

- [ ] **Step 3: Implement `query.py`**

Create `prototype/veridion/query.py`:
```python
class ModuleNotFoundInEvidenceError(Exception):
    def __init__(self, file_path: str):
        super().__init__(f"'{file_path}' is not present in evidence.repository.modules")
        self.file_path = file_path


def _find_module(evidence: dict, file_path: str) -> dict:
    for module in evidence["repository"]["modules"]:
        if module["path"] == file_path:
            return module
    raise ModuleNotFoundInEvidenceError(file_path)


def find_imports(evidence: dict, file_path: str) -> list[str]:
    return _find_module(evidence, file_path)["imports"]


def find_imported_by(evidence: dict, file_path: str) -> list[str]:
    return _find_module(evidence, file_path)["imported_by"]


def find_symbols(evidence: dict, file_path: str) -> dict:
    return _find_module(evidence, file_path)["symbols"]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_query.py -v
```
Expected: PASS (4 tests).

- [ ] **Step 5: Write the failing CLI tests**

Append to `prototype/tests/test_cli.py`:
```python
def test_main_scan_only_runs_scan_phase(tmp_path, monkeypatch, capsys):
    repo = tmp_path
    (repo / "main.py").write_text("x = 1\n")
    monkeypatch.setattr(sys, "argv", ["veridion", "scan", str(repo)])

    exit_code = main()

    assert exit_code == 0
    assert (repo / ".veridion" / "evidence.json").exists()
    captured = capsys.readouterr()
    assert "audit-report.md" not in captured.out


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


def test_main_query_without_evidence_errors_clearly(tmp_path, monkeypatch, capsys):
    repo = tmp_path
    monkeypatch.setattr(
        sys, "argv", ["veridion", "query", "imports", "app/auth.py", "--path", str(repo)]
    )

    exit_code = main()

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "veridion scan" in captured.out
```

Add `import sys` and `from veridion.cli import main` to the test file's imports if not already
present (check the existing `test_main_requires_a_command`/`test_main_audit_invokes_audit_flow`
tests — they already import `main`, so only `sys` may need adding).

- [ ] **Step 6: Run CLI tests to verify they fail**

```bash
pytest tests/test_cli.py -v
```
Expected: FAIL (`scan` and `query` are not recognized subcommands yet).

- [ ] **Step 7: Wire `scan` and `query` into `cli.py`**

In `prototype/veridion/cli.py`, add a `_scan` function (factor the existing scan-phase lines
out of `_audit` so both commands share it), a `_query` function, and register both
subparsers:

```python
import json

from veridion.query import ModuleNotFoundInEvidenceError, find_imported_by, find_imports, find_symbols


def _scan(repo_path: str) -> tuple[int, dict, Path]:
    repo = Path(repo_path).resolve()
    print(f"Scanning {repo}...")
    evidence = scan_repository(repo)
    evidence_path = write_evidence(evidence, repo)
    print(f"Evidence written to {evidence_path}")
    return 0, evidence, evidence_path


def _audit(repo_path: str, forced_agent: str | None) -> int:
    _, evidence, evidence_path = _scan(repo_path)
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


QUERY_FUNCTIONS = {
    "imports": find_imports,
    "imported-by": find_imported_by,
    "symbols": find_symbols,
}


def _query(kind: str, file_path: str, repo_path: str) -> int:
    repo = Path(repo_path).resolve()
    evidence_path = repo / ".veridion" / "evidence.json"
    if not evidence_path.exists():
        print(f"error: no evidence found at {evidence_path}")
        print(f"Run 'veridion scan {repo}' first.")
        return 1

    evidence = json.loads(evidence_path.read_text())
    try:
        result = QUERY_FUNCTIONS[kind](evidence, file_path)
    except ModuleNotFoundInEvidenceError as exc:
        print(f"error: {exc}")
        return 1

    print(json.dumps(result, indent=2))
    return 0
```

Update `main()`'s subparser registration:
```python
    scan_parser = subparsers.add_parser("scan", help="run only the deterministic scan phase")
    scan_parser.add_argument("path", nargs="?", default=".")

    query_parser = subparsers.add_parser("query", help="query an existing evidence.json")
    query_parser.add_argument("kind", choices=list(QUERY_FUNCTIONS.keys()))
    query_parser.add_argument("file_path")
    query_parser.add_argument("--path", dest="repo_path", default=".")
```

And in the dispatch block:
```python
    if args.command == "audit":
        return _audit(args.path, args.agent)
    if args.command == "scan":
        exit_code, _, _ = _scan(args.path)
        return exit_code
    if args.command == "query":
        return _query(args.kind, args.file_path, args.repo_path)
```

- [ ] **Step 8: Run tests to verify they pass**

```bash
pytest -v
```
Expected: PASS (all tests, including the 4 new `test_query.py` tests and 3 new CLI tests).

- [ ] **Step 9: Verify against a real repo**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion
veridion scan .
veridion query imports prototype/veridion/cli.py
veridion query imported-by prototype/veridion/report.py
```
Confirm the output matches what's actually in `.veridion/evidence.json` for those paths (spot
check with `python3 -c "import json; ..."` the same way prior tasks were verified) and that no
live agent call happens (no `claude` invocation, no `audit-report.md` written/touched).

- [ ] **Step 10: Commit**

```bash
git add prototype/veridion/query.py prototype/veridion/cli.py
git add prototype/tests/test_query.py prototype/tests/test_cli.py
git commit -m "feat: add veridion scan and veridion query subcommands"
```

## Self-Review Notes

- **Spec coverage:** Task 1 (scaffolding) → Task 2 (Part I-III manual, spec's "Manual content" section) → Task 3 (`detect.py`, spec's Part II tech/framework/build/monorepo detection) → Task 4 (`graph.py`, spec's module/dependency graph) → Task 5 (`analyzer.py`, spec's Part III git intelligence, including the `git.available: false` error-handling case) → Task 6 (`evidence.py`, spec's full JSON schema and file-write behavior) → Task 7 (adapter interface + Claude Code adapter, spec's reasoning-phase architecture and "no adapter found" / invocation-failure error handling) → Task 8 (CLI wiring, spec's "multiple adapters found" interactive-prompt and non-interactive `--agent` flag requirement) → Task 9 (spec's Success Criteria section, run as a manual acceptance gate per the spec's Testing Strategy). All Non-Goals from the spec (Parts IV/V/VI-X, multi-adapter support beyond Claude Code, hosted/SaaS, scored git outputs) have no corresponding task, by design.
- **Placeholder scan:** no TBDs; every step has complete, runnable code.
- **Type consistency:** `scan_repository` (Task 6) calls `detect_languages`/`detect_frameworks`/`detect_build_tools`/`detect_monorepo` (Task 3) and `build_module_graph` (Task 4) and `analyze_git` (Task 5) using the exact function names and return shapes defined in those tasks' Interfaces blocks. `cli.py` (Task 8) calls `scan_repository`/`write_evidence` (Task 6) and `select_adapter`/`run_reasoning_phase` (Task 8) and `ClaudeCodeAdapter`/`AdapterInvocationError` (Task 7) using the exact names defined there.
