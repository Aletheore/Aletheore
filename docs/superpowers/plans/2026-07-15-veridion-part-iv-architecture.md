# Veridion Part IV (Architecture Review) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add structural clustering and layer-direction violation detection to Veridion's
evidence scanner, following the same deterministic-scan-then-agent-reasoning split as v1 and
Part V.

**Architecture:** One new module (`veridion/architecture.py`) with two independent, pure
functions operating on the existing `repository.dependency_graph` — no new scanning of the
filesystem, no new evidence sources, just graph analysis on data that already exists.
`evidence.py` wires both into a new top-level `architecture` block. A new
`manual/part-4-architecture.md` teaches the agent how to read it.

**Tech Stack:** Python stdlib plus `networkx` (new dependency, justified below) for
deterministic graph-community detection — no network calls anywhere in this plan, unlike
Part V.

## Global Constraints

- Both `build_clusters` and `detect_layer_violations` operate **only** on
  `repository.dependency_graph` (the `{"nodes": [...], "edges": [[from, to], ...]}` shape
  already produced by `scanner/graph.py`) — no new file-scanning logic.
- Clustering must be **deterministic**: the same `dependency_graph` must always produce the
  same cluster assignment, verified by running twice against a real repo and diffing.
- `networkx`'s `greedy_modularity_communities` was verified live during design to handle
  empty graphs, isolated nodes, single nodes, and tiny graphs gracefully with **no special
  casing needed** — do not add defensive branches for these cases, they aren't necessary.
- Layer-convention detection requires **at least 2 distinct ranks** actually present among
  classified nodes — a single matching folder name alone is not sufficient evidence of an
  intentional layering scheme.
- No secret's real value, no network calls, no LLM calls anywhere in this plan (matching v1
  and Part V's existing constraints, restated here since this is a new contributor's first
  read of this plan, not because anything in this task touches those areas directly).

---

## Task 1: Structural clustering (`architecture.py` — `build_clusters`)

**Files:**
- Create: `prototype/veridion/architecture.py`
- Test: `prototype/tests/test_architecture.py`
- Modify: `prototype/pyproject.toml` (add `networkx` dependency)

**Interfaces:**
- Consumes: nothing from other tasks — operates on a plain `dependency_graph` dict shaped
  `{"nodes": list[str], "edges": list[list[str]]}`, exactly what `scanner/graph.py`'s
  `build_module_graph` already returns as its second element.
- Produces: `build_clusters(dependency_graph: dict) -> tuple[list[dict], list[dict]]`
  returning `(clusters, cross_cluster_edges)`, where each cluster is
  `{"id": int, "modules": list[str], "internal_edges": int}` and each cross-cluster entry is
  `{"from_cluster": int, "to_cluster": int, "count": int, "edges": list[list[str]]}`. Task 3
  calls this function by this exact name and return shape.

- [ ] **Step 1: Add the `networkx` dependency**

In `prototype/pyproject.toml`, update the `dependencies` list:
```toml
dependencies = [
    "tree-sitter>=0.25,<0.26",
    "tree-sitter-python>=0.25,<0.26",
    "tree-sitter-javascript>=0.25,<0.26",
    "tree-sitter-typescript>=0.23,<0.24",
    "certifi>=2024.0.0",
    "networkx>=3.0,<4.0",
]
```

- [ ] **Step 2: Write the failing test**

