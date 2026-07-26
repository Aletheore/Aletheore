# Aletheore PR-Review Benchmark Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the scripts and case corpus for the public PR-review benchmark defined in `docs/superpowers/specs/2026-07-26-aletheore-pr-review-benchmark-design.md`, comparing Aletheore against Qodo/PR-Agent and DeepSource (named) and CodeRabbit (anonymized).

**Architecture:** A new `benchmarks/pr-review-benchmark/` directory, separate from the shipped `aletheore` package. Small, single-purpose Python modules under `scripts/` (case loading, checkout preparation, per-tool normalization, automated citation/grounding checks, blind anonymization, LLM judging, aggregation) — each a plain function over dicts, matching `src/aletheore/citation_verifier.py`'s existing style, each with its own pytest file. `run_case.py` wires these together per case; live invocation of each external tool (creating throwaway GitHub PRs, installing CodeRabbit's app, calling PR-Agent/DeepSource/an LLM judge) is manual/integration work documented in a README runbook, not unit-tested — everything upstream of that boundary (checkout construction, normalization, grounding checks, anonymization, aggregation) is pure and fully covered by tests.

**Tech Stack:** Python 3.11+, pytest, PyYAML, the already-installed `aletheore` package (editable install from `src/`), `openai` client for the LLM judge (a different model provider than whichever powers the tools under test, per the spec's independence requirement).

## Global Constraints

- Python 3.11+ (matches `src/pyproject.toml:9`'s `requires-python`).
- Tests use plain `assert` + pytest, no `pytest-randomly` reordering (`src/pyproject.toml:70`: `addopts = ["-p", "no:randomly"]`) — mirror that in this directory's test runs too.
- Reuse `aletheore.citation_verifier.extract_citations` (`src/aletheore/citation_verifier.py:17`) rather than re-implementing citation parsing.
- Structured data (ground truth, scoring sheets, sealed mappings) is YAML or JSON, never free-text parsing of prose — `pyyaml` is already a dependency (`src/pyproject.toml:42`).
- CodeRabbit's raw output is never committed to the repo in any form — only its anonymized, scored summary. Enforced by directory convention (`results/raw/` excluded from git for the CodeRabbit entry specifically — see Task 9).
- The sealed tool↔label mapping for a case is never opened until that case's manual and LLM scoring are both recorded.
- The LLM judge model must be a different provider/family than whichever model powers the tools under test in a given run (recorded in `METHODOLOGY.md`).
- Every module in `scripts/` is a plain function over dicts/paths — no new classes, dataclasses, or frameworks introduced where a function suffices, matching `citation_verifier.py`'s existing shape.

---

## File Structure

```
benchmarks/pr-review-benchmark/
  README.md                  # runbook: how to author a case, run the live tools, score, aggregate
  scripts/
    cases.py                 # load/validate a case directory
    build_case_repo.py       # clone + checkout + apply pr.diff -> local checkout
    adapters.py               # real per-tool invocation (subprocess/API), injectable for tests
    normalize.py              # per-tool raw output -> common finding schema
    check_citations.py        # automated grounding check against the real checkout
    anonymize.py               # blind relabeling + sealed mapping + reveal
    scoring_template.py        # blank manual/LLM scoring sheet generator
    llm_judge.py                # independent LLM scoring pass
    aggregate.py                 # cross-case scorecard + human/LLM agreement rate
    run_case.py                   # orchestrates one case end-to-end
  tests/
    test_cases.py
    test_build_case_repo.py
    test_adapters.py
    test_normalize.py
    test_check_citations.py
    test_anonymize.py
    test_scoring_template.py
    test_llm_judge.py
    test_aggregate.py
    test_run_case.py
  cases/
    <case-id>/
      repo.txt                # repo_url=..., base_commit=..., optional pr_url=...
      pr.diff
      ground_truth.yaml        # structured, machine-readable ground truth
      ground_truth.md           # prose version for the published report
  results/                       # gitignored except the final anonymized/aggregated artifacts
    raw/<case-id>/<tool>.json      # CodeRabbit's excluded from git, see Task 9
    grounding/<case-id>/<tool>.json
    anon/<case-id>/<label>.json
    sealed/<case-id>.json
    scored/<case-id>.yaml
    llm_judged/<case-id>.json
  REPORT.md
  METHODOLOGY.md
```

---

### Task 1: Case schema — load and validate a test case directory

**Files:**
- Create: `benchmarks/pr-review-benchmark/scripts/cases.py`
- Test: `benchmarks/pr-review-benchmark/tests/test_cases.py`

**Interfaces:**
- Produces: `load_repo_pointer(case_dir: Path) -> dict` (keys: `repo_url`, `base_commit`, optionally `pr_url`, `deepsource_run_id`); `load_ground_truth(case_dir: Path) -> dict` (keys: `case_id`, `language`, `category` one of `real_bug_fix`/`injected_bug`/`clean`, `bug_type`, `expected_file`, `expected_line`, `fix_reference`, `description`); `load_case(case_dir: Path) -> dict` (keys: `case_id`, `repo`, `diff_path`, `ground_truth`) — later tasks (`build_case_repo.py`, `run_case.py`) consume exactly this shape.

- [ ] **Step 1: Write the failing tests**

```python
# benchmarks/pr-review-benchmark/tests/test_cases.py
import pytest
from scripts.cases import load_case, load_repo_pointer, load_ground_truth


def make_case(tmp_path, category="injected_bug"):
    case_dir = tmp_path / "001-example"
    case_dir.mkdir()
    (case_dir / "repo.txt").write_text(
        "repo_url=https://example.com/repo.git\nbase_commit=abc123\n"
    )
    (case_dir / "pr.diff").write_text("diff --git a/x.py b/x.py\n")
    (case_dir / "ground_truth.yaml").write_text(
        "case_id: 001-example\n"
        "language: python\n"
        f"category: {category}\n"
        "bug_type: sql-injection\n"
        "expected_file: x.py\n"
        "expected_line: 10\n"
        "fix_reference: null\n"
        "description: test case\n"
    )
    return case_dir


def test_load_repo_pointer_parses_key_value_pairs(tmp_path):
    case_dir = make_case(tmp_path)
    pointer = load_repo_pointer(case_dir)
    assert pointer == {"repo_url": "https://example.com/repo.git", "base_commit": "abc123"}


def test_load_repo_pointer_requires_repo_url_and_base_commit(tmp_path):
    case_dir = tmp_path / "002-bad"
    case_dir.mkdir()
    (case_dir / "repo.txt").write_text("repo_url=https://example.com/repo.git\n")
    with pytest.raises(ValueError, match="repo_url and base_commit"):
        load_repo_pointer(case_dir)


def test_load_ground_truth_rejects_unknown_category(tmp_path):
    case_dir = make_case(tmp_path, category="not_a_real_category")
    with pytest.raises(ValueError, match="category must be one of"):
        load_ground_truth(case_dir)


def test_load_case_returns_combined_case_dict(tmp_path):
    case_dir = make_case(tmp_path)
    case = load_case(case_dir)
    assert case["case_id"] == "001-example"
    assert case["repo"]["base_commit"] == "abc123"
    assert case["ground_truth"]["category"] == "injected_bug"
    assert case["diff_path"] == case_dir / "pr.diff"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd benchmarks/pr-review-benchmark && pytest tests/test_cases.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.cases'` (or `scripts`).

- [ ] **Step 3: Write minimal implementation**

```python
# benchmarks/pr-review-benchmark/scripts/cases.py
"""Loads and validates a benchmark test case directory."""
from pathlib import Path
import yaml

VALID_CATEGORIES = {"real_bug_fix", "injected_bug", "clean"}


def load_repo_pointer(case_dir: Path) -> dict:
    text = (Path(case_dir) / "repo.txt").read_text()
    pointer = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        key, _, value = line.partition("=")
        pointer[key.strip()] = value.strip()
    if "repo_url" not in pointer or "base_commit" not in pointer:
        raise ValueError(f"{case_dir}/repo.txt must define repo_url and base_commit")
    return pointer


def load_ground_truth(case_dir: Path) -> dict:
    data = yaml.safe_load((Path(case_dir) / "ground_truth.yaml").read_text())
    if data.get("category") not in VALID_CATEGORIES:
        raise ValueError(
            f"{case_dir}/ground_truth.yaml: category must be one of {VALID_CATEGORIES}"
        )
    return data


def load_case(case_dir: Path) -> dict:
    case_dir = Path(case_dir)
    return {
        "case_id": case_dir.name,
        "repo": load_repo_pointer(case_dir),
        "diff_path": case_dir / "pr.diff",
        "ground_truth": load_ground_truth(case_dir),
    }
```

Also create an empty `benchmarks/pr-review-benchmark/scripts/__init__.py` and `benchmarks/pr-review-benchmark/tests/__init__.py`, and a `benchmarks/pr-review-benchmark/pytest.ini`:

```ini
[pytest]
addopts = -p no:randomly
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd benchmarks/pr-review-benchmark && pytest tests/test_cases.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add benchmarks/pr-review-benchmark/scripts/cases.py \
        benchmarks/pr-review-benchmark/scripts/__init__.py \
        benchmarks/pr-review-benchmark/tests/test_cases.py \
        benchmarks/pr-review-benchmark/tests/__init__.py \
        benchmarks/pr-review-benchmark/pytest.ini
git commit -m "feat(benchmark): add case schema loader"
```

---

### Task 2: Case checkout builder

**Files:**
- Create: `benchmarks/pr-review-benchmark/scripts/build_case_repo.py`
- Test: `benchmarks/pr-review-benchmark/tests/test_build_case_repo.py`

**Interfaces:**
- Consumes: `repo_pointer` dict shape from Task 1 (`repo_url`, `base_commit`).
- Produces: `prepare_case_checkout(repo_pointer: dict, diff_path: Path, workdir: Path) -> Path` — returns the checkout directory with `pr.diff` already applied on top of `base_commit`. Consumed by `run_case.py` (Task 9) and `check_citations.py`'s callers.

- [ ] **Step 1: Write the failing test**

```python
# benchmarks/pr-review-benchmark/tests/test_build_case_repo.py
import subprocess
from scripts.build_case_repo import prepare_case_checkout


def _run(*args, cwd=None):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


def make_fixture_repo(tmp_path):
    remote = tmp_path / "remote.git"
    work = tmp_path / "seed"
    work.mkdir()
    _run("git", "init", cwd=work)
    _run("git", "config", "user.email", "test@example.com", cwd=work)
    _run("git", "config", "user.name", "Test", cwd=work)
    (work / "x.py").write_text("value = 1\n")
    _run("git", "add", "x.py", cwd=work)
    _run("git", "commit", "-m", "base", cwd=work)
    base_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=work, check=True, capture_output=True, text=True
    ).stdout.strip()

    (work / "x.py").write_text("value = 2\n")
    _run("git", "add", "x.py", cwd=work)
    _run("git", "commit", "-m", "change value", cwd=work)

    diff_path = tmp_path / "pr.diff"
    diff = subprocess.run(
        ["git", "diff", base_commit, "HEAD"], cwd=work, check=True, capture_output=True, text=True
    ).stdout
    diff_path.write_text(diff)

    _run("git", "clone", "--bare", str(work), str(remote))
    return remote, base_commit, diff_path


def test_prepare_case_checkout_applies_pr_diff_on_base_commit(tmp_path):
    remote, base_commit, diff_path = make_fixture_repo(tmp_path)
    workdir = tmp_path / "work"
    workdir.mkdir()

    checkout_dir = prepare_case_checkout(
        {"repo_url": str(remote), "base_commit": base_commit}, diff_path, workdir
    )

    assert (checkout_dir / "x.py").read_text() == "value = 2\n"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd benchmarks/pr-review-benchmark && pytest tests/test_build_case_repo.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# benchmarks/pr-review-benchmark/scripts/build_case_repo.py
"""Prepares a local checkout of a test case's PR-under-test: clone the
repo, check out base_commit, and apply pr.diff on top."""
import subprocess
from pathlib import Path


def prepare_case_checkout(repo_pointer: dict, diff_path: Path, workdir: Path) -> Path:
    checkout_dir = Path(workdir) / "checkout"
    subprocess.run(
        ["git", "clone", repo_pointer["repo_url"], str(checkout_dir)],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "checkout", repo_pointer["base_commit"]],
        cwd=checkout_dir, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "apply", str(diff_path)],
        cwd=checkout_dir, check=True, capture_output=True,
    )
    return checkout_dir
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd benchmarks/pr-review-benchmark && pytest tests/test_build_case_repo.py -v`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add benchmarks/pr-review-benchmark/scripts/build_case_repo.py \
        benchmarks/pr-review-benchmark/tests/test_build_case_repo.py
git commit -m "feat(benchmark): add case checkout builder"
```

---

### Task 3: Per-tool output normalizers

**Files:**
- Create: `benchmarks/pr-review-benchmark/scripts/normalize.py`
- Test: `benchmarks/pr-review-benchmark/tests/test_normalize.py`

**Interfaces:**
- Consumes: `aletheore.citation_verifier.extract_citations` (existing, `src/aletheore/citation_verifier.py:17`).
- Produces: `normalize_aletheore(report_text: str) -> list[dict]`, `normalize_pr_agent(raw: dict) -> list[dict]`, `normalize_deepsource(raw: dict) -> list[dict]`, `normalize_coderabbit(raw_comments: list[dict]) -> list[dict]` — each returns the common finding schema `{"file": str|None, "line": int|None, "message": str, "severity": str|None}`, consumed by `check_citations.py` (Task 4) and `run_case.py` (Task 9).

**Note on assumed schemas:** `normalize_pr_agent` assumes PR-Agent's `--pr_url ... review` JSON output has a top-level `code_suggestions` list with `relevant_file`/`relevant_line`/`suggestion_content`/`label` keys, and `normalize_deepsource` assumes an `issues` list with `location.path`/`location.position.begin.line`/`title`/`severity`. These are documented assumptions based on each tool's public docs, not verified against a live run yet — **Task 9's live data-collection step must confirm the actual shape and adjust these two functions (and their tests) if the real output differs.** `normalize_coderabbit` is lower-risk: it consumes the GitHub REST API's PR review-comment shape (`path`, `line`/`original_line`, `body`), which is stable regardless of which bot posted the comment.

- [ ] **Step 1: Write the failing tests**

```python
# benchmarks/pr-review-benchmark/tests/test_normalize.py
from scripts.normalize import (
    normalize_aletheore,
    normalize_pr_agent,
    normalize_deepsource,
    normalize_coderabbit,
)


def test_normalize_aletheore_extracts_citation_and_paragraph_as_message():
    report = (
        "This endpoint has no auth check at `app/routes.py:42`, which allows "
        "unauthenticated access.\n\n"
        "Unrelated paragraph with no citation."
    )
    findings = normalize_aletheore(report)
    assert findings == [{
        "file": "app/routes.py",
        "line": 42,
        "message": (
            "This endpoint has no auth check at `app/routes.py:42`, which allows "
            "unauthenticated access."
        ),
        "severity": None,
    }]


def test_normalize_pr_agent_reads_code_suggestions():
    raw = {
        "code_suggestions": [
            {
                "relevant_file": "app.py",
                "relevant_line": 10,
                "suggestion_content": "Use a parameterized query here.",
                "label": "possible bug",
            }
        ]
    }
    findings = normalize_pr_agent(raw)
    assert findings == [{
        "file": "app.py",
        "line": 10,
        "message": "Use a parameterized query here.",
        "severity": "possible bug",
    }]


def test_normalize_deepsource_reads_issues():
    raw = {
        "issues": [
            {
                "title": "Unused import",
                "severity": "minor",
                "location": {"path": "app.py", "position": {"begin": {"line": 3}}},
            }
        ]
    }
    findings = normalize_deepsource(raw)
    assert findings == [{
        "file": "app.py",
        "line": 3,
        "message": "Unused import",
        "severity": "minor",
    }]


def test_normalize_coderabbit_reads_github_review_comments():
    raw_comments = [{"path": "app.py", "line": 5, "body": "Missing null check."}]
    findings = normalize_coderabbit(raw_comments)
    assert findings == [{
        "file": "app.py",
        "line": 5,
        "message": "Missing null check.",
        "severity": None,
    }]


def test_normalize_coderabbit_falls_back_to_original_line():
    raw_comments = [{"path": "app.py", "original_line": 9, "body": "Stale comment."}]
    findings = normalize_coderabbit(raw_comments)
    assert findings[0]["line"] == 9
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd benchmarks/pr-review-benchmark && pytest tests/test_normalize.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# benchmarks/pr-review-benchmark/scripts/normalize.py
"""Normalizes each tool's raw output into a common finding schema:
{"file": str|None, "line": int|None, "message": str, "severity": str|None}."""
from aletheore.citation_verifier import extract_citations


def normalize_aletheore(report_text: str) -> list[dict]:
    findings = []
    for paragraph in report_text.split("\n\n"):
        for citation in extract_citations(paragraph):
            findings.append({
                "file": citation["file"],
                "line": citation["line"],
                "message": paragraph.strip(),
                "severity": None,
            })
    return findings


def normalize_pr_agent(raw: dict) -> list[dict]:
    return [
        {
            "file": suggestion.get("relevant_file"),
            "line": suggestion.get("relevant_line"),
            "message": suggestion.get("suggestion_content", ""),
            "severity": suggestion.get("label"),
        }
        for suggestion in raw.get("code_suggestions", [])
    ]


def normalize_deepsource(raw: dict) -> list[dict]:
    findings = []
    for issue in raw.get("issues", []):
        location = issue.get("location", {})
        findings.append({
            "file": location.get("path"),
            "line": location.get("position", {}).get("begin", {}).get("line"),
            "message": issue.get("title", ""),
            "severity": issue.get("severity"),
        })
    return findings


def normalize_coderabbit(raw_comments: list[dict]) -> list[dict]:
    return [
        {
            "file": comment.get("path"),
            "line": comment.get("line") or comment.get("original_line"),
            "message": comment.get("body", ""),
            "severity": None,
        }
        for comment in raw_comments
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd benchmarks/pr-review-benchmark && pytest tests/test_normalize.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add benchmarks/pr-review-benchmark/scripts/normalize.py \
        benchmarks/pr-review-benchmark/tests/test_normalize.py
git commit -m "feat(benchmark): add per-tool output normalizers"
```

---

### Task 4: Automated grounding/citation check against the real checkout

**Files:**
- Create: `benchmarks/pr-review-benchmark/scripts/check_citations.py`
- Test: `benchmarks/pr-review-benchmark/tests/test_check_citations.py`

**Interfaces:**
- Consumes: common finding schema from Task 3.
- Produces: `verify_findings_against_checkout(findings: list[dict], checkout_dir: Path) -> dict` (keys: `total_findings`, `verified`, `unverified`, `grounding_rate`) — consumed by `run_case.py` (Task 9) and the report-building step (Task 11).

This is deliberately **not** blinded — it's a fully automated, deterministic check with no human judgment involved, so there's no bias it needs protecting against (unlike Task 6/7's manual and LLM scoring, which score subjective recall/actionability and must stay blind). It runs on real tool names directly.

- [ ] **Step 1: Write the failing tests**

```python
# benchmarks/pr-review-benchmark/tests/test_check_citations.py
from scripts.check_citations import verify_findings_against_checkout


def make_checkout(tmp_path):
    (tmp_path / "app.py").write_text("line1\nline2\nline3\n")
    return tmp_path


def test_verify_findings_marks_valid_file_and_line_as_verified(tmp_path):
    checkout = make_checkout(tmp_path)
    findings = [{"file": "app.py", "line": 2, "message": "ok", "severity": None}]
    result = verify_findings_against_checkout(findings, checkout)
    assert result == {
        "total_findings": 1,
        "verified": findings,
        "unverified": [],
        "grounding_rate": 1.0,
    }


def test_verify_findings_marks_missing_file_as_unverified(tmp_path):
    checkout = make_checkout(tmp_path)
    findings = [{"file": "ghost.py", "line": 1, "message": "hallucinated", "severity": None}]
    result = verify_findings_against_checkout(findings, checkout)
    assert result["verified"] == []
    assert result["unverified"] == findings
    assert result["grounding_rate"] == 0.0


def test_verify_findings_marks_out_of_bounds_line_as_unverified(tmp_path):
    checkout = make_checkout(tmp_path)
    findings = [{"file": "app.py", "line": 99, "message": "bad line", "severity": None}]
    result = verify_findings_against_checkout(findings, checkout)
    assert result["unverified"] == findings


def test_verify_findings_treats_missing_file_key_as_unverified(tmp_path):
    checkout = make_checkout(tmp_path)
    findings = [{"file": None, "line": None, "message": "vague comment", "severity": None}]
    result = verify_findings_against_checkout(findings, checkout)
    assert result["unverified"] == findings


def test_verify_findings_handles_empty_findings_list(tmp_path):
    checkout = make_checkout(tmp_path)
    result = verify_findings_against_checkout([], checkout)
    assert result == {
        "total_findings": 0,
        "verified": [],
        "unverified": [],
        "grounding_rate": None,
    }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd benchmarks/pr-review-benchmark && pytest tests/test_check_citations.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# benchmarks/pr-review-benchmark/scripts/check_citations.py
"""Extends citation_verifier.py's file-existence check with a real
line-bounds check against an actual local checkout (not AIR evidence
— the benchmark harness has direct filesystem access, unlike the
shipped product's evidence schema)."""
from pathlib import Path


def verify_findings_against_checkout(findings: list[dict], checkout_dir: Path) -> dict:
    verified = []
    unverified = []
    for finding in findings:
        file_path = finding.get("file")
        line = finding.get("line")
        if not file_path:
            unverified.append(finding)
            continue
        full_path = Path(checkout_dir) / file_path
        if not full_path.is_file():
            unverified.append(finding)
            continue
        if line is not None:
            line_count = sum(1 for _ in full_path.open())
            if line < 1 or line > line_count:
                unverified.append(finding)
                continue
        verified.append(finding)

    return {
        "total_findings": len(findings),
        "verified": verified,
        "unverified": unverified,
        "grounding_rate": (len(verified) / len(findings)) if findings else None,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd benchmarks/pr-review-benchmark && pytest tests/test_check_citations.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add benchmarks/pr-review-benchmark/scripts/check_citations.py \
        benchmarks/pr-review-benchmark/tests/test_check_citations.py
git commit -m "feat(benchmark): add automated grounding check against real checkout"
```

---

### Task 5: Blind anonymization + sealed mapping

**Files:**
- Create: `benchmarks/pr-review-benchmark/scripts/anonymize.py`
- Test: `benchmarks/pr-review-benchmark/tests/test_anonymize.py`

**Interfaces:**
- Produces: `assign_labels(tool_names: list[str], rng: random.Random) -> dict[str, str]` (label → real tool name), `write_anonymized_case(case_id: str, findings_by_tool: dict, results_dir: Path, rng: random.Random) -> dict` (writes `results/anon/<case_id>/<label>.json` and `results/sealed/<case_id>.json`), `reveal_mapping(case_id: str, results_dir: Path) -> dict` — consumed by `run_case.py` (Task 9), the manual scoring workflow (README), and the final report-building step (Task 11).

**Important:** the label↔tool mapping is re-randomized **independently per case** (a fresh `random.Random` seed per case at call time in real runs — tests inject a fixed seed for determinism). If the same tool always mapped to the same label across all ~25 cases, a scorer would learn the pattern within a handful of cases and the blinding would be defeated well before the corpus is finished.

- [ ] **Step 1: Write the failing tests**

```python
# benchmarks/pr-review-benchmark/tests/test_anonymize.py
import json
import random
from scripts.anonymize import assign_labels, write_anonymized_case, reveal_mapping


def test_assign_labels_maps_each_tool_to_a_distinct_label():
    rng = random.Random(42)
    mapping = assign_labels(["aletheore", "pr_agent", "deepsource", "coderabbit"], rng)
    assert set(mapping.keys()) == {"Tool A", "Tool B", "Tool C", "Tool D"}
    assert set(mapping.values()) == {"aletheore", "pr_agent", "deepsource", "coderabbit"}


def test_assign_labels_rejects_more_tools_than_labels():
    rng = random.Random(1)
    try:
        assign_labels(["a", "b", "c", "d", "e"], rng)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_write_anonymized_case_and_reveal_mapping_round_trip(tmp_path):
    rng = random.Random(7)
    findings_by_tool = {
        "aletheore": [{"file": "x.py", "line": 1, "message": "m", "severity": None}],
        "pr_agent": [{"file": "y.py", "line": 2, "message": "n", "severity": None}],
    }
    result = write_anonymized_case("001-example", findings_by_tool, tmp_path, rng)

    anon_files = sorted(p.name for p in result["anon_dir"].iterdir())
    assert len(anon_files) == 2

    revealed = reveal_mapping("001-example", tmp_path)
    assert set(revealed.values()) == {"aletheore", "pr_agent"}

    for label, tool in revealed.items():
        anon_path = result["anon_dir"] / f"{label.replace(' ', '_').lower()}.json"
        assert json.loads(anon_path.read_text()) == findings_by_tool[tool]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd benchmarks/pr-review-benchmark && pytest tests/test_anonymize.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# benchmarks/pr-review-benchmark/scripts/anonymize.py
"""Blind relabeling of tool outputs per case, so manual and LLM
scoring never see which tool produced which output. Re-randomized
independently per case so a scorer can't learn "label X is always
tool Y" across the corpus."""
import json
import random
from pathlib import Path

LABELS = ["Tool A", "Tool B", "Tool C", "Tool D"]


def assign_labels(tool_names: list[str], rng: random.Random) -> dict:
    if len(tool_names) > len(LABELS):
        raise ValueError("more tools than available labels")
    shuffled = list(tool_names)
    rng.shuffle(shuffled)
    return dict(zip(LABELS, shuffled))


def write_anonymized_case(
    case_id: str, findings_by_tool: dict, results_dir: Path, rng: random.Random
) -> dict:
    label_to_tool = assign_labels(list(findings_by_tool.keys()), rng)
    tool_to_label = {tool: label for label, tool in label_to_tool.items()}

    anon_dir = Path(results_dir) / "anon" / case_id
    anon_dir.mkdir(parents=True, exist_ok=True)
    for tool, findings in findings_by_tool.items():
        label = tool_to_label[tool]
        out_path = anon_dir / f"{label.replace(' ', '_').lower()}.json"
        out_path.write_text(json.dumps(findings, indent=2))

    sealed_dir = Path(results_dir) / "sealed"
    sealed_dir.mkdir(parents=True, exist_ok=True)
    sealed_path = sealed_dir / f"{case_id}.json"
    sealed_path.write_text(json.dumps(label_to_tool, indent=2))

    return {"anon_dir": anon_dir, "sealed_path": sealed_path}


def reveal_mapping(case_id: str, results_dir: Path) -> dict:
    sealed_path = Path(results_dir) / "sealed" / f"{case_id}.json"
    return json.loads(sealed_path.read_text())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd benchmarks/pr-review-benchmark && pytest tests/test_anonymize.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add benchmarks/pr-review-benchmark/scripts/anonymize.py \
        benchmarks/pr-review-benchmark/tests/test_anonymize.py
git commit -m "feat(benchmark): add blind anonymization with sealed mapping"
```

---

### Task 6: Blank scoring template generator

**Files:**
- Create: `benchmarks/pr-review-benchmark/scripts/scoring_template.py`
- Test: `benchmarks/pr-review-benchmark/tests/test_scoring_template.py`

**Interfaces:**
- Produces: `build_blank_scorecard(case_id: str, labels: list[str]) -> dict`, `write_blank_scorecard(case_id: str, labels: list[str], out_path: Path) -> None` — the YAML shape (`{"case_id": ..., "scores": {label: {"recall": None, "false_positives": [], "actionability": None}}}`) is the exact shape `aggregate.py` (Task 8) reads back from `results/scored/<case_id>.yaml` once filled in by hand.

- [ ] **Step 1: Write the failing tests**

```python
# benchmarks/pr-review-benchmark/tests/test_scoring_template.py
import yaml
from scripts.scoring_template import build_blank_scorecard, write_blank_scorecard


def test_build_blank_scorecard_has_one_entry_per_label():
    card = build_blank_scorecard("001-example", ["Tool A", "Tool B"])
    assert card == {
        "case_id": "001-example",
        "scores": {
            "Tool A": {"recall": None, "false_positives": [], "actionability": None},
            "Tool B": {"recall": None, "false_positives": [], "actionability": None},
        },
    }


def test_write_blank_scorecard_writes_valid_yaml(tmp_path):
    out_path = tmp_path / "001-example.yaml"
    write_blank_scorecard("001-example", ["Tool A"], out_path)
    loaded = yaml.safe_load(out_path.read_text())
    assert loaded["case_id"] == "001-example"
    assert loaded["scores"]["Tool A"]["recall"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd benchmarks/pr-review-benchmark && pytest tests/test_scoring_template.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# benchmarks/pr-review-benchmark/scripts/scoring_template.py
"""Generates a blank manual/LLM scoring template for a case, keyed
only by anonymized label -- never by real tool name."""
from pathlib import Path
import yaml


def build_blank_scorecard(case_id: str, labels: list[str]) -> dict:
    return {
        "case_id": case_id,
        "scores": {
            label: {"recall": None, "false_positives": [], "actionability": None}
            for label in labels
        },
    }


def write_blank_scorecard(case_id: str, labels: list[str], out_path: Path) -> None:
    Path(out_path).write_text(
        yaml.safe_dump(build_blank_scorecard(case_id, labels), sort_keys=False)
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd benchmarks/pr-review-benchmark && pytest tests/test_scoring_template.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add benchmarks/pr-review-benchmark/scripts/scoring_template.py \
        benchmarks/pr-review-benchmark/tests/test_scoring_template.py
git commit -m "feat(benchmark): add blank scoring template generator"
```

---

### Task 7: Independent LLM judge

**Files:**
- Create: `benchmarks/pr-review-benchmark/scripts/llm_judge.py`
- Test: `benchmarks/pr-review-benchmark/tests/test_llm_judge.py`

**Interfaces:**
- Consumes: ground truth dict (Task 1 shape), anonymized findings dict (`{label: list[finding]}` from Task 5's `anon_dir` contents).
- Produces: `build_judge_prompt(ground_truth: dict, anonymized_findings: dict) -> str`, `parse_judge_response(response_text: str) -> dict` (shape matches Task 6's per-label score dict, minus `false_positives` defaulting to `[]` only if present), `call_judge_model(client, prompt: str, model: str) -> str` — consumed by `run_case.py`'s live-judging step (documented in README, not itself unit-tested beyond argument-passing since it's a real API call).

- [ ] **Step 1: Write the failing tests**

```python
# benchmarks/pr-review-benchmark/tests/test_llm_judge.py
import json
import pytest
from scripts.llm_judge import build_judge_prompt, parse_judge_response, call_judge_model


def test_build_judge_prompt_includes_ground_truth_and_findings():
    ground_truth = {"category": "injected_bug", "expected_file": "x.py", "expected_line": 5}
    anonymized_findings = {"Tool A": [{"file": "x.py", "line": 5, "message": "bug here"}]}
    prompt = build_judge_prompt(ground_truth, anonymized_findings)
    assert "injected_bug" in prompt
    assert "Tool A" in prompt
    assert "bug here" in prompt
    assert "recall" in prompt


def test_parse_judge_response_extracts_json_object():
    response_text = (
        "Here is my scoring:\n"
        '{"Tool A": {"recall": "hit", "false_positives": [], "actionability": 4}}'
    )
    result = parse_judge_response(response_text)
    assert result == {"Tool A": {"recall": "hit", "false_positives": [], "actionability": 4}}


def test_parse_judge_response_rejects_invalid_recall_value():
    response_text = '{"Tool A": {"recall": "maybe", "false_positives": [], "actionability": 3}}'
    with pytest.raises(ValueError, match="recall must be hit/partial/miss"):
        parse_judge_response(response_text)


def test_parse_judge_response_raises_when_no_json_present():
    with pytest.raises(ValueError, match="did not contain a JSON object"):
        parse_judge_response("I refuse to answer in JSON.")


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self, text):
        self._text = text
        self.last_call = None

    def create(self, **kwargs):
        self.last_call = kwargs
        return _FakeResponse(self._text)


class _FakeChat:
    def __init__(self, text):
        self.completions = _FakeCompletions(text)


class _FakeClient:
    def __init__(self, text):
        self.chat = _FakeChat(text)


def test_call_judge_model_passes_prompt_and_model_and_returns_text():
    client = _FakeClient("mocked response text")
    result = call_judge_model(client, "the prompt", model="gpt-judge-1")
    assert result == "mocked response text"
    assert client.chat.completions.last_call["model"] == "gpt-judge-1"
    assert client.chat.completions.last_call["messages"] == [
        {"role": "user", "content": "the prompt"}
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd benchmarks/pr-review-benchmark && pytest tests/test_llm_judge.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# benchmarks/pr-review-benchmark/scripts/llm_judge.py
"""Independent LLM scoring pass: builds a rubric prompt from
anonymized findings and ground truth, and parses the model's
structured response into scoring_template.py's score shape. The
judge model must be a different provider/family than whichever
model powers the tools under test in a given run (see
METHODOLOGY.md)."""
import json


def build_judge_prompt(ground_truth: dict, anonymized_findings: dict) -> str:
    return (
        "You are scoring anonymized code-review tool outputs against a known "
        "ground truth. Tools are labeled Tool A/B/C/D; you do not know their real "
        "identities.\n\n"
        f"Ground truth:\n{json.dumps(ground_truth, indent=2)}\n\n"
        f"Anonymized findings:\n{json.dumps(anonymized_findings, indent=2)}\n\n"
        "For each tool label, score:\n"
        '- recall: "hit", "partial", or "miss" against the ground truth issue\n'
        "- false_positives: list of findings that are not the ground truth issue and "
        "are not legitimate secondary issues\n"
        "- actionability: 1-5, is the finding specific enough to act on\n\n"
        "Respond with ONLY a JSON object: "
        '{"Tool A": {"recall": ..., "false_positives": [...], "actionability": ...}, ...}'
    )


def parse_judge_response(response_text: str) -> dict:
    start = response_text.find("{")
    end = response_text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("judge response did not contain a JSON object")
    parsed = json.loads(response_text[start:end + 1])
    for label, score in parsed.items():
        if score.get("recall") not in {"hit", "partial", "miss"}:
            raise ValueError(
                f"{label}: recall must be hit/partial/miss, got {score.get('recall')!r}"
            )
    return parsed


def call_judge_model(client, prompt: str, model: str) -> str:
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd benchmarks/pr-review-benchmark && pytest tests/test_llm_judge.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add benchmarks/pr-review-benchmark/scripts/llm_judge.py \
        benchmarks/pr-review-benchmark/tests/test_llm_judge.py
git commit -m "feat(benchmark): add independent LLM judge"
```

---

### Task 8: Cross-case aggregation and human/LLM agreement rate

**Files:**
- Create: `benchmarks/pr-review-benchmark/scripts/aggregate.py`
- Test: `benchmarks/pr-review-benchmark/tests/test_aggregate.py`

**Interfaces:**
- Consumes: `results/scored/<case_id>.yaml` (Task 6 shape, filled in by hand) and `results/llm_judged/<case_id>.json` (Task 7's `parse_judge_response` shape).
- Produces: `load_manual_scores(results_dir: Path) -> dict`, `load_llm_scores(results_dir: Path) -> dict`, `build_scorecard(manual_scores: dict, llm_scores: dict) -> dict` (keys: `per_tool` keyed by label, `human_llm_agreement` keyed by dimension) — consumed by the report-building step (Task 11).

- [ ] **Step 1: Write the failing tests**

```python
# benchmarks/pr-review-benchmark/tests/test_aggregate.py
import json
import yaml
from scripts.aggregate import load_manual_scores, load_llm_scores, build_scorecard


def test_load_manual_scores_reads_all_scored_yaml_files(tmp_path):
    scored_dir = tmp_path / "scored"
    scored_dir.mkdir()
    (scored_dir / "001.yaml").write_text(yaml.safe_dump({
        "case_id": "001",
        "scores": {"Tool A": {"recall": "hit", "false_positives": [], "actionability": 4}},
    }))
    scores = load_manual_scores(tmp_path)
    assert scores == {"001": {"Tool A": {"recall": "hit", "false_positives": [], "actionability": 4}}}


def test_load_llm_scores_reads_all_llm_judged_json_files(tmp_path):
    llm_dir = tmp_path / "llm_judged"
    llm_dir.mkdir()
    (llm_dir / "001.json").write_text(json.dumps({
        "Tool A": {"recall": "hit", "false_positives": [], "actionability": 4}
    }))
    scores = load_llm_scores(tmp_path)
    assert scores == {"001": {"Tool A": {"recall": "hit", "false_positives": [], "actionability": 4}}}


def test_build_scorecard_counts_recall_buckets_and_false_positives():
    manual_scores = {
        "001": {"Tool A": {"recall": "hit", "false_positives": [], "actionability": 5}},
        "002": {"Tool A": {"recall": "miss", "false_positives": ["noise"], "actionability": 2}},
    }
    scorecard = build_scorecard(manual_scores, llm_scores={})
    assert scorecard["per_tool"]["Tool A"]["hit"] == 1
    assert scorecard["per_tool"]["Tool A"]["miss"] == 1
    assert scorecard["per_tool"]["Tool A"]["false_positive_count"] == 1
    assert scorecard["per_tool"]["Tool A"]["actionability_total"] == 7
    assert scorecard["per_tool"]["Tool A"]["actionability_count"] == 2


def test_build_scorecard_computes_human_llm_agreement_rate():
    manual_scores = {
        "001": {"Tool A": {"recall": "hit", "false_positives": [], "actionability": 4}},
        "002": {"Tool A": {"recall": "miss", "false_positives": [], "actionability": 2}},
    }
    llm_scores = {
        "001": {"Tool A": {"recall": "hit", "actionability": 4}},
        "002": {"Tool A": {"recall": "hit", "actionability": 2}},
    }
    scorecard = build_scorecard(manual_scores, llm_scores)
    assert scorecard["human_llm_agreement"]["recall"] == 0.5
    assert scorecard["human_llm_agreement"]["actionability"] == 1.0


def test_build_scorecard_handles_no_llm_scores_at_all():
    manual_scores = {"001": {"Tool A": {"recall": "hit", "false_positives": [], "actionability": 4}}}
    scorecard = build_scorecard(manual_scores, llm_scores={})
    assert scorecard["human_llm_agreement"]["recall"] is None
    assert scorecard["human_llm_agreement"]["actionability"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd benchmarks/pr-review-benchmark && pytest tests/test_aggregate.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# benchmarks/pr-review-benchmark/scripts/aggregate.py
"""Builds the final cross-case scorecard from manual and LLM-judge
scoring records. Runs after both scoring passes are complete for
every case; grounding_rate (Task 4's automated, non-blind check) is
merged in separately at report-build time, keyed by real tool name
since it needs no blinding."""
import json
from pathlib import Path
import yaml


def load_manual_scores(results_dir: Path) -> dict:
    scores = {}
    for path in sorted((Path(results_dir) / "scored").glob("*.yaml")):
        data = yaml.safe_load(path.read_text())
        scores[data["case_id"]] = data["scores"]
    return scores


def load_llm_scores(results_dir: Path) -> dict:
    scores = {}
    for path in sorted((Path(results_dir) / "llm_judged").glob("*.json")):
        scores[path.stem] = json.loads(path.read_text())
    return scores


def build_scorecard(manual_scores: dict, llm_scores: dict) -> dict:
    per_tool = {}
    agreement_counts = {"recall": 0, "actionability": 0}
    compared_counts = {"recall": 0, "actionability": 0}

    for case_id, case_scores in manual_scores.items():
        llm_case_scores = llm_scores.get(case_id, {})
        for label, manual in case_scores.items():
            bucket = per_tool.setdefault(label, {
                "hit": 0, "partial": 0, "miss": 0,
                "false_positive_count": 0,
                "actionability_total": 0, "actionability_count": 0,
            })
            recall = manual.get("recall")
            if recall in ("hit", "partial", "miss"):
                bucket[recall] += 1
            bucket["false_positive_count"] += len(manual.get("false_positives") or [])
            if manual.get("actionability") is not None:
                bucket["actionability_total"] += manual["actionability"]
                bucket["actionability_count"] += 1

            llm = llm_case_scores.get(label)
            if llm is not None:
                if "recall" in llm:
                    compared_counts["recall"] += 1
                    if llm["recall"] == recall:
                        agreement_counts["recall"] += 1
                if "actionability" in llm:
                    compared_counts["actionability"] += 1
                    if llm["actionability"] == manual.get("actionability"):
                        agreement_counts["actionability"] += 1

    human_llm_agreement = {}
    for dimension, compared in compared_counts.items():
        human_llm_agreement[dimension] = (
            agreement_counts[dimension] / compared if compared else None
        )

    return {"per_tool": per_tool, "human_llm_agreement": human_llm_agreement}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd benchmarks/pr-review-benchmark && pytest tests/test_aggregate.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add benchmarks/pr-review-benchmark/scripts/aggregate.py \
        benchmarks/pr-review-benchmark/tests/test_aggregate.py
git commit -m "feat(benchmark): add cross-case scorecard aggregation"
```

---

### Task 9: Real tool adapters + case orchestrator

**Files:**
- Create: `benchmarks/pr-review-benchmark/scripts/adapters.py`
- Create: `benchmarks/pr-review-benchmark/scripts/run_case.py`
- Test: `benchmarks/pr-review-benchmark/tests/test_adapters.py`
- Test: `benchmarks/pr-review-benchmark/tests/test_run_case.py`
- Modify: `.gitignore` (repo root) — add the CodeRabbit-specific raw-output exclusion.

**Interfaces:**
- Consumes: `load_case` (Task 1), `prepare_case_checkout` (Task 2), normalizer functions (Task 3), `verify_findings_against_checkout` (Task 4).
- Produces: `aletheore_adapter`, `pr_agent_adapter`, `deepsource_adapter`, `coderabbit_adapter` (each `(checkout_dir, case, ...) -> raw output`, real invocation, injectable for tests); `run_case(case_dir: Path, workdir: Path, results_dir: Path, adapters: dict, normalizers: dict) -> dict` — consumed by the live per-case run documented in `README.md`.

**Note on live invocation:** `adapters.py`'s functions genuinely shell out to `aletheore audit`, invoke PR-Agent's CLI against a real `pr_url`, and (for DeepSource/CodeRabbit) call injected fetch functions that hit real APIs — actually running these against live GitHub PRs is integration work done by hand per the README runbook (Task 9 only builds and unit-tests the *wiring*, with fakes standing in for the real subprocess/API calls).

- [ ] **Step 1: Write the failing tests**

```python
# benchmarks/pr-review-benchmark/tests/test_adapters.py
from scripts.adapters import (
    aletheore_adapter,
    pr_agent_adapter,
    deepsource_adapter,
    coderabbit_adapter,
)


class _FakeCompletedProcess:
    def __init__(self, stdout):
        self.stdout = stdout


def test_aletheore_adapter_invokes_audit_against_checkout_dir(tmp_path):
    calls = []

    def fake_runner(args, **kwargs):
        calls.append(args)
        return _FakeCompletedProcess("report text")

    result = aletheore_adapter(tmp_path, case={}, runner=fake_runner)
    assert calls == [["aletheore", "audit", str(tmp_path)]]
    assert result == "report text"


def test_pr_agent_adapter_invokes_cli_with_pr_url(tmp_path):
    calls = []

    def fake_runner(args, **kwargs):
        calls.append(args)
        return _FakeCompletedProcess('{"code_suggestions": []}')

    case = {"repo": {"pr_url": "https://github.com/example/repo/pull/1"}}
    result = pr_agent_adapter(tmp_path, case, runner=fake_runner)
    assert calls == [[
        "python", "-m", "pr_agent.cli",
        "--pr_url", "https://github.com/example/repo/pull/1", "review",
    ]]
    assert result == {"code_suggestions": []}


def test_deepsource_adapter_calls_fetch_issues_with_run_id(tmp_path):
    case = {"repo": {"deepsource_run_id": "run-42"}}
    captured = {}

    def fake_fetch(run_id):
        captured["run_id"] = run_id
        return {"issues": []}

    result = deepsource_adapter(tmp_path, case, fetch_issues=fake_fetch)
    assert captured["run_id"] == "run-42"
    assert result == {"issues": []}


def test_coderabbit_adapter_calls_fetch_pr_comments_with_pr_url(tmp_path):
    case = {"repo": {"pr_url": "https://github.com/example/repo/pull/1"}}
    captured = {}

    def fake_fetch(pr_url):
        captured["pr_url"] = pr_url
        return [{"path": "x.py", "line": 1, "body": "comment"}]

    result = coderabbit_adapter(tmp_path, case, fetch_pr_comments=fake_fetch)
    assert captured["pr_url"] == "https://github.com/example/repo/pull/1"
    assert result == [{"path": "x.py", "line": 1, "body": "comment"}]
```

```python
# benchmarks/pr-review-benchmark/tests/test_run_case.py
import json
from scripts.run_case import run_case
from tests.test_build_case_repo import make_fixture_repo


def test_run_case_writes_raw_and_grounding_output_for_each_tool(tmp_path):
    remote, base_commit, diff_path = make_fixture_repo(tmp_path)

    case_dir = tmp_path / "cases" / "001-example"
    case_dir.mkdir(parents=True)
    (case_dir / "repo.txt").write_text(f"repo_url={remote}\nbase_commit={base_commit}\n")
    diff_path.rename(case_dir / "pr.diff")
    (case_dir / "ground_truth.yaml").write_text(
        "case_id: 001-example\nlanguage: python\ncategory: injected_bug\n"
        "bug_type: test\nexpected_file: x.py\nexpected_line: 1\n"
        "fix_reference: null\ndescription: test\n"
    )

    workdir = tmp_path / "work"
    workdir.mkdir()
    results_dir = tmp_path / "results"

    adapters = {"fake_tool": lambda checkout_dir, case: "finding at `x.py:1`."}
    normalizers = {
        "fake_tool": lambda raw: [{"file": "x.py", "line": 1, "message": raw, "severity": None}]
    }

    result = run_case(case_dir, workdir, results_dir, adapters, normalizers)

    assert result["case_id"] == "001-example"
    raw_path = results_dir / "raw" / "001-example" / "fake_tool.json"
    assert json.loads(raw_path.read_text()) == "finding at `x.py:1`."

    grounding_path = results_dir / "grounding" / "001-example" / "fake_tool.json"
    grounding = json.loads(grounding_path.read_text())
    assert grounding["grounding_rate"] == 1.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd benchmarks/pr-review-benchmark && pytest tests/test_adapters.py tests/test_run_case.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# benchmarks/pr-review-benchmark/scripts/adapters.py
"""Real per-tool invocation. Each adapter takes (checkout_dir, case,
...) and returns raw output ready for scripts/normalize.py. Adapters
that shell out or call an API accept an injectable runner/fetcher so
command construction is unit-testable without actually invoking
external tools or the network."""
import json
import subprocess


def aletheore_adapter(checkout_dir, case, runner=subprocess.run):
    result = runner(
        ["aletheore", "audit", str(checkout_dir)],
        capture_output=True, text=True, check=True,
    )
    return result.stdout


def pr_agent_adapter(checkout_dir, case, runner=subprocess.run):
    result = runner(
        ["python", "-m", "pr_agent.cli", "--pr_url", case["repo"]["pr_url"], "review"],
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


def deepsource_adapter(checkout_dir, case, fetch_issues):
    return fetch_issues(case["repo"]["deepsource_run_id"])


def coderabbit_adapter(checkout_dir, case, fetch_pr_comments):
    return fetch_pr_comments(case["repo"]["pr_url"])
```

```python
# benchmarks/pr-review-benchmark/scripts/run_case.py
"""Orchestrates one full case run: prepare checkout, invoke each tool
adapter, normalize output, run the automated grounding check, and
store raw + grounding results."""
import json
from pathlib import Path

from scripts.cases import load_case
from scripts.build_case_repo import prepare_case_checkout
from scripts.check_citations import verify_findings_against_checkout


def run_case(case_dir: Path, workdir: Path, results_dir: Path, adapters: dict, normalizers: dict) -> dict:
    case = load_case(case_dir)
    checkout_dir = prepare_case_checkout(case["repo"], case["diff_path"], workdir)

    raw_dir = Path(results_dir) / "raw" / case["case_id"]
    raw_dir.mkdir(parents=True, exist_ok=True)
    grounding_dir = Path(results_dir) / "grounding" / case["case_id"]
    grounding_dir.mkdir(parents=True, exist_ok=True)

    findings_by_tool = {}
    for tool_name, adapter in adapters.items():
        raw_output = adapter(checkout_dir, case)
        (raw_dir / f"{tool_name}.json").write_text(json.dumps(raw_output, indent=2))

        findings = normalizers[tool_name](raw_output)
        grounding = verify_findings_against_checkout(findings, checkout_dir)
        (grounding_dir / f"{tool_name}.json").write_text(json.dumps(grounding, indent=2))

        findings_by_tool[tool_name] = findings

    return {"case_id": case["case_id"], "checkout_dir": checkout_dir, "findings_by_tool": findings_by_tool}
```

Add to the repo root `.gitignore`:

```
# PR-review benchmark: CodeRabbit's raw transcript is never committed (ToS §4.2 — see
# docs/superpowers/specs/2026-07-26-aletheore-pr-review-benchmark-design.md); only its
# anonymized scored summary is published.
benchmarks/pr-review-benchmark/results/raw/*/coderabbit.json
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd benchmarks/pr-review-benchmark && pytest tests/test_adapters.py tests/test_run_case.py -v`
Expected: 4 passed (adapters) + 1 passed (run_case).

- [ ] **Step 5: Commit**

```bash
git add benchmarks/pr-review-benchmark/scripts/adapters.py \
        benchmarks/pr-review-benchmark/scripts/run_case.py \
        benchmarks/pr-review-benchmark/tests/test_adapters.py \
        benchmarks/pr-review-benchmark/tests/test_run_case.py \
        .gitignore
git commit -m "feat(benchmark): add real tool adapters and case orchestrator"
```

---

### Task 10: Author the 25-case corpus

**Files:**
- Create: `benchmarks/pr-review-benchmark/cases/<case-id>/repo.txt`, `pr.diff`, `ground_truth.yaml`, `ground_truth.md` — one set per case, ~25 total.

This is data curation, not code — no tests apply. Follow this exact repeatable procedure per case type. **Never assert a commit hash or PR URL you have not personally verified with `git log` / `gh api` against the real repo** — an unverified citation in the benchmark's own ground truth would undercut the entire premise.

**Real bug-fix reconstructions (~15, aim for ~4 Python, ~4 TypeScript/JS, ~4 Go, ~3 Java):**
1. Pick a popular OSS repo in the target language.
2. Search its history for a clear bug-fix commit: `git log --oneline --grep='fix' <repo>` (or GitHub's search UI for `is:pr is:merged fix` filtered to that repo), then read the actual commit diff and message to confirm it's a real, single-purpose bug fix (not a refactor mislabeled "fix") and ideally references an issue number.
3. Record the fix commit's hash. Verify its parent commit is a clean checkout point: `git show <fix_commit>^:<file>` for each changed file.
4. Generate the case's `pr.diff` as the **inverse** of the fix (this reintroduces the bug as the "proposed change" under test): `git diff <fix_commit> <fix_commit>^ -- <changed files> > pr.diff`.
5. Set `repo.txt`'s `base_commit` to `<fix_commit>` (the post-fix state — applying the inverse diff on top reproduces the pre-fix buggy state).
6. Write `ground_truth.yaml` with `category: real_bug_fix`, `fix_reference` set to the fix commit's full URL, `expected_file`/`expected_line` pointing at the actual buggy line the fix changed, and a one-paragraph `description`.
7. Write `ground_truth.md`: 2-4 sentences in prose, for the published report, describing what the bug was and why it mattered — written for a reader who isn't going to open the diff.

**Injected bugs (~6, targeting categories underrepresented in the real set — likely candidates: SQL injection via string concatenation, off-by-one loop bound, missing null/None check, race condition via non-atomic read-modify-write, hardcoded secret, swallowed exception):**
1. Pick a small, clean PR (or a clean file state) from any of the corpus repos or a new one.
2. Hand-author a diff that introduces exactly one of the bug patterns above — keep the change small and the bug unambiguous (this is ground truth; it must not be debatable whether it's really a bug).
3. Write `ground_truth.yaml` with `category: injected_bug`, `fix_reference: null`, and precise `expected_file`/`expected_line`/`bug_type`/`description` — written and committed **before** any tool is run against this case (pre-registration; do not adjust ground truth after seeing what a tool flagged).
4. Write `ground_truth.md` in the same prose style as the real cases.

**Clean PRs (~4, no real bug):**
1. Pick a genuinely clean, small, real merged PR (a docs fix, a rename, a trivial refactor) with no defects.
2. `ground_truth.yaml`: `category: clean`, `bug_type: null`, `expected_file: null`, `expected_line: null`, `description` explains why this PR is clean and what a false positive here would look like.
3. `ground_truth.md`: short note that this is a deliberately clean control case.

- [ ] **Step 1: Author all ~25 `cases/<case-id>/` directories per the procedure above.**
- [ ] **Step 2: Run `pytest tests/test_cases.py` plus a manual spot-check** — `python -c "from scripts.cases import load_case; load_case('cases/<case-id>')"` for every case directory — to confirm every case parses without raising.
- [ ] **Step 3: Commit**

```bash
git add benchmarks/pr-review-benchmark/cases/
git commit -m "data(benchmark): author 25-case test corpus (real, injected, clean)"
```

---

### Task 11: Report and methodology generation

**Files:**
- Create: `benchmarks/pr-review-benchmark/scripts/build_report.py`
- Create: `benchmarks/pr-review-benchmark/METHODOLOGY.md` (template, filled in at run time)
- Test: `benchmarks/pr-review-benchmark/tests/test_build_report.py`

**Interfaces:**
- Consumes: `build_scorecard` output (Task 8), `reveal_mapping` (Task 5), grounding results (Task 4, loaded per real tool name from `results/grounding/`).
- Produces: `merge_grounding_into_scorecard(scorecard: dict, grounding_by_case_and_tool: dict, case_label_maps: dict) -> dict` (attaches `grounding_rate` onto each tool's real name after reveal), `render_report_markdown(scorecard: dict) -> str` — writes the final `REPORT.md`.

- [ ] **Step 1: Write the failing tests**

```python
# benchmarks/pr-review-benchmark/tests/test_build_report.py
from scripts.build_report import merge_grounding_into_scorecard, render_report_markdown


def test_merge_grounding_into_scorecard_attaches_rate_by_real_tool_name():
    scorecard = {"per_tool": {"Tool A": {"hit": 1, "miss": 0}}, "human_llm_agreement": {}}
    grounding_by_case_and_tool = {"001": {"aletheore": {"grounding_rate": 1.0}}}
    case_label_maps = {"001": {"Tool A": "aletheore"}}

    merged = merge_grounding_into_scorecard(scorecard, grounding_by_case_and_tool, case_label_maps)
    assert merged["per_tool"]["aletheore"]["grounding_rate"] == 1.0


def test_render_report_markdown_includes_per_tool_table():
    scorecard = {
        "per_tool": {
            "aletheore": {
                "hit": 10, "partial": 2, "miss": 3,
                "false_positive_count": 1,
                "actionability_total": 45, "actionability_count": 15,
                "grounding_rate": 0.97,
            }
        },
        "human_llm_agreement": {"recall": 0.9, "actionability": 0.8},
    }
    markdown = render_report_markdown(scorecard)
    assert "aletheore" in markdown
    assert "0.97" in markdown
    assert "0.9" in markdown
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd benchmarks/pr-review-benchmark && pytest tests/test_build_report.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# benchmarks/pr-review-benchmark/scripts/build_report.py
"""Merges the automated grounding check (Task 4, real tool names, no
blinding needed) into the blind-scored scorecard (Task 8, label-keyed)
after each case's mapping has been revealed, and renders the final
report table."""


def merge_grounding_into_scorecard(scorecard: dict, grounding_by_case_and_tool: dict, case_label_maps: dict) -> dict:
    real_name_by_label = {}
    for case_id, label_to_tool in case_label_maps.items():
        for label, tool in label_to_tool.items():
            real_name_by_label[label] = tool

    grounding_rates_by_tool = {}
    for case_id, grounding_by_tool in grounding_by_case_and_tool.items():
        for tool, grounding in grounding_by_tool.items():
            grounding_rates_by_tool.setdefault(tool, []).append(grounding["grounding_rate"])

    per_tool = {}
    for label, stats in scorecard["per_tool"].items():
        real_name = real_name_by_label.get(label, label)
        merged_stats = dict(stats)
        rates = grounding_rates_by_tool.get(real_name)
        if rates:
            merged_stats["grounding_rate"] = sum(rates) / len(rates)
        per_tool[real_name] = merged_stats

    return {"per_tool": per_tool, "human_llm_agreement": scorecard["human_llm_agreement"]}


def render_report_markdown(scorecard: dict) -> str:
    lines = [
        "# Aletheore PR-Review Benchmark — Results",
        "",
        "| Tool | Hit | Partial | Miss | False Positives | Avg Actionability | Grounding Rate |",
        "|---|---|---|---|---|---|---|",
    ]
    for tool, stats in scorecard["per_tool"].items():
        avg_actionability = (
            stats["actionability_total"] / stats["actionability_count"]
            if stats.get("actionability_count") else "n/a"
        )
        grounding_rate = stats.get("grounding_rate", "n/a")
        lines.append(
            f"| {tool} | {stats.get('hit', 0)} | {stats.get('partial', 0)} | "
            f"{stats.get('miss', 0)} | {stats.get('false_positive_count', 0)} | "
            f"{avg_actionability} | {grounding_rate} |"
        )

    lines += [
        "",
        "## Human/LLM judge agreement",
        "",
        f"- Recall agreement: {scorecard['human_llm_agreement'].get('recall', 'n/a')}",
        f"- Actionability agreement: {scorecard['human_llm_agreement'].get('actionability', 'n/a')}",
    ]
    return "\n".join(lines)
```

Create `benchmarks/pr-review-benchmark/METHODOLOGY.md` with this starting template (fields filled in when the live run happens):

```markdown
# Methodology

- **Run date:** TBD at execution time
- **Aletheore version/model:** TBD
- **Qodo/PR-Agent version/model:** TBD (pinned to the same model as Aletheore where possible)
- **DeepSource plan/version:** TBD
- **CodeRabbit plan/version (anonymized as "Tool D" or similar in the report):** TBD
- **LLM judge model:** TBD (must be a different provider/family than the models above)
- **Corpus:** 25 cases — 15 real bug-fix reconstructions, 6 injected bugs, 4 clean PRs, across Python/TypeScript/Go/Java
- **Known limitations:** see `docs/superpowers/specs/2026-07-26-aletheore-pr-review-benchmark-design.md`'s
  "Known Limitations" section — reproduced in full in the published report, not just linked.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd benchmarks/pr-review-benchmark && pytest tests/test_build_report.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add benchmarks/pr-review-benchmark/scripts/build_report.py \
        benchmarks/pr-review-benchmark/tests/test_build_report.py \
        benchmarks/pr-review-benchmark/METHODOLOGY.md
git commit -m "feat(benchmark): add grounding merge and report rendering"
```

---

### Task 12: README runbook + end-to-end dry run on 2 cases

**Files:**
- Create: `benchmarks/pr-review-benchmark/README.md`

**Interfaces:** none new — this task wires Tasks 1-11 together as documented, manual steps and validates the pipeline actually works end-to-end before running the full 25-case corpus.

- [ ] **Step 1: Write `README.md`** covering: how to author a case (link to Task 10's procedure), how to open a real throwaway PR per case on a scratch repo/fork, how to install CodeRabbit's GitHub App on that scratch repo, how to run PR-Agent against it (`python -m pr_agent.cli --pr_url ... review`), how to fetch DeepSource's issue list and CodeRabbit's PR review comments via their respective APIs to feed into `adapters.py`'s injected `fetch_issues`/`fetch_pr_comments` callables, how to run `scripts/run_case.py` per case, how to fill in `results/scored/<case_id>.yaml` by hand from the anonymized `results/anon/<case_id>/` files, how to run the LLM judge, and how to run `scripts/build_report.py`'s functions to produce the final `REPORT.md`.

- [ ] **Step 2: Pick 2 already-authored cases (one real, one injected) and run the full pipeline live**: prepare checkout, run all four real adapters, normalize, check citations, anonymize, fill in the manual scoring YAML blind, run the LLM judge, reveal, merge, render. Confirm every step in the README actually works as written against real tools — fix any place `normalize_pr_agent`/`normalize_deepsource`'s assumed schema (flagged in Task 3) doesn't match the real output, updating both the normalizer and its test.

- [ ] **Step 3: Commit**

```bash
git add benchmarks/pr-review-benchmark/README.md
git commit -m "docs(benchmark): add runbook, validated against a 2-case dry run"
```

If Step 2 required normalizer fixes, commit those separately first with their own updated tests, referencing what the real tool output actually looked like.

---

## Self-Review Notes

- **Spec coverage:** every design-spec section maps to a task — corpus/ground truth (Task 10), competitor lineup/legal handling (Task 9's adapters + `.gitignore` exclusion + Task 9's ToS-driven CodeRabbit exclusion from raw commits), execution pipeline (Tasks 1, 2, 9), scoring rubric + blind judging + dual LLM/manual pass (Tasks 5, 6, 7, 8), publication/reproducibility (Task 11), known limitations (reproduced verbatim in `METHODOLOGY.md`, Task 11).
- **Placeholder scan:** no TBD/TODO in code; `METHODOLOGY.md`'s template TBDs are explicitly a fill-in-at-runtime template, not an unfinished task.
- **Type consistency:** the finding schema (`file`/`line`/`message`/`severity`) is identical across Tasks 3, 4, 9; the score schema (`recall`/`false_positives`/`actionability`) is identical across Tasks 6, 7, 8; `case_id`/`repo`/`diff_path`/`ground_truth` from Task 1 is consumed unchanged by Tasks 2 and 9.
