# Veridion MCP Server Design

**Status:** Draft, pending review
**Date:** 2026-07-15

## Problem

Direction 3 of the four-direction differentiation brainstorm (against CodeRabbit, Dependabot,
RepoWise, Obsidian — directions 1 and 2, continuity/history and CI/PR integration, are both
shipped and merged to master; direction 4, diagnose-vs-act, is deliberately saved for after
this one). RepoWise — a codebase documentation MCP server installed in this very environment —
lets an agent query a codebase's structure directly as tools, without re-reading files or
shelling out to a CLI. Veridion already computes nearly everything needed for this
(`evidence.json`'s module graph, clusters, ownership, findings) but only exposes it through a
CLI (`veridion query`) or a one-shot report — an agent working inside a coding session has to
either shell out via Bash on every lookup, or re-derive structural facts by reading files
directly, both of which cost far more tokens than a direct tool call would.

## Goals

- Expose Veridion's existing 9 deterministic query functions (`imports`, `imported-by`,
  `symbols`, `branch`, `ownership`, `secrets`, `vulnerabilities`, `cluster`,
  `layer-violations`) plus `changes` (from direction 1) as MCP tools, callable directly by an
  agent inside a coding session.
- Add one new composite tool, `veridion_neighborhood`, returning a module's imports +
  dependents + cluster in a single call — the actual "graph traversal without N round-trips"
  capability that motivated this direction.
- Add one new capability that doesn't exist anywhere in Veridion today: deterministic,
  regex-or-literal full-text search over tracked source files (`veridion_search`) — Veridion's
  existing query functions only ever read structured `evidence.json` fields, never file
  contents.
- Add a `veridion_scan` tool so an agent can trigger a fresh scan from within the same MCP
  session, without a separate CLI round-trip — returning a compact summary, not the full
  evidence dump, since dumping the whole scan result back through a tool response would defeat
  the point of a token-efficient tool surface.
- A new `veridion mcp <path>` CLI command starts a stdio MCP server scoped to that one repo.

## Non-Goals

- No `get_why`/`get_risk`-style reasoning or narrative-explanation tools. These require either
  Veridion making its own LLM calls internally (a genuinely different, much bigger
  architectural step — real API costs, a new reasoning surface inside what has always been a
  deterministic scanner) or are simply redundant: when Veridion's tools run inside an
  MCP-capable coding agent, that agent already *is* the reasoning layer and can answer "why"
  itself once handed the deterministic facts. This isn't a lesser version of RepoWise's
  capability — it's recognizing that capability doesn't need to be re-built inside Veridion at
  all in this context.
- No semantic or embedding-based search — `veridion_search` is literal/regex text matching
  only, staying fully deterministic like everything else in the scanner.
- No multi-repo server (one server instance handles exactly one repo, fixed at startup) — every
  other piece of Veridion's design (`.veridion.json`, `evidence.json`, `.veridion/history/`) is
  already single-repo-scoped; introducing multi-repo indexing now isn't justified by any stated
  need.
- No remote transport (HTTP/SSE) — stdio only, matching how virtually every local dev-tool MCP
  server (including RepoWise itself) actually runs, launched as a subprocess by the client's own
  config.
- No new `evidence.json` schema — `veridion_scan`'s summary is computed from the existing
  scan result at call time, not persisted as a new field.
- The local web dashboard (live-updating stats page) raised alongside this idea is explicitly
  out of scope here — a genuinely separate subsystem (HTTP server, browser UI, a live-update
  mechanism, none of which exist in this codebase), to be brainstormed as its own next item
  immediately after this one ships.

## Architecture

New module `prototype/veridion/mcp_server.py`, using the official `mcp` Python SDK's
`FastMCP` (confirmed installed locally at v1.23.3, latest on PyPI 1.28.1) — decorator-based
tool registration (`@mcp.tool()`), served via `.run(transport="stdio")`. This is a genuinely
necessary new dependency, not a convenience addition: hand-rolling MCP's JSON-RPC framing
would be unreasonable when the official SDK exists, matching how `tree-sitter`, `networkx`,
and `certifi` were each justified earlier this project.

`veridion mcp <path>` (new CLI subcommand) resolves `<path>`, constructs a `FastMCP` instance,
registers all 13 tools bound to that repo path via closures (each tool function reads
`.veridion/evidence.json` fresh from that fixed path on every call — no in-memory caching
across calls, since evidence.json can change between calls if `veridion_scan` or an external
`veridion scan` run updates it, and staleness would silently give wrong answers), and runs the
server. Tool functions are thin wrappers: existing tools call straight into the existing
`QUERY_FUNCTIONS`/`compute_diff` logic (zero duplicated logic), new tools (`neighborhood`,
`search`, `scan`) get their own small implementations.

