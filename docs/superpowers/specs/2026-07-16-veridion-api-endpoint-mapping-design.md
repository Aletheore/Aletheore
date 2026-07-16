# Veridion API Endpoint Mapping + Live Health Check Design

**Status:** Draft, pending review
**Date:** 2026-07-16

## Problem

Veridion already extracts a deterministic module/import graph, dependency vulnerabilities, and
dependency licenses from a scanned repo — but it has no notion of the repo's own API surface.
For a repo built with Flask/FastAPI/Django/Express (all frameworks Veridion already detects via
`detect_frameworks`), "what HTTP endpoints does this thing expose, and did any get added or
removed between two commits" is exactly the kind of deterministic, citable fact the rest of
`evidence.json` already captures for imports, licenses, and vulnerabilities — and it doesn't
exist today.

Separately, once endpoints are known statically, there's a natural adjacent capability that is
new in kind for Veridion: actually calling those endpoints against a running instance to check
liveness. Every other piece of Veridion evidence is 100% static (parsed from files) or read-only
metadata lookups against third-party registries (PyPI, npm, OSV.dev) — never a network call to
the thing being audited. This is scoped as an explicit second phase, kept out of the
static/deterministic evidence model entirely.

## Goals

**Phase 1 — static mapping:**
- Detect API route definitions in Flask, FastAPI, Django, and Express code (the four
  route-hosting frameworks among the ones `detect_frameworks` already recognizes — `react`,
  `vue`, and `next` are frontend and out of scope).
- Surface the result as a new `repository.api_endpoints` evidence block.
- Wire it through every existing surface the same way `licenses` was: `veridion query
  endpoints`, a `veridion_endpoints` MCP tool, a `--no-map-endpoints` CLI flag.
