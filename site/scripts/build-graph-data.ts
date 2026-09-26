import { readFileSync, writeFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { layout3d, project, type GraphInput } from "../src/lib/graph3d.ts";

const airPath = process.argv[2] ?? "/tmp/aletheore-evidence/.aletheore/air.json";
const repoDir = airPath.replace(/\/\.aletheore\/air\.json$/, "");
const air = JSON.parse(readFileSync(airPath, "utf8"));
const graph = air.repository.dependency_graph as { nodes: string[]; edges: [string, string][] };

const degree = new Map<string, number>();
for (const [a, b] of graph.edges) {
  degree.set(a, (degree.get(a) ?? 0) + 1);
  degree.set(b, (degree.get(b) ?? 0) + 1);
}
const CAP = 200;
const keep = [...degree.entries()]
  .sort((x, y) => y[1] - x[1] || x[0].localeCompare(y[0]))
  .slice(0, CAP)
  .map(([id]) => id)
  .sort();
const keepSet = new Set(keep);
const label = (id: string) => id.split("/").slice(-2).join("/");
const input: GraphInput = {
  nodes: keep.map((id) => ({ id, label: label(id) })),
  edges: graph.edges.filter(([a, b]) => keepSet.has(a) && keepSet.has(b)),
};

const laid = layout3d(input, { seed: 7, repulsion: 3200, rest: 120, spring: 0.012, center: 0.0006, iterations: 520 });
const commit = execFileSync("git", ["-C", repoDir, "rev-parse", "--short", "HEAD"]).toString().trim();
const out = {
  source: "aletheore scan of the Aletheore repository (own dependency graph, top modules by degree)",
  scannedAt: air.scanned_at as string,
  commit,
  nodes: laid.nodes.map((n) => ({ ...n, x: +n.x.toFixed(4), y: +n.y.toFixed(4), z: +n.z.toFixed(4) })),
  edges: laid.edges,
};
writeFileSync(new URL("../src/data/graph.json", import.meta.url), JSON.stringify(out));

// Pre-rendered still of the same layout: used as the LCP placeholder and as the fallback for
// visitors without WebGL or with reduced motion.
const W = 900, H = 900;
const o = { rotY: 0.6, rotX: -0.35, distance: 3.6, fov: 1150, width: W, height: H };
const pts = out.nodes.map((n) => project(n, o));
const lines = out.edges
  .map(([i, j]) => `<line x1="${pts[i].x.toFixed(1)}" y1="${pts[i].y.toFixed(1)}" x2="${pts[j].x.toFixed(1)}" y2="${pts[j].y.toFixed(1)}"/>`)
  .join("");
const dots = pts.map((p, i) => `<circle cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="${((out.nodes[i].hub ? 5.5 : 3.2) * p.scale).toFixed(1)}"/>`).join("");
const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="Dependency graph of the Aletheore repository"><g stroke="#16140F" stroke-opacity="0.28" stroke-width="1">${lines}</g><g fill="#16140F">${dots}</g></svg>`;
writeFileSync(new URL("../public/hero-graph-static.svg", import.meta.url), svg);
console.log(`graph.json: ${out.nodes.length} nodes, ${out.edges.length} edges, commit ${commit}`);