## Tool Specifications

**Existing query wrappers** (10 tools — 9 `QUERY_FUNCTIONS` entries + `changes`): identical
semantics to the CLI's `veridion query <kind> <target>`, reading `.veridion/evidence.json`
fresh from the fixed repo path each call. Missing-evidence and target-not-found cases raise a
clean tool error (via `ModuleNotFoundInEvidenceError`/`BranchNotFoundInEvidenceError`, already
defined in `query.py`) rather than an uncaught traceback — FastMCP surfaces raised exceptions
as proper MCP tool-call errors.

**`veridion_neighborhood(target: str) -> dict`** (new):
```json
{
  "target": "app/auth.py",
  "imports": ["app/config.py", "app/db.py"],
  "imported_by": ["app/routes.py"],
  "cluster": {"id": 2, "modules": ["app/auth.py", "app/routes.py"]}
}
```
`cluster` is `null` if the target isn't assigned to any cluster. Raises
`ModuleNotFoundInEvidenceError` if `target` isn't in `repository.modules` at all (checked
first, before attempting the three lookups).

**`veridion_search(pattern: str, regex: bool = False, path_glob: str | None = None) -> list[dict]`**
(new): walks the repo excluding `IGNORED_DIRS` (imported from `veridion.scanner.detect`, the
same constant already shared by `secrets.py` and `graph.py` — no new exclusion list), skipping
files that aren't decodable as UTF-8 text (same "skip, don't crash" handling `secrets.py`
already uses for binary files). If `path_glob` is given, only files whose relative path matches
it (via `pathlib.PurePath.match`) are searched. Matching is literal substring by default,
`re.search` when `regex=True`. Returns matches as
`[{"path": str, "line": int, "text": str}, ...]`, capped at 200 matches (a `truncated: true`
flag added to the response if the cap was hit) — an unbounded result would itself defeat the
token-saving goal of this whole feature.

**`veridion_scan() -> dict`** (new): calls the existing `scan_repository` + `write_evidence` +
`save_snapshot` (reusing direction 1's history mechanism, so a scan triggered via MCP
participates in the same rolling history as a CLI-triggered scan), then returns a compact
summary — not the full evidence:
```json
{
  "scanned_at": "2026-07-15T12:00:00+00:00",
  "module_count": 214,
  "languages": [{"name": "python", "file_count": 180}, ...],
  "secrets": {"total_findings": 3, "real_findings": 0, "history_findings": 3},
  "vulnerabilities": {"checked": true, "finding_count": 0},
  "layer_violations": {"convention_detected": true, "violation_count": 2},
  "cluster_count": 12
}
```
`real_findings` is the count of `security.secrets.findings` where `likely_placeholder` is
`false` — the same distinction `--fail-on-new-secrets` already uses.

## Reproducibility

The 10 existing-query wrappers and `veridion_neighborhood` inherit the underlying functions'
existing purity (same evidence.json in, same answer out). `veridion_search` is deterministic
given the same repo file contents. `veridion_scan` inherits `scan_repository`'s existing
reproducibility guarantees — this tool doesn't introduce any new non-determinism, it just makes
an already-reproducible operation callable from within an MCP session.

## Testing Strategy

Unit tests for the new logic (`neighborhood`, `search`, `scan`'s summary-building) calling the
underlying Python functions directly, bypassing the MCP protocol layer entirely — these are
plain functions under the hood, not meaningfully different to test than any other module. The
10 wrapped tools need no new logic tests (already covered by `query.py`'s and `history.py`'s
existing test suites) but do need a thin registration test confirming all 13 tools are actually
registered on the `FastMCP` instance with the right names. One live end-to-end smoke test using
the `mcp` SDK's own client library to actually connect over stdio to a running
`veridion mcp <path>` process and call one real tool — proving the protocol wiring itself
works, not just the underlying functions.

## Success Criteria

1. All 13 tools are registered and individually callable via a real stdio MCP client
   connection against a real repo (Procta), each returning correct results matching the
   equivalent CLI command's output.
2. `veridion_search` finds a known string in Procta's codebase with correct file/line results,
   respects `IGNORED_DIRS`, and correctly caps + flags truncation on a deliberately
   high-frequency search term.
3. `veridion_scan` triggered via MCP produces a new snapshot in `.veridion/history/` (visible
   to a subsequent `veridion query changes` CLI call) — proving the MCP-triggered scan
   participates in the same history mechanism as a CLI-triggered one, not a separate one.
4. `veridion_neighborhood` on a real module returns imports/imported_by/cluster matching what
   three separate existing tool calls (`imports`, `imported-by`, `cluster`) would return
   individually.
