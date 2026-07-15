# Veridion Local Dashboard Design

**Status:** Draft, pending review
**Date:** 2026-07-15

## Problem

Split out from direction 3 (persistent queryability) of the four-direction differentiation
brainstorm — the MCP server (agent-facing) and this dashboard (human-facing) both close the
same underlying gap: `evidence.json` and `.veridion/history/` already contain rich data about a
repo, but today the only way to see it is a one-shot markdown report or raw JSON via the CLI.
The user's own framing: "a user-facing page on localhost so they can see their own stats, and
also live update it with each new scan."

## Goals

- `veridion dashboard <path>` starts a local, read-only web dashboard for exactly one repo,
  showing repo overview, git activity, security, and architecture stats, plus an interactive
  visual dependency graph and trend sparklines over recent scan history.
- Genuine live updates via Server-Sent Events — the page reflects a new scan (triggered by
  `veridion scan`, `veridion_scan` via MCP, or `veridion audit`) without a manual reload.
- Zero new dependencies — `starlette`, `uvicorn`, and `sse-starlette` are already transitively
  available via the `mcp` dependency (confirmed installed and importable locally: starlette
  0.52.1, uvicorn 0.49.0, sse-starlette present).
- A reference panel showing the MCP server's actual registered tools (direction 3), introspected
  live from a running `build_server(repo_path)` rather than a hand-maintained duplicate list
  that could drift out of sync.

## Non-Goals

- No write operations, no scan-triggering button in the UI — strictly read-only, reflecting
  scans triggered elsewhere (CLI or MCP). Adding a "scan now" button would blur into
  CLI/MCP-tool territory and isn't what was asked for.
- No multi-repo dashboard — single repo per running instance, fixed at startup, matching every
  other piece of Veridion's design (`.veridion.json`, `evidence.json`, `.veridion/history/`,
  the MCP server).
- No remote/network exposure — binds to `127.0.0.1` only, never `0.0.0.0`, matching Veridion's
  stated "100% local, nothing leaves this machine" identity (already stated in the CLI's own
  `SPONSOR_NOTE`). No authentication is needed or added, since the local-only bind *is* the
  security boundary.
- No third-party JS dependency, vendored or CDN-loaded — the graph visualization is a hand-rolled
  minimal force-directed layout in vanilla JS/SVG. A local tool phoning out to a CDN at
  dashboard-load time would itself violate the "nothing leaves this machine" property, even for
  something as innocuous as a graph-rendering library.
- No new `evidence.json` fields, no changes to any existing scanner/query/history code — this
  is purely a new read-only view over data that already exists.

## Architecture

