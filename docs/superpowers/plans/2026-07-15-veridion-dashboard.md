# Veridion Local Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `veridion dashboard <path>` starts a local, read-only, live-updating web dashboard for one repo — stat cards, trend sparklines, an interactive dependency graph, and an MCP-tools reference panel.

**Architecture:** A new `prototype/veridion/dashboard.py` builds a Starlette `app` bound to a fixed `repo_path`. Pure summary-building functions (no HTTP) come first, then the Starlette routes wrapping them, then a single-file HTML/CSS/JS frontend served as a plain string, then the CLI command.

**Tech Stack:** `starlette` 0.52.1, `uvicorn` 0.49.0, `sse-starlette` — all confirmed already installed as transitive dependencies of `mcp` (already landed in `prototype/pyproject.toml`). No new dependency needed. Vanilla JS/SVG frontend, no framework, no build step, no third-party JS (vendored or CDN).

## Global Constraints

- Binds to `127.0.0.1` only, never `0.0.0.0` — non-negotiable, matches Veridion's "100% local" identity.
- Default port `8420`, overridable via `--port`.
- Read-only — no scan-triggering UI, no write endpoints.
- Single repo per running instance, fixed at startup (same pattern as the MCP server's `build_server`).
- No new `evidence.json` fields, no changes to any scanner/query/history logic — this plan only reads existing data.

## Reference: confirmed APIs (verified locally, do not deviate)

`sse-starlette`'s working pattern (verified live against the installed version):
```python
from sse_starlette.sse import EventSourceResponse

async def event_generator():
    yield {"event": "refresh", "data": json.dumps({"scanned_at": "..."})}

async def events(request):
    return EventSourceResponse(event_generator())
```
Yielding a `dict` with `event`/`data` keys from an async generator produces correct
`event: refresh\ndata: {...}\n\n` SSE wire output — confirmed via a live `TestClient` stream
read, not assumed.

`prototype/veridion/history.py`'s `list_snapshots(repo_path: Path) -> list[Path]` returns
chronologically-sorted paths to full `evidence.json`-shaped snapshot files (confirmed by
reading the file: each snapshot is `json.dumps(evidence, indent=2)` of the exact same shape as
`.veridion/evidence.json` itself).

`prototype/veridion/architecture.py`'s cluster shape (confirmed): `{"id": int, "modules":
[str, ...], "internal_edges": int}`.

`prototype/veridion/git_intel/analyzer.py`'s `analyze_git` output (confirmed): `branches`
entries carry `ahead_of_main`; `commit_cadence` carries `weekly_counts`/`trend`;
top-level also has `total_commits`, `ownership`.

**Codex's MCP server implementation is already complete and merged** (`prototype/veridion/mcp_server.py`,
all 13 tools, `build_server(repo_path: Path) -> FastMCP`) — confirmed via `git log` and a fresh
read, not assumed. `mcp_server.py` has a private `_read_evidence(repo_path: Path) -> dict`
helper (raises `FileNotFoundError` with a clear message if `.veridion/evidence.json` is
missing) used at 3 call sites inside that file. Task 1 below renames it to `read_evidence`
(public) so `dashboard.py` can import and reuse it rather than duplicating this exact logic a
third time in the codebase — confirmed via `grep` that no test file references the private name
directly, so this rename has no other call sites to update.

---

### Task 1: Dashboard summary-building functions

**Files:**
- Modify: `prototype/veridion/mcp_server.py` (rename `_read_evidence` → `read_evidence`,
  update its 3 call sites within that file)
- Create: `prototype/veridion/dashboard.py`
- Test: `prototype/tests/test_dashboard.py`

**Interfaces:**
- Consumes: `read_evidence(repo_path: Path) -> dict` (renamed, was `_read_evidence`) from
  `veridion.mcp_server`. Consumes `list_snapshots(repo_path: Path) -> list[Path]` from
  `veridion.history`.
- Produces: `build_evidence_summary(evidence: dict) -> dict`, `build_history_summary(repo_path:
  Path) -> list[dict]` — both used by Task 3's endpoint handlers.

- [ ] **Step 1: Rename `_read_evidence` to `read_evidence` in `mcp_server.py`**

Change the definition and all 3 call sites (`grep -n "_read_evidence" veridion/mcp_server.py`
to find them precisely) from `_read_evidence` to `read_evidence`. Run the full existing suite
afterward to confirm nothing broke:

Run: `cd prototype && python3 -m pytest -q`
Expected: 163 passed (no change in count, this is a pure rename)

- [ ] **Step 2: Write the failing tests**

```python
# prototype/tests/test_dashboard.py
import json
from pathlib import Path

