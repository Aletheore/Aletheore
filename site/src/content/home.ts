export const home = {
  evidenceStrip: {
    label: "Every alert, review, audit, and query resolves back to:",
    items: ["file", "line", "symbol", "owner", "commit", "dependency", "risk"],
  },
  surfacesIntro: {
    eyebrow: "What it covers",
    title: "One evidence layer. Four useful surfaces.",
    intro: "Run locally with the CLI, install as a GitHub App, or give coding agents a smaller evidence context through MCP.",
  },
  surfaces: [
    {
      id: "review", title: "Code Review",
      body: "Automatic PR comments and Flash reviews on every push, plus managed audits and branch-protection checks that cite the changed file and line.",
      bullets: ["Review only what changed", "Catch secrets before merge", "Keep findings source-grounded"],
    },
    {
      id: "intelligence", title: "Repository Intelligence",
      body: "Architecture clusters, dependency graphs, hotspots, owners, dead code, and AIRview (auto-generated architecture maps) from the same canonical evidence.",
      bullets: ["Understand unfamiliar repos faster", "Map systems without hand-written docs", "Give agents precise context"],
    },
    {
      id: "security", title: "Security & Dependencies",
      body: "Secrets, vulnerable packages, licenses, infrastructure, database usage, and AI usage are detected before an audit is written.",
      bullets: ["13 language ecosystems", "No fake coverage or invented signals", "Risk tied to code evidence"],
    },
    {
      id: "monitoring", title: "Production Monitoring",
      body: "Source-mapped endpoint health checks alert when a route breaks and point back to the handler that owns it.",
      bullets: ["Reachability and latency checks", "Slack and Teams alerts", "Status data grounded in source"],
    },
  ],
  why: {
    eyebrow: "Why it is different",
    title: "Most AI tools summarize. Aletheore resolves.",
    body: "Aletheore starts with deterministic repository evidence, then lets AI reason only over what the scanner can prove. The result is friendlier than raw static analysis and more trustworthy than a generic AI summary.",
  },
  docs: {
    eyebrow: "Docs",
    title: "Nobody wants to write the docs. So nobody has to.",
    body: "Every team says \"we'll document it later,\" and later never comes: the code always outruns the wiki. Aletheore AIR writes the docs instead: grounded, per-symbol descriptions generated straight from your real source, kept current automatically, with zero engineer hours spent typing a docstring nobody wanted to write. Export the whole set as a single Markdown file whenever you need it outside the dashboard.",
    honestEyebrow: "How Docs stays honest",
    honestTitle: "Generated from your code. Never invented.",
    honestBody: "Same evidence-grounded discipline as every other Aletheore surface: AI only describes what the scanner can prove exists.",
    points: [
      { title: "Written as you push", body: "Every push to an open pull request, Flash drafts a description for any public symbol that's still missing one, batched per changed file, so docs never fall behind the code." },
      { title: "Fully documented on day one", body: "The moment you connect a repo, AIR runs a full first pass across everything already public, so you're not starting from a blank docs page." },
      { title: "Marked, never mistaken for yours", body: "Every AI-touched description is grounded in the real source snippet and carries an explicit AI-generated or AI-polished marker, never silently presented as something a developer wrote." },
      { title: "Self-healing by design", body: "Progress is saved symbol by symbol, so one failing file never costs the ones that already succeeded. A 48-hour sweep automatically finishes any repo with real commit activity since its last pass." },
    ],
  },
  teams: {
    eyebrow: "How teams use it",
    title: "Start local. Then let the GitHub App keep watch.",
    steps: [
      { k: "$ aletheore scan", title: "Build the evidence", body: "Tree-sitter and git history produce air.json and air.toon. No LLM in the scan path." },
      { k: "$ aletheore query", title: "Ask grounded questions", body: "Query architecture, owners, dependencies, endpoints, secrets, hotspots, and semantic search from one evidence file." },
      { k: "on: pull_request", title: "Review every change", body: "The GitHub App comments on PRs, gates new secrets, and can run managed audits or Flash reviews on paid plans." },
      { k: "GET /health", title: "Monitor what shipped", body: "Live endpoint checks alert on outages and latency regressions with source handler context." },
    ],
    primary: { label: "Install the GitHub App", href: "https://github.com/apps/aletheore/installations/new" },
    secondary: { label: "See Pricing", href: "/pricing" },
    note: "Installable today on any GitHub account or organization you administer.",
  },
  proof: {
    eyebrow: "Proof",
    title: "Scanned for real. Not a mockup.",
    intro: "Three real public repositories, actually scanned by aletheore scan at pinned commits. The numbers below are read directly from that output.",
    writeup: { label: "Read the full writeup, findings and all", href: "/dogfooding" },
    benchmarks: { label: "See the full benchmarks", href: "/benchmarks" },
    repos: [
      { name: "Django", meta: "Python, full git history", commit: "3d34265", stats: [["modules", "3,038"], ["dependency edges", "9,479"], ["clusters", "896"], ["commits scanned", "34,793"], ["license findings", "0"], ["vulnerability findings", "1"]] },
      { name: "Express", meta: "JavaScript, real npm dependencies", commit: "ae6dd37", stats: [["modules", "141"], ["dependency edges", "0"], ["clusters", "141"], ["commits scanned", "6,157"], ["license findings", "0"], ["vulnerability findings", "0"]] },
      { name: "Kubernetes", meta: "Go, the stress test", commit: "bd1a1b8", stats: [["modules", "17,499"], ["dependency edges", "59,174"], ["clusters", "12,641"], ["license findings", "40"], ["vulnerability findings", "9"], ["seconds to scan", "368"]] },
    ],
    linux: {
      title: "The Linux kernel.",
      eyebrow: "Real stress test",
      body: "Not a curated sample: the actual torvalds/linux repository, cloned in full and scanned end to end: 94,849 files, 8.2GB, 1.46 million commits.",
      stats: [
        { label: "Files parsed", value: "64,634" }, { label: "C files", value: "36,851" }, { label: "C++ / header files", value: "26,887" },
        { label: "Dependency edges", value: "71,929" }, { label: "Architecture clusters", value: "29,428" }, { label: "Commits analyzed", value: "1,463,552" },
        { label: "License issues found", value: "0" }, { label: "Known vulnerabilities", value: "0" }, { label: "Scan time", value: "4m 20s" },
      ],
    },
    toon: {
      title: "The same data, TOON-encoded.",
      body: "Measured against Aletheore's own air.json / air.toon pair from a real self-scan, the exact data the MCP server and audit coding-agent adapter read.",
      stats: [{ value: "45.5%", label: "fewer tokens" }, { value: "55.5%", label: "smaller on disk" }],
    },
  },
  cta: {
    title: "Humanist tools for a technical world.",
    body: "Nothing leaves your machine when you run the deterministic local scan. Bring your own API key for AI-assisted audit reports, or skip them entirely: the evidence pipeline stands on its own.",
    points: [
      "Source-available and local-first.",
      "Query, diff, dashboard, MCP, and reports all read the same evidence.",
      "Hosted paid-plan features use derived evidence, not raw source storage.",
    ],
    terminal: [
      "$ aletheore scan .",
      "Scanning /Users/you/your-repo...",
      "  Detecting languages, frameworks, and build tools",
      "  Building module dependency graph",
      "  Checking dependency vulnerabilities",
      "Evidence written to .aletheore/air.json",
      "Snapshot saved to .aletheore/history/",
    ],
  },
} as const;
