import asyncio
import json
from pathlib import Path

from sse_starlette.sse import EventSourceResponse
from starlette.applications import Starlette
from starlette.responses import HTMLResponse, JSONResponse
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

    return Starlette(
        routes=[
            Route("/", index),
            Route("/api/evidence", api_evidence),
            Route("/api/history", api_history),
            Route("/api/graph", api_graph),
            Route("/api/mcp-tools", api_mcp_tools),
            Route("/events", events),
        ]
    )


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
