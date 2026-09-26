import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";

const repo = process.argv[2] ?? "/tmp/aletheore-evidence";
const graph = JSON.parse(readFileSync(new URL("../src/data/graph.json", import.meta.url), "utf8")) as { nodes: { id: string }[] };
const inGraph = new Set(graph.nodes.map((n) => n.id));
const q = (...args: string[]) => execFileSync("aletheore", ["query", ...args], { cwd: repo, encoding: "utf8", maxBuffer: 64 * 1024 * 1024 });

const endpoints = (JSON.parse(q("endpoints")).endpoints as { method: string; path: string; file: string; line: number; handler: string; unresolved: boolean }[])
  .filter((e) => !e.unresolved && inGraph.has(e.file) && e.method === "GET" && e.path.length > 1 && !e.path.includes("{affiliate"))
  .sort((a, b) => a.path.localeCompare(b.path));
if (endpoints.length === 0) throw new Error("no resolved GET endpoint whose file is in the graph");
const ep = endpoints[0];

const sym = q("symbol-source", ep.file, ep.handler);
const start = /start_line:\s*(\d+)/.exec(sym)?.[1];
const end = /end_line:\s*(\d+)/.exec(sym)?.[1];
if (!start || !end) throw new Error("could not read the symbol's line range");

const owners = JSON.parse(q("ownership", ep.file)) as { names: string[]; commit_count: number; percent: number }[];
const top = owners[0];
const hot = (JSON.parse(q("hotspots")) as { path: string; churn_count: number; dependents_count: number }[]).find((h) => h.path === ep.file);
const log = execFileSync("git", ["-C", repo, "log", "-1", "--format=%h|%ad|%s", "--date=short", "--", ep.file], { encoding: "utf8" }).trim();
const [sha, date, subject] = log.split("|");
const commit = execFileSync("git", ["-C", repo, "rev-parse", "--short", "HEAD"], { encoding: "utf8" }).trim();
if (!hot) throw new Error("file is not in the hotspot list, so there is no verdict to state");

const touched = execFileSync("git", ["-C", repo, "show", "--name-only", "--format=", sha], { encoding: "utf8" }).split("\n").filter((f) => inGraph.has(f));

const out = {
  source: "aletheore scan and queries run against the Aletheore repository itself",
  commit,
  file: ep.file,
  ownerCommits: top.commit_count,
  commitFiles: touched,
  steps: [
    { id: "endpoint", label: "endpoint", value: `${ep.method} ${ep.path}`, detail: `A route the scanner found in ${ep.file}.` },
    { id: "handler", label: "handler", value: `${ep.file.split("/").pop()}:${ep.line}`, detail: `The scanner resolved the route to ${ep.file} at line ${ep.line}.` },
    { id: "symbol", label: "symbol", value: ep.handler, detail: `${ep.handler} spans lines ${start} to ${end} of ${ep.file}.` },
    { id: "owner", label: "owner", value: top.names[top.names.length - 1], detail: `${top.commit_count} commits touch this file, ${Math.round(top.percent * 100)}% of them by this author.` },
    { id: "touched", label: "last touched", value: sha, detail: `${date}: ${subject}` },
    { id: "verdict", label: "verdict", value: `Hotspot: ${hot.churn_count} commits`, detail: `${hot.churn_count} commits and ${hot.dependents_count} dependent modules put this file on the hotspot list.` },
  ],
};
writeFileSync(new URL("../src/data/evidence-chain.json", import.meta.url), JSON.stringify(out, null, 2));
console.log(`evidence-chain.json: ${ep.method} ${ep.path} -> ${ep.file}:${ep.line} (${ep.handler})`);