- Track endpoints added/removed between two scans via `veridion diff`, the same way secrets and
  vulnerabilities already are — this is what makes the feature useful for debugging ("this PR
  removed a route the frontend still calls"), not just descriptive.

**Phase 2 — live health check:**
- Given a base URL for a running instance of the scanned app, send a **GET-only** request to
  each statically-mapped endpoint and report reachability, status code, and latency.
- Expose it as its own CLI command (`veridion healthcheck`) and its own MCP tool
  (`veridion_healthcheck`), so an agent debugging a live app can trigger a check and get
  structured results back directly, without hand-writing curl commands.
- Optionally persist each run under `.veridion/healthchecks/`, mirroring the existing
  `.veridion/history/` snapshot pattern, so health is trackable over time, not just a one-shot
  check.

## Non-Goals

- Any HTTP method other than GET is ever sent live. Phase 2 never calls POST/PUT/DELETE/PATCH,
  even for endpoints the static map identifies as such — they appear in the health-check report
  marked `skipped`, never invoked. No flag in this design changes that.
- Authentication handling. An endpoint that requires auth simply reports its real status (401/
  403) in Phase 2 — that's itself useful signal, not a gap to fill.
- Frameworks beyond Flask/FastAPI/Django/Express (e.g. Rails, Spring, ASP.NET, Gin) in this
  phase. Veridion's language support is broader than its framework-route support; extending
  route mapping to more frameworks is a natural, separately-scoped follow-up once this pattern
  is proven, not part of this spec.
- Resolving nested routing indirection: Django's `include()` and FastAPI's
  `app.include_router(router, prefix=...)` / `APIRouter(prefix=...)`. Real Django projects
  almost always split `urls.py` per app via `include()`, and real FastAPI projects commonly
  compose routers with prefixes — fully resolving either means tracing calls across files,
  which is a meaningfully bigger problem than pattern-matching a decorator or a single
  `urlpatterns` list. v1 records what it can see directly (the path segment literally written
  on the decorator or `path()` call) and does **not** claim to reconstruct the final mounted
  URL when prefixes are added via `include()`/`include_router()`/router-level `prefix=`. This is
  called out explicitly here, and again in the Phase 1 section below, so it's a known, visible
  limitation rather than a silent correctness gap discovered later.
- Folding Phase 2 into `evidence.json`'s reproducibility model. Every other field in evidence is
  a deterministic function of repo content — same repo in, same evidence out. A live health
  check depends on runtime state outside the repo (is a server even running, what does it
  return right now), so it is deliberately never part of `scan`/`audit`, never merged into
  `evidence.json`, and never diffed by `veridion diff`. It gets its own command and its own
  storage location precisely so this exception stays visible and contained, not smuggled into
  the property the rest of the tool depends on.
- A dashboard UI card for either phase. Deferred, same call made for `licenses` — the data is
  reachable via `/api/evidence` (Phase 1) the moment it exists; a UI treatment can follow later
  if wanted.

## Phase 1: Static Endpoint Mapping

### New module: `veridion/endpoints.py`

`map_api_endpoints(repo_path: Path) -> dict` walks the same tree-sitter parse trees the module
graph already builds for Python/JavaScript/TypeScript files (reusing `LANGUAGE_BY_EXTENSION`
from `scanner/graph.py` rather than re-detecting languages), looking for framework-specific
route patterns:

- **Flask**: decorator calls `@app.route(path, methods=[...])` and `@app.get/post/put/delete/
  patch(path)`, including on `Blueprint` instances (`@bp.route(...)`).
- **FastAPI**: decorator calls `@app.get/post/put/delete/patch(path)` and the same on
  `APIRouter()` instances. If the router was constructed with `APIRouter(prefix=...)`, that
  prefix is recorded on the entry as `router_prefix` (informational) but is not merged into
  `path` — and a mount-time prefix from `app.include_router(router, prefix=...)` isn't tracked
  at all (see Non-Goals).
- **Django**: calls to `path(route, view)` / `re_path(route, view)` found inside a module-level
  `urlpatterns` list, conventionally in a file named `urls.py`. An `include(...)` entry in
  `urlpatterns` is recorded as its own entry with `"framework": "django", "handler": "include(...)",
  "path": "<the literal prefix string>"` rather than being traced into the included module (see
  Non-Goals) — so nested app URLs won't appear as fully-resolved paths in v1.
- **Express**: calls `app.get/post/put/delete/patch(path, handler)` and the same on `Router()`
  instances (`router.get(...)`). A mounted router (`app.use('/prefix', router)`) is not traced
  into — the same limitation as Django's `include()`.

Each match produces one entry:

```python
{
    "method": "GET",
    "path": "/users/<int:id>",
    "framework": "flask",
    "file": "app/routes.py",
    "line": 12,
    "handler": "get_user",
}
```

`path` is kept in the framework's own placeholder syntax (`<int:id>`, `{id}`, `:id`) rather than
normalized to one convention — normalizing would lose information (e.g. Flask's type converter)
for no benefit, since nothing downstream needs cross-framework path comparison within a single
repo scan.

Return shape:

```python
{
    "checked": True,
    "endpoints": [...],  # possibly empty
}
```

(A `checked: False` / empty-endpoints shape is used when `map_endpoints=False` is passed, same
convention as `dependency_licenses`/`dependency_vulnerabilities`.)

### Wiring (mirrors the `licenses` feature exactly)

- `evidence.py`: `scan_repository` gains `map_endpoints: bool = True`; on success, `evidence["
  repository"]["api_endpoints"] = map_api_endpoints(repo_path)`; on skip, the `checked: False`
  shape.
- `cli.py`: `--no-map-endpoints` flag on both `scan` and `audit` subcommands, threaded through
  `_scan`/`_audit` the same way `--no-check-licenses` is.
- `query.py`: `find_endpoints(evidence, target) -> evidence["repository"]["api_endpoints"]`
  (whole block, `requires_target=False` — there's no natural single-target lookup here, same as
  `vulnerabilities`/`licenses`).
- `mcp_server.py`: `_TOOL_NAME_TO_QUERY_KIND["veridion_endpoints"] = "endpoints"` (15 tools,
  up from 14).

### Diff support (`history.py`)

`_compute_curated_diff` gains one more `_new_and_resolved` call, keyed on `("method", "path")`:

```python
new_endpoints, resolved_endpoints = _new_and_resolved(
    old["repository"]["api_endpoints"]["endpoints"],
    new["repository"]["api_endpoints"]["endpoints"],
    ("method", "path"),
)
result["endpoints"] = {"new": new_endpoints, "resolved": resolved_endpoints}
```

A `checked` state-change caveat (same pattern as the existing vulnerability/history-scan
caveats) is added for when `map_endpoints` was toggled between the two scans being diffed, so a
disappearing endpoint isn't misread as a real removal when it was actually just unmapped.

## Phase 2: Live Health Check

### New module: `veridion/healthcheck.py`

`run_healthcheck(endpoints: list[dict], base_url: str, timeout: float = 5.0) -> dict`:

- For each endpoint with `method == "GET"`: substitute any path placeholder (Flask
  `<converter:name>` or `<name>`, FastAPI `{name}`, Express `:name`) with the literal string
  `"1"`, join onto `base_url`, send a real GET request, and record `status_code`, `latency_ms`,
  and `reachable: bool` (network-level failure vs. a real HTTP response, even a 4xx/5xx one,
  which still counts as reachable).
- If any placeholder substitution happened, the entry carries `"note": "path contains
  parameters, tested with placeholder value(s)"` so a legitimate 404 (real ID doesn't exist)
  isn't misread as a broken route.
- For every endpoint with a non-GET method: no request is sent; the entry is included with
  `"skipped": True, "reason": "only GET is health-checked"` — kept visible in the report rather
  than silently dropped, since the point of health-checking is a complete picture of the
  mapped surface.

Return shape:

```python
{
    "base_url": "http://localhost:8000",
    "checked_at": "2026-07-16T12:00:00+00:00",
    "results": [
        {"method": "GET", "path": "/users", "status_code": 200, "latency_ms": 12.3,
         "reachable": True, "note": None},
        {"method": "GET", "path": "/users/<int:id>", "status_code": 404, "latency_ms": 8.1,
         "reachable": True, "note": "path contains parameters, tested with placeholder value(s)"},
        {"method": "POST", "path": "/users", "skipped": True,
         "reason": "only GET is health-checked"},
    ],
}
```

### CLI: `veridion healthcheck <repo_path> --base-url <url>`

Reads `.veridion/evidence.json` for `repository.api_endpoints.endpoints` (errors clearly,
suggesting `veridion scan` first, if evidence doesn't exist or has no endpoint data — same error
convention `query` already uses). Runs `run_healthcheck`, prints a simple table to stdout
(method, path, status, latency, note), exit code 0 regardless of individual endpoint results
(this is a report, not a gate — nothing about "is my app healthy" should silently fail a CI
step; a `--fail-on-unreachable` flag is left as an explicit future addition if ever wanted, not
built speculatively here).

### MCP tool: `veridion_healthcheck`

Takes `repo_path` and `base_url` params, calls the same code path as the CLI command, returns
the JSON result directly. This is the piece that answers the "very easy calling of endpoints,
easy tracking for debugging" framing this feature started from: an agent (or you, through an
agent) can trigger a live check of a running dev server or staging instance mid-debugging
session with one tool call, instead of composing curl commands by hand.

### Optional persistence: `.veridion/healthchecks/<timestamp>.json`

Every `healthcheck` run (CLI or MCP) writes its result JSON to this directory, mirroring
`history.py`'s `.veridion/history/` snapshot pattern (same rotation-by-count approach, reusing
`_rotate` rather than duplicating it). No new query/diff wiring for this in Phase 2 — it exists
so "was this endpoint also down yesterday" is answerable by reading the directory, not so it
needs its own subcommand yet. If trend analysis is wanted later, it can read this directory the
same way `dashboard.py`'s `build_history_summary` reads scan history.

## Testing Strategy

Consistent with how every language/feature addition so far in this project has been verified —
real running code, not hand-written fixtures alone:

**Phase 1:** one small real Flask app, one real FastAPI app, one real Django project (with a
real `urls.py`), and one real Express app are created as test fixtures and their route
decorators are parsed for real (not typed-out synthetic ASTs). Fixture apps use direct, flat routing (no `include()`, no mounted/prefixed routers) so the
"matches the repo's actual routes exactly" success criterion is achievable and honestly tested;
one additional test per framework confirms the unresolved case (an `include()` call, a
prefixed `APIRouter`, a mounted Express router) is recorded as its raw/unresolved form rather
than silently dropped or crashing, matching the Non-Goals section. Unit tests per framework
cover: a plain route, a route with a path parameter, a route with an explicit
`methods=[...]`/verb decorator, and a Blueprint/Router-nested route (still within one file, not
mounted with a prefix). Plus: evidence wiring test (`checked: True` with
endpoints present, `checked: False` shape when `map_endpoints=False`), query test, MCP tool
registration test (count bump to 15), and a diff test using the verified `_new_and_resolved`
pattern (one endpoint added, one removed, one unchanged) confirming the `endpoints` diff section
shape.

**Phase 2:** one of the Phase 1 fixture apps (Flask is simplest) is actually run as a live
process on localhost during the test, `run_healthcheck` is pointed at it for real, and the test
asserts real status codes come back (200 for a real route, 404 for a placeholder-substituted
path param hitting a nonexistent ID, skip-marked entries for any POST routes in the fixture).
A second test confirms `reachable: False` behavior when pointed at a port nothing is listening
on (connection refused, not a hang — bounded by `timeout`).

## Success Criteria

1. Running `veridion scan` against a real Flask or FastAPI repo produces
   `repository.api_endpoints.endpoints` entries that exactly match that repo's actual routes —
   verified against a real app, not just a fixture.
2. `veridion query endpoints --path <repo>` and the `veridion_endpoints` MCP tool both return
   that same block.
3. `veridion diff` between two real scans of a repo, one with a route added and one without,
   shows that route under `endpoints.new` — and the reverse (a removed route) shows under
   `endpoints.resolved`.
4. `veridion healthcheck <repo> --base-url http://localhost:<port>`, run against a real live
   instance of that repo's app, reports accurate status codes and latencies for every GET
   endpoint, marks non-GET endpoints as skipped without ever sending them, and reports
   `reachable: False` cleanly (no crash, no hang past `timeout`) when the target isn't
   listening.
