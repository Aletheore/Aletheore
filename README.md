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

**Working code lives in [`prototype/`](prototype/) — start there:** [`prototype/README.md`](prototype/README.md)
has full setup, every CLI command, the MCP tool list, and the dashboard.

## What's actually shipped

- **`aletheore scan`** — run the deterministic scanner, write `.aletheore/air.json`, save a
  history snapshot. No LLM call, safe to run in CI.
- **`aletheore audit`** — scan, then shell out to an installed coding-agent CLI (Claude Code
  today) to write a full grounded markdown report, citing exact evidence fields. Meant to be
  run by hand against your own repo, not from automation — see
  [`prototype/README.md`](prototype/README.md) for why.
- **`aletheore query`** / **`aletheore diff`** — answer targeted questions or compare two scans
  from existing evidence, no re-scan or LLM call needed.
- **`aletheore mcp`** — a stdio MCP server exposing 13 tools (module lookups, ownership,
  clusters, full-text search, a compact scan trigger) so a coding agent can query a repo's
  structure directly instead of shelling out or re-reading files.
- **`aletheore dashboard`** — a live local web UI: dependency graph, an Obsidian-style cluster
  graph, trend charts, MCP tool list.
- **A GitHub Action** (`action.yml`) — scans a PR's base and head refs and posts a diff (new
  secrets, layer violations, dependency vulnerabilities) — CI only ever runs `scan` + `diff`,
  never the full agent-driven `audit`.

## Repository layout

- `prototype/` — the actual, working code (see its README for everything above in detail).
- `docs/superpowers/` — design specs and implementation plans written during development.
- `constitution/`, `GOVERNANCE.md`, `VISION.md`, `ROADMAP.md`, `CONTRIBUTING.md` — an earlier,
  more elaborate governance/specification framework scaffolded before any real code existed.
  `prototype/` is explicitly a bypass of that process (see `prototype/README.md`'s "Status"
  line) rather than a product of it — treat those files as historical, not as documentation of
  what's actually built.

Aletheore is free and open source. If it's useful to you, consider
[sponsoring development](https://github.com/sponsors/ArihantK15) — no accounts, no tracking,
nothing leaves your machine when you run it.
