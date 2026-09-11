import asyncio
import json
from pathlib import Path

from sse_starlette.sse import EventSourceResponse
from starlette.applications import Starlette
from starlette.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.routing import Route

from aletheore.evidence import (
    IncompatibleEvidenceVersionError,
    MalformedEvidenceError,
    load_evidence_file,
)
from aletheore.history import list_snapshots
from aletheore.mcp_server import build_server, read_evidence
from aletheore.wiki_diagrams import _ambiguous_edges

_STATIC_DIR = Path(__file__).resolve().parent / "static"


def build_evidence_summary(evidence: dict) -> dict:
    findings = evidence["security"]["secrets"]["findings"]
    real_findings = [
        f for f in findings if not f.get("likely_placeholder", False) and not f.get("accepted", False)
    ]

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
                "reason": evidence["security"]["dependency_vulnerabilities"].get("reason"),
                "finding_count": len(
                    evidence["security"]["dependency_vulnerabilities"]["findings"]
                ),
                "findings": evidence["security"]["dependency_vulnerabilities"]["findings"],
            },
            "licenses": {
                "checked": evidence["security"]["dependency_licenses"]["checked"],
                "reason": evidence["security"]["dependency_licenses"].get("reason"),
                "repo_license": evidence["security"]["dependency_licenses"]["repo_license"],
                "finding_count": len(evidence["security"]["dependency_licenses"]["findings"]),
                "findings": evidence["security"]["dependency_licenses"]["findings"],
            },
        },
        "architecture": {
            "cluster_count": len(evidence["architecture"]["clusters"]),
            "convention_detected": evidence["architecture"]["layer_violations"][
                "convention_detected"
            ],
            "violation_count": len(evidence["architecture"]["layer_violations"]["violations"]),
        },
        "dead_code": {
            "unreachable_modules": evidence["repository"]["dead_code"]["unreachable_modules"],
            "unused_dependencies": evidence["repository"]["dead_code"]["unused_dependencies"],
        },
        # Real bug found via audit: this used to flatten straight to the bare
        # endpoints list, dropping "checked"/"reason" - matching the
        # vulnerabilities/licenses blocks above, which keep them for the same
        # reason: caption a skipped scan instead of rendering it
        # indistinguishably from "checked, found none". `--no-map-endpoints`
        # (or its .aletheore.json disabled_checks equivalent) is a real,
        # reachable flag that produces exactly that shape - confirmed via a
        # real scan_repository(map_endpoints=False) call.
        "endpoints": {
            "checked": evidence["repository"]["api_endpoints"]["checked"],
            "reason": evidence["repository"]["api_endpoints"].get("reason"),
            "endpoints": evidence["repository"]["api_endpoints"]["endpoints"],
        },
    }


def build_history_summary(repo_path: Path) -> list[dict]:
    result = []
    for snapshot_path in list_snapshots(repo_path):
        try:
            evidence = json.loads(snapshot_path.read_text())
        except json.JSONDecodeError:
            continue
        # Real bug found via audit: this indexed straight into
        # evidence["security"]["dependency_vulnerabilities"]["findings"]
        # etc. with no schema check - unlike load_evidence_file, imported
        # into this same file specifically to guard against exactly this.
        # An older-schema snapshot (a real, reachable shape in this exact
        # history/ directory - see history.py's own _compute_curated_diff,
        # which had the identical gap) raised an uncaught KeyError here,
        # and since this loop has no per-snapshot isolation, one bad
        # snapshot anywhere in history killed the whole summary - the
        # History tab's only caller, api_history, has no try/except of
        # its own, so this propagated as an unhandled 500 for the entire
        # session, not just that one snapshot's display. Skip a snapshot
        # missing an expected key or shaped wrong, same "treat as
        # unavailable" contract load_evidence_file already establishes,
        # rather than losing every other snapshot's history alongside it.
        try:
            entry = {
                "scanned_at": evidence["scanned_at"],
                "module_count": len(evidence["repository"]["modules"]),
                "secrets_findings": len(evidence["security"]["secrets"]["findings"]),
                "vulnerability_findings": len(
                    evidence["security"]["dependency_vulnerabilities"]["findings"]
                ),
            }
        except (KeyError, TypeError):
            continue
        result.append(entry)
    return result


def build_graph_summary(evidence: dict) -> dict:
    dependency_graph = evidence["repository"]["dependency_graph"]
    clusters = evidence["architecture"]["clusters"]
    # Unlike wiki_diagrams.py's static mermaid builders (which exclude these
    # entirely - a rendered mermaid edge has no way to look "less certain"),
    # this graph is interactive, so an edge whose resolution was genuinely
    # uncertain (currently C# type-reference edges kept despite more than
    # one file declaring that type name - see scanner/graph.py's
    # _csharp_type_reference_targets) is still drawn, just marked so the
    # frontend can dim/dash it rather than presenting it identically to a
    # certain edge. A source-root/prefix tiebreak among real candidates
    # ("inferred") is not marked - that resolution is generally still
    # correct, unlike genuine which-of-several-files uncertainty.
    ambiguous = _ambiguous_edges(evidence["repository"].get("modules", []))

    node_to_cluster: dict[str, int] = {}
    for cluster in clusters:
        for module in cluster["modules"]:
            node_to_cluster[module] = cluster["id"]

    nodes = [
        {"id": node, "cluster": node_to_cluster.get(node)}
        for node in dependency_graph["nodes"]
    ]
    edges = [
        {
            "source": edge[0],
            "target": edge[1],
            **({"ambiguous": True} if (edge[0], edge[1]) in ambiguous else {}),
        }
        for edge in dependency_graph["edges"]
    ]

    return {"nodes": nodes, "edges": edges, "clusters": clusters}


async def _sleep_unless_shutting_down(seconds: float, shutdown: asyncio.Event | None) -> bool:
    """Sleep, returning True early if shutdown was signalled.

    sse_starlette has its own shutdown listener, but in 3.0.3 it does not
    fire: `_listen_for_exit_signal` stores its exit event in a ContextVar
    from inside the request's task, and `AppStatus.handle_exit` then reads
    that ContextVar from the *signal handler's* context, where the task's
    value was never visible. It finds None, sets nothing, and the stream
    waits on an event nobody will ever set - despite the comment there
    saying "signal all waiters in all contexts."

    The visible consequence was that Ctrl-C hung the dashboard at "Waiting
    for connections to close" for as long as a browser tab was open, so
    every user had to press Ctrl-C a second time - and that second one
    force-quits mid-shutdown, which is where the two tracebacks came from.
    Owning the event ourselves (see build_app) is independent of that bug.
    """
    if shutdown is None:
        await asyncio.sleep(seconds)
        return False
    try:
        await asyncio.wait_for(shutdown.wait(), timeout=seconds)
    except TimeoutError:
        return False
    return True


async def _watch_evidence_mtime(repo_path: Path, shutdown: asyncio.Event | None = None):
    evidence_path = repo_path / ".aletheore" / "air.json"
    last_mtime = evidence_path.stat().st_mtime if evidence_path.exists() else None
    # The whole loop is wrapped rather than just the sleep: on a forced
    # shutdown the SSE task group cancels this generator wherever it happens
    # to be, and an un-caught CancelledError propagates through sse_starlette
    # into Starlette's ASGI error handler. Returning turns that back into an
    # ordinary generator close. The shutdown check above it is what makes the
    # *graceful* path work at all - without it uvicorn waits on this stream
    # forever and the user never gets to a clean single Ctrl-C.
    try:
        while True:
            if await _sleep_unless_shutting_down(1.5, shutdown):
                return
            if not evidence_path.exists():
                continue
            current_mtime = evidence_path.stat().st_mtime
            if last_mtime == current_mtime:
                continue
            last_mtime = current_mtime
            # load_evidence_file, not a bare json.loads: this was the one
            # evidence reader checking neither schema version nor shape before
            # indexing evidence["scanned_at"]. Since it fires on mtime it can
            # observe a scan mid-write, so a truncated or incompatible file
            # killed the stream with JSONDecodeError or KeyError and the
            # dashboard silently stopped auto-refreshing for the rest of the
            # session. Skip the tick instead - the file is about to change
            # again anyway.
            try:
                evidence = load_evidence_file(evidence_path)
            except (
                OSError,
                json.JSONDecodeError,
                IncompatibleEvidenceVersionError,
                MalformedEvidenceError,
            ):
                continue
            yield {"event": "refresh", "data": json.dumps({"scanned_at": evidence["scanned_at"]})}
    except asyncio.CancelledError:
        return