from veridion.dashboard import build_evidence_summary, build_history_summary


def make_evidence(scanned_at: str, module_count: int = 2, secrets_count: int = 0) -> dict:
    return {
        "scanned_at": scanned_at,
        "repository": {
            "languages": [{"name": "python", "file_count": module_count}],
            "modules": [{"path": f"m{i}.py"} for i in range(module_count)],
            "monorepo": {"detected": False, "workspaces": []},
            "dependency_graph": {"nodes": [], "edges": []},
        },
        "git": {
            "total_commits": 10,
            "commit_cadence": {"weekly_counts": [1, 2, 3], "trend": "steady"},
            "ownership": [{"path": "m0.py", "top_author": "alice"}],
            "branches": [{"name": "main", "ahead_of_main": 0}],
        },
        "security": {
            "secrets": {
                "findings": [
                    {
                        "path": f"s{i}.py",
                        "pattern": "aws_access_key_id",
                        "match_preview": "AKIA...MNOP",
                        "likely_placeholder": i % 2 == 0,
                    }
                    for i in range(secrets_count)
                ],
                "history_findings": [],
            },
            "dependency_vulnerabilities": {"checked": True, "reason": None, "findings": []},
        },
        "architecture": {
            "clusters": [{"id": 0, "modules": ["m0.py"], "internal_edges": 0}],
            "layer_violations": {"convention_detected": True, "layers": [], "violations": []},
        },
    }


def test_build_evidence_summary_shape():
    evidence = make_evidence("2026-07-15T12:00:00+00:00", module_count=3, secrets_count=2)

    summary = build_evidence_summary(evidence)

    assert summary["scanned_at"] == "2026-07-15T12:00:00+00:00"
    assert summary["repo_overview"]["module_count"] == 3
    assert summary["repo_overview"]["languages"] == [{"name": "python", "file_count": 3}]
    assert summary["git_activity"]["total_commits"] == 10
    assert summary["git_activity"]["branches"] == [{"name": "main", "ahead_of_main": 0}]
    assert summary["security"]["secrets"]["total_findings"] == 2
    assert summary["security"]["secrets"]["real_findings"] == 1
    assert summary["architecture"]["cluster_count"] == 1
    assert summary["architecture"]["convention_detected"] is True


def test_build_history_summary_reads_all_snapshots(tmp_path):
    repo = tmp_path / "repo"
    history_dir = repo / ".veridion" / "history"
    history_dir.mkdir(parents=True)
    (history_dir / "2026-07-15T10-00-00.json").write_text(
        json.dumps(make_evidence("2026-07-15T10:00:00+00:00", module_count=2, secrets_count=0))
    )
    (history_dir / "2026-07-15T11-00-00.json").write_text(
        json.dumps(make_evidence("2026-07-15T11:00:00+00:00", module_count=3, secrets_count=1))
    )

    result = build_history_summary(repo)

    assert len(result) == 2
    assert result[0] == {
        "scanned_at": "2026-07-15T10:00:00+00:00",
        "module_count": 2,
        "secrets_findings": 0,
        "vulnerability_findings": 0,
    }
    assert result[1]["module_count"] == 3
    assert result[1]["secrets_findings"] == 1


def test_build_history_summary_skips_corrupt_snapshots(tmp_path):
    repo = tmp_path / "repo"
    history_dir = repo / ".veridion" / "history"
    history_dir.mkdir(parents=True)
    (history_dir / "2026-07-15T10-00-00.json").write_text("{not valid json")
    (history_dir / "2026-07-15T11-00-00.json").write_text(
        json.dumps(make_evidence("2026-07-15T11:00:00+00:00"))
    )

    result = build_history_summary(repo)

    assert len(result) == 1
    assert result[0]["scanned_at"] == "2026-07-15T11:00:00+00:00"


