# Veridion Clustering & Layer-Convention Configurability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a scanned repo declare custom layer-folder markers and clustering resolution via
a committed `.veridion.json`, closing both items from Part IV's original open questions.

**Architecture:** One new function (`load_architecture_config`) reads and validates
`.veridion.json`. `build_clusters` and `detect_layer_violations` each gain one new optional
parameter. `evidence.py` loads the config once and threads the relevant piece into each.

**Tech Stack:** Python stdlib only (`json`). No new dependencies, no network calls anywhere in
this plan.

## Global Constraints

- `.veridion.json` is read from the **scanned repo's own root** — no invoker-supplied
  configuration (no CLI flag, no personal config file) exists anywhere in this plan.
- Missing file, invalid JSON, or wrong-typed values are all treated identically: ignored,
  defaults apply, no crash — same graceful-degradation shape as `_npm_dependencies`'s
  `JSONDecodeError` handling elsewhere in this codebase.
- Custom `layer_markers` **extend/override** `LAYER_FOLDER_MARKERS`, never fully replace it.
- `evidence.architecture.config_applied` is `null` when no valid config was found — never an
  empty dict, so "no config" and "an empty config object" stay distinguishable.
- Reproducibility must hold: the same repo state (config file included) must always produce
  identical evidence — this is verified explicitly in Task 4, not assumed.

---

## Task 1: `load_architecture_config`

**Files:**
- Modify: `prototype/veridion/architecture.py`
- Test: `prototype/tests/test_architecture.py`

**Interfaces:**
- Consumes: nothing from other tasks — self-contained.
- Produces: `load_architecture_config(repo_path: Path) -> dict | None` returning `None` when
  no valid config exists, otherwise always both keys present:
  `{"layer_markers": dict[str, int], "cluster_resolution": float}`. Task 3 calls this by its
  exact name and return shape. No leading underscore — unlike this file's existing
  underscore-prefixed internal helpers, this function is imported into `evidence.py`.

- [ ] **Step 1: Write the failing tests**

