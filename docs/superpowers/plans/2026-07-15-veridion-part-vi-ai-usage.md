# Veridion Part VI (AI/LLM Usage Detection) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect AI/LLM provider, orchestration, vector-store, local-inference, and MCP
package usage in a scanned repository, using the same manifest-matching mechanism
`detect_frameworks` already uses.

**Architecture:** Extract two shared parsing helpers out of `detect_frameworks`'s current
inline logic so the new five-category detector doesn't duplicate manifest parsing five times.
`ai_usage` becomes a new key inside the existing `evidence.repository` dict — not a new
top-level `evidence.json` key, since it's the same kind of fact `languages`/`frameworks`
already are, just a different package category.

**Tech Stack:** Python stdlib only — no new dependencies, no network calls, reuses the exact
`requirements.txt`/`package.json` files `detect_frameworks` already reads.

## Global Constraints

- `ai_usage` lives at `evidence.repository.ai_usage`, sibling to `languages`/`frameworks`/
  `build_tools`/`monorepo`/`modules`/`dependency_graph`/`unparseable_files` — **not** a new
  top-level `evidence.json` key like `architecture`/`security` are. This means no new numbered
  section in Part I's output contract — findings fold into the existing "Repository
  Intelligence" section (section 2), governed by Part VI's manual for interpretation.
- Detection only — no judgment about whether AI usage is "good practice," no prompt-injection
  assessment, no RAG-quality evaluation. A detected package is a fact about presence, evidenced
  by the exact manifest line, nothing more.
- `detect_frameworks`'s existing behavior and existing tests must be unchanged after the
  helper refactor — this is a regression risk, not just a refactor to trust blindly.
- The five category names and exact package lists come directly from the design spec's
  Detection List section — do not add or remove packages without checking there first.

---

## Task 1: Shared manifest-parsing helpers, `detect_frameworks` refactor, `detect_ai_usage`

**Files:**
- Modify: `prototype/veridion/scanner/detect.py`
- Test: `prototype/tests/test_detect.py`

**Interfaces:**
- Consumes: nothing from other tasks — self-contained within `detect.py`.
- Produces: `detect_ai_usage(repo_path: Path) -> dict` returning
  `{"providers": list[dict], "orchestration": list[dict], "vector_stores": list[dict],
  "local_inference": list[dict], "mcp": list[dict]}`, each entry shaped
  `{"name": str, "evidence": str}` identically to `frameworks` entries. Task 2 calls this
  function by this exact name and return shape. `detect_frameworks`'s existing signature and
  behavior are unchanged — only its internals move to shared helpers.

- [ ] **Step 1: Write the failing tests**

Append to `prototype/tests/test_detect.py`:
```python
from veridion.scanner.detect import detect_ai_usage


def test_detect_ai_usage_finds_a_provider_in_requirements_txt(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "requirements.txt").write_text("openai==1.30.0\nrequests==2.31.0\n")

    result = detect_ai_usage(repo)

    names = {p["name"] for p in result["providers"]}
    assert "openai" in names
    entry = next(p for p in result["providers"] if p["name"] == "openai")
    assert entry["evidence"] == "requirements.txt:openai==1.30.0"


def test_detect_ai_usage_finds_orchestration_and_vector_store(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "requirements.txt").write_text("langchain==0.2.0\nchromadb==0.5.0\n")

    result = detect_ai_usage(repo)

    assert {p["name"] for p in result["orchestration"]} == {"langchain"}
    assert {p["name"] for p in result["vector_stores"]} == {"chromadb"}


def test_detect_ai_usage_finds_local_inference_and_mcp(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "requirements.txt").write_text("transformers==4.40.0\nmcp==1.0.0\n")

    result = detect_ai_usage(repo)

    assert {p["name"] for p in result["local_inference"]} == {"transformers"}
    assert {p["name"] for p in result["mcp"]} == {"mcp"}


def test_detect_ai_usage_reads_package_json(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "package.json").write_text(
        json.dumps(
            {
                "dependencies": {
                    "@anthropic-ai/sdk": "^0.20.0",
                    "@modelcontextprotocol/sdk": "^1.0.0",
                }
            }
        )
    )

    result = detect_ai_usage(repo)

    assert {p["name"] for p in result["providers"]} == {"@anthropic-ai/sdk"}
    assert {p["name"] for p in result["mcp"]} == {"@modelcontextprotocol/sdk"}


def test_detect_ai_usage_empty_lists_when_nothing_matches(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "requirements.txt").write_text("requests==2.31.0\n")

    result = detect_ai_usage(repo)

    assert result == {
        "providers": [],
        "orchestration": [],
        "vector_stores": [],
        "local_inference": [],
        "mcp": [],
    }
```