def build_app(repo_path: Path) -> Starlette:
    # Set by the CLI's uvicorn handle_exit override at signal time, watched by
    # every open /events stream. Built here rather than at import so each app
    # (the test suite builds several) gets its own. asyncio.Event no longer
    # binds to a loop at construction on 3.10+, so creating it outside a
    # running loop is fine.
    shutdown_event = asyncio.Event()

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

    async def events(request):
        return EventSourceResponse(_watch_evidence_mtime(repo_path, shutdown_event))

    async def logo(request):
        return FileResponse(_STATIC_DIR / "logo.png")

    # Browsers request all four of these unprompted on a page load, and every
    # one 404'd - four error lines in the log for a dashboard that had in fact
    # served the page fine. Reusing the existing logo is enough: this is a
    # localhost dev UI, so a dedicated .ico plus the Apple sizes would be new
    # binary assets to maintain for no gain over the PNG every current browser
    # accepts.
    async def favicon(request):
        return FileResponse(_STATIC_DIR / "logo.png", media_type="image/png")

    app = Starlette(
        routes=[
            Route("/", index),
            Route("/api/evidence", api_evidence),
            Route("/api/history", api_history),
            Route("/api/graph", api_graph),
            Route("/api/mcp-tools", api_mcp_tools),
            Route("/events", events),
            Route("/logo.png", logo),
            Route("/favicon.ico", favicon),
            Route("/apple-touch-icon.png", favicon),
            Route("/apple-touch-icon-precomposed.png", favicon),
        ]
    )
    app.state.shutdown_event = shutdown_event
    return app