Append to `prototype/tests/test_architecture.py`:
```python
import json

from veridion.architecture import load_architecture_config


def test_load_architecture_config_reads_a_valid_file(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".veridion.json").write_text(
        json.dumps({"layer_markers": {"biz": 1}, "cluster_resolution": 1.5})
    )

    result = load_architecture_config(repo)

    assert result == {"layer_markers": {"biz": 1}, "cluster_resolution": 1.5}


def test_load_architecture_config_returns_none_when_file_missing(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    assert load_architecture_config(repo) is None


def test_load_architecture_config_returns_none_on_malformed_json(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".veridion.json").write_text("{not valid json")

    assert load_architecture_config(repo) is None


def test_load_architecture_config_fills_defaults_when_only_one_key_present(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".veridion.json").write_text(json.dumps({"layer_markers": {"biz": 1}}))

    result = load_architecture_config(repo)

    assert result == {"layer_markers": {"biz": 1}, "cluster_resolution": 1.0}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd prototype && pytest tests/test_architecture.py -v -k load_architecture_config
```
Expected: FAIL (`load_architecture_config` doesn't exist yet).

- [ ] **Step 3: Implement `load_architecture_config`**

In `prototype/veridion/architecture.py`, add `import json` at the top, and add the function
after the existing imports (before `LAYER_FOLDER_MARKERS` or after — placement among the
module's other top-level definitions doesn't matter, just keep it out of any existing
function body):
```python
def load_architecture_config(repo_path: Path) -> dict | None:
    config_file = repo_path / ".veridion.json"
    if not config_file.exists():
        return None
    try:
        data = json.loads(config_file.read_text(encoding="utf-8", errors="ignore"))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    layer_markers = data.get("layer_markers", {})
    if not isinstance(layer_markers, dict):
        layer_markers = {}

    cluster_resolution = data.get("cluster_resolution", 1.0)
    if not isinstance(cluster_resolution, (int, float)) or isinstance(cluster_resolution, bool):
        cluster_resolution = 1.0

    return {"layer_markers": layer_markers, "cluster_resolution": float(cluster_resolution)}
```

Note the `isinstance(cluster_resolution, bool)` check: in Python, `bool` is a subclass of
`int`, so `isinstance(True, (int, float))` is `True` — without explicitly excluding `bool`, a
config value of `"cluster_resolution": true` would silently pass the type check and become
`1.0` (since `float(True) == 1.0`), masking a genuinely malformed config value as if it were
the intentional default. Excluding `bool` makes that case fall through to the same `1.0`
default for a different, correct reason (wrong type, not "happens to look like a valid float").

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd prototype && pytest tests/test_architecture.py -v -k load_architecture_config
```
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add prototype/veridion/architecture.py prototype/tests/test_architecture.py
git commit -m "feat: add .veridion.json config loading for architecture settings"
```

---

## Task 2: `resolution` and `custom_markers` parameters

**Files:**
- Modify: `prototype/veridion/architecture.py`
- Test: `prototype/tests/test_architecture.py`

**Interfaces:**
- Consumes: nothing from Task 1 directly (this task doesn't call `load_architecture_config` —
  that wiring is Task 3's job). This task only changes the two functions' own signatures.
- Produces: `build_clusters(dependency_graph: dict, resolution: float = 1.0) -> tuple[list[dict], list[dict]]`
  and `detect_layer_violations(dependency_graph: dict, custom_markers: dict[str, int] | None = None) -> dict`
  — both gain one new optional parameter, existing callers (with no third argument) are
  unaffected. Task 3 calls both with the new parameter.

- [ ] **Step 1: Write the failing tests**

Append to `prototype/tests/test_architecture.py`:
```python
def test_build_clusters_resolution_parameter_changes_cluster_count():
    # Verified live before this plan was written: two tightly-coupled triangles bridged
    # by one edge produce exactly 2 communities at resolution 1.0 (the default), and split
    # into 6 singleton communities at resolution 5.0.
    dependency_graph = {
        "nodes": ["a", "b", "c", "d", "e", "f"],
        "edges": [
            ["a", "b"], ["a", "c"], ["b", "c"],
            ["d", "e"], ["d", "f"], ["e", "f"],
            ["c", "d"],
        ],
    }

    clusters_default, _ = build_clusters(dependency_graph)
    clusters_high_resolution, _ = build_clusters(dependency_graph, resolution=5.0)

    assert len(clusters_default) == 2
    assert len(clusters_high_resolution) == 6


def test_detect_layer_violations_custom_marker_enables_detection():
    dependency_graph = {
        "nodes": ["app/biz/order.py", "app/routers/orders.py"],
        "edges": [["app/routers/orders.py", "app/biz/order.py"]],
    }

    without_custom = detect_layer_violations(dependency_graph)
    assert without_custom["convention_detected"] is False

    with_custom = detect_layer_violations(dependency_graph, custom_markers={"biz": 1})
    assert with_custom["convention_detected"] is True
    layer_names = {layer["name"] for layer in with_custom["layers"]}
    assert layer_names == {"biz", "routers"}


def test_detect_layer_violations_custom_marker_overrides_built_in_rank():
    dependency_graph = {
        "nodes": ["app/domain/user.py", "app/services/auth.py"],
        "edges": [["app/domain/user.py", "app/services/auth.py"]],
    }

    # built-in ranks: domain=0, services=1 -> domain importing services is a violation
    default_result = detect_layer_violations(dependency_graph)
    assert len(default_result["violations"]) == 1

    # override services to rank 0 (same as domain) -> no longer a violation
    overridden_result = detect_layer_violations(dependency_graph, custom_markers={"services": 0})
    assert overridden_result["violations"] == []
    services_layer = next(l for l in overridden_result["layers"] if l["name"] == "services")
    assert services_layer["rank"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd prototype && pytest tests/test_architecture.py -v -k "resolution or custom_marker"
```
Expected: FAIL (`resolution` and `custom_markers` parameters don't exist yet).

- [ ] **Step 3: Update `build_clusters`, `_classify_module_rank`, and `detect_layer_violations`**

In `prototype/veridion/architecture.py`, update `build_clusters`'s signature and its call to
`greedy_modularity_communities`:
```python
def build_clusters(dependency_graph: dict, resolution: float = 1.0) -> tuple[list[dict], list[dict]]:
    graph = nx.Graph()
    graph.add_nodes_from(dependency_graph["nodes"])
    graph.add_edges_from(dependency_graph["edges"])

    communities = list(greedy_modularity_communities(graph, resolution=resolution))
    ...
```
(the rest of `build_clusters`'s body is unchanged — only the signature and the
`greedy_modularity_communities` call gain the new argument).

Change `_classify_module_rank` to take a `markers` parameter instead of referencing
`LAYER_FOLDER_MARKERS` directly:
```python
def _classify_module_rank(rel_path: str, markers: dict[str, int]) -> tuple[str, int] | None:
    parts = Path(rel_path).parts
    for part in parts[:-1]:
        if part in markers:
            return part, markers[part]
    return None
```

Update `detect_layer_violations` to build the merged dict and pass it through:
```python
def detect_layer_violations(
    dependency_graph: dict, custom_markers: dict[str, int] | None = None
) -> dict:
    effective_markers = {**LAYER_FOLDER_MARKERS, **(custom_markers or {})}

    classifications: dict[str, tuple[str, int]] = {}
    for node in dependency_graph["nodes"]:
        result = _classify_module_rank(node, effective_markers)
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
        {"name": name, "rank": effective_markers[name], "folders": sorted(folders)}
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
(this replaces the entire existing function body — the only real changes are the new
parameter, the `effective_markers` merge at the top, passing `effective_markers` into
`_classify_module_rank`, and using `effective_markers[name]` instead of
`LAYER_FOLDER_MARKERS[name]` when building `layers`; the violation-detection loop itself is
byte-for-byte unchanged).

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd prototype && pytest tests/test_architecture.py -v
```
Expected: PASS — full file, including all pre-existing Part IV tests (which call both
functions with no third argument, exercising the new parameters' defaults) and the 3 new
tests from this task.

- [ ] **Step 5: Commit**

```bash
git add prototype/veridion/architecture.py prototype/tests/test_architecture.py
git commit -m "feat: add resolution and custom_markers parameters to clustering/layer-violation detection"
```

---

## Task 3: Wire config into `evidence.py`

**Files:**
- Modify: `prototype/veridion/evidence.py`
- Test: `prototype/tests/test_evidence.py`

**Interfaces:**
- Consumes: `load_architecture_config` (Task 1), `build_clusters`'s `resolution` parameter and
  `detect_layer_violations`'s `custom_markers` parameter (Task 2), all by exact name.
- Produces: `evidence["architecture"]["config_applied"]` — a new key, `None` when no config
  was found, otherwise the exact dict `load_architecture_config` returned.

- [ ] **Step 1: Write the failing test**

Append to `prototype/tests/test_evidence.py`:
```python
def test_scan_repository_applies_veridion_json_config(tmp_path):
    repo = tmp_path / "repo"
    (repo / "app" / "biz").mkdir(parents=True)
    (repo / "app" / "routers").mkdir(parents=True)
    (repo / "app" / "biz" / "order.py").write_text("x = 1\n")
    (repo / "app" / "routers" / "orders.py").write_text("from app.biz import order\n")
    (repo / ".veridion.json").write_text('{"layer_markers": {"biz": 1}}')

    with patch("veridion.evidence.check_dependency_vulnerabilities") as mock_check:
        mock_check.return_value = {"checked": True, "reason": None, "findings": []}
        evidence = scan_repository(repo, scan_git_history=False)

    assert evidence["architecture"]["config_applied"] == {
        "layer_markers": {"biz": 1},
        "cluster_resolution": 1.0,
    }
    assert evidence["architecture"]["layer_violations"]["convention_detected"] is True


def test_scan_repository_config_applied_is_none_without_veridion_json(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("x = 1\n")

    with patch("veridion.evidence.check_dependency_vulnerabilities") as mock_check:
        mock_check.return_value = {"checked": True, "reason": None, "findings": []}
        evidence = scan_repository(repo, scan_git_history=False)

    assert evidence["architecture"]["config_applied"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd prototype && pytest tests/test_evidence.py -v -k config_applied
```
Expected: FAIL (`config_applied` key doesn't exist yet).

- [ ] **Step 3: Modify `evidence.py`**

Update the import:
```python
from veridion.architecture import build_clusters, detect_layer_violations, load_architecture_config
```

In `scan_repository`, load the config once and thread it through:
```python
    architecture_config = load_architecture_config(repo_path)
    resolution = architecture_config["cluster_resolution"] if architecture_config else 1.0
    custom_markers = architecture_config["layer_markers"] if architecture_config else None

    clusters, cross_cluster_edges = build_clusters(dependency_graph, resolution=resolution)
    layer_violations = detect_layer_violations(dependency_graph, custom_markers=custom_markers)
```
(this replaces the existing two lines `clusters, cross_cluster_edges = build_clusters(dependency_graph)`
and `layer_violations = detect_layer_violations(dependency_graph)` — place the config-loading
line anywhere before them, e.g. right above where they currently sit).

Update the `"architecture"` block in the returned dict:
```python
        "architecture": {
            "clusters": clusters,
            "cross_cluster_edges": cross_cluster_edges,
            "layer_violations": layer_violations,
            "config_applied": architecture_config,
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
git commit -m "feat: wire .veridion.json config into scan_repository"
```

---

## Task 4: Live verification (not automated)

No further code changes — confirms the regression-safety and real-world behavior of this
change. No live agent/LLM call needed anywhere in this task (entirely local file/git
operations).

- [ ] **Step 1: Reinstall the prototype**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion/prototype
pip install -e ".[dev]"
```

- [ ] **Step 2: Regression check — confirm identical architecture evidence without `.veridion.json`**

Neither Procta nor Veridion's own repo has a `.veridion.json` today. Run against both and
confirm `config_applied` is `null` and the clusters/layer_violations shape matches what earlier
sessions already verified (same cluster counts, same `convention_detected` values):
```bash
python3 -c "
from pathlib import Path
from veridion.evidence import scan_repository

for label, path in [('Procta', '/Users/arihantkaul/proctored-browser'), ('Veridion', '/Users/arihantkaul/Documents/GitHub/Veridion')]:
    evidence = scan_repository(Path(path), check_vulnerabilities=False, scan_git_history=False)
    arch = evidence['architecture']
    print(label, '-> config_applied:', arch['config_applied'], '| clusters:', len(arch['clusters']), '| convention_detected:', arch['layer_violations']['convention_detected'])
"
```
This is Success Criterion 2 — confirm `config_applied: None` for both, and that the cluster
count / `convention_detected` values match what was already verified in earlier sessions for
these two repos (Procta: `convention_detected: true` via `routers`/`services`; Veridion:
`convention_detected: false`).

- [ ] **Step 3: Real end-to-end check with an actual `.veridion.json`**

```bash
python3 -c "
import json
import shutil
import tempfile
from pathlib import Path
from veridion.evidence import scan_repository

scratch = Path(tempfile.mkdtemp()) / 'repo'
(scratch / 'app' / 'biz').mkdir(parents=True)
(scratch / 'app' / 'routers').mkdir(parents=True)
(scratch / 'app' / 'biz' / 'order.py').write_text('x = 1\n')
(scratch / 'app' / 'routers' / 'orders.py').write_text('from app.biz import order\n')
(scratch / '.veridion.json').write_text(json.dumps({'layer_markers': {'biz': 1}}))

evidence = scan_repository(scratch, check_vulnerabilities=False, scan_git_history=False)
arch = evidence['architecture']
print('config_applied:', arch['config_applied'])
print('convention_detected:', arch['layer_violations']['convention_detected'])
print('layers:', arch['layer_violations']['layers'])
shutil.rmtree(scratch.parent)
"
```
Confirm `config_applied` reflects the written file exactly, and `convention_detected` is
`true` with `biz` correctly classified — a folder name that has no built-in marker entry. This
is Success Criterion 1.

- [ ] **Step 4: Reproducibility check**

Re-run Step 3's scan a second time against the same scratch repo (before deleting it) and
confirm `config_applied` and `clusters` are identical both times — Success Criterion 4.

- [ ] **Step 5: Record the outcome**

If all four success criteria pass, this feature is done — report back with the real
regression-check numbers for Procta/Veridion. If anything fails, that's the next debugging
task, not a new plan.

---

## Self-Review Notes

**Spec coverage:** the config schema and graceful-degradation behavior including the
bool-vs-numeric edge case (Task 1), both new function parameters with the verified resolution
fixture and both custom-marker scenarios (Task 2), the `config_applied` evidence wiring (Task
3), and all 4 numbered success criteria (Task 4, mapped 1:1) are each covered by a specific
task.

**Placeholder scan:** no TBD/TODO; every code block is complete, and the resolution-parameter
fixture was verified against real `networkx` output before being written into this plan, not
assumed to produce the stated numbers.

**Type consistency:** `load_architecture_config` (Task 1) → consumed by `evidence.py` (Task 3)
with the exact same `{"layer_markers", "cluster_resolution"}` shape. `build_clusters`'s
`resolution` and `detect_layer_violations`'s `custom_markers` (Task 2) → threaded through
`evidence.py` (Task 3) with matching parameter names at every call site — no renaming across
the chain.