Check the top of `prototype/tests/test_detect.py` for its existing `import json` — reuse it.

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd prototype && pytest tests/test_detect.py -v
```
Expected: FAIL (`detect_ai_usage` doesn't exist yet); all existing tests still PASS (nothing
has been changed yet).

- [ ] **Step 3: Extract shared helpers and refactor `detect_frameworks`**

In `prototype/veridion/scanner/detect.py`, add two helpers above `detect_frameworks` and
rewrite `detect_frameworks` to use them:
```python
def _iter_pip_package_lines(repo_path: Path) -> list[tuple[str, str]]:
    requirements = repo_path / "requirements.txt"
    if not requirements.exists():
        return []
    results = []
    for line in requirements.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        package_name = line.split("==")[0].split(">=")[0].split("<=")[0].strip().lower()
        results.append((package_name, line))
    return results


def _npm_dependencies(repo_path: Path) -> dict[str, str]:
    package_json = repo_path / "package.json"
    if not package_json.exists():
        return {}
    try:
        data = json.loads(package_json.read_text(encoding="utf-8", errors="ignore"))
    except json.JSONDecodeError:
        return {}
    return {**data.get("dependencies", {}), **data.get("devDependencies", {})}


def detect_frameworks(repo_path: Path) -> list[dict]:
    frameworks: list[dict] = []

    for package_name, line in _iter_pip_package_lines(repo_path):
        if package_name in FRAMEWORK_MARKERS_PY:
            frameworks.append(
                {"name": FRAMEWORK_MARKERS_PY[package_name], "evidence": f"requirements.txt:{line}"}
            )

    for name, version in _npm_dependencies(repo_path).items():
        key = name.lower()
        if key in FRAMEWORK_MARKERS_JS:
            frameworks.append(
                {"name": FRAMEWORK_MARKERS_JS[key], "evidence": f"package.json:{name}@{version}"}
            )

    return frameworks
```

This replaces `detect_frameworks`'s entire previous body (the inline `requirements.exists()`
block and inline `package_json.exists()` block) — delete the old inline versions of this logic
now that it lives in the two helpers.

- [ ] **Step 4: Run existing tests to confirm no regression**

```bash
cd prototype && pytest tests/test_detect.py -v -k "frameworks"
```
Expected: PASS — `test_detect_frameworks_reads_requirements_txt` and
`test_detect_frameworks_reads_package_json` must behave identically to before the refactor.

- [ ] **Step 5: Add the AI marker dicts and `detect_ai_usage`**

In `prototype/veridion/scanner/detect.py`, add the marker dicts near the existing
`FRAMEWORK_MARKERS_PY`/`FRAMEWORK_MARKERS_JS` (same section of the file), and the new function
after `detect_frameworks`:
```python
AI_PROVIDER_MARKERS_PY = {
    "openai": "openai",
    "anthropic": "anthropic",
    "google-generativeai": "google-generativeai",
    "google-genai": "google-genai",
    "cohere": "cohere",
    "mistralai": "mistralai",
}

AI_PROVIDER_MARKERS_JS = {
    "openai": "openai",
    "@anthropic-ai/sdk": "@anthropic-ai/sdk",
    "@google/generative-ai": "@google/generative-ai",
}

AI_ORCHESTRATION_MARKERS_PY = {
    "langchain": "langchain",
    "llama-index": "llama-index",
    "llama_index": "llama-index",
    "crewai": "crewai",
    "autogen": "autogen",
}

AI_ORCHESTRATION_MARKERS_JS = {
    "langchain": "langchain",
}

AI_VECTOR_STORE_MARKERS_PY = {
    "pinecone-client": "pinecone",
    "pinecone": "pinecone",
    "chromadb": "chromadb",
    "weaviate-client": "weaviate",
    "qdrant-client": "qdrant",
    "faiss-cpu": "faiss",
}

