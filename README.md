<p align="center">
  <img src="assets/logo.png" alt="Aletheore" width="360">
</p>

# Aletheore

Aletheore is an evidence-grounded repository audit tool. A deterministic scanner (no LLM,
fully unit-tested) reads a repo and writes `air.json` — languages, dependency graph,
module clusters, git activity and ownership, secrets, dependency vulnerabilities, layer
violations. Everything downstream — the written report, the query tools, the MCP server, the
local dashboard — reads from that same evidence and never states a claim it can't point back
to a specific field in it.

**Working code lives in [`src/`](src/) — start there:** [`src/README.md`](src/README.md)
has full setup, every CLI command, the MCP tool list, and the dashboard.

## What's actually shipped

- **`aletheore scan`** — run the deterministic scanner, write `.aletheore/air.json`, save a
  history snapshot. No LLM call, safe to run in CI.
- **`aletheore audit`** — scan, then shell out to an installed coding-agent CLI (Claude Code
  today) to write a full grounded markdown report, citing exact evidence fields. Meant to be
  run by hand against your own repo, not from automation — see
  [`src/README.md`](src/README.md) for why.
- **`aletheore query`** / **`aletheore diff`** — answer targeted questions or compare two scans
  from existing evidence, no re-scan or LLM call needed.
- **`aletheore mcp`** — a stdio MCP server exposing 28 tools (module/symbol/dependency
  lookups, ownership, clusters, dead code, hotspots, full-text and semantic search, scan and
  index triggers) so a coding agent can query a repo's structure directly instead of shelling
  out or re-reading files. See [`src/README.md`](src/README.md) for the full tool list.
- **`aletheore dashboard`** — a live local web UI: dependency graph, an Obsidian-style cluster
  graph, trend charts, MCP tool list.
- **A GitHub Action** (`action.yml`) — scans a PR's base and head refs and posts a diff (new
  secrets, layer violations, dependency vulnerabilities) — CI only ever runs `scan` + `diff`,
  never the full agent-driven `audit`.

## Repository layout

- `src/` — the actual, working code (see its README for everything above in detail).
- `github-app/` — the hosted GitHub App: FastAPI server, RQ workers, migrations.
- `website/` — the marketing site and live demo.
- `docs/superpowers/` — design specs and implementation plans written during development.
- `docs/operations/` — current operational baselines: incident response, data handling, SLOs,
  deployment verification, branch protection, support process.
- `SECURITY.md` — vulnerability reporting and response targets.

Aletheore is free and open source. If it's useful to you, consider
[sponsoring development](https://github.com/sponsors/ArihantK15) — no accounts, no tracking,
nothing leaves your machine when you run it.