New `prototype/veridion/dashboard.py` builds a Starlette `app` bound to one fixed `repo_path`
(same closure-over-a-fixed-path pattern as the MCP server's `build_server`). New
`veridion dashboard <path> [--port N]` CLI command resolves the repo path, constructs the app,
and runs it via `uvicorn.run(app, host="127.0.0.1", port=port)` — default port `8420` (chosen
to avoid the much more commonly-already-in-use 3000/5000/8000/8080), then calls
`webbrowser.open(f"http://127.0.0.1:{port}")` (stdlib, no new dependency) before/after starting
the server, per your confirmed preference for auto-open.

## Backend Endpoints

- **`GET /`** — the single HTML page: inline CSS/JS, no build step, no frontend framework.
- **`GET /api/evidence`** — a dashboard-tailored summary (its own function, deliberately not
  the same shape as `veridion_scan`'s MCP summary, since the dashboard's stat cards need more
  fields — git ownership, branch staleness, monorepo status — than that token-optimized summary
  carries):
  ```json
  {
    "scanned_at": "2026-07-15T12:00:00+00:00",
    "repo_overview": {
      "languages": [{"name": "python", "file_count": 180}],
      "module_count": 214,
      "monorepo": {"detected": false, "workspaces": []}
    },
    "git_activity": {
      "total_commits": 1703,
      "commit_cadence": {"weekly_counts": [...], "trend": "steady"},
      "ownership": [{"path": "a.py", "top_author": "alice"}],
      "branches": [{"name": "main", "ahead_of_main": 0}]
    },
    "security": {
      "secrets": {"total_findings": 3, "real_findings": 0, "history_findings": 3},
      "vulnerabilities": {"checked": true, "finding_count": 0}
    },
    "architecture": {
      "cluster_count": 12,
      "convention_detected": true,
      "violation_count": 2
    }
  }
  ```
  `git.branches`/`commit_cadence` field names confirmed against `git_intel/analyzer.py`'s
  `analyze_git` (`branches` entries carry `ahead_of_main`, `commit_cadence` carries
  `weekly_counts`/`trend`) — not guessed.
- **`GET /api/history`** — trend data for sparklines, one entry per retained snapshot (up to
  the existing 20-snapshot cap from direction 1 — no separate limit parameter needed, the
  history mechanism's own retention already bounds this):
  ```json
  [
    {"scanned_at": "...", "module_count": 210, "secrets_findings": 2, "vulnerability_findings": 1},
    {"scanned_at": "...", "module_count": 214, "secrets_findings": 3, "vulnerability_findings": 0}
  ]
  ```
- **`GET /api/graph`** — `{"nodes": [...], "edges": [...], "clusters": [...]}` taken directly
  from `repository.dependency_graph` and `architecture.clusters`, reshaped for the frontend
  renderer (each node annotated with its cluster id for coloring).
- **`GET /api/mcp-tools`** — calls `build_server(repo_path)` (from `veridion.mcp_server`,
  direction 3) and its `list_tools()`, returning `[{"name": ..., "description": ...}, ...]` for
  all 13 tools — always reflects whatever the MCP server actually exposes, no duplicate list to
  keep in sync.
- **`GET /events`** — SSE endpoint via `sse-starlette`'s `EventSourceResponse`. A server-side
  async loop checks `.veridion/evidence.json`'s mtime every 1.5 seconds; when it changes from
  the last-seen value, emits an `event: refresh` with a minimal payload (`{"scanned_at":
  "..."}`) and updates the last-seen mtime. The event carries no data payload beyond the
  timestamp — deliberately decoupled from the actual data-fetching, so the frontend reacts by
  re-calling the same `/api/*` endpoints used for the initial page load, not a second parallel
  data path.

## Frontend

Single HTML page, vanilla JS, no framework, no build step:
- Stat cards for repo overview, git activity, security, architecture (rendered from
  `/api/evidence`).
- Trend sparklines (module count, secrets findings, vulnerability findings) from `/api/history`.
- Interactive dependency graph: hand-rolled force-directed layout (repulsion between all node
  pairs, attraction along edges, iterated a fixed number of steps client-side), rendered as SVG
  circles + lines, nodes colored by cluster, from `/api/graph`.
- MCP tools reference panel, listing name + description for each of the 13 tools, from
  `/api/mcp-tools`.
- An `EventSource` connected to `/events`; on `refresh`, re-fetches `/api/evidence`,
  `/api/history`, and `/api/graph` (not `/api/mcp-tools`, which doesn't change between scans)
  and updates the DOM in place.

## Reproducibility

The dashboard reads the same `evidence.json`/`.veridion/history/*.json` files everything else
in Veridion reads — it introduces no new persisted state, and computing any of its summary
shapes twice from the same evidence produces identical output (same discipline as `compute_diff`
and the MCP server's summary functions).

## Testing Strategy

Unit tests for the dashboard-summary-building functions (`/api/evidence`'s shape,
`/api/history`'s shape) calling them directly as plain Python functions, not through HTTP.
Starlette's own `TestClient` for endpoint-level tests (`GET /api/evidence` returns the right
shape and status code, `GET /api/graph` reflects a real dependency graph, `GET /api/mcp-tools`
returns all 13 tool names). The SSE endpoint and the hand-rolled JS graph renderer need live
manual verification (starting the real server, watching `/events` fire after a real scan,
visually confirming the graph renders) rather than automated tests — consistent with how the
MCP server's stdio wiring and the GitHub Action's live behavior were both verified manually in
this session, not unit-tested.

## Success Criteria

1. `veridion dashboard <path>` starts, binds to `127.0.0.1` only (confirmed by attempting to
   connect from a non-loopback interface and having it fail), and opens the browser
   automatically to a page showing real stats for a real repo (Procta).
2. Running a real `veridion scan` against that same repo in another terminal while the
   dashboard is open causes the page to update within a few seconds, with no manual reload —
   the actual live-update behavior the user asked for, verified live.
3. The dependency graph renders as a real interactive visual (not just a node/edge count),
   correctly grouping modules by cluster, for a repo with a non-trivial module count.
4. `/api/mcp-tools` returns exactly the 13 tools the MCP server (direction 3) actually
   registers — verified by comparing its output against a direct `list_tools()` call against
   the same repo.