AI_LOCAL_INFERENCE_MARKERS_PY = {
    "transformers": "transformers",
    "ollama": "ollama",
    "llama-cpp-python": "llama-cpp-python",
    "vllm": "vllm",
}

AI_MCP_MARKERS_PY = {
    "mcp": "mcp",
}

AI_MCP_MARKERS_JS = {
    "@modelcontextprotocol/sdk": "@modelcontextprotocol/sdk",
}


def _match_ai_markers(
    pip_markers: dict[str, str],
    js_markers: dict[str, str],
    pip_lines: list[tuple[str, str]],
    npm_deps: dict[str, str],
) -> list[dict]:
    matches: list[dict] = []
    for package_name, line in pip_lines:
        if package_name in pip_markers:
            matches.append({"name": pip_markers[package_name], "evidence": f"requirements.txt:{line}"})
    for name, version in npm_deps.items():
        key = name.lower()
        if key in js_markers:
            matches.append({"name": js_markers[key], "evidence": f"package.json:{name}@{version}"})
    return matches


def detect_ai_usage(repo_path: Path) -> dict:
    pip_lines = _iter_pip_package_lines(repo_path)
    npm_deps = _npm_dependencies(repo_path)

    return {
        "providers": _match_ai_markers(
            AI_PROVIDER_MARKERS_PY, AI_PROVIDER_MARKERS_JS, pip_lines, npm_deps
        ),
        "orchestration": _match_ai_markers(
            AI_ORCHESTRATION_MARKERS_PY, AI_ORCHESTRATION_MARKERS_JS, pip_lines, npm_deps
        ),
        "vector_stores": _match_ai_markers(AI_VECTOR_STORE_MARKERS_PY, {}, pip_lines, npm_deps),
        "local_inference": _match_ai_markers(
            AI_LOCAL_INFERENCE_MARKERS_PY, {}, pip_lines, npm_deps
        ),
        "mcp": _match_ai_markers(AI_MCP_MARKERS_PY, AI_MCP_MARKERS_JS, pip_lines, npm_deps),
    }
```

Note the npm `@anthropic-ai/sdk` and `@modelcontextprotocol/sdk` marker keys are already
lowercase (npm scoped package names are conventionally lowercase already), so the existing
`key = name.lower()` matching in `_match_ai_markers` works without special-casing the `@`
prefix.

- [ ] **Step 6: Run tests to verify they pass**

```bash
cd prototype && pytest tests/test_detect.py -v
```
Expected: PASS — all tests, including the 5 new `detect_ai_usage` tests and the pre-existing
`detect_frameworks`/`detect_languages`/`detect_build_tools`/`detect_monorepo` tests unchanged.

- [ ] **Step 7: Commit**

```bash
git add prototype/veridion/scanner/detect.py prototype/tests/test_detect.py
git commit -m "feat: add AI/LLM usage detection, extract shared manifest-parsing helpers"
```

---

## Task 2: Wire `ai_usage` into `evidence.py`

**Files:**
- Modify: `prototype/veridion/evidence.py`
- Modify: `prototype/tests/test_evidence.py`

**Interfaces:**
- Consumes: `detect_ai_usage` (Task 1) by its exact name and return shape.
- Produces: `evidence["repository"]["ai_usage"]` — a new key inside the existing `repository`
  dict, not a new top-level `evidence.json` key.

- [ ] **Step 1: Write the failing test**

Append to `prototype/tests/test_evidence.py`:
```python
def test_scan_repository_includes_ai_usage_in_repository_block(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "requirements.txt").write_text("openai==1.30.0\n")
    (repo / "main.py").write_text("x = 1\n")

    with patch("veridion.evidence.check_dependency_vulnerabilities") as mock_check:
        mock_check.return_value = {"checked": True, "reason": None, "findings": []}
        evidence = scan_repository(repo)

    assert "ai_usage" in evidence["repository"]
    names = {p["name"] for p in evidence["repository"]["ai_usage"]["providers"]}
    assert "openai" in names