Create `prototype/tests/test_architecture.py`:
```python
from veridion.architecture import build_clusters


def test_build_clusters_finds_two_clusters_with_a_thin_bridge():
    dependency_graph = {
        "nodes": ["a.py", "b.py", "c.py", "x.py", "y.py", "z.py"],
        "edges": [
            ["a.py", "b.py"], ["b.py", "a.py"], ["a.py", "c.py"], ["c.py", "b.py"],
            ["x.py", "y.py"], ["y.py", "x.py"], ["x.py", "z.py"], ["z.py", "y.py"],
            ["a.py", "x.py"],
        ],
    }

    clusters, cross_cluster_edges = build_clusters(dependency_graph)

    cluster_by_module = {}
    for cluster in clusters:
        for module in cluster["modules"]:
            cluster_by_module[module] = cluster["id"]

    assert cluster_by_module["a.py"] == cluster_by_module["b.py"] == cluster_by_module["c.py"]
    assert cluster_by_module["x.py"] == cluster_by_module["y.py"] == cluster_by_module["z.py"]
    assert cluster_by_module["a.py"] != cluster_by_module["x.py"]

    abc_cluster = next(c for c in clusters if "a.py" in c["modules"])
    assert abc_cluster["internal_edges"] == 4

    assert len(cross_cluster_edges) == 1
    bridge = cross_cluster_edges[0]
    assert bridge["count"] == 1
    assert bridge["edges"] == [["a.py", "x.py"]]


def test_build_clusters_handles_isolated_nodes_without_crashing():
    dependency_graph = {"nodes": ["a.py", "b.py", "c.py"], "edges": []}

    clusters, cross_cluster_edges = build_clusters(dependency_graph)

    all_modules = sorted(m for c in clusters for m in c["modules"])
    assert all_modules == ["a.py", "b.py", "c.py"]
    assert cross_cluster_edges == []


def test_build_clusters_handles_empty_graph():
    clusters, cross_cluster_edges = build_clusters({"nodes": [], "edges": []})

    assert clusters == []
    assert cross_cluster_edges == []


def test_build_clusters_is_deterministic_across_runs():
    dependency_graph = {
        "nodes": ["a.py", "b.py", "c.py", "x.py", "y.py", "z.py"],
        "edges": [
            ["a.py", "b.py"], ["b.py", "a.py"], ["a.py", "c.py"], ["c.py", "b.py"],
            ["x.py", "y.py"], ["y.py", "x.py"], ["x.py", "z.py"], ["z.py", "y.py"],
            ["a.py", "x.py"],
        ],
    }

    first = build_clusters(dependency_graph)
    second = build_clusters(dependency_graph)

    assert first == second
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd prototype && pip install -e ".[dev]" && pytest tests/test_architecture.py -v
```
Expected: FAIL (`veridion.architecture` doesn't exist — `ModuleNotFoundError`).

- [ ] **Step 4: Implement `build_clusters`**

Create `prototype/veridion/architecture.py`:
```python
import networkx as nx
from networkx.algorithms.community import greedy_modularity_communities


def build_clusters(dependency_graph: dict) -> tuple[list[dict], list[dict]]:
    graph = nx.Graph()
    graph.add_nodes_from(dependency_graph["nodes"])
    graph.add_edges_from(dependency_graph["edges"])

    communities = list(greedy_modularity_communities(graph))

    cluster_of: dict[str, int] = {}
    clusters = []
    for cluster_id, community in enumerate(communities):
        modules = sorted(community)
        for module in modules:
            cluster_of[module] = cluster_id
        clusters.append({"id": cluster_id, "modules": modules, "internal_edges": 0})

    for a, b in dependency_graph["edges"]:
        if cluster_of.get(a) is not None and cluster_of.get(a) == cluster_of.get(b):
            clusters[cluster_of[a]]["internal_edges"] += 1

    cross_pairs: dict[tuple[int, int], list[list[str]]] = {}
    for a, b in dependency_graph["edges"]:
        ca, cb = cluster_of.get(a), cluster_of.get(b)
        if ca is None or cb is None or ca == cb:
            continue
        cross_pairs.setdefault((ca, cb), []).append([a, b])

    cross_cluster_edges = [
        {"from_cluster": ca, "to_cluster": cb, "count": len(edges), "edges": edges}
        for (ca, cb), edges in sorted(cross_pairs.items())
    ]

    return clusters, cross_cluster_edges
```

Note: no special-casing for empty graphs or isolated nodes — `greedy_modularity_communities`
was verified during design to handle `nx.Graph()` with no edges, a single node, and isolated
nodes mixed with connected components correctly on its own (returns one singleton community
per isolated node, `[]` for a fully empty graph). Adding defensive branches for these cases
would be redundant, untested-by-necessity code.

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd prototype && pytest tests/test_architecture.py -v
```
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add prototype/veridion/architecture.py prototype/tests/test_architecture.py prototype/pyproject.toml
git commit -m "feat: add structural clustering via deterministic graph community detection"
```

---

## Task 2: Layer-direction violation detection (`architecture.py` — `detect_layer_violations`)

**Files:**
- Modify: `prototype/veridion/architecture.py`
- Modify: `prototype/tests/test_architecture.py`

**Interfaces:**
- Consumes: the same `dependency_graph` shape as Task 1 — this task does not depend on
  `build_clusters`, the two functions are independent.
- Produces: `detect_layer_violations(dependency_graph: dict) -> dict` returning
  `{"convention_detected": bool, "layers": list[dict], "violations": list[dict]}`, where each
  layer entry is `{"name": str, "rank": int, "folders": list[str]}` and each violation is
  `{"from": str, "to": str, "reason": str}`. Task 3 calls this function by this exact name and
  return shape.

- [ ] **Step 1: Write the failing tests**

Append to `prototype/tests/test_architecture.py`:
```python
from veridion.architecture import detect_layer_violations


def test_detect_layer_violations_finds_a_real_violation():
    dependency_graph = {
        "nodes": ["app/domain/user.py", "app/infrastructure/db.py", "app/services/auth.py"],
        "edges": [
            ["app/domain/user.py", "app/infrastructure/db.py"],
            ["app/services/auth.py", "app/domain/user.py"],
        ],
    }

    result = detect_layer_violations(dependency_graph)

    assert result["convention_detected"] is True
    assert len(result["violations"]) == 1
    violation = result["violations"][0]
    assert violation["from"] == "app/domain/user.py"
    assert violation["to"] == "app/infrastructure/db.py"
    assert "domain" in violation["reason"]
    assert "infrastructure" in violation["reason"]

    layer_names = {layer["name"] for layer in result["layers"]}
    assert layer_names == {"domain", "infrastructure", "services"}


def test_detect_layer_violations_clean_case_no_violations():
    dependency_graph = {
        "nodes": ["app/domain/user.py", "app/infrastructure/db.py"],
        "edges": [["app/infrastructure/db.py", "app/domain/user.py"]],
    }

    result = detect_layer_violations(dependency_graph)

    assert result["convention_detected"] is True
    assert result["violations"] == []


def test_detect_layer_violations_no_convention_when_only_one_rank_present():
    dependency_graph = {
        "nodes": ["app/domain/a.py", "app/domain/b.py"],
        "edges": [["app/domain/a.py", "app/domain/b.py"]],
    }

    result = detect_layer_violations(dependency_graph)

    assert result == {"convention_detected": False, "layers": [], "violations": []}


def test_detect_layer_violations_no_convention_when_no_layer_folders_at_all():
    dependency_graph = {
        "nodes": ["app/routes.py", "app/helpers.py"],
        "edges": [["app/routes.py", "app/helpers.py"]],
    }

    result = detect_layer_violations(dependency_graph)

    assert result == {"convention_detected": False, "layers": [], "violations": []}


def test_detect_layer_violations_recognizes_infra_abbreviation():
    dependency_graph = {
        "nodes": ["app/domain/user.py", "app/infra/db.py"],
        "edges": [["app/domain/user.py", "app/infra/db.py"]],
    }

    result = detect_layer_violations(dependency_graph)

    assert result["convention_detected"] is True
    assert len(result["violations"]) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd prototype && pytest tests/test_architecture.py -v
```
Expected: the 5 new tests FAIL (`detect_layer_violations` doesn't exist yet); the 4 clustering
tests from Task 1 still PASS.

- [ ] **Step 3: Implement `detect_layer_violations`**

Append to `prototype/veridion/architecture.py`:
```python
from pathlib import Path

# "infra" is included alongside "infrastructure" because it's a common enough
# real-world abbreviation that it turned up by accident while writing this
# module's own test fixtures - a good sign it's worth covering deliberately
# rather than only the unabbreviated form.
LAYER_FOLDER_MARKERS = {
    "domain": 0, "core": 0, "entities": 0,
    "application": 1, "services": 1, "use_cases": 1,
    "infrastructure": 2, "infra": 2, "adapters": 2, "api": 2, "routers": 2,
    "web": 2, "controllers": 2,
}


def _classify_module_rank(rel_path: str) -> tuple[str, int] | None:
    parts = Path(rel_path).parts
    for part in parts[:-1]:
        if part in LAYER_FOLDER_MARKERS:
            return part, LAYER_FOLDER_MARKERS[part]
    return None


def detect_layer_violations(dependency_graph: dict) -> dict:
    classifications: dict[str, tuple[str, int]] = {}
    for node in dependency_graph["nodes"]:
        result = _classify_module_rank(node)
        if result is not None:
            classifications[node] = result

    distinct_ranks = {rank for _, rank in classifications.values()}
    if len(distinct_ranks) < 2:
        return {"convention_detected": False, "layers": [], "violations": []}

    layer_folders: dict[str, set[str]] = {}
    for node, (name, _rank) in classifications.items():
        parts = Path(node).parts
        idx = parts.index(name)
        folder = str(Path(*parts[: idx + 1]))
        layer_folders.setdefault(name, set()).add(folder)

    layers = [
        {"name": name, "rank": LAYER_FOLDER_MARKERS[name], "folders": sorted(folders)}
        for name, folders in sorted(layer_folders.items())
    ]

    violations = []
    for from_node, to_node in dependency_graph["edges"]:
        from_info = classifications.get(from_node)
        to_info = classifications.get(to_node)
        if from_info is None or to_info is None:
            continue
        from_name, from_rank = from_info
        to_name, to_rank = to_info
        if from_rank < to_rank:
            violations.append(
                {
                    "from": from_node,
                    "to": to_node,
                    "reason": f"inner layer '{from_name}' imports outer layer '{to_name}'",
                }
            )

    return {"convention_detected": True, "layers": layers, "violations": violations}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd prototype && pytest tests/test_architecture.py -v
```
Expected: PASS (9 tests total — 4 from Task 1, 5 from this task).

- [ ] **Step 5: Commit**

```bash
git add prototype/veridion/architecture.py prototype/tests/test_architecture.py
git commit -m "feat: add layer-direction violation detection"
```

---

## Task 3: Wire `architecture` into `evidence.py`

**Files:**
- Modify: `prototype/veridion/evidence.py`
- Modify: `prototype/tests/test_evidence.py`

**Interfaces:**
- Consumes: `build_clusters` (Task 1), `detect_layer_violations` (Task 2) by their exact
  names and return shapes.
- Produces: `scan_repository` gains a new `evidence["architecture"]` key shaped
  `{"clusters": [...], "cross_cluster_edges": [...], "layer_violations": {...}}`. No new
  parameters on `scan_repository` — unlike Part V's vulnerability check, there's no
  network/opt-out concern here, this always runs.

- [ ] **Step 1: Write the failing test**

Append to `prototype/tests/test_evidence.py`:
```python
def test_scan_repository_includes_architecture_block(tmp_path):
    repo = tmp_path / "repo"
    (repo / "app").mkdir(parents=True)
    (repo / "app" / "__init__.py").write_text("")
    (repo / "app" / "a.py").write_text("from app import b\n")
    (repo / "app" / "b.py").write_text("x = 1\n")

    with patch("veridion.evidence.check_dependency_vulnerabilities") as mock_check:
        mock_check.return_value = {"checked": True, "reason": None, "findings": []}
        evidence = scan_repository(repo)

    assert "architecture" in evidence
    assert "clusters" in evidence["architecture"]
    assert "cross_cluster_edges" in evidence["architecture"]
    assert "layer_violations" in evidence["architecture"]
    assert evidence["architecture"]["layer_violations"]["convention_detected"] is False
```

Check the top of `prototype/tests/test_evidence.py` for its existing `from unittest.mock import
patch` import (added in Part V's Task 3) — reuse it, don't re-import.

- [ ] **Step 2: Run test to verify it fails**

```bash
cd prototype && pytest tests/test_evidence.py -v
```
Expected: FAIL (`evidence["architecture"]` doesn't exist yet).

- [ ] **Step 3: Modify `evidence.py`**

In `prototype/veridion/evidence.py`, add the import and wire the new block into
`scan_repository`'s return value:
```python
import json
from datetime import datetime, timezone
from pathlib import Path

from veridion.architecture import build_clusters, detect_layer_violations
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
    clusters, cross_cluster_edges = build_clusters(dependency_graph)
    layer_violations = detect_layer_violations(dependency_graph)

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
        "architecture": {
            "clusters": clusters,
            "cross_cluster_edges": cross_cluster_edges,
            "layer_violations": layer_violations,
        },
    }


def write_evidence(evidence: dict, repo_path: Path) -> Path:
    veridion_dir = repo_path / ".veridion"
    veridion_dir.mkdir(parents=True, exist_ok=True)
    output_path = veridion_dir / "evidence.json"
    output_path.write_text(json.dumps(evidence, indent=2))
    return output_path
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd prototype && pytest -v
```
Expected: PASS — full suite, no regressions in any earlier task.

- [ ] **Step 5: Commit**

```bash
git add prototype/veridion/evidence.py prototype/tests/test_evidence.py
git commit -m "feat: wire architecture evidence into scan_repository"
```

---

## Task 4: Part IV manual content

**Files:**
- Create: `prototype/manual/part-4-architecture.md`

**Interfaces:**
- Consumes: the exact `evidence.architecture` schema produced by Task 3.
- Produces: nothing consumed by other tasks — read by the reasoning-phase agent only.

- [ ] **Step 1: Write the manual file**

Create `prototype/manual/part-4-architecture.md`:
```markdown
# Part IV — Architecture Review

This section governs how to read `evidence.architecture`. Follow the mandatory verification
rules in Part I for everything below.

## What's in `evidence.architecture`

- `clusters`: groups of modules found by running graph-community detection on
  `repository.dependency_graph` — each with an `id`, its `modules` list, and its
  `internal_edges` count (edges between two modules in the same cluster).
- `cross_cluster_edges`: every edge that crosses a cluster boundary, grouped by
  `(from_cluster, to_cluster)` pair, with the exact `edges` list and a `count`.
- `layer_violations.convention_detected`: whether a recognizable layering convention
  (domain/application/infrastructure-style folder naming) was found in this repository.
- `layer_violations.layers`: the specific folder-name-to-rank mapping actually detected, when
  `convention_detected` is `true`.
- `layer_violations.violations`: modules that import from an outer architectural layer while
  themselves belonging to an inner one — each with the exact `from`/`to` file paths and a
  `reason`.

## Mandatory rules

- **A cluster is a structural grouping derived from import coupling, not evidence of
  intentional architectural design.** Never claim a cluster represents a deliberate module
  boundary the codebase's author chose — say what the clustering found, not why it exists.
- **When `convention_detected` is `false`, state plainly that no layering convention was
  detected.** This is the common, normal case for most repositories — most codebases don't
  use domain/infrastructure-style folder naming. Do not describe this as a limitation, gap, or
  something to apologize for.
- **Every cross-cluster or violation claim must cite the exact file path(s)** from
  `cross_cluster_edges[].edges` or `layer_violations.violations[]` — never a cluster ID or a
  count alone, and never a claim about coupling that isn't backed by a specific edge in the
  evidence.

## What counts as noteworthy

- **A `layer_violations.violations` entry** is worth naming explicitly — both files involved
  and the two layer names crossed (state them exactly as they appear in `reason`).
- **A high `cross_cluster_edges` count relative to a cluster's `internal_edges`** is worth
  noting as something worth investigating, never as a confirmed problem. A shared utility
  module legitimately imported by many otherwise-unrelated clusters is expected, normal
  structure — that is a very different situation from two specific clusters being unexpectedly
  tangled with each other. Distinguish "many clusters depend on this one shared thing"
  (usually fine, common, not inherently a finding) from "these two particular clusters share
  an unusually large number of edges with each other" (more interesting, worth naming
  specifically) before treating cross-cluster coupling as noteworthy.

## What this section does not produce

Do not attempt to name or label an architectural pattern (hexagonal, clean, layered, MVC,
CQRS, event sourcing, microservices, DDD, or any other named style). Do not assess
abstraction quality, interface design, or identify named design patterns (repository, factory,
observer, mediator, etc.) — none of that is determinable from the evidence this scanner
produces. Report clusters and layer violations as raw structural facts; leave architectural
labeling and design-quality judgment out of scope entirely.
```

- [ ] **Step 2: Commit**

```bash
git add prototype/manual/part-4-architecture.md
git commit -m "docs: add Part IV architecture manual"
```

---

## Task 5: Live dogfood acceptance gate (not automated)

This task has no code changes — the go/no-go check from the design spec's Success Criteria,
run manually against Procta and Veridion's own repo. No live agent call is needed anywhere in
this task (unlike Part V/v1's Task 9/5) — everything here is the scan phase only, which
involves no network and no LLM.

- [ ] **Step 1: Reinstall the prototype**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion/prototype
pip install -e ".[dev]"
```

- [ ] **Step 2: Run the scan phase against Procta twice, diff the cluster assignments**

```bash
python3 -c "
from pathlib import Path
from veridion.evidence import scan_repository

repo = Path('/Users/arihantkaul/proctored-browser')
first = scan_repository(repo, check_vulnerabilities=False)
second = scan_repository(repo, check_vulnerabilities=False)

print('clusters identical across two runs:', first['architecture']['clusters'] == second['architecture']['clusters'])
print('cross_cluster_edges identical:', first['architecture']['cross_cluster_edges'] == second['architecture']['cross_cluster_edges'])
print('cluster count:', len(first['architecture']['clusters']))
print('convention_detected:', first['architecture']['layer_violations']['convention_detected'])
"
```
(`check_vulnerabilities=False` here just avoids an unnecessary live OSV.dev call while
checking something unrelated to vulnerabilities — it has no bearing on the architecture
result.) Confirm both `identical` lines print `True` — this is Success Criterion 1 from the
design spec (reproducibility).

- [ ] **Step 3: Confirm `convention_detected` is `false` on both real repos**

Continue in the same script or a fresh one, also checking Veridion's own repo:
```bash
python3 -c "
from pathlib import Path
from veridion.evidence import scan_repository

for label, path in [('Procta', '/Users/arihantkaul/proctored-browser'), ('Veridion', '/Users/arihantkaul/Documents/GitHub/Veridion')]:
    evidence = scan_repository(Path(path), check_vulnerabilities=False)
    lv = evidence['architecture']['layer_violations']
    print(label, '-> convention_detected:', lv['convention_detected'])
"
```
Confirm both print `False` — this is Success Criterion 2. Per the design spec's stated
coverage gap, this is expected: neither repo uses domain/infrastructure-style folder naming,
so this only validates the "no convention" path on real data. The "convention found,
violation caught" path was validated by Task 2's synthetic fixtures only, and stays that way
— this is a known, accepted limitation of this increment, not something to "fix" by forcing a
convention onto real data that doesn't have one.

- [ ] **Step 4: Full reasoning-phase run (requires explicit go-ahead, same as v1/Part V)**

Only after checking with the user first — this is a live `claude` call against Procta's
private source:
```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion
veridion audit /Users/arihantkaul/proctored-browser
```
Confirm the resulting `audit-report.md` has an Architecture section that cites exact cluster
membership and cross-cluster edges for any coupling claim, states plainly that no layering
convention was detected (rather than omitting the topic or forcing a guess), and contains
zero named-architecture-pattern claims anywhere in the output (no "hexagonal," "MVC," "clean
architecture," etc.) — this is Success Criterion 3.

- [ ] **Step 5: Record the outcome**

If all criteria pass, Part IV is done — report back with any surprises (cluster counts that
seem too coarse or too fine on real data, cross-cluster edges that turned out to be
particularly interesting or particularly noisy). If any criterion fails, that's the next
debugging task, not a new plan.

---

## Self-Review Notes

**Spec coverage:** every section of the Part IV design spec maps to a task — clustering
mechanism and reproducibility (Task 1), layer-violation mechanism including the
"infra"-abbreviation refinement discovered during design verification (Task 2), evidence
schema wiring (Task 3), manual content including the false-positive-avoidance guidance about
shared utility modules (Task 4), and all three numbered success criteria (Task 5, mapped 1:1).

**Placeholder scan:** no TBD/TODO; every code block is complete and was verified against real
execution during design (the clustering determinism check, the edge-case handling, and the
layer-violation logic were all run live before this plan was written, not assumed).

**Type consistency:** `build_clusters` and `detect_layer_violations` (Task 1/2) → consumed by
`evidence.py` (Task 3) with the exact same return shapes throughout, no naming collisions this
time (unlike Part V's `check_vulnerabilities` collision) since neither function name collides
with any `scan_repository` parameter.