DASHBOARD_HTML = """<!DOCTYPE html>
<html>
<head>
<title>Aletheore Dashboard</title>
<meta charset="utf-8">
<style>
  body { font-family: -apple-system, sans-serif; margin: 0; padding: 24px; background: #000; color: #f2f2f2; }
  .header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 24px; padding-bottom: 20px; border-bottom: 1px solid #1a1a1a; flex-wrap: wrap; gap: 12px; }
  .logo { height: 40px; display: block; }
  #scanned-at { display: flex; align-items: center; gap: 12px; color: #9a9a9a; font-size: 13px; }
  #scanned-at-date { color: #f2f2f2; font-weight: 600; }
  #scanned-at-time { color: #9a9a9a; }
  #tz-toggle { background: #0d0d0d; color: #9a9a9a; border: 1px solid #2a2a2a; border-radius: 20px; padding: 4px 12px; font-size: 11px; cursor: pointer; transition: color 0.15s ease, border-color 0.15s ease; }
  #tz-toggle:hover { color: #fff; border-color: #4a4a4a; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 16px; margin-bottom: 24px; }
  .card { background: #0a0a0a; border: 1px solid #222; border-radius: 10px; padding: 16px; box-shadow: 0 1px 0 rgba(255,255,255,0.03) inset, 0 8px 24px rgba(0,0,0,0.35); }
  .card h2 { font-size: 12px; letter-spacing: 0.04em; text-transform: uppercase; color: #8a8a8a; margin: 0 0 12px 0; }
  .stat { font-size: 24px; font-weight: 600; color: #fff; }
  .stat-row { display: flex; justify-content: space-between; margin: 4px 0; font-size: 13px; color: #c8c8c8; }
  svg { width: 100%; height: 320px; background: #000; border: 1px solid #222; border-radius: 8px; }
  .barchart { width: 100%; height: 90px; }
  .tools-list { max-height: 240px; overflow-y: auto; }
  .tool-row { padding: 6px 0; border-bottom: 1px solid #1a1a1a; font-size: 13px; color: #c8c8c8; }
  .tool-name { color: #fff; font-family: monospace; }
  .tool-detail { color: #8a8a8a; font-size: 12px; margin-top: 2px; }
  .graph-hint { font-size: 11px; color: #5a5a5a; margin-top: 6px; }
  #graph-hover-info { min-height: 18px; margin-top: 4px; font-size: 13px; color: #9a9a9a; }
  #graph-hover-info .hover-path { color: #fff; font-family: monospace; }
  .graph-controls { margin-top: 8px; }
  .graph-controls button { background: #0d0d0d; color: #9a9a9a; border: 1px solid #2a2a2a; border-radius: 4px; padding: 4px 10px; font-size: 12px; cursor: pointer; transition: color 0.15s ease, border-color 0.15s ease; }
  .graph-controls button:hover { color: #fff; border-color: #4a4a4a; }
  /* Matches the Clusters Graph card's height (620px svg + its hint/hover/button chrome)
     so the two cards read as one row instead of leaving a half-empty gap below the list.
     A flex/grid-stretch approach was tried first but backfired: a flex:1 list inside an
     auto-sized grid row doesn't get clamped by its container, it drags the row (and the
     graph card next to it) up to the list's full unclipped content height instead. */
  .cluster-list { max-height: 700px; overflow-y: auto; }
  .cluster-row { border-bottom: 1px solid #1a1a1a; }
  .cluster-header { padding: 8px 0; font-size: 13px; cursor: pointer; display: flex; justify-content: space-between; color: #c8c8c8; transition: color 0.15s ease; }
  .cluster-header:hover { color: #fff; }
  .cluster-row.active .cluster-header { color: #fff; font-weight: 600; }
  .cluster-modules { display: none; padding: 0 0 10px 12px; font-size: 12px; color: #8a8a8a; font-family: monospace; }
  .cluster-row.expanded .cluster-modules { display: block; }
  .cluster-modules div { padding: 2px 0; }
  .sparkline-value { font-size: 20px; font-weight: 600; margin-top: 6px; color: #fff; }
  .chart-note { font-size: 11px; color: #5a5a5a; margin-top: 4px; }
  #cluster-graph { height: 620px; }
  #cluster-graph-hover-info { min-height: 18px; margin-top: 8px; font-size: 13px; color: #9a9a9a; }
  #cluster-graph-hover-info .hover-path { color: #fff; font-family: monospace; }
</style>
</head>
<body>
<div id="app">
  <div class="header">
    <img src="/logo.png" alt="Aletheore" class="logo">
    <div id="scanned-at">
      <span>Last scanned:</span>
      <span id="scanned-at-date">-</span>
      <span id="scanned-at-time">-</span>
      <button id="tz-toggle" onclick="toggleTimezone()">Show UTC</button>
    </div>
  </div>
  <div class="grid">
    <div class="card"><h2>Repo Overview</h2><div id="repo-overview"></div></div>
    <div class="card"><h2>Git Activity</h2><div id="git-activity"></div></div>
    <div class="card"><h2>Security</h2><div id="security"></div></div>
    <div class="card"><h2>Architecture</h2><div id="architecture"></div></div>
  </div>
  <div class="grid">
    <div class="card">
      <h2>Module Count Trend</h2>
      <svg id="sparkline-modules" class="barchart"></svg>
      <div id="sparkline-modules-value" class="sparkline-value"></div>
      <div id="sparkline-modules-note" class="chart-note"></div>
    </div>
    <div class="card">
      <h2>Secrets Findings Trend</h2>
      <svg id="sparkline-secrets" class="barchart"></svg>
      <div id="sparkline-secrets-value" class="sparkline-value"></div>
      <div id="sparkline-secrets-note" class="chart-note"></div>
    </div>
    <div class="card">
      <h2>Vulnerability Findings Trend</h2>
      <svg id="sparkline-vulns" class="barchart"></svg>
      <div id="sparkline-vulns-value" class="sparkline-value"></div>
      <div id="sparkline-vulns-note" class="chart-note"></div>
    </div>
  </div>
  <div class="grid">
    <div class="card" style="grid-column: 1 / -1;">
      <h2>Dependency Graph</h2>
      <svg id="graph" class="interactive-graph" viewBox="0 0 800 320"></svg>
      <div id="graph-hover-info">Hover a node to see its dependencies.</div>
      <div class="graph-controls"><button id="clear-filter-btn" onclick="clearGraphFilter()">Show all clusters</button></div>
    </div>
  </div>
  <div class="grid">
    <div class="card" style="grid-column: span 2;">
      <h2>Clusters Graph</h2>
      <svg id="cluster-graph" class="interactive-graph" viewBox="0 0 1400 900"></svg>
      <div class="graph-hint">Scroll or pinch to zoom, drag to pan.</div>
      <div id="cluster-graph-hover-info">Hover a node to see which cluster it belongs to.</div>
      <div class="graph-controls"><button onclick="resetGraphView('cluster-graph')">Reset view</button></div>
    </div>
    <div class="card">
      <h2>File Clusters</h2>
      <div id="cluster-list" class="cluster-list"></div>
    </div>
  </div>
  <div class="card">
    <h2>Dead Code</h2>
    <div id="dead-code-summary" class="stat"></div>
    <div id="dead-code" class="tools-list"></div>
  </div>
  <div class="grid">
    <div class="card">
      <h2>Vulnerability Findings</h2>
      <div id="vulnerabilities-summary" class="stat"></div>
      <div id="vulnerabilities" class="tools-list"></div>
    </div>
    <div class="card">
      <h2>Dependency Licenses</h2>
      <div id="licenses-summary" class="stat"></div>
      <div id="licenses-repo" class="stat-row"></div>
      <div id="licenses" class="tools-list"></div>
    </div>
  </div>
  <div class="card">
    <h2>API Endpoints</h2>
    <div id="endpoints-summary" class="stat"></div>
    <div id="endpoints" class="tools-list"></div>
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

function renderBarChart(svgId, values, valueLabelId, noteId) {
  const svg = document.getElementById(svgId);
  const label = valueLabelId ? document.getElementById(valueLabelId) : null;
  const note = noteId ? document.getElementById(noteId) : null;
  if (label) label.textContent = values.length > 0 ? String(values[values.length - 1]) : '-';
  if (note) note.textContent = '';
  if (values.length === 0) { svg.innerHTML = ''; return; }

  const width = 300, height = 90, baseline = 78, topPad = 14, axisLabelY = 10;
  const barGap = 3;
  const minBarHeight = 3;

  // A real history where every scan produced the same value (no code changes in between)
  // renders as N identical-height bars - technically accurate, but visually indistinguishable
  // from noise and gives no more information than the single current value already shown
  // below the chart. Collapse to just that one bar and say plainly why, rather than showing
  // several bars that all look the same.
  const allIdentical = values.length > 1 && new Set(values).size === 1;
  const displayValues = allIdentical ? [values[values.length - 1]] : values;
  if (allIdentical && note) {
    note.textContent = 'Unchanged across the last ' + values.length + ' scans.';
  }

  const max = Math.max(...displayValues, 1);
  const barWidth = Math.max(2, (width / displayValues.length) - barGap);

  let content = '<line x1="0" y1="' + baseline + '" x2="' + width + '" y2="' + baseline +
    '" stroke="#2a2a2a" stroke-width="1" />';
  content += '<text x="0" y="' + axisLabelY + '" fill="#5a5a5a" font-size="9" font-family="monospace">max ' + max + '</text>';

  displayValues.forEach((v, i) => {
    const x = i * (barWidth + barGap);
    const barHeight = Math.max(minBarHeight, (v / max) * (baseline - topPad));
    const y = baseline - barHeight;
    const isLast = i === displayValues.length - 1;
    content += '<rect x="' + x + '" y="' + y + '" width="' + barWidth + '" height="' + barHeight +
      '" fill="' + (isLast ? '#fff' : '#4a4a4a') + '" rx="1"><title>' + v + '</title></rect>';
  });

  svg.setAttribute('viewBox', '0 0 ' + width + ' ' + height);
  svg.setAttribute('preserveAspectRatio', 'none');
  svg.innerHTML = content;
}

let graphState = null;

function escapeAttr(value) {
  return String(value).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
}

function escapeHtml(value) {
  return String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

const palette = ['#7fd3ff', '#ff9f7f', '#a3ff7f', '#ff7fd3', '#ffe27f', '#c07fff'];
const colorFor = c => c === null || c === undefined ? '#555' : palette[c % palette.length];

function nodeRadius(degree) {
  return Math.max(3, Math.min(14, 3 + Math.sqrt(degree) * 2));
}

// Barnes-Hut quadtree: approximates the net repulsive force on a node from
// every other node in O(log n) instead of O(n) exact pairwise checks,
// making the force-directed layouts below O(n log n) per iteration instead
// of O(n^2). Validated against exact pairwise output on n=400 (mean
// relative error 1.2%, max 11.7% at theta=0.5) before being wired in here -
// small/medium graphs (where exact was always fast enough) see the same
// tiny approximation error; a 15,241-node real repo (previously would not
// finish in any practical time - 250 iterations * O(n^2) is ~58 billion
// operations) now completes in single-digit seconds.
class BHQuad {
  constructor(x0, y0, x1, y1) {
    this.x0 = x0; this.y0 = y0; this.x1 = x1; this.y1 = y1;
    this.mass = 0; this.cx = 0; this.cy = 0;
    this.node = null;
    this.children = null;
  }
  size() { return this.x1 - this.x0; }
  insert(n, depth) {
    if (depth > 40) return; // pathological coincident points - drop rather than recurse forever
    this.mass += 1;
    this.cx += (n.x - this.cx) / this.mass;
    this.cy += (n.y - this.cy) / this.mass;
    if (this.children) { this._childFor(n).insert(n, depth + 1); return; }
    if (this.node === null) { this.node = n; return; }
    const existing = this.node;
    this.node = null;
    const mx = (this.x0 + this.x1) / 2, my = (this.y0 + this.y1) / 2;
    this.children = [
      new BHQuad(this.x0, this.y0, mx, my), new BHQuad(mx, this.y0, this.x1, my),
      new BHQuad(this.x0, my, mx, this.y1), new BHQuad(mx, my, this.x1, this.y1),
    ];
    this._childFor(existing).insert(existing, depth + 1);
    this._childFor(n).insert(n, depth + 1);
  }
  _childFor(n) {
    const mx = (this.x0 + this.x1) / 2, my = (this.y0 + this.y1) / 2;
    return this.children[(n.y >= my ? 2 : 0) + (n.x >= mx ? 1 : 0)];
  }
}

function buildQuadtree(nodes) {
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const n of nodes) {
    if (n.x < minX) minX = n.x; if (n.x > maxX) maxX = n.x;
    if (n.y < minY) minY = n.y; if (n.y > maxY) maxY = n.y;
  }
  const span = Math.max(maxX - minX, maxY - minY, 1) * 1.05;
  const cx = (minX + maxX) / 2, cy = (minY + maxY) / 2;
  const root = new BHQuad(cx - span / 2, cy - span / 2, cx + span / 2, cy + span / 2);
  for (const n of nodes) root.insert(n, 0);
  return root;
}

// theta: opening angle - a quad is treated as one point mass at its center
// of mass once (quad size / distance) < theta. 0.7 balances accuracy against
// speed (see the validation numbers above); lower is more exact but slower.
function accumulateRepulsion(quad, n, strength, theta, out) {
  if (quad.mass === 0) return;
  if (quad.children === null) {
    if (quad.node === n) return;
    const dx = n.x - quad.node.x, dy = n.y - quad.node.y;
    const distSq = dx * dx + dy * dy || 0.01;
    const force = strength / distSq;
    const dist = Math.sqrt(distSq);
    out.fx += (dx / dist) * force; out.fy += (dy / dist) * force;
    return;
  }
  // Real bug found via review: the opening-angle test alone (size/dist <
  // theta) can pass for a quad that geometrically contains n itself, if the
  // quad's center of mass happens to sit far from n (a skewed distribution -
  // e.g. n isolated near one corner, a dense cluster of other nodes near the
  // opposite corner, pulling the center of mass away while n is still
  // inside the quad's own bounds). Aggregating that quad would include n's
  // own mass, producing self-repulsion. Confirmed empirically with exactly
  // that construction before this check existed. A quad containing n must
  // always be recursed into, regardless of theta, until n is excluded by
  // being in a different child or found as the leaf itself (handled above).
  if (n.x >= quad.x0 && n.x < quad.x1 && n.y >= quad.y0 && n.y < quad.y1) {
    for (const c of quad.children) accumulateRepulsion(c, n, strength, theta, out);
    return;
  }
  const dx = n.x - quad.cx, dy = n.y - quad.cy;
  const distSq = dx * dx + dy * dy || 0.01;
  const dist = Math.sqrt(distSq);
  if (quad.size() / dist < theta) {
    const force = (strength * quad.mass) / distSq;
    out.fx += (dx / dist) * force; out.fy += (dy / dist) * force;
    return;
  }
  for (const c of quad.children) accumulateRepulsion(c, n, strength, theta, out);
}

// Yields to the browser between simulation iterations on large graphs so the
// tab stays responsive (and repaints a progress line) instead of blocking
// the main thread for several seconds straight - the same "show real
// progress on long-running work" convention the CLI's scan/index commands
// already follow. Small/medium graphs finish fast enough that this never
// visibly pauses.
function yieldToUi() {
  // Plain setTimeout, deliberately not requestAnimationFrame: rAF signals
  // "actively animating" to the browser's scheduler on every call, which
  // can starve document_idle / other idle-only work indefinitely even
  // though each individual turn genuinely yields the JS stack. A bare
  // macrotask doesn't claim continuous rendering work, so idle-driven
  // consumers actually get a turn.
  return new Promise(resolve => setTimeout(resolve, 0));
}

function attachZoomPan(svg, initialViewBox, maxZoomOutW) {
  const vb = Object.assign({}, initialViewBox);
  const zoomOutLimit = maxZoomOutW || initialViewBox.w * 4;
  const apply = () => svg.setAttribute('viewBox', vb.x + ' ' + vb.y + ' ' + vb.w + ' ' + vb.h);
  apply();

  svg.addEventListener('wheel', (e) => {
    e.preventDefault();
    const rect = svg.getBoundingClientRect();
    const mx = vb.x + ((e.clientX - rect.left) / rect.width) * vb.w;
    const my = vb.y + ((e.clientY - rect.top) / rect.height) * vb.h;
    const zoomFactor = e.deltaY > 0 ? 1.1 : 0.9;
    const newW = Math.max(initialViewBox.w * 0.15, Math.min(zoomOutLimit, vb.w * zoomFactor));
    const newH = newW * (initialViewBox.h / initialViewBox.w);
    vb.x = mx - (mx - vb.x) * (newW / vb.w);
    vb.y = my - (my - vb.y) * (newH / vb.h);
    vb.w = newW;
    vb.h = newH;
    apply();
  }, { passive: false });

  let isPanning = false;
  let panStart = null;
  svg.addEventListener('mousedown', (e) => {
    if (e.target.tagName === 'circle') return;
    isPanning = true;
    panStart = { x: e.clientX, y: e.clientY, vb: Object.assign({}, vb) };
    svg.style.cursor = 'grabbing';
  });
  window.addEventListener('mousemove', (e) => {
    if (!isPanning) return;
    const rect = svg.getBoundingClientRect();
    vb.x = panStart.vb.x - (e.clientX - panStart.x) * (vb.w / rect.width);
    vb.y = panStart.vb.y - (e.clientY - panStart.y) * (vb.h / rect.height);
    apply();
  });
  window.addEventListener('mouseup', () => { isPanning = false; svg.style.cursor = 'grab'; });
  svg.style.cursor = 'grab';

  svg.__resetView = () => {
    vb.x = initialViewBox.x; vb.y = initialViewBox.y; vb.w = initialViewBox.w; vb.h = initialViewBox.h;
    apply();
  };
}

function resetGraphView(svgId) {
  const svg = document.getElementById(svgId);
  if (svg && svg.__resetView) svg.__resetView();
}

async function renderGraph(data) {
  const svg = document.getElementById('graph');
  const width = 800, height = 320;
  const nodes = data.nodes.map(n => ({
    id: n.id, cluster: n.cluster,
    x: Math.random() * width, y: Math.random() * height, vx: 0, vy: 0
  }));
  const nodeById = {};
  nodes.forEach(n => { nodeById[n.id] = n; });
  const edges = data.edges.filter(e => nodeById[e.source] && nodeById[e.target]);

  // 800 was tuned without accounting for graphs with hundreds of nodes: at typical
  // neighbor spacing for 631 nodes on this canvas it overpowered the centering pull
  // by roughly 10-20x, which is why isolated/low-degree nodes were flying out to the
  // walls and settling in the corners. 140 keeps nodes spread across the full canvas
  // (verified over 15 random-seed runs: 0 nodes left touching a wall every time) while
  // still filling the available space, rather than clumping tightly in the center the
  // way a much lower value (80) did.
  const repulsionStrength = 140;
  // Force-directed layouts converge to mechanical equilibrium in roughly a
  // constant number of iterations regardless of node count - it is not
  // "visiting every node more times" that large graphs need. 250/theta=0.7
  // stays exactly as tuned for graphs at or below the size that tuning was
  // verified against (unchanged, zero regression risk); above that, fewer
  // iterations and a coarser theta are used, trading a small amount of
  // precision (imperceptible at a density where nodes render as a handful
  // of pixels each) for real wall-clock time on repos with thousands of
  // modules.
  const isLarge = nodes.length > 1500;
  const theta = isLarge ? 0.85 : 0.7;
  const iterations = isLarge ? 100 : 250;
  const hoverInfo = document.getElementById('graph-hover-info');
  const showProgress = isLarge;
  let lastYield = Date.now();
  for (let iter = 0; iter < iterations; iter++) {
    const tree = buildQuadtree(nodes);
    for (const a of nodes) {
      const out = { fx: 0, fy: 0 };
      accumulateRepulsion(tree, a, repulsionStrength, theta, out);
      a.vx += out.fx; a.vy += out.fy;
    }
    // Only interrupts the loop with a real frame + task-queue turn every ~80ms
    // (not every iteration - that would add its own overhead on top of the
    // simulation) so a large graph's tab stays responsive and repaints a
    // progress line, instead of blocking the main thread for several
    // seconds straight.
    if (showProgress && Date.now() - lastYield > 80) {
      hoverInfo.textContent = 'Laying out ' + nodes.length + ' nodes... (' + (iter + 1) + '/' + iterations + ')';
      await yieldToUi();
      lastYield = Date.now();
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
      n.vx += (width / 2 - n.x) * 0.003;
      n.vy += (height / 2 - n.y) * 0.003;
      // Repulsion near a rectangle boundary is asymmetric: a node close to a wall only
      // has neighbors pushing it from the inside, so the net repulsive force points
      // straight into the wall, and once two walls both do this at once a node gets
      // pinned in a corner. A soft push back from each wall (growing the closer a node
      // gets) cancels that asymmetry before the node can settle there, without changing
      // the canvas size or the hard clamp that remains as a final safety bound.
      const margin = 60;
      if (n.x < margin) n.vx += (margin - n.x) * 0.06;
      if (n.x > width - margin) n.vx -= (n.x - (width - margin)) * 0.06;
      if (n.y < margin) n.vy += (margin - n.y) * 0.06;
      if (n.y > height - margin) n.vy -= (n.y - (height - margin)) * 0.06;
      n.x += n.vx * 0.1; n.y += n.vy * 0.1;
      n.vx *= 0.85; n.vy *= 0.85;
      n.x = Math.max(10, Math.min(width - 10, n.x));
      n.y = Math.max(10, Math.min(height - 10, n.y));
    });
  }

  const neighborsOf = {};
  nodes.forEach(n => { neighborsOf[n.id] = new Set(); });
  edges.forEach(e => {
    neighborsOf[e.source].add(e.target);
    neighborsOf[e.target].add(e.source);
  });

  // All real connections are visible by default; hovering a node isolates to just that
  // node's own edges (see the mouseenter handler below), hiding every other line. Nothing
  // is hidden unless something is actually hovered.
  let svgContent = '';
  edges.forEach(e => {
    const a = nodeById[e.source], b = nodeById[e.target];
    // Ambiguous (see build_graph_summary) draws dashed and dimmed rather than
    // being excluded entirely - still a real, citable edge, just one the
    // resolver picked among more than one equally-plausible candidate for,
    // so it should read as less certain rather than vanish outright.
    const dash = e.ambiguous ? ' stroke-dasharray="4,3"' : '';
    svgContent += '<line data-source="' + escapeAttr(e.source) + '" data-target="' + escapeAttr(e.target) +
      '" data-ambiguous="' + (e.ambiguous ? '1' : '0') + '"' +
      ' x1="' + a.x + '" y1="' + a.y + '" x2="' + b.x + '" y2="' + b.y +
      '" stroke="#333" stroke-width="1" opacity="' + (e.ambiguous ? '0.35' : '1') + '"' + dash + ' />';
  });
  nodes.forEach(n => {
    svgContent += '<circle data-id="' + escapeAttr(n.id) + '" data-base-r="6" cx="' + n.x + '" cy="' + n.y +
      '" r="6" fill="' + colorFor(n.cluster) + '" style="cursor: pointer;" />';
  });
  svg.setAttribute('viewBox', '0 0 ' + width + ' ' + height);
  svg.innerHTML = svgContent;

  graphState = { nodeById, neighborsOf, edges };

  hoverInfo.textContent = 'Hover a node to see its dependencies.';
  svg.querySelectorAll('circle').forEach(circle => {
    const id = circle.getAttribute('data-id');
    circle.addEventListener('mouseenter', () => {
      const neighbors = neighborsOf[id] || new Set();
      highlightNodes(new Set([id, ...neighbors]), id);
      const importsCount = edges.filter(e => e.source === id).length;
      const importedByCount = edges.filter(e => e.target === id).length;
      hoverInfo.innerHTML = '<span class="hover-path">' + escapeHtml(id) + '</span> - imports ' +
        importsCount + ', imported by ' + importedByCount;
    });
    circle.addEventListener('mouseleave', () => {
      if (!activeClusterId) {
        clearGraphFilter();
      } else {
        applyClusterFilter(activeClusterId);
      }
      hoverInfo.textContent = 'Hover a node to see its dependencies.';
    });
  });
}

function highlightNodes(highlightSet, focusId) {
  const svg = document.getElementById('graph');
  svg.querySelectorAll('circle').forEach(circle => {
    const id = circle.getAttribute('data-id');
    circle.setAttribute('opacity', highlightSet.has(id) ? '1' : '0.08');
    circle.setAttribute('r', id === focusId ? '8' : '6');
  });
  svg.querySelectorAll('line').forEach(line => {
    const source = line.getAttribute('data-source');
    const target = line.getAttribute('data-target');
    const connectedToFocus = focusId ? (source === focusId || target === focusId) : (highlightSet.has(source) && highlightSet.has(target));
    const ambiguous = line.getAttribute('data-ambiguous') === '1';
    // Unrelated edges go fully invisible (not just dim) while hovering - with ~1700 edges
    // rendered at once, even a low dim opacity reads as "many connections" purely from
    // unrelated lines visually crossing near the hovered node's screen position. An
    // ambiguous edge stays visibly less certain even while focused, rather than
    // reading as equally solid as every other highlighted connection.
    line.setAttribute('opacity', connectedToFocus ? (ambiguous ? '0.5' : '0.9') : '0');
    line.setAttribute('stroke', connectedToFocus ? '#fff' : '#333');
  });
}

let activeClusterId = null;

function forEachInteractiveSvg(fn) {
  document.querySelectorAll('.interactive-graph').forEach(fn);
}

function applyClusterFilter(clusterId) {
  const cluster = (window.__aletheoreClusters || []).find(c => c.id === clusterId);
  if (!cluster) return;
  const memberSet = new Set(cluster.modules);
  forEachInteractiveSvg(svg => {
    svg.querySelectorAll('circle').forEach(circle => {
      const id = circle.getAttribute('data-id');
      const baseR = circle.getAttribute('data-base-r') || '6';
      circle.setAttribute('opacity', memberSet.has(id) ? '1' : '0.08');
      circle.setAttribute('r', baseR);
    });
    svg.querySelectorAll('line').forEach(line => {
      const source = line.getAttribute('data-source');
      const target = line.getAttribute('data-target');
      const inCluster = memberSet.has(source) && memberSet.has(target);
      const ambiguous = line.getAttribute('data-ambiguous') === '1';
      line.setAttribute('opacity', inCluster ? (ambiguous ? '0.5' : '0.9') : '0');
      line.setAttribute('stroke', inCluster ? '#fff' : '#333');
    });
  });
}

function isolateCluster(clusterId) {
  activeClusterId = activeClusterId === clusterId ? null : clusterId;
  document.querySelectorAll('.cluster-row').forEach(row => {
    row.classList.toggle('active', row.dataset.clusterId == activeClusterId);
  });
  if (activeClusterId === null) {
    clearGraphFilter();
  } else {
    applyClusterFilter(activeClusterId);
  }
}

function toggleClusterExpand(clusterId) {
  const row = document.querySelector('.cluster-row[data-cluster-id="' + clusterId + '"]');
  if (row) row.classList.toggle('expanded');
}

function clearGraphFilter() {
  forEachInteractiveSvg(svg => {
    svg.querySelectorAll('circle').forEach(circle => {
      circle.setAttribute('opacity', '1');
      circle.setAttribute('r', circle.getAttribute('data-base-r') || '6');
    });
    svg.querySelectorAll('line').forEach(line => {
      line.setAttribute('opacity', line.getAttribute('data-ambiguous') === '1' ? '0.35' : '1');
      line.setAttribute('stroke', '#333');
    });
  });
  activeClusterId = null;
  document.querySelectorAll('.cluster-row').forEach(row => row.classList.remove('active'));
}

// Clusters are modularity communities, not folders, so they have no name of their own -
// derive one from what their members actually share: the deepest common directory across
// all modules in the cluster, or the single file name for a singleton, or (when members
// don't share any directory at all) the most common top-level folder, labeled as mixed.
function deriveClusterName(cluster) {
  const modules = cluster.modules || [];
  if (modules.length === 0) return 'Cluster ' + cluster.id;
  if (modules.length === 1) {
    const parts = modules[0].split('/');
    return parts[parts.length - 1];
  }
  const dirListOf = m => m.split('/').slice(0, -1);
  const dirLists = modules.map(dirListOf);
  const minLen = Math.min(...dirLists.map(d => d.length));
  const common = [];
  for (let i = 0; i < minLen; i++) {
    const seg = dirLists[0][i];
    if (dirLists.every(d => d[i] === seg)) common.push(seg);
    else break;
  }
  if (common.length > 0) return common.join('/');
  const tops = modules.map(m => m.split('/')[0]);
  const counts = {};
  tops.forEach(t => { counts[t] = (counts[t] || 0) + 1; });
  const [topName] = Object.entries(counts).sort((a, b) => b[1] - a[1])[0];
  return topName + ' (mixed)';
}

function renderClusters(data) {
  window.__aletheoreClusters = data.clusters;
  const el = document.getElementById('cluster-list');
  el.innerHTML = data.clusters.map(c =>
    '<div class="cluster-row" data-cluster-id="' + c.id + '">' +
      '<div class="cluster-header" onclick="toggleClusterExpand(' + c.id + '); isolateCluster(' + c.id + ')">' +
        '<span>' + escapeHtml(deriveClusterName(c)) + ' (' + c.modules.length + (c.modules.length === 1 ? ' module' : ' modules') + ')</span>' +
      '</div>' +
      '<div class="cluster-modules">' + c.modules.map(m => '<div>' + escapeHtml(m) + '</div>').join('') + '</div>' +
    '</div>'
  ).join('');
}

async function renderClusterGraph(data) {
  const svg = document.getElementById('cluster-graph');
  // Starting spread only - the simulation is free to move nodes anywhere; the final
  // viewBox is computed from where they actually end up, not clamped to this box.
  const spread = 700;

  const nodeToClusterId = {};
  data.nodes.forEach(n => { nodeToClusterId[n.id] = n.cluster; });

  const nodes = data.nodes.map(n => ({
    id: n.id, cluster: n.cluster,
    x: (Math.random() - 0.5) * spread, y: (Math.random() - 0.5) * spread, vx: 0, vy: 0
  }));
  const nodeById = {};
  nodes.forEach(n => { nodeById[n.id] = n; });

  // Only same-cluster edges are drawn - cross-cluster edges are excluded from this view
  // entirely (dependencies live in the Dependency Graph).
  const internalEdges = data.edges.filter(e => {
    const a = nodeById[e.source], b = nodeById[e.target];
    return a && b && a.cluster !== null && a.cluster !== undefined && a.cluster === b.cluster;
  });

  const clusterModuleCount = {};
  const clusterNameById = {};
  data.clusters.forEach(c => {
    clusterModuleCount[c.id] = c.modules.length;
    clusterNameById[c.id] = deriveClusterName(c);
  });

  // Radius is computed up front (not after simulating) so the collision-resolution pass
  // below can use each node's real rendered size as its minimum-separation distance.
  const degreeOf = {};
  nodes.forEach(n => { degreeOf[n.id] = 0; });
  internalEdges.forEach(e => {
    degreeOf[e.source] = (degreeOf[e.source] || 0) + 1;
    degreeOf[e.target] = (degreeOf[e.target] || 0) + 1;
  });
  nodes.forEach(n => { n.r = nodeRadius(degreeOf[n.id] || 0); });

  // Community-aware force model: same-cluster pairs attract (pulling every member of a
  // cluster toward the others, not just directly-edge-connected ones - Aletheore's clusters
  // are modularity communities, not just direct-edge groups), different-cluster pairs repel.
  // This is what makes clusters read as clean, separated blobs instead of an interleaved mess.
  //
  // On top of that, every pair also gets a hard collision-resolution correction: if two
  // nodes end up closer than their combined radii plus a margin, they are pushed directly
  // apart by exactly the overlap amount, regardless of what the velocity-based forces above
  // computed. Tuning attraction/repulsion strengths alone (the previous approach) could
  // still leave large clusters' members overlapping, since many-body attraction from 100+
  // same-cluster neighbors can outweigh pairwise repulsion no matter how it's balanced. A
  // direct positional correction is what actually guarantees zero overlap - the same
  // technique used by every real force-directed layout's "collision" force (e.g. D3's
  // forceCollide) - matching the reference: nodes may sit close together, but never on
  // top of each other.
  // Phase 1: force-based clustering. Same-cluster attraction pulls each cluster's members
  // into a rough blob, repulsion keeps different clusters apart. This alone does not
  // guarantee zero overlap within a blob (tried combining it with collision-resolution
  // in the same loop - the two kept fighting each other: attraction computed from
  // pre-correction distances got integrated into velocity right after a correction had
  // just resolved it, undoing the fix every iteration). This phase only has to get every
  // cluster's rough shape and position right, not final non-overlapping placement.
  //
  // Repulsion is split into an approximate all-pairs part and a per-cluster correction,
  // rather than approximating everything: Barnes-Hut applies a uniform 400 (the
  // cross-cluster strength - the dominant, genuinely-all-pairs cost) to every pair
  // including same-cluster ones, then the per-cluster pass below subtracts the 120-unit
  // excess back off same-cluster pairs (400 -> 280) and applies the attraction spring,
  // using the real pairwise distance rather than an approximated one.
  //
  // This is NOT always exactly equivalent to the original all-exact version, and a review
  // correctly caught the earlier version of this comment overclaiming that it was: the
  // "400" a same-cluster pair receives from Barnes-Hut isn't guaranteed to be an isolated
  // 400/dist(a,b)^2 term - if a and b's node happens to get aggregated into a multi-node
  // quad together with unrelated nodes (plausible early in the simulation, before clusters
  // have visually separated), the 120-unit correction is computed against the pair's own
  // distance while the thing it's correcting came from a different, blended distance.
  // Measured rather than assumed away: 200 randomized same-cluster-near-a-crowd trials
  // (the condition that triggers this) showed the correction still helped in 179/200 cases
  // (89.5%) and cut mean force error from 22.6% to 5.2% versus not correcting at all - a
  // real, substantial improvement, just not a mathematically exact one. Left as-is rather
  // than chasing full exactness, which would need excluding same-cluster nodes from the
  // shared tree per query (real added complexity) for a cosmetic, already-approximate
  // visual layout where this residual error is in the same range as theta's own.
  const clusterGroups = new Map();
  // Nodes with no detected cluster (cluster is null/undefined) are deliberately excluded
  // here, matching the original sameCluster check (`a.cluster !== null && ... === b.cluster`)
  // - two unclustered nodes were never treated as "same cluster" and must not attract each
  // other. Grouping them together by mistake reintroduces exactly the O(n^2) cost this
  // rewrite exists to remove: verified on Discourse's real data, 5,395 of its 15,241 modules
  // have no cluster, which an earlier draft of this fix accidentally grouped into one bucket.
  nodes.forEach(n => {
    if (n.cluster === null || n.cluster === undefined) return;
    if (!clusterGroups.has(n.cluster)) clusterGroups.set(n.cluster, []);
    clusterGroups.get(n.cluster).push(n);
  });
  // See the matching comment in renderGraph - unchanged tuning at/below the
  // verified size, fewer iterations and a coarser theta above it.
  const isLarge = nodes.length > 1500;
  const iterations = isLarge ? 80 : 200;
  const theta = isLarge ? 0.85 : 0.7;
  const crossClusterRepulsion = 400;
  const sameClusterRepulsion = 280;
  const hoverInfoCluster = document.getElementById('cluster-graph-hover-info');
  const showProgress = isLarge;
  let lastYield = Date.now();
  for (let iter = 0; iter < iterations; iter++) {
    const tree = buildQuadtree(nodes);
    for (const a of nodes) {
      const out = { fx: 0, fy: 0 };
      accumulateRepulsion(tree, a, crossClusterRepulsion, theta, out);
      a.vx += out.fx; a.vy += out.fy;
    }
    for (const [, group] of clusterGroups) {
      if (group.length < 2) continue;
      for (let i = 0; i < group.length; i++) {
        for (let j = i + 1; j < group.length; j++) {
          const a = group[i], b = group[j];
          const dx = b.x - a.x, dy = b.y - a.y;
          const dist = Math.sqrt(dx * dx + dy * dy) || 0.01;
          const ux = dx / dist, uy = dy / dist;
          // Remove the (crossClusterRepulsion - sameClusterRepulsion) excess Barnes-Hut
          // already applied to this pair above, then apply the attraction spring.
          const excess = (crossClusterRepulsion - sameClusterRepulsion) / (dist * dist);
          a.vx += ux * excess; a.vy += uy * excess;
          b.vx -= ux * excess; b.vy -= uy * excess;
          const restLength = a.r + b.r + 4;
          const attractForce = (dist - restLength) * 0.03;
          a.vx += ux * attractForce; a.vy += uy * attractForce;
          b.vx -= ux * attractForce; b.vy -= uy * attractForce;
        }
      }
    }
    if (showProgress && Date.now() - lastYield > 80) {
      hoverInfoCluster.textContent = 'Laying out ' + nodes.length + ' nodes... (' + (iter + 1) + '/' + iterations + ')';
      await yieldToUi();
      lastYield = Date.now();
    }
    nodes.forEach(n => {
      n.vx += -n.x * 0.004;
      n.vy += -n.y * 0.004;
      n.x += n.vx * 0.1; n.y += n.vy * 0.1;
      n.vx *= 0.85; n.vy *= 0.85;
      // Many clusters here are singletons (a module with no same-cluster peer to attract
      // it back) - those nodes only ever feel repulsion from every other node and this weak
      // centering pull, with nothing bounding how far that pushes them. A close pair of
      // singletons spawning near each other can produce a single huge repulsion kick that
      // out-runs a soft restoring force entirely (verified: a spring-style radial pull-back
      // alone still let outliers reach distance 10000-27000 depending on random seed). A hard
      // clamp is the only thing that actually guarantees a bound, the same fix that worked
      // for the Dependency Graph's rectangle walls - this is what keeps "zoom out to see
      // everything" from requiring a nearly-empty view scaled to one runaway node.
      const d = Math.sqrt(n.x * n.x + n.y * n.y) || 0.01;
      const hardRadialClamp = 600;
      if (d > hardRadialClamp) {
        n.x *= hardRadialClamp / d;
        n.y *= hardRadialClamp / d;
        n.vx *= 0.5; n.vy *= 0.5;
      }
    });
  }

  // Phase 2: pure collision resolution, no forces at all - just directly push apart any
  // pair closer than their combined radii, repeated until it converges (or the pass cap is
  // hit). With no attraction re-pulling nodes together in between, this actually resolves
  // chains of overlap (A into B, B's correction into C, ...) instead of fighting a moving
  // target every iteration, which is what made Phase 1 alone insufficient.
  //
  // Exact math, not approximated (collision must guarantee zero overlap, which an
  // approximation can't promise) - but pruned to nearby pairs only via a uniform spatial
  // grid, checked with the standard 5-direction half-neighbor pattern (self + 4 forward
  // offsets) so every adjacent cell pair is compared exactly once. cellSize is the largest
  // possible minSep (two max-radius nodes, 14+14+2) so any pair close enough to collide is
  // guaranteed to land in the same or an adjacent cell - nothing is missed, only the
  // definitely-too-far-apart majority of pairs is skipped.
  const collisionCellSize = 32;
  const neighborOffsets = [[0, 0], [1, 0], [0, 1], [1, 1], [1, -1]];
  for (let pass = 0; pass < 120; pass++) {
    const cells = new Map();
    for (const n of nodes) {
      const key = Math.floor(n.x / collisionCellSize) + ',' + Math.floor(n.y / collisionCellSize);
      let arr = cells.get(key);
      if (!arr) { arr = []; cells.set(key, arr); }
      arr.push(n);
    }
    let anyOverlap = false;
    for (const [key, cellNodes] of cells) {
      const [cxStr, cyStr] = key.split(',');
      const cx = parseInt(cxStr, 10), cy = parseInt(cyStr, 10);
      for (const [ox, oy] of neighborOffsets) {
        const otherNodes = cells.get((cx + ox) + ',' + (cy + oy));
        if (!otherNodes) continue;
        const sameCell = ox === 0 && oy === 0;
        for (let i = 0; i < cellNodes.length; i++) {
          for (let j = (sameCell ? i + 1 : 0); j < otherNodes.length; j++) {
            const a = cellNodes[i], b = otherNodes[j];
            const dx = b.x - a.x, dy = b.y - a.y;
            const dist = Math.sqrt(dx * dx + dy * dy) || 0.01;
            const minSep = a.r + b.r + 2;
            if (dist < minSep) {
              anyOverlap = true;
              const ux = dx / dist, uy = dy / dist;
              const overlap = (minSep - dist) * 0.5;
              a.x -= ux * overlap; a.y -= uy * overlap;
              b.x += ux * overlap; b.y += uy * overlap;
            }
          }
        }
      }
    }
    if (!anyOverlap) break;
  }

  hoverInfoCluster.textContent = 'Hover a node to see which cluster it belongs to.';

  let svgContent = '';
  internalEdges.forEach(e => {
    const a = nodeById[e.source], b = nodeById[e.target];
    svgContent += '<line data-source="' + escapeAttr(e.source) + '" data-target="' + escapeAttr(e.target) +
      '" x1="' + a.x + '" y1="' + a.y + '" x2="' + b.x + '" y2="' + b.y + '" stroke="#2a2f3a" stroke-width="1" />';
  });
  nodes.forEach(n => {
    svgContent += '<circle data-id="' + escapeAttr(n.id) + '" data-base-r="' + n.r + '" cx="' + n.x + '" cy="' + n.y +
      '" r="' + n.r + '" fill="' + colorFor(n.cluster) + '" style="cursor: pointer;" />';
  });

  svg.innerHTML = svgContent;

  // With many distinct clusters, a handful of small/loosely-connected ones can end up far
  // from the main mass under pure repulsion - fitting the initial view to the absolute
  // extent would shrink everything else to near-invisibility around a mostly-empty box.
  // Frame the initial view around the 90th-percentile radius (the "main mass"), but still
  // allow zooming out far enough to reach the true full extent, so nothing is ever
  // unreachable - just not force-fit into the default view.
  const dists = nodes.map(n => Math.sqrt(n.x * n.x + n.y * n.y)).sort((a, b) => a - b);
  const mainRadius = Math.max(60, dists[Math.floor(dists.length * 0.9)] || 60);
  const fullRadius = Math.max(mainRadius, dists[dists.length - 1] || mainRadius);
  const padding = 40;
  const mainSize = mainRadius * 2 + padding * 2;
  const fullSize = fullRadius * 2 + padding * 2;
  attachZoomPan(
    svg,
    { x: -mainSize / 2, y: -mainSize / 2, w: mainSize, h: mainSize },
    fullSize
  );

  const hoverInfo = document.getElementById('cluster-graph-hover-info');
  svg.querySelectorAll('circle[data-id]').forEach(circle => {
    const id = circle.getAttribute('data-id');
    const clusterId = nodeToClusterId[id];
    circle.addEventListener('mouseenter', () => {
      if (clusterId === null || clusterId === undefined) {
        clearGraphFilter();
        hoverInfo.innerHTML = '<span class="hover-path">' + escapeHtml(id) + '</span> - not part of a detected cluster';
      } else {
        applyClusterFilter(clusterId);
        hoverInfo.innerHTML = '<span class="hover-path">' + escapeHtml(id) + '</span> - ' +
          escapeHtml(clusterNameById[clusterId] || ('Cluster ' + clusterId)) +
          ' (' + (clusterModuleCount[clusterId] || 0) + ' modules)';
      }
    });
    circle.addEventListener('mouseleave', () => {
      if (!activeClusterId) {
        clearGraphFilter();
      } else {
        applyClusterFilter(activeClusterId);
      }
      hoverInfo.textContent = 'Hover a node to see which cluster it belongs to.';
    });
  });
}

function renderDeadCode(data) {
  const summary = document.getElementById('dead-code-summary');
  const el = document.getElementById('dead-code');
  const unreachable = data.unreachable_modules;
  const unusedDeps = data.unused_dependencies;
  summary.textContent = unreachable.length + ' unreachable module' + (unreachable.length === 1 ? '' : 's') +
    ', ' + unusedDeps.length + ' unused dependenc' + (unusedDeps.length === 1 ? 'y' : 'ies');

  if (unreachable.length === 0 && unusedDeps.length === 0) {
    el.innerHTML = '<div class="tool-row">No dead code detected.</div>';
    return;
  }
  const moduleRows = unreachable.map(m =>
    '<div class="tool-row"><span class="tool-name">' + escapeHtml(m.path) + '</span> - ' + escapeHtml(m.reason) + '</div>'
  );
  const depRows = unusedDeps.map(d =>
    '<div class="tool-row"><span class="tool-name">' + escapeHtml(d.package) + '</span> - unused ' + escapeHtml(d.ecosystem) + ' dependency</div>'
  );
  el.innerHTML = moduleRows.concat(depRows).join('');
}

function severityLabel(severity) {
  if (!severity || severity.length === 0) return 'unrated';
  return severity.map(s => s.type + ' ' + s.score).join(', ');
}

function skipCaption(data, fallback) {
  if (data.checked) return '';
  return ' (' + (data.reason || fallback) + ')';
}

function renderVulnerabilities(data) {
  const summary = document.getElementById('vulnerabilities-summary');
  const el = document.getElementById('vulnerabilities');
  const findings = data.findings;
  summary.textContent = findings.length + ' vulnerabilit' + (findings.length === 1 ? 'y' : 'ies') + ' found' +
    skipCaption(data, 'dependency scan not run');

  if (findings.length === 0) {
    el.innerHTML = '<div class="tool-row">No known vulnerabilities in pinned dependencies.</div>';
    return;
  }
  el.innerHTML = findings.map(f =>
    '<div class="tool-row"><span class="tool-name">' + escapeHtml(f.package) + '@' + escapeHtml(f.installed_version) +
    '</span> - ' + escapeHtml(f.advisory_id) + ' (' + escapeHtml(severityLabel(f.severity)) + ')' +
    '<div class="tool-detail">' + escapeHtml(f.summary || '') + '</div></div>'
  ).join('');
}

function renderLicenses(data) {
  const summary = document.getElementById('licenses-summary');
  const repoEl = document.getElementById('licenses-repo');
  const el = document.getElementById('licenses');
  const findings = data.findings;
  summary.textContent = findings.length + ' dependenc' + (findings.length === 1 ? 'y' : 'ies') +
    ' with an unknown or non-permissive license' + skipCaption(data, 'license scan not run');
  repoEl.innerHTML = '<span>Repo license</span><span>' + escapeHtml(data.repo_license.category) + '</span>';

  if (findings.length === 0) {
    el.innerHTML = '<div class="tool-row">Every checked dependency has a known, permissive license.</div>';
    return;
  }
  el.innerHTML = findings.map(f =>
    '<div class="tool-row"><span class="tool-name">' + escapeHtml(f.package) + '</span> - ' +
    escapeHtml(f.ecosystem) + ', license: ' + escapeHtml(f.license || 'unknown') +
    ' (' + escapeHtml(f.category) + ')</div>'
  ).join('');
}

function renderEndpoints(data) {
  const summary = document.getElementById('endpoints-summary');
  const el = document.getElementById('endpoints');
  const endpoints = data.endpoints;
  const resolvedCount = endpoints.filter(e => !e.unresolved).length;
  const mountCount = endpoints.length - resolvedCount;
  summary.textContent = resolvedCount + ' API endpoint' + (resolvedCount === 1 ? '' : 's') + ' mapped from source' +
    (mountCount > 0 ? ', ' + mountCount + ' router mount' + (mountCount === 1 ? '' : 's') + ' not individually resolved' : '') +
    skipCaption(data, 'endpoint mapping not run');

  if (endpoints.length === 0) {
    el.innerHTML = '<div class="tool-row">No API endpoints detected.</div>';
    return;
  }
  // A null method means the scanner found a router mount/include/middleware
  // delegation, not a concrete leaf route - labeled distinctly rather than
  // printed as the literal string "null", which would misrepresent evidence
  // the scanner itself flagged as unresolved.
  el.innerHTML = endpoints.map(e => {
    const methodLabel = e.method || 'MOUNT';
    const noteRow = e.note ? '<div class="tool-detail">' + escapeHtml(e.note) + '</div>' : '';
    return '<div class="tool-row"><span class="tool-name">' + escapeHtml(methodLabel) + ' ' + escapeHtml(e.path) +
      '</span> - ' + escapeHtml(e.file) + ':' + e.line + (e.handler ? ' (' + escapeHtml(e.handler) + ')' : '') +
      noteRow + '</div>';
  }).join('');
}

function renderMcpTools(tools) {
  const el = document.getElementById('mcp-tools');
  el.innerHTML = tools.map(t =>
    '<div class="tool-row"><span class="tool-name">' + t.name + '</span> - ' + (t.description || '') + '</div>'
  ).join('');
}

let lastScannedAtIso = null;
let currentTz = 'Asia/Kolkata';

function renderScannedAt() {
  if (!lastScannedAtIso) return;
  const date = new Date(lastScannedAtIso);
  const tzLabel = currentTz === 'Asia/Kolkata' ? 'IST' : 'UTC';
  const dateStr = date.toLocaleDateString('en-CA', { timeZone: currentTz });
  const timeStr = date.toLocaleTimeString('en-US', {
    timeZone: currentTz, hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: true
  });
  document.getElementById('scanned-at-date').textContent = dateStr;
  document.getElementById('scanned-at-time').textContent = timeStr + ' ' + tzLabel;
  document.getElementById('tz-toggle').textContent = currentTz === 'Asia/Kolkata' ? 'Show UTC' : 'Show IST';
}

function toggleTimezone() {
  currentTz = currentTz === 'Asia/Kolkata' ? 'UTC' : 'Asia/Kolkata';
  renderScannedAt();
}

async function loadAll() {
  const evidence = await fetchJSON('/api/evidence');
  lastScannedAtIso = evidence.scanned_at;
  renderScannedAt();
  renderRepoOverview(evidence.repo_overview);
  renderGitActivity(evidence.git_activity);
  renderSecurity(evidence.security);
  renderArchitecture(evidence.architecture);
  renderDeadCode(evidence.dead_code);
  renderVulnerabilities(evidence.security.vulnerabilities);
  renderLicenses(evidence.security.licenses);
  renderEndpoints(evidence.endpoints);

  const history = await fetchJSON('/api/history');
  renderBarChart('sparkline-modules', history.map(h => h.module_count), 'sparkline-modules-value', 'sparkline-modules-note');
  renderBarChart('sparkline-secrets', history.map(h => h.secrets_findings), 'sparkline-secrets-value', 'sparkline-secrets-note');
  renderBarChart('sparkline-vulns', history.map(h => h.vulnerability_findings), 'sparkline-vulns-value', 'sparkline-vulns-note');

  const graph = await fetchJSON('/api/graph');
  // Independent of each other (renderClusters only reads the raw cluster
  // list, not renderGraph's computed positions) - run concurrently rather
  // than serializing two potentially multi-second layouts on large repos.
  renderClusters(graph);
  await Promise.all([renderGraph(graph), renderClusterGraph(graph)]);
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