```

Check the top of `prototype/tests/test_evidence.py` for its existing `from unittest.mock
import patch` import (added in Part V's Task 3) — reuse it.

- [ ] **Step 2: Run test to verify it fails**

```bash
cd prototype && pytest tests/test_evidence.py -v
```
Expected: FAIL (`evidence["repository"]["ai_usage"]` doesn't exist yet).

- [ ] **Step 3: Modify `evidence.py`**

In `prototype/veridion/evidence.py`, update the import and `scan_repository`:
```python
from veridion.scanner.detect import (
    detect_ai_usage,
    detect_build_tools,
    detect_frameworks,
    detect_languages,
    detect_monorepo,
)
```

In `scan_repository`, add the call and wire it into the returned `repository` dict:
```python
    languages = detect_languages(repo_path)
    frameworks = detect_frameworks(repo_path)
    ai_usage = detect_ai_usage(repo_path)
    build_tools = detect_build_tools(repo_path)
```
and
```python
        "repository": {
            "languages": languages,
            "frameworks": frameworks,
            "ai_usage": ai_usage,
            "build_tools": build_tools,
            "monorepo": monorepo,
            "modules": modules,
            "dependency_graph": dependency_graph,
            "unparseable_files": unparseable_files,
        },
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd prototype && pytest -v
```
Expected: PASS — full suite, no regressions in any earlier task.

- [ ] **Step 5: Commit**

```bash
git add prototype/veridion/evidence.py prototype/tests/test_evidence.py
git commit -m "feat: wire ai_usage into evidence.repository"
```

---

## Task 3: Part VI manual content and Part II cross-reference

**Files:**
- Create: `prototype/manual/part-6-ai-usage.md`
- Modify: `prototype/manual/part-2-repository-intelligence.md`

**Interfaces:**
- Consumes: the exact `evidence.repository.ai_usage` schema produced by Task 2.
- Produces: nothing consumed by other tasks — read by the reasoning-phase agent only.

`ai_usage` does **not** get a new numbered section in Part I's output contract — unlike
Architecture (`evidence.architecture`) and Security (`evidence.security`), which are new
top-level `evidence.json` keys with their own report sections, `ai_usage` lives inside the
existing `evidence.repository` block alongside `languages`/`frameworks`. Its findings belong in
the existing "Repository Intelligence" report section (section 2), which Part II already
governs. **Do not edit `part-1-operating-instructions.md` in this task** — its output contract
is already correct as-is.

- [ ] **Step 1: Create the Part VI manual file**

Create `prototype/manual/part-6-ai-usage.md`:
```markdown
# Part VI — AI/LLM Usage Detection

This section governs how to read `evidence.repository.ai_usage`. Its findings belong in the
Repository Intelligence report section (Part II governs that section's overall structure) —
this part only covers how to interpret the `ai_usage` sub-key specifically. Follow the
mandatory verification rules in Part I for everything below.

## What's in `evidence.repository.ai_usage`

Five categories, each a list of `{name, evidence}` entries in the same shape as
`repository.frameworks`:

- `providers`: detected LLM API client packages (e.g. `openai`, `anthropic`).
- `orchestration`: detected agent/orchestration frameworks (e.g. `langchain`, `llama-index`).
- `vector_stores`: detected vector database client packages (e.g. `chromadb`, `pinecone`).
- `local_inference`: detected local/self-hosted model tooling (e.g. `transformers`, `ollama`).
- `mcp`: detected Model Context Protocol packages.

All five keys are always present, even when empty — an empty list means no package in that
category was found in `requirements.txt` or `package.json`, not that detection didn't run.

## Mandatory rules

- **A detected entry is a fact about package presence only**, evidenced by the exact manifest
  line in its `evidence` field — never a judgment about how well the package is used, how
  safely, or how it's architected. Cite the entry's `name` and `evidence` field directly.
- **Do not speculate about AI/LLM usage beyond what `ai_usage`'s five lists contain.** If none
  of the five categories have any entries, state plainly that no AI/LLM package usage was
  detected — do not infer AI usage from file names, comments, or general repository purpose.
- **`ai_usage` only reflects `requirements.txt` and `package.json`.** A repository whose
  dependencies are declared elsewhere (`pyproject.toml`, `Pipfile`, a lockfile without a
  manifest) will show empty lists here even if it does use AI/LLM packages — if
  `repository.build_tools` or other evidence suggests a dependency file format outside this
  scanner's coverage, note that as a gap rather than asserting "no AI usage."

## What counts as noteworthy

- **Any non-empty category** is worth naming explicitly with the exact package name(s) and
  their `evidence` manifest lines.
- **Multiple providers detected together** (e.g. both `openai` and `anthropic` present) is
  worth stating as a fact — evidence does not show how they're actually used in code (a
  fallback strategy, a multi-provider router, or simply two unrelated features), so do not
  speculate about the reason both are present.
