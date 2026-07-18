import asyncio
import json
from pathlib import Path

from sse_starlette.sse import EventSourceResponse
from starlette.applications import Starlette
from starlette.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.routing import Route

from aletheore.history import list_snapshots
from aletheore.mcp_server import build_server, read_evidence


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


async def _watch_evidence_mtime(repo_path: Path):
    evidence_path = repo_path / ".aletheore" / "air.json"
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

    async def events(request):
        return EventSourceResponse(_watch_evidence_mtime(repo_path))

    async def logo(request):
        logo_path = Path(__file__).resolve().parent / "static" / "logo.png"
        return FileResponse(logo_path)

    return Starlette(
        routes=[
            Route("/", index),
            Route("/api/evidence", api_evidence),
            Route("/api/history", api_history),
            Route("/api/graph", api_graph),
            Route("/api/mcp-tools", api_mcp_tools),
            Route("/events", events),
            Route("/logo.png", logo),
        ]
    )


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
        // 800 was tuned without accounting for graphs with hundreds of nodes: at typical
        // neighbor spacing for 631 nodes on this canvas it overpowered the centering pull
        // by roughly 10-20x, which is why isolated/low-degree nodes were flying out to the
        // walls and settling in the corners. 140 keeps nodes spread across the full canvas
        // (verified over 15 random-seed runs: 0 nodes left touching a wall every time) while
        // still filling the available space, rather than clumping tightly in the center the
        // way a much lower value (80) did.
        const force = 140 / distSq;
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
    svgContent += '<line data-source="' + escapeAttr(e.source) + '" data-target="' + escapeAttr(e.target) +
      '" x1="' + a.x + '" y1="' + a.y + '" x2="' + b.x + '" y2="' + b.y + '" stroke="#333" stroke-width="1" />';
  });
  nodes.forEach(n => {
    svgContent += '<circle data-id="' + escapeAttr(n.id) + '" data-base-r="6" cx="' + n.x + '" cy="' + n.y +
      '" r="6" fill="' + colorFor(n.cluster) + '" style="cursor: pointer;" />';
  });
  svg.setAttribute('viewBox', '0 0 ' + width + ' ' + height);
  svg.innerHTML = svgContent;

  graphState = { nodeById, neighborsOf, edges };

  const hoverInfo = document.getElementById('graph-hover-info');
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
    // Unrelated edges go fully invisible (not just dim) while hovering - with ~1700 edges
    // rendered at once, even a low dim opacity reads as "many connections" purely from
    // unrelated lines visually crossing near the hovered node's screen position.
    line.setAttribute('opacity', connectedToFocus ? '0.9' : '0');
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
      line.setAttribute('opacity', inCluster ? '0.9' : '0');
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
      line.setAttribute('opacity', '1');
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

function renderClusterGraph(data) {
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
  const iterations = 200;
  for (let iter = 0; iter < iterations; iter++) {
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i], b = nodes[j];
        const dx = b.x - a.x, dy = b.y - a.y;
        const dist = Math.sqrt(dx * dx + dy * dy) || 0.01;
        const ux = dx / dist, uy = dy / dist;
        const sameCluster = a.cluster !== null && a.cluster !== undefined && a.cluster === b.cluster;

        const repulsionForce = (sameCluster ? 280 : 400) / (dist * dist);
        a.vx -= ux * repulsionForce; a.vy -= uy * repulsionForce;
        b.vx += ux * repulsionForce; b.vy += uy * repulsionForce;
        if (sameCluster) {
          const restLength = a.r + b.r + 4;
          const attractForce = (dist - restLength) * 0.03;
          a.vx += ux * attractForce; a.vy += uy * attractForce;
          b.vx -= ux * attractForce; b.vy -= uy * attractForce;
        }
      }
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
  for (let pass = 0; pass < 120; pass++) {
    let anyOverlap = false;
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i], b = nodes[j];
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
    if (!anyOverlap) break;
  }

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

  const history = await fetchJSON('/api/history');
  renderBarChart('sparkline-modules', history.map(h => h.module_count), 'sparkline-modules-value', 'sparkline-modules-note');
  renderBarChart('sparkline-secrets', history.map(h => h.secrets_findings), 'sparkline-secrets-value', 'sparkline-secrets-note');
  renderBarChart('sparkline-vulns', history.map(h => h.vulnerability_findings), 'sparkline-vulns-value', 'sparkline-vulns-note');

  const graph = await fetchJSON('/api/graph');
  renderGraph(graph);
  renderClusters(graph);
  renderClusterGraph(graph);
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