def test_build_history_summary_empty_when_no_history(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    assert build_history_summary(repo) == []
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd prototype && python3 -m pytest tests/test_dashboard.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'veridion.dashboard'`

- [ ] **Step 4: Write the implementation**

```python
# prototype/veridion/dashboard.py
import json
from pathlib import Path

from veridion.history import list_snapshots


def build_evidence_summary(evidence: dict) -> dict:
    findings = evidence["security"]["secrets"]["findings"]
    real_findings = [f for f in findings if not f.get("likely_placeholder", False)]

    return {
        "scanned_at": evidence["scanned_at"],
        "repo_overview": {
            "languages": evidence["repository"]["languages"],
            "module_count": len(evidence["repository"]["modules"]),
            "monorepo": evidence["repository"]["monorepo"],
        },
        "git_activity": {
            "total_commits": evidence["git"]["total_commits"],
            "commit_cadence": evidence["git"]["commit_cadence"],
            "ownership": evidence["git"]["ownership"],
            "branches": evidence["git"]["branches"],
        },
        "security": {
            "secrets": {
                "total_findings": len(findings),
                "real_findings": len(real_findings),
                "history_findings": len(evidence["security"]["secrets"]["history_findings"]),
            },
            "vulnerabilities": {
                "checked": evidence["security"]["dependency_vulnerabilities"]["checked"],
                "finding_count": len(
                    evidence["security"]["dependency_vulnerabilities"]["findings"]
                ),
            },
        },
        "architecture": {
            "cluster_count": len(evidence["architecture"]["clusters"]),
            "convention_detected": evidence["architecture"]["layer_violations"][
                "convention_detected"
            ],
            "violation_count": len(evidence["architecture"]["layer_violations"]["violations"]),
        },
    }


def build_history_summary(repo_path: Path) -> list[dict]:
    result = []
    for snapshot_path in list_snapshots(repo_path):
        try:
            evidence = json.loads(snapshot_path.read_text())
        except json.JSONDecodeError:
            continue
        result.append(
            {
                "scanned_at": evidence["scanned_at"],
                "module_count": len(evidence["repository"]["modules"]),
                "secrets_findings": len(evidence["security"]["secrets"]["findings"]),
                "vulnerability_findings": len(
                    evidence["security"]["dependency_vulnerabilities"]["findings"]
                ),
            }
        )
    return result
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd prototype && python3 -m pytest tests/test_dashboard.py -v`
Expected: 4 passed

Run: `cd prototype && python3 -m pytest -q`
Expected: 167 passed (163 existing + 4 new), no regressions

- [ ] **Step 6: Commit**

```bash
cd prototype && git add veridion/mcp_server.py veridion/dashboard.py tests/test_dashboard.py
git commit -m "feat: add dashboard evidence/history summary functions"
```

---

### Task 2: Dependency graph summary function

**Files:**
- Modify: `prototype/veridion/dashboard.py`
- Modify: `prototype/tests/test_dashboard.py`

**Interfaces:**
- Produces: `build_graph_summary(evidence: dict) -> dict` — used by Task 3.

- [ ] **Step 1: Write the failing tests**

Append to `prototype/tests/test_dashboard.py`:

```python
from veridion.dashboard import build_graph_summary


def test_build_graph_summary_annotates_nodes_with_cluster_id():
    evidence = {
        "repository": {
            "dependency_graph": {
                "nodes": ["a.py", "b.py", "c.py"],
                "edges": [["a.py", "b.py"], ["b.py", "c.py"]],
            }
        },
        "architecture": {
            "clusters": [
                {"id": 0, "modules": ["a.py", "b.py"], "internal_edges": 1},
                {"id": 1, "modules": ["c.py"], "internal_edges": 0},
            ]
        },
    }

    result = build_graph_summary(evidence)

    assert result["nodes"] == [
        {"id": "a.py", "cluster": 0},
        {"id": "b.py", "cluster": 0},
        {"id": "c.py", "cluster": 1},
    ]
    assert result["edges"] == [
        {"source": "a.py", "target": "b.py"},
        {"source": "b.py", "target": "c.py"},
    ]
    assert result["clusters"] == evidence["architecture"]["clusters"]


def test_build_graph_summary_handles_unclustered_node():
    evidence = {
        "repository": {"dependency_graph": {"nodes": ["orphan.py"], "edges": []}},
        "architecture": {"clusters": []},
    }

    result = build_graph_summary(evidence)

    assert result["nodes"] == [{"id": "orphan.py", "cluster": None}]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd prototype && python3 -m pytest tests/test_dashboard.py -v -k graph_summary`
Expected: FAIL with `ImportError: cannot import name 'build_graph_summary'`

- [ ] **Step 3: Write the implementation**

Append to `prototype/veridion/dashboard.py`:

```python
def build_graph_summary(evidence: dict) -> dict:
    dependency_graph = evidence["repository"]["dependency_graph"]
    clusters = evidence["architecture"]["clusters"]

    node_to_cluster: dict[str, int] = {}
    for cluster in clusters:
        for module in cluster["modules"]:
            node_to_cluster[module] = cluster["id"]

    nodes = [
        {"id": node, "cluster": node_to_cluster.get(node)}
        for node in dependency_graph["nodes"]
    ]
    edges = [
        {"source": edge[0], "target": edge[1]} for edge in dependency_graph["edges"]
    ]

    return {"nodes": nodes, "edges": edges, "clusters": clusters}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd prototype && python3 -m pytest tests/test_dashboard.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
cd prototype && git add veridion/dashboard.py tests/test_dashboard.py
git commit -m "feat: add dashboard dependency graph summary function"
```

---

### Task 3: Starlette app with all 5 endpoints

**Files:**
- Modify: `prototype/veridion/dashboard.py`
- Modify: `prototype/tests/test_dashboard.py`

**Interfaces:**
- Consumes: `build_server(repo_path: Path) -> FastMCP` from `veridion.mcp_server` (confirmed
  signature, already implemented) — `FastMCP.list_tools()` is an async method returning a list
  of tool objects with `.name` and `.description` attributes (confirmed during the MCP server
  plan's own verification: `list_tools()` returns objects exposing `t.name`).
- Produces: `build_app(repo_path: Path) -> Starlette` — used by Task 5's CLI wiring.

- [ ] **Step 1: Write the failing tests**

Append to `prototype/tests/test_dashboard.py`:

```python
from starlette.testclient import TestClient

from veridion.dashboard import build_app


def make_repo_with_evidence(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    veridion_dir = repo / ".veridion"
    veridion_dir.mkdir(parents=True)
    (veridion_dir / "evidence.json").write_text(json.dumps(make_evidence("2026-07-15T12:00:00+00:00")))
    return repo


def test_root_serves_html_page(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    app = build_app(repo)
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert 'id="app"' in response.text


def test_api_evidence_returns_summary(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    app = build_app(repo)
    client = TestClient(app)

    response = client.get("/api/evidence")

    assert response.status_code == 200
    body = response.json()
    assert body["scanned_at"] == "2026-07-15T12:00:00+00:00"
    assert "repo_overview" in body


def test_api_history_returns_list(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    app = build_app(repo)
    client = TestClient(app)

    response = client.get("/api/history")

    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_api_graph_returns_shape(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    app = build_app(repo)
    client = TestClient(app)

    response = client.get("/api/graph")

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"nodes", "edges", "clusters"}


def test_api_mcp_tools_returns_13_tools(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    app = build_app(repo)
    client = TestClient(app)

    response = client.get("/api/mcp-tools")

    assert response.status_code == 200
    tools = response.json()
    assert len(tools) == 13
    names = {t["name"] for t in tools}
    assert "veridion_scan" in names
    assert "veridion_search" in names
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd prototype && python3 -m pytest tests/test_dashboard.py -v -k "root_serves or api_"`
Expected: FAIL with `ImportError: cannot import name 'build_app'`

- [ ] **Step 3: Write the implementation**

Add these imports at the top of `prototype/veridion/dashboard.py`:

```python
import asyncio

from starlette.applications import Starlette
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from veridion.mcp_server import build_server, read_evidence
```

Append to `prototype/veridion/dashboard.py`:

```python
def build_app(repo_path: Path) -> Starlette:
    async def index(request):
        return HTMLResponse(DASHBOARD_HTML)

    async def api_evidence(request):
        evidence = read_evidence(repo_path)
        return JSONResponse(build_evidence_summary(evidence))

    async def api_history(request):
        return JSONResponse(build_history_summary(repo_path))

    async def api_graph(request):
        evidence = read_evidence(repo_path)
        return JSONResponse(build_graph_summary(evidence))

    async def api_mcp_tools(request):
        server = build_server(repo_path)
        tools = await server.list_tools()
        return JSONResponse([{"name": t.name, "description": t.description} for t in tools])

    return Starlette(
        routes=[
            Route("/", index),
            Route("/api/evidence", api_evidence),
            Route("/api/history", api_history),
            Route("/api/graph", api_graph),
            Route("/api/mcp-tools", api_mcp_tools),
        ]
    )


DASHBOARD_HTML = """<!DOCTYPE html>
<html>
<head><title>Veridion Dashboard</title></head>
<body><div id="app">loading...</div></body>
</html>"""
```

**Note:** `DASHBOARD_HTML` is a minimal placeholder here — Task 4 replaces it entirely with the
real page (stat cards, sparklines, graph, MCP panel, `/events` wiring). It's written minimally
in this task only so `test_root_serves_html_page`'s `id="app"` assertion passes without
prematurely writing frontend code that Task 4 owns.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd prototype && python3 -m pytest tests/test_dashboard.py -v`
Expected: 11 passed

Run: `cd prototype && python3 -m pytest -q`
Expected: 172 passed, no regressions

- [ ] **Step 5: Commit**

```bash
cd prototype && git add veridion/dashboard.py tests/test_dashboard.py
git commit -m "feat: add dashboard Starlette app with evidence/history/graph/mcp-tools endpoints"
```

---

### Task 4: Frontend — stat cards, sparklines, graph, live updates

**Files:**
- Modify: `prototype/veridion/dashboard.py`

**Interfaces:**
- Consumes: nothing new — this task only replaces the `DASHBOARD_HTML` constant and adds the
  `/events` SSE route to `build_app`.

No new automated tests beyond what Task 3 already has (`test_root_serves_html_page`'s `id="app"`
check still passes since the mount point is preserved) — the SSE stream and the JS graph
renderer are verified live in Task 6, per the spec's own Testing Strategy.

- [ ] **Step 1: Add the `/events` SSE route**

Add this import to `prototype/veridion/dashboard.py`:

```python
from sse_starlette.sse import EventSourceResponse
```

Add this function and register the route in `build_app`:

```python
async def _watch_evidence_mtime(repo_path: Path):
    evidence_path = repo_path / ".veridion" / "evidence.json"
    last_mtime = evidence_path.stat().st_mtime if evidence_path.exists() else None
    while True:
        await asyncio.sleep(1.5)
        if not evidence_path.exists():
            continue
        current_mtime = evidence_path.stat().st_mtime
        if last_mtime is None or current_mtime != last_mtime:
            last_mtime = current_mtime
            evidence = json.loads(evidence_path.read_text())
            yield {"event": "refresh", "data": json.dumps({"scanned_at": evidence["scanned_at"]})}
```

In `build_app`, add the route handler and register it:

```python
    async def events(request):
        return EventSourceResponse(_watch_evidence_mtime(repo_path))
```

Add `Route("/events", events)` to the `routes` list in `build_app`'s `Starlette(...)` call.

- [ ] **Step 2: Run the existing test suite to confirm nothing broke**

Run: `cd prototype && python3 -m pytest tests/test_dashboard.py -v`
Expected: 11 passed (no new test for `/events` here — SSE streams are verified live in Task 6,
consistent with the spec's Testing Strategy)

- [ ] **Step 3: Replace `DASHBOARD_HTML` with the real page**

Replace the entire `DASHBOARD_HTML = """..."""` block with:

```python
DASHBOARD_HTML = """<!DOCTYPE html>
<html>
<head>
<title>Veridion Dashboard</title>
<meta charset="utf-8">
<style>
  body { font-family: -apple-system, sans-serif; margin: 0; padding: 24px; background: #0b0e14; color: #e6e6e6; }
  h1 { font-size: 20px; margin-bottom: 4px; }
  #scanned-at { color: #8a8f98; font-size: 13px; margin-bottom: 20px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 16px; margin-bottom: 24px; }
  .card { background: #151a24; border: 1px solid #262b36; border-radius: 8px; padding: 16px; }
  .card h2 { font-size: 13px; text-transform: uppercase; color: #8a8f98; margin: 0 0 12px 0; }
  .stat { font-size: 24px; font-weight: 600; }
  .stat-row { display: flex; justify-content: space-between; margin: 4px 0; font-size: 13px; }
  svg { width: 100%; height: 320px; background: #0b0e14; border: 1px solid #262b36; border-radius: 8px; }
  .sparkline { width: 100%; height: 40px; }
  .tools-list { max-height: 240px; overflow-y: auto; }
  .tool-row { padding: 6px 0; border-bottom: 1px solid #1c212b; font-size: 13px; }
  .tool-name { color: #7fd3ff; font-family: monospace; }
</style>
</head>
<body>
<div id="app">
  <h1>Veridion Dashboard</h1>
  <div id="scanned-at">loading...</div>
  <div class="grid">
    <div class="card"><h2>Repo Overview</h2><div id="repo-overview"></div></div>
    <div class="card"><h2>Git Activity</h2><div id="git-activity"></div></div>
    <div class="card"><h2>Security</h2><div id="security"></div></div>
    <div class="card"><h2>Architecture</h2><div id="architecture"></div></div>
  </div>
  <div class="grid">
    <div class="card"><h2>Module Count Trend</h2><svg id="sparkline-modules" class="sparkline"></svg></div>
    <div class="card"><h2>Secrets Findings Trend</h2><svg id="sparkline-secrets" class="sparkline"></svg></div>
    <div class="card"><h2>Vulnerability Findings Trend</h2><svg id="sparkline-vulns" class="sparkline"></svg></div>
  </div>
  <div class="card" style="margin-bottom: 24px;">
    <h2>Dependency Graph</h2>
    <svg id="graph" viewBox="0 0 800 320"></svg>
  </div>
  <div class="card">
    <h2>MCP Tools Available for This Repo</h2>
    <div id="mcp-tools" class="tools-list"></div>
  </div>
</div>
<script>
async function fetchJSON(path) {
  const response = await fetch(path);
  return response.json();
}

function renderRepoOverview(data) {
  const el = document.getElementById('repo-overview');
  const langs = data.languages.map(l => l.name + ' (' + l.file_count + ')').join(', ');
  el.innerHTML =
    '<div class="stat">' + data.module_count + ' modules</div>' +
    '<div class="stat-row"><span>Languages</span><span>' + langs + '</span></div>' +
    '<div class="stat-row"><span>Monorepo</span><span>' + (data.monorepo.detected ? 'yes' : 'no') + '</span></div>';
}

function renderGitActivity(data) {
  const el = document.getElementById('git-activity');
  const staleBranches = data.branches.filter(b => b.ahead_of_main > 0).length;
  el.innerHTML =
    '<div class="stat">' + data.total_commits + ' commits</div>' +
    '<div class="stat-row"><span>Cadence trend</span><span>' + data.commit_cadence.trend + '</span></div>' +
    '<div class="stat-row"><span>Branches ahead of main</span><span>' + staleBranches + '</span></div>';
}

function renderSecurity(data) {
  const el = document.getElementById('security');
  el.innerHTML =
    '<div class="stat">' + data.secrets.real_findings + ' real secret findings</div>' +
    '<div class="stat-row"><span>Total (incl. placeholders)</span><span>' + data.secrets.total_findings + '</span></div>' +
    '<div class="stat-row"><span>History findings</span><span>' + data.secrets.history_findings + '</span></div>' +
    '<div class="stat-row"><span>Vulnerabilities</span><span>' + data.vulnerabilities.finding_count + '</span></div>';
}

function renderArchitecture(data) {
  const el = document.getElementById('architecture');
  el.innerHTML =
    '<div class="stat">' + data.cluster_count + ' clusters</div>' +
    '<div class="stat-row"><span>Convention detected</span><span>' + (data.convention_detected ? 'yes' : 'no') + '</span></div>' +
    '<div class="stat-row"><span>Layer violations</span><span>' + data.violation_count + '</span></div>';
}

function renderSparkline(svgId, values) {
  const svg = document.getElementById(svgId);
  if (values.length < 2) { svg.innerHTML = ''; return; }
  const max = Math.max(...values, 1);
  const min = Math.min(...values, 0);
  const range = max - min || 1;
  const width = 100, height = 100;
  const step = width / (values.length - 1);
  const points = values.map((v, i) => {
    const x = i * step;
    const y = height - ((v - min) / range) * height;
    return x + ',' + y;
  }).join(' ');
  svg.setAttribute('viewBox', '0 0 ' + width + ' ' + height);
  svg.setAttribute('preserveAspectRatio', 'none');
  svg.innerHTML = '<polyline points="' + points + '" fill="none" stroke="#7fd3ff" stroke-width="2" />';
}

function renderGraph(data) {
  const svg = document.getElementById('graph');
  const width = 800, height = 320;
  const nodes = data.nodes.map(n => ({
    id: n.id, cluster: n.cluster,
    x: Math.random() * width, y: Math.random() * height, vx: 0, vy: 0
  }));
  const nodeById = {};
  nodes.forEach(n => { nodeById[n.id] = n; });
  const edges = data.edges.filter(e => nodeById[e.source] && nodeById[e.target]);

  const iterations = 250;
  for (let iter = 0; iter < iterations; iter++) {
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i], b = nodes[j];
        let dx = a.x - b.x, dy = a.y - b.y;
        let distSq = dx * dx + dy * dy || 0.01;
        const force = 800 / distSq;
        const dist = Math.sqrt(distSq);
        dx /= dist; dy /= dist;
        a.vx += dx * force; a.vy += dy * force;
        b.vx -= dx * force; b.vy -= dy * force;
      }
    }
    edges.forEach(e => {
      const a = nodeById[e.source], b = nodeById[e.target];
      const dx = b.x - a.x, dy = b.y - a.y;
      const dist = Math.sqrt(dx * dx + dy * dy) || 0.01;
      const force = (dist - 80) * 0.02;
      const fx = (dx / dist) * force, fy = (dy / dist) * force;
      a.vx += fx; a.vy += fy;
      b.vx -= fx; b.vy -= fy;
    });
    nodes.forEach(n => {
      n.vx += (width / 2 - n.x) * 0.001;
      n.vy += (height / 2 - n.y) * 0.001;
      n.x += n.vx * 0.1; n.y += n.vy * 0.1;
      n.vx *= 0.85; n.vy *= 0.85;
      n.x = Math.max(10, Math.min(width - 10, n.x));
      n.y = Math.max(10, Math.min(height - 10, n.y));
    });
  }

  const palette = ['#7fd3ff', '#ff9f7f', '#a3ff7f', '#ff7fd3', '#ffe27f', '#c07fff'];
  const colorFor = c => c === null || c === undefined ? '#555' : palette[c % palette.length];

  let svgContent = '';
  edges.forEach(e => {
    const a = nodeById[e.source], b = nodeById[e.target];
    svgContent += '<line x1="' + a.x + '" y1="' + a.y + '" x2="' + b.x + '" y2="' + b.y + '" stroke="#333" stroke-width="1" />';
  });
  nodes.forEach(n => {
    svgContent += '<circle cx="' + n.x + '" cy="' + n.y + '" r="6" fill="' + colorFor(n.cluster) + '"><title>' + n.id + '</title></circle>';
  });
  svg.setAttribute('viewBox', '0 0 ' + width + ' ' + height);
  svg.innerHTML = svgContent;
}

function renderMcpTools(tools) {
  const el = document.getElementById('mcp-tools');
  el.innerHTML = tools.map(t =>
    '<div class="tool-row"><span class="tool-name">' + t.name + '</span> - ' + (t.description || '') + '</div>'
  ).join('');
}

async function loadAll() {
  const evidence = await fetchJSON('/api/evidence');
  document.getElementById('scanned-at').textContent = 'Last scanned: ' + evidence.scanned_at;
  renderRepoOverview(evidence.repo_overview);
  renderGitActivity(evidence.git_activity);
  renderSecurity(evidence.security);
  renderArchitecture(evidence.architecture);

  const history = await fetchJSON('/api/history');
  renderSparkline('sparkline-modules', history.map(h => h.module_count));
  renderSparkline('sparkline-secrets', history.map(h => h.secrets_findings));
  renderSparkline('sparkline-vulns', history.map(h => h.vulnerability_findings));

  const graph = await fetchJSON('/api/graph');
  renderGraph(graph);
}

async function loadMcpTools() {
  const tools = await fetchJSON('/api/mcp-tools');
  renderMcpTools(tools);
}

loadAll();
loadMcpTools();

const eventSource = new EventSource('/events');
eventSource.addEventListener('refresh', () => { loadAll(); });
</script>
</body>
</html>"""
```

- [ ] **Step 4: Run the existing test suite**

Run: `cd prototype && python3 -m pytest tests/test_dashboard.py -v`
Expected: 11 passed (the `id="app"` check in `test_root_serves_html_page` still passes since
the real page preserves that mount point)

Run: `cd prototype && python3 -m pytest -q`
Expected: 172 passed, no regressions

- [ ] **Step 5: Commit**

```bash
cd prototype && git add veridion/dashboard.py
git commit -m "feat: add dashboard frontend with stat cards, sparklines, graph, and live updates"
```

---

### Task 5: `veridion dashboard` CLI command

**Files:**
- Modify: `prototype/veridion/cli.py`

**Interfaces:**
- Consumes: `build_app(repo_path: Path) -> Starlette` from `veridion.dashboard`.
- Produces: `_dashboard(repo_path: str, port: int) -> int` in `cli.py`.

**Before starting this task, re-read `prototype/veridion/cli.py` fresh** — Codex's MCP server
plan already landed the `mcp` subcommand in this same file (confirmed via `git log` showing
commit `d3962c7 feat: add veridion mcp CLI command`), so the current file state must be checked
live rather than assumed from any earlier read, to see exactly where the `mcp_parser` block and
its dispatch `if` sit before adding `dashboard` alongside them in the same style.

- [ ] **Step 1: Write the failing test**

Add to `prototype/tests/test_cli.py` (following the exact pattern `test_main_mcp_invokes_mcp_flow`
already uses to avoid actually starting a blocking server):

```python
def test_main_dashboard_invokes_dashboard_flow(tmp_path):
    with patch("sys.argv", ["veridion", "dashboard", str(tmp_path)]):
        with patch("veridion.cli._dashboard", return_value=0) as mock_dashboard:
            exit_code = main()
    assert exit_code == 0
    mock_dashboard.assert_called_once_with(str(tmp_path), 8420)


def test_main_dashboard_threads_custom_port(tmp_path):
    with patch("sys.argv", ["veridion", "dashboard", str(tmp_path), "--port", "9000"]):
        with patch("veridion.cli._dashboard", return_value=0) as mock_dashboard:
            exit_code = main()
    assert exit_code == 0
    mock_dashboard.assert_called_once_with(str(tmp_path), 9000)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd prototype && python3 -m pytest tests/test_cli.py -v -k main_dashboard`
Expected: FAIL with `invalid choice: 'dashboard'`

- [ ] **Step 3: Write the implementation**

In `prototype/veridion/cli.py`, add the import:

```python
import webbrowser

import uvicorn

from veridion.dashboard import build_app
```

Add a new function near `_mcp`:

```python
def _dashboard(repo_path: str, port: int) -> int:
    repo = Path(repo_path).resolve()
    app = build_app(repo)
    url = f"http://127.0.0.1:{port}"
    print(f"Dashboard running at {url}")
    webbrowser.open(url)
    uvicorn.run(app, host="127.0.0.1", port=port)
    return 0
```

In `main()`, add the subparser (in the same style as `mcp_parser`, alongside it):

```python
    dashboard_parser = subparsers.add_parser(
        "dashboard", help="run a live local dashboard scoped to a repository"
    )
    dashboard_parser.add_argument("path", nargs="?", default=".")
    dashboard_parser.add_argument("--port", type=int, default=8420)
```

And in the dispatch section (alongside the existing `if args.command == "mcp":` block):

```python
    if args.command == "dashboard":
        return _dashboard(args.path, args.port)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd prototype && python3 -m pytest tests/test_cli.py -v -k main_dashboard`
Expected: 2 passed

Run: `cd prototype && python3 -m pytest -q`
Expected: 174 passed, no regressions

- [ ] **Step 5: Commit**

```bash
cd prototype && git add veridion/cli.py tests/test_cli.py
git commit -m "feat: add veridion dashboard CLI command"
```

---

### Task 6: Live verification

Not automated — no live agent call needed, matching the pattern used for every prior
increment's final task this session.

- [ ] **Step 1: Start the dashboard against a real scanned repo and confirm localhost-only binding**

```bash
cd prototype
python3 -m veridion.cli scan /Users/arihantkaul/proctored-browser --no-check-vulnerabilities --no-scan-git-history
python3 -m veridion.cli dashboard /Users/arihantkaul/proctored-browser &
sleep 2
curl -s http://127.0.0.1:8420/api/evidence | head -c 300
echo
# Confirm it is NOT reachable via a non-loopback bind - attempt binding check:
python3 -c "
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
result = s.connect_ex(('0.0.0.0', 8420))
print('0.0.0.0 connect result (nonzero = refused/not listening there):', result)
"
```

Expected: `/api/evidence` returns real JSON. The socket check against `0.0.0.0` should behave
consistently with a server bound only to `127.0.0.1` (on most systems this specific check may
still connect since `0.0.0.0` as a *client* target routes to loopback locally — the more
reliable confirmation is inspecting the actual listening socket):

```bash
lsof -iTCP:8420 -sTCP:LISTEN -P -n
```

Expected: shows the process bound to `127.0.0.1:8420`, not `*:8420` or `0.0.0.0:8420`.

- [ ] **Step 2: Confirm live update via a real scan while the dashboard is running**

With the dashboard still running from Step 1, in another terminal:

```bash
cd "/Users/arihantkaul/Documents/GitHub/Veridion/prototype"
python3 -m veridion.cli scan /Users/arihantkaul/proctored-browser --no-check-vulnerabilities --no-scan-git-history
```

Then open `http://127.0.0.1:8420` in a real browser (or re-`curl` `/api/evidence` and compare
`scanned_at` before/after) and confirm the page's displayed `scanned_at` updates within
~2 seconds of the scan completing, without a manual reload.

- [ ] **Step 3: Visually confirm the graph renders and clusters correctly**

With the dashboard open in a real browser, visually inspect the Dependency Graph card: confirm
nodes appear as colored circles, edges as lines, and nodes belonging to the same cluster share
a color (cross-reference against `/api/graph`'s `clusters` field for a specific repo).

- [ ] **Step 4: Confirm `/api/mcp-tools` matches a direct `list_tools()` call**

```bash
cd prototype
curl -s http://127.0.0.1:8420/api/mcp-tools | python3 -c "import json,sys; print(sorted(t['name'] for t in json.load(sys.stdin)))"
python3 -c "
import asyncio
from pathlib import Path
from veridion.mcp_server import build_server

async def main():
    server = build_server(Path('/Users/arihantkaul/proctored-browser'))
    tools = await server.list_tools()
    print(sorted(t.name for t in tools))

asyncio.run(main())
"
```

Expected: both printed lists are identical.

- [ ] **Step 5: Stop the background dashboard process**

```bash
kill %1
```

Record the actual output of each check when reporting completion — do not report this task
done without having run all four verification steps and inspected real output, matching the
review discipline used for every other increment this session.