- **A provider or orchestration package alongside no vector store** (or vice versa) is not
  inherently noteworthy — many legitimate AI integrations use only a provider client with no
  RAG/vector-store component at all. Do not imply an architecture is incomplete just because
  it doesn't span all five categories.

## What this section does not produce

No prompt-injection risk assessment, no RAG pipeline quality evaluation, no agent-architecture
review, no guardrail or safety evaluation, no latency or inference-cost analysis, no model
selection judgment. None of that is determinable from a manifest-matching scan — only package
presence is.
```

- [ ] **Step 2: Update Part II's manual to reference `ai_usage`**

In `prototype/manual/part-2-repository-intelligence.md`, update the "What's in
`evidence.repository`" list to add an entry after `frameworks`:
```markdown
- `frameworks`: detected frameworks, each with an `evidence` string naming the manifest line
  that proves it (e.g. `"requirements.txt:fastapi==0.110.0"`).
- `ai_usage`: detected AI/LLM provider, orchestration, vector-store, local-inference, and MCP
  package usage — see Part VI for how to interpret this sub-key specifically.
- `build_tools`: detected build tooling, same evidence-string pattern.
```

- [ ] **Step 3: Commit**

```bash
git add prototype/manual/part-6-ai-usage.md prototype/manual/part-2-repository-intelligence.md
git commit -m "docs: add Part VI AI/LLM usage manual, cross-reference from Part II"
```

---

## Task 4: Live verification (not automated)

No further code changes — confirms the wired-up detector behaves correctly against real
repositories. No network or LLM calls needed except the final, explicitly-gated
reasoning-phase step.

- [ ] **Step 1: Reinstall the prototype**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion/prototype
pip install -e ".[dev]"
```

- [ ] **Step 2: Scan Veridion's own repo and confirm the known limitation**

```bash
python3 -c "
from pathlib import Path
from veridion.evidence import scan_repository
evidence = scan_repository(Path('/Users/arihantkaul/Documents/GitHub/Veridion'), check_vulnerabilities=False)
print(evidence['repository']['ai_usage'])
"
```
Expected: all five lists empty. Veridion's own prototype declares its dependencies in
`pyproject.toml`, not `requirements.txt`/`package.json`, so this is the known, spec-documented
limitation — not a bug to chase.

- [ ] **Step 3: Scan Procta and record whatever the real result is**

```bash
python3 -c "
from pathlib import Path
from veridion.evidence import scan_repository
evidence = scan_repository(Path('/Users/arihantkaul/proctored-browser'), check_vulnerabilities=False)
print(evidence['repository']['ai_usage'])
"
```
Either real findings or clean empty lists are both a valid pass per the design spec — the
criterion is that the mechanism runs correctly and returns the right five-key shape, not a
specific expected finding.

- [ ] **Step 4: Full reasoning-phase run (requires explicit go-ahead, same as every previous part)**

Only after checking with the user first:
```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion
veridion audit /Users/arihantkaul/proctored-browser
```
Confirm any `ai_usage` findings are reported within the Repository Intelligence section (not
as a separate new top-level section), cite exact package names and manifest lines, and contain
no prompt-injection/RAG-quality/architecture judgments anywhere in the report.

- [ ] **Step 5: Record the outcome**

If all checks pass, Part VI is done — report back with whatever Procta's real `ai_usage`
result was and any surprises. If anything fails, that's the next debugging task, not a new
plan.

---

## Self-Review Notes

**Spec coverage:** the five-category detection list with exact package names (Task 1), the
shared-helper refactor with an explicit regression check (Task 1 Step 4), the
`evidence.repository.ai_usage` placement decision (Task 2, plus the explicit non-edit of Part
I's output contract in Task 3), the two-tier manual content (Task 3), and all stated success
criteria (Task 4) are each covered by a specific task.

**Placeholder scan:** no TBD/TODO; every code and manual-content block is complete and final.

**Type consistency:** `detect_ai_usage` (Task 1) → consumed by `evidence.py` (Task 2) with the
exact same five-key dict shape throughout. No naming collisions this time (unlike Part V's
`check_vulnerabilities`) since `detect_ai_usage` doesn't share a name with any parameter or
existing import in `evidence.py`.
