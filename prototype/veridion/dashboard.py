import asyncio
import json
from pathlib import Path

from sse_starlette.sse import EventSourceResponse
from starlette.applications import Starlette
from starlette.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.routing import Route

from veridion.history import list_snapshots
from veridion.mcp_server import build_server, read_evidence


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
<title>Veridion Dashboard</title>
<meta charset="utf-8">
<style>
  body { font-family: -apple-system, sans-serif; margin: 0; padding: 24px; background: #000; color: #f2f2f2; }
  .header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px; flex-wrap: wrap; gap: 12px; }
  .logo { height: 28px; display: block; }
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
  .cluster-list { max-height: 320px; overflow-y: auto; }
  .cluster-row { border-bottom: 1px solid #1a1a1a; }
  .cluster-header { padding: 8px 0; font-size: 13px; cursor: pointer; display: flex; justify-content: space-between; color: #c8c8c8; transition: color 0.15s ease; }
  .cluster-header:hover { color: #fff; }
  .cluster-row.active .cluster-header { color: #fff; font-weight: 600; }
  .cluster-modules { display: none; padding: 0 0 10px 12px; font-size: 12px; color: #8a8a8a; font-family: monospace; }
  .cluster-row.expanded .cluster-modules { display: block; }
  .cluster-modules div { padding: 2px 0; }
  .sparkline-value { font-size: 20px; font-weight: 600; margin-top: 6px; color: #fff; }
  #cluster-graph { height: 620px; }
  #cluster-graph-hover-info { min-height: 18px; margin-top: 8px; font-size: 13px; color: #9a9a9a; }
  #cluster-graph-hover-info .hover-path { color: #fff; font-family: monospace; }
</style>
</head>
<body>
<div id="app">
  <div class="header">
    <img src="/logo.png" alt="Veridion" class="logo">
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
    </div>
    <div class="card">
      <h2>Secrets Findings Trend</h2>
      <svg id="sparkline-secrets" class="barchart"></svg>
      <div id="sparkline-secrets-value" class="sparkline-value"></div>
    </div>
    <div class="card">
      <h2>Vulnerability Findings Trend</h2>
      <svg id="sparkline-vulns" class="barchart"></svg>
      <div id="sparkline-vulns-value" class="sparkline-value"></div>
    </div>
  </div>
  <div class="grid">
    <div class="card" style="grid-column: span 2;">
      <h2>Dependency Graph</h2>
      <svg id="graph" class="interactive-graph" viewBox="0 0 800 320"></svg>
      <div id="graph-hover-info">Hover a node to see its dependencies.</div>
      <div class="graph-controls"><button id="clear-filter-btn" onclick="clearGraphFilter()">Show all clusters</button></div>
    </div>
    <div class="card">
      <h2>File Clusters</h2>
      <div id="cluster-list" class="cluster-list"></div>
    </div>
  </div>
  <div class="grid">
    <div class="card" style="grid-column: 1 / -1;">
      <h2>Clusters Graph</h2>
      <svg id="cluster-graph" class="interactive-graph" viewBox="0 0 1400 900"></svg>
      <div class="graph-hint">Scroll or pinch to zoom, drag to pan.</div>
      <div id="cluster-graph-hover-info">Hover a node to see which cluster it belongs to.</div>
      <div class="graph-controls"><button onclick="resetGraphView('cluster-graph')">Reset view</button></div>
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

function renderBarChart(svgId, values, valueLabelId) {
  const svg = document.getElementById(svgId);
  const label = valueLabelId ? document.getElementById(valueLabelId) : null;
  if (label) label.textContent = values.length > 0 ? String(values[values.length - 1]) : '-';
  if (values.length === 0) { svg.innerHTML = ''; return; }

  const width = 300, height = 90, baseline = 78, topPad = 14, axisLabelY = 10;
  const max = Math.max(...values, 1);
  const barGap = 3;
  const barWidth = Math.max(2, (width / values.length) - barGap);
  const minBarHeight = 3;

  let content = '<line x1="0" y1="' + baseline + '" x2="' + width + '" y2="' + baseline +
    '" stroke="#2a2a2a" stroke-width="1" />';
  content += '<text x="0" y="' + axisLabelY + '" fill="#5a5a5a" font-size="9" font-family="monospace">max ' + max + '</text>';

  values.forEach((v, i) => {
    const x = i * (barWidth + barGap);
    const barHeight = Math.max(minBarHeight, (v / max) * (baseline - topPad));
    const y = baseline - barHeight;
    const isLast = i === values.length - 1;
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

  const neighborsOf = {};
  nodes.forEach(n => { neighborsOf[n.id] = new Set(); });
  edges.forEach(e => {
    neighborsOf[e.source].add(e.target);
    neighborsOf[e.target].add(e.source);
  });

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
    circle.setAttribute('opacity', highlightSet.has(id) ? '1' : '0.15');
    circle.setAttribute('r', id === focusId ? '8' : '6');
  });
  svg.querySelectorAll('line').forEach(line => {
    const source = line.getAttribute('data-source');
    const target = line.getAttribute('data-target');
    const connectedToFocus = focusId ? (source === focusId || target === focusId) : (highlightSet.has(source) && highlightSet.has(target));
    line.setAttribute('opacity', connectedToFocus ? '0.9' : '0.05');
    line.setAttribute('stroke', connectedToFocus ? '#7fd3ff' : '#333');
  });
}

let activeClusterId = null;

function forEachInteractiveSvg(fn) {
  document.querySelectorAll('.interactive-graph').forEach(fn);
}

function applyClusterFilter(clusterId) {
  const cluster = (window.__veridionClusters || []).find(c => c.id === clusterId);
  if (!cluster) return;
  const memberSet = new Set(cluster.modules);
  forEachInteractiveSvg(svg => {
    svg.querySelectorAll('circle').forEach(circle => {
      const id = circle.getAttribute('data-id');
      const baseR = circle.getAttribute('data-base-r') || '6';
      circle.setAttribute('opacity', memberSet.has(id) ? '1' : '0.15');
      circle.setAttribute('r', baseR);
    });
    svg.querySelectorAll('line').forEach(line => {
      const source = line.getAttribute('data-source');
      const target = line.getAttribute('data-target');
      const inCluster = memberSet.has(source) && memberSet.has(target);
      line.setAttribute('opacity', inCluster ? '0.9' : '0.05');
      line.setAttribute('stroke', inCluster ? '#7fd3ff' : '#333');
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

function renderClusters(data) {
  window.__veridionClusters = data.clusters;
  const el = document.getElementById('cluster-list');
  el.innerHTML = data.clusters.map(c =>
    '<div class="cluster-row" data-cluster-id="' + c.id + '">' +
      '<div class="cluster-header" onclick="toggleClusterExpand(' + c.id + '); isolateCluster(' + c.id + ')">' +
        '<span>Cluster ' + c.id + ' (' + c.modules.length + ' modules)</span>' +
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
  data.clusters.forEach(c => { clusterModuleCount[c.id] = c.modules.length; });

  // Community-aware force model: same-cluster pairs attract (pulling every member of a
  // cluster toward the others, not just directly-edge-connected ones - Veridion's clusters
  // are modularity communities, not just direct-edge groups), different-cluster pairs repel.
  // This is what makes clusters read as clean, separated blobs instead of an interleaved mess.
  const iterations = 200;
  for (let iter = 0; iter < iterations; iter++) {
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i], b = nodes[j];
        const dx = b.x - a.x, dy = b.y - a.y;
        const dist = Math.sqrt(dx * dx + dy * dy) || 0.01;
        const sameCluster = a.cluster !== null && a.cluster !== undefined && a.cluster === b.cluster;
        // Repulsion always applies, to every pair - this is what keeps nodes from ever
        // fully overlapping (and keeps internal edges visible as real lines, not hidden
        // under a stack of coincident circles). Same-cluster pairs get extra attraction on
        // top, which pulls them closer than different-cluster pairs without eliminating
        // the minimum spacing repulsion guarantees.
        const repulsionForce = (sameCluster ? 280 : 400) / (dist * dist);
        const rfx = (dx / dist) * repulsionForce, rfy = (dy / dist) * repulsionForce;
        a.vx -= rfx; a.vy -= rfy;
        b.vx += rfx; b.vy += rfy;
        if (sameCluster) {
          const attractForce = (dist - 26) * 0.03;
          const afx = (dx / dist) * attractForce, afy = (dy / dist) * attractForce;
          a.vx += afx; a.vy += afy;
          b.vx -= afx; b.vy -= afy;
        }
      }
    }
    nodes.forEach(n => {
      n.vx += -n.x * 0.0025;
      n.vy += -n.y * 0.0025;
      n.x += n.vx * 0.1; n.y += n.vy * 0.1;
      n.vx *= 0.85; n.vy *= 0.85;
    });
  }

  const degreeOf = {};
  nodes.forEach(n => { degreeOf[n.id] = 0; });
  internalEdges.forEach(e => {
    degreeOf[e.source] = (degreeOf[e.source] || 0) + 1;
    degreeOf[e.target] = (degreeOf[e.target] || 0) + 1;
  });

  let svgContent = '';
  internalEdges.forEach(e => {
    const a = nodeById[e.source], b = nodeById[e.target];
    svgContent += '<line data-source="' + escapeAttr(e.source) + '" data-target="' + escapeAttr(e.target) +
      '" x1="' + a.x + '" y1="' + a.y + '" x2="' + b.x + '" y2="' + b.y + '" stroke="#2a2f3a" stroke-width="1" />';
  });
  nodes.forEach(n => {
    const r = nodeRadius(degreeOf[n.id] || 0);
    svgContent += '<circle data-id="' + escapeAttr(n.id) + '" data-base-r="' + r + '" cx="' + n.x + '" cy="' + n.y +
      '" r="' + r + '" fill="' + colorFor(n.cluster) + '" style="cursor: pointer;" />';
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
        hoverInfo.innerHTML = '<span class="hover-path">' + escapeHtml(id) + '</span> - Cluster ' + clusterId +
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
  renderBarChart('sparkline-modules', history.map(h => h.module_count), 'sparkline-modules-value');
  renderBarChart('sparkline-secrets', history.map(h => h.secrets_findings), 'sparkline-secrets-value');
  renderBarChart('sparkline-vulns', history.map(h => h.vulnerability_findings), 'sparkline-vulns-value');

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
