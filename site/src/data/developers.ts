import tools from "@/data/mcp-tools.json";

export const developersHead = {
  eyebrow: "Three surfaces, one evidence file",
  title: "Start local. Stay local as long as you want.",
  intro: "Every surface below reads the exact same air.json. Nothing is re-derived, re-guessed, or inconsistent between them.",
};

export const flow = [
  { k: "$ aletheore scan", title: "Build the evidence", body: "Tree-sitter and git history produce air.json and air.toon. No LLM in the scan path, no account, no network access." },
  { k: "on: pull_request", title: "The GitHub App keeps watch", body: "Automatic PR comments, branch-protection checks, and Flash reviews on every push: same evidence, hosted." },
  { k: "$ aletheore mcp .", title: "Agents query it directly", body: "Read-only tools over stdio, TOON-encoded: a coding agent gets exact answers instead of re-reading files on every lookup." },
] as const;

export const quickstart = {
  eyebrow: "CLI workflow",
  title: "Scan once. Query the evidence many ways.",
  intro: "The deterministic scan writes AIR, Aletheore's Intermediate Representation. Downstream commands read AIR instead of scanning blindly again.",
  commands: [
    { title: "Install and scan", code: "pip install Aletheore\naletheore scan .", body: "Builds .aletheore/air.json, .aletheore/air.toon, and history snapshots." },
    { title: "Audit locally or managed", code: "aletheore audit .\naletheore audit . --managed", body: "Use your own provider locally, or run a managed audit with an Aletheore API token." },
    { title: "Search and dashboard", code: "aletheore index .\naletheore query search-codebase \"auth flow\"\naletheore dashboard .", body: "Build a local semantic index, ask targeted questions, and inspect evidence visually." },
  ],
};

export type TermLine = { text: string; kind?: "cmd" | "out" };
export const terminals: { id: string; label: string; copy: string; lines: TermLine[] }[] = [
  {
    id: "install",
    label: "Install",
    copy: "pip install Aletheore\naletheore scan .\naletheore query endpoints\naletheore dashboard",
    lines: [
      { text: "pip install Aletheore", kind: "cmd" },
      { text: "aletheore scan .", kind: "cmd" },
      { text: "aletheore query endpoints", kind: "cmd" },
      { text: "aletheore dashboard", kind: "cmd" },
    ],
  },
  {
    id: "scan",
    label: "scan output",
    copy: "aletheore scan .",
    lines: [
      { text: "aletheore scan .", kind: "cmd" },
      { text: "Scanning /path/to/repo..." },
      { text: "  → Detecting languages, frameworks, and build tools" },
      { text: "  → Building module dependency graph (parsing source with tree-sitter)" },
      { text: "  → Analyzing git history and ownership" },
      { text: "  → Scanning working tree for secrets" },
      { text: "  → Scanning git history for secrets (can be slow on large histories)" },
      { text: "  → Clustering modules and checking layer conventions" },
      { text: "  → Mapping API endpoints" },
      { text: "  → Detecting dead code" },
      { text: "  → Computing git hotspots" },
      { text: "  → Checking dependencies for known vulnerabilities (OSV.dev)" },
      { text: "  → Checking dependency licenses (one registry lookup per pinned dependency)" },
      { text: "  → Running static analysis scanners" },
      { text: "  → Checking dependency licenses: 1/196 ... 196/196" },
      { text: "✓ Scan complete" },
      { text: "  Evidence written to .aletheore/air.json" },
    ],
  },
  {
    id: "hotspots",
    label: "query hotspots",
    copy: "aletheore query hotspots",
    lines: [
      { text: "aletheore query hotspots", kind: "cmd" },
      { text: "[" },
      { text: "  {" },
      { text: "    \"path\": \"github-app/scan_worker/jobs.py\"," },
      { text: "    \"churn_count\": 136," },
      { text: "    \"co_change_partners\": [" },
      { text: "    ..." },
    ],
  },
];
export const terminalNote =
  "Real output from scanning the Aletheore repository itself. The path is shortened and the one-per-package licence progress lines are collapsed to one; nothing else is edited.";

export const queryHead = {
  eyebrow: "Query kinds",
  title: "Ask precise questions without sending the whole repo.",
};
export const queryGroups = Object.entries(tools.queryGroups) as [string, string[]][];
export const queryKindCount = queryGroups.reduce((n, [, kinds]) => n + kinds.length, 0);

export const mcpHead = {
  eyebrow: "MCP tools",
  title: "An agent gets exact answers, not a re-read of your repo.",
  intro: "Aletheore's MCP server exposes repository evidence as structured, TOON-encoded tool results. Agents can ask for imports, owners, endpoints, symbols, semantic search, source evidence, and managed audits without pasting the whole repository into context.",
  features: [
    { name: "aletheore_overview()", body: "Languages, frameworks, monorepo structure, dependency-graph size, module and cluster counts, git age and commit cadence. The first call on any unfamiliar repo." },
    { name: "aletheore_neighborhood(target)", body: "A module's imports, dependents, and cluster in one call instead of three round-trips." },
    { name: "aletheore_ast_pattern(language, query)", body: "Structural search by shape, not words: a real tree-sitter S-expression query against every file of one language." },
    { name: "aletheore_verify_citations(report_text)", body: "Checks every file:line citation in a report against real evidence and real file line counts, the same check `aletheore verify` runs in CI." },
  ],
};
export const mcpTools = {
  version: tools.version,
  defaults: tools.default as string[],
  optional: [
    { name: "aletheore_answer", why: "Only registered when the server is started with --agent." },
    { name: "aletheore_managed_audit", why: "Requires ALETHEORE_MCP_ALLOW to include 'external'. Off by default." },
  ].filter((o) => (tools.optional as string[]).includes(o.name)),
};
export const mcpToolNote = `${mcpTools.defaults.length} tools are registered by default in aletheore ${mcpTools.version}. aletheore_answer is added when the server is started with --agent, and aletheore_managed_audit only with explicit consent to transmit evidence externally (ALETHEORE_MCP_ALLOW=...,external).`;

export const contract = {
  eyebrow: "Evidence contract",
  title: "One source of truth.",
  intro: "Everything downstream reads the same deterministic evidence. AIRview, the dashboard, query, MCP, and audits do not invent files, routes, symbols, or owners that are not present in AIR.",
  files: [
    { name: ".aletheore/air.json", body: "Canonical repository evidence: modules, symbols, dependencies, endpoints, architecture, security, ownership, and history." },
    { name: ".aletheore/air.toon", body: "TOON-encoded evidence for agent-facing and model-facing contexts where token cost matters." },
    { name: ".aletheore/history/", body: "Snapshots used for changes, drift, and continuity between scans." },
  ],
};

export const depth = {
  eyebrow: "Depth without mystery",
  title: "Technical surfaces that stay evidence-first.",
  cards: [
    { tag: "AIRview", title: "Architecture maps", body: "AI-written subsystem pages and dependency diagrams grounded in deterministic architecture clusters." },
    { tag: "MCP", title: "Agent context savings", body: "Agents request focused evidence instead of dragging entire repositories into model context." },
    { tag: "Security", title: "Risk resolution", body: "Secrets, vulnerabilities, dependencies, endpoints, and infrastructure findings trace back to source." },
    { tag: "Health", title: "Endpoint checks", body: "aletheore healthcheck runs GET-only checks against mapped API endpoints and saves the result locally." },
    { tag: "Local", title: "Privacy by default", body: "The CLI scan runs locally. Managed and hosted flows use derived evidence according to the selected workflow." },
  ],
};
