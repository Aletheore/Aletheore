# Aletheore Hosted MCP — Design Spec

## Overview

Today, Aletheore ships a **local** MCP server (`prototype/aletheore/mcp_server.py`, stdio transport) that a developer runs on their own machine via `aletheore mcp <repo>`, and a CLI installer (`aletheore mcp-install`) that wires it into Claude Code / Cursor / VS Code / Kiro / Opencode / Codex CLI. It reads local disk and local git directly — full tool access, zero server cost, but single-machine and requires local setup (including a locally-running Ollama for two of the tools).

This spec adds a **hosted** MCP endpoint: paid-plan users point their coding agent at `https://mcp.aletheore.com` with a Bearer token instead of running anything locally. The value isn't "more powerful tools" — it's zero setup and team-wide consistency: any teammate's agent, on any machine (including one that's never cloned the repo), gets the same always-current, server-maintained view of the repo. This only makes sense for repos that already have AIRview's server-side evidence pipeline running, which today is strictly a paid-plan feature — so hosted MCP inherits that exact boundary.

## Goals

- Expose the same ~23 MCP tools (all of `prototype/aletheore/mcp_server.py`'s tools except `aletheore_managed_audit`, which is already hosted-backend-driven) over `streamable-http`, backed by server-side data instead of local disk.
- Gate access on `installations.plan != "free"` — the same boundary the AIRview wiki build already uses. No new tier logic.
- Reuse existing infrastructure wherever it already does the job: the `api_tokens` Bearer scheme, the push-webhook trigger, the hosted Ollama container, the `evidence_packet_cache`/`flash_review_cache` tenant-scoped-array-column pattern, and the `aletheore` package (already pip-installed into both `app-server` and `scan-worker` images — `github-app/Dockerfile.app-server:5-7`, `github-app/Dockerfile.scan-worker:5-7` — so `aletheore.search_index`, `aletheore.query`, `aletheore.mcp_server`'s tool logic, etc. are directly importable server-side, not subprocess-only).
- Strict per-tenant isolation for the two tools with shared server-side state (`search_codebase`, `answer`), verified by tests that specifically try to make it leak.
- Zero external LLM API cost for `search_codebase`/`answer` — both run against the existing hosted Ollama container, with a new small generation model added alongside the existing embedding model.

## Non-Goals (deferred, explicitly out of scope for this spec)

- **Option B** (ad hoc "any repo, scanned on demand" hosted MCP) — rejected during design; would require cloning arbitrary third-party repos server-side on a user's behalf, a new abuse/security surface, for a use case (using hosted MCP without an installed App) that doesn't match the actual buyer.
- Free-tier hosted MCP, or exposing `repo_history`'s raw (non-AIRview) evidence to free installations via MCP — the plan/gating boundary stays identical to AIRview's existing paid-only line; revisit only as a deliberate future decision.
- A real disk-quota/billing system for mirror storage — v1 uses a simple flat per-repo size cap (see Disk Lifecycle) given the host currently has 172GB free and this is an early-stage feature.
- GPU-backed model serving — the model choice (Qwen2.5-3B-Instruct) is explicitly picked to run acceptably on the existing CPU-only host.
- Enterprise SSO/RBAC as an auth mechanism for hosted MCP — auth stays the existing per-installation Bearer token; no per-user identity inside a hosted MCP session.

## Architecture

### Transport & mounting

Verified against the pinned SDK (`mcp==1.23.3`, `prototype/pyproject.toml:39`): `FastMCP` exposes `.streamable_http_app() -> Starlette`, which returns a mountable ASGI app. New module `github-app/app_server/mcp_hosted.py` builds a `FastMCP` instance reusing the same tool-registration logic as `prototype/aletheore/mcp_server.py` (see "Tool data-access layers" below), and `main.py` mounts it: `app.mount("/mcp", build_hosted_mcp_app())`. This runs inside the existing `app-server` container behind Caddy — not a new service, no new port, no new deploy step beyond the existing `docker compose build app-server && docker compose up -d`.

`FastMCP(..., stateless_http=True)` is used deliberately: hosted MCP is called by many different installations concurrently, and stateless mode avoids pinning a client session to in-process server state that could leak between requests — reinforces the isolation requirement below rather than fighting it.

### Auth model — plain Bearer, not MCP SDK OAuth

The MCP SDK ships a full OAuth 2.1 `TokenVerifier`/`AuthSettings` abstraction (`mcp.server.auth.provider.TokenVerifier`, `mcp.server.auth.settings.AuthSettings` — requires `issuer_url`, supports RFC 8707 resource indicators, client registration, etc.). That's the wrong shape for what exists: Aletheore's real auth is a single static per-installation Bearer token against the existing `api_tokens` table (already used by `managed_audit_api.py` and the CLI, resolved via `get_installation_by_token_hash()`, `github-app/app_server/db.py:483`). Bolting on the SDK's OAuth machinery would mean either building a fake authorization server or fighting the abstraction for no benefit.

Instead: a thin Starlette middleware wraps the mounted MCP ASGI app. It reads the `Authorization: Bearer <token>` header, runs the *exact same* hash-and-lookup as `managed_audit_api.py`'s `_authenticate_token()`, and:
- No token / invalid token → `401` before the request reaches the MCP protocol handler at all.
- Valid token, `installation["plan"] == "free"` → `402`.
- Valid token, paid plan → sets a `contextvars.ContextVar[int]` (`CURRENT_INSTALLATION_ID`, defined in `mcp_hosted.py`) to the resolved `installation_id`, then calls through to the MCP app.

Every tool implementation reads `CURRENT_INSTALLATION_ID.get()` at the top of its body to scope its query — this is the single choke point the isolation tests target. Using a `ContextVar` set by ASGI middleware (rather than threading an argument through FastMCP's tool-call machinery, which has no first-class concept of "per-request tenant") is a standard pattern for smuggling per-request context into an ASGI app whose framework doesn't natively support it, and keeps every tool function's signature identical to the local server's.

### Tool data-access layers

Same ~23 tool *names and signatures* as `prototype/aletheore/mcp_server.py`; three different backing implementations depending on what each needs, selected by adding a data-source abstraction rather than duplicating tool logic:

1. **21 pure-query tools** (`imports`, `imported_by`, `symbols`, `branch`, `ownership`, `secrets`, `vulnerabilities`, `licenses`, `endpoints`, `cluster`, `layer_violations`, `dead_code`, `hotspots`, `database`, `infrastructure`, `environment_variables`, `changes`, `neighborhood`, `find_evidence_for_endpoint`, `find_evidence_for_symbol`, `find_evidence_for_dependency`) dispatch through the exact same `QUERY_FUNCTIONS` table `prototype/aletheore/query.py` already defines — these functions all take an `evidence: dict` argument. Locally, that dict comes from reading `.aletheore/air.json`; server-side, it comes from `get_latest_evidence(installation_id, repo_full_name)` (`github-app/scan_worker/jobs.py:902`, already used to feed the AIRview wiki builder) — same shape, different source. No changes needed to `query.py` itself.
2. **Exact-text tools** (`symbol_source`, `search`, `scan`) need real file content, which AIRview's derived evidence doesn't carry — these read from the new **git-synced mirror** (see Data Flow).
3. **`search_codebase` / `answer`** query a new tenant-scoped embedding table built off the mirror, generate via hosted Ollama.

## Data Model Changes

Two new tables, following the exact pattern `evidence_packet_cache`/`flash_review_cache` already establish (tenant-scoped array columns, no pgvector — confirmed: `grep -r "CREATE EXTENSION\|vector" github-app/migrations/*.sql` returns nothing; embeddings are plain `DOUBLE PRECISION[]` compared in Python, e.g. `github-app/scan_worker/embedding_client.py`).

`github-app/migrations/016_mcp_git_mirrors.sql`:
```sql
CREATE TABLE mcp_git_mirrors (
    id BIGSERIAL PRIMARY KEY,
    installation_id BIGINT NOT NULL REFERENCES installations(installation_id) ON DELETE CASCADE,
    repo_full_name TEXT NOT NULL,
    local_path TEXT NOT NULL,
    last_synced_commit TEXT,
    last_synced_at TIMESTAMPTZ,
    size_bytes BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (installation_id, repo_full_name)
);
```

`github-app/migrations/017_mcp_code_embeddings.sql`:
```sql
CREATE TABLE mcp_code_embeddings (
    id BIGSERIAL PRIMARY KEY,
    installation_id BIGINT NOT NULL REFERENCES installations(installation_id) ON DELETE CASCADE,
    repo_full_name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    chunk_index INT NOT NULL,
    content_hash TEXT NOT NULL,
    chunk_text TEXT NOT NULL,
    embedding DOUBLE PRECISION[] NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX mcp_code_embeddings_lookup ON mcp_code_embeddings (installation_id, repo_full_name, file_path);
```

Both cascade-delete via the existing `installations` FK — consistent with every other per-installation table, and with the uninstall hook below.

`content_hash` per chunk lets the re-index step skip unchanged files on incremental sync (hash the chunk text, compare before re-embedding) rather than re-embedding the whole repo on every push.

## Data Flow

The existing push-webhook path (`github-app/app_server/webhooks/pull_request.py` → `run_pr_scan_job`, `github-app/scan_worker/jobs.py:290-358`) already runs unconditionally per PR event and already calls `_maybe_update_live_wiki` for paid installations. Hosted MCP adds two more steps to that same paid-only branch — no new trigger, no new webhook registration:

1. **Git mirror sync** (new function `_sync_mcp_mirror(installation_id, repo_full_name, clone_url)` in `jobs.py`). Reuses the existing `_clone_url` helper (`jobs.py:104-105`) for the authenticated clone URL. Unlike `_clone_ref`/`_clone_pr_head` (which always target `_job_temp_dir()` and get `shutil.rmtree`'d in a `finally` block), this targets a **persistent** path: `MIRROR_ROOT / str(installation_id) / repo_full_name.replace("/", "__")`, `MIRROR_ROOT = Path("/var/aletheore/mirrors")`. Logic: if the directory doesn't exist, `git clone --bare` (or a full working clone — full clone chosen for simplicity, since these tools need a working tree to read files from, not just objects) into it; if it exists, `git fetch` + `git reset --hard origin/<default-branch>` — **fetch-then-reset, never delete-then-reclone**, so an in-flight read from a tool call never sees a half-populated directory (git's ref update is atomic; a concurrent reader either sees the pre-fetch tree or the post-reset tree, never a partial one). Upserts the `mcp_git_mirrors` row (`local_path`, `last_synced_commit`, `size_bytes` via `shutil.disk_usage` or a `du`-equivalent walk, `last_synced_at`).
2. **Embedding re-index** (new function `_reindex_mcp_embeddings(installation_id, repo_full_name, mirror_path)`). Walks the mirror, chunks changed files (reusing `aletheore.search_index`'s existing chunking logic — confirmed importable server-side since the whole `aletheore` package is pip-installed into both `app-server` and `scan-worker` images per the Dockerfiles), computes `content_hash` per chunk, skips chunks whose hash already exists in `mcp_code_embeddings` for that `(installation_id, repo_full_name, file_path)`, embeds the rest via the existing `embedding_client.py` pattern (same Ollama call `evidence_packet_cache`/`flash_review_cache` already use), and upserts rows. Deletes rows for files no longer present in the mirror.

Both run inside the existing `run_pr_scan_job` RQ job, after the existing wiki-update call, still gated on `installation["plan"] != "free"`.

At connect time, the auth middleware resolves `installation_id` once per session; every tool call within that session is scoped to that one installation — there's no "pick a repo" step the way Option B would have needed, since the Bearer token itself defines the scope. (If an installation covers multiple repos, tools take a `repo_full_name` argument exactly as the local tools already do, scoped-checked against `mcp_git_mirrors`/evidence rows for that `installation_id` only.)

## Isolation & Security Requirements

This is the section flagged as needing to be done properly, not glossed over, since one server process now serves many paying tenants concurrently:

1. **Embedding index isolation**: every read and write against `mcp_code_embeddings` filters by `installation_id` taken from `CURRENT_INSTALLATION_ID.get()` (server-resolved from the auth token), never from a client-supplied parameter. A malicious or buggy client cannot pass an `installation_id` and have it honored — there is no such parameter on any tool.
2. **Git mirror isolation**: one directory per `(installation_id, repo_full_name)` pair, never shared — two different installations of the same public repo (a real case: someone forks a popular OSS project and both the fork and original have Aletheore installed) get fully separate mirror directories and fully separate embedding rows, even though the file content may be near-identical.
3. **Fail-closed reads**: the fail-open philosophy used elsewhere (audit-report signing, caching) protects a primary deliverable from being destroyed by a side-effect failure — it does not apply here, because for hosted MCP, correctly-scoped data *is* the primary deliverable. If `mcp_git_mirrors.last_synced_at` is missing or older than some threshold (proposed: 2x the expected sync interval, i.e. stale if no successful sync in the time it'd take two pushes to land), `symbol_source`/`search`/`scan` return an explicit "mirror not yet synced / resync pending" tool error rather than serving whatever happens to be on disk.

**Test plan** (goes into the implementation plan as its own task): seed two or more fake installations in a test DB with deliberately overlapping/similar file content (same filenames, similar function names, near-duplicate code) across separate mirror directories and embedding rows, then assert:
- `search_codebase`/`answer` for installation A's token never surfaces any chunk whose `installation_id` is B's, under sequential calls.
- Same assertion holds under concurrent async requests interleaving A's and B's sessions (rules out any request-scoped state — e.g. an accidentally-shared `ContextVar` default, or a connection-pool-level bleed — leaking across `asyncio` tasks).
- `symbol_source`/`search` for A's token only ever reads from A's mirror path, verified by asserting the resolved path is always prefixed by `MIRROR_ROOT / str(installation_id_A)`.

## Error Handling

- **Auth**: invalid/expired/revoked token → `401`. Valid token, free-plan installation → `402`. Both raised before the request reaches the MCP protocol layer (middleware-level).
- **Stale/missing mirror or index**: explicit "not available, resync pending" tool-level error (not an HTTP error — the MCP session itself stays open; this is a per-tool-call result), per the fail-closed rule above.
- **Ollama unavailable or timed out**: `search_codebase`/`answer` wrap the Ollama call with an explicit timeout (proposed: 8s for embeddings, 20s for generation, both configurable via env var) and return a tool-level "model temporarily unavailable, try again" error rather than hanging the MCP session indefinitely.

## Model Serving

**Resource allocation**: verified live on the production host (`$PROD_SSH`, 4 vCPU / 15GB RAM total, 14GB currently free — confirmed via `free -h`/`nproc` over SSH; every other container combined currently uses well under 200MB). The `ollama` service's current `docker-compose.yml` limits (`cpus: "1.0"`, `mem_limit: 1g`) are self-imposed, not a host ceiling. Bump to `cpus: "2.0"`, `mem_limit: 6g` — `nomic-embed-text` (~274MB) plus Qwen2.5-3B-Instruct quantized (~2GB) leaves real headroom for request buffers/KV cache, and still leaves 2 CPUs / 8GB+ for the rest of the stack.

**Model**: add `ollama pull qwen2.5:3b-instruct` (or the appropriate quantized tag) to the `ollama` service's startup command alongside the existing `ollama pull nomic-embed-text` (`github-app/docker-compose.yml`'s `ollama.command`).

**Concurrency control**: Ollama on CPU serves generation requests effectively serially, not in true parallel — without a limiter, a burst of `answer` calls from one team would queue every other team's requests behind it with unbounded wait. New module `github-app/app_server/model_concurrency.py` wraps generation calls in an `asyncio.Semaphore(2)` (embeddings, being much cheaper, get a separate looser semaphore or none) with a bounded wait (proposed: 15s) before returning the "server busy, retry" tool error rather than queuing indefinitely. This is v1-scale tuning, explicitly expected to be revisited once real load data exists.

## CLI Changes

`prototype/aletheore/cli.py`'s `mcp-install` command gains a hosted mode. Rather than a new top-level command, add `--hosted <token>` as a flag: when present, for every selected target, write a **hosted** entry (`{"url": "https://mcp.aletheore.com/mcp", "headers": {"Authorization": "Bearer <token>"}}`, shape adapted per client exactly as the existing `_MCP_CLIENT_CONFIGS` table already adapts local stdio entries per client) instead of the local stdio entry — reusing the exact same merge-safe JSON/TOML writers (`_write_json_mcp_client_config`, `_write_toml_mcp_client_config`) already built this session, just swapping which entry-builder function gets called. PyCharm/vim/Neovim/Emacs guidance text gets a one-line addition pointing at the hosted URL+token as an alternative to local `mcp` for those tools' manual-config paths.

## Disk Lifecycle

- **Creation/update**: handled by the sync job above, on every paid-plan push event.
- **Cleanup on uninstall**: `github-app/app_server/webhooks/installation.py`'s `handle_installation_event` (the `action == "deleted"` branch, line 10-11) currently only does `DELETE FROM installations` (which cascades to every FK'd table, including the two new ones — so DB rows are already handled for free). It does **zero filesystem cleanup** today, confirmed by inspection — this is a real, currently-latent gap even before hosted MCP (nothing deletes old PR-scan temp dirs' parent... though those already self-clean via the existing `finally: shutil.rmtree`). Add one line to that handler: before or after `delete_installation`, look up (or just construct, since the path is deterministic) `MIRROR_ROOT / str(installation_id)` and `shutil.rmtree(..., ignore_errors=True)` — removes every mirror for that installation across all its repos in one call.
- **Size cap**: no disk-quota mechanism exists anywhere in the codebase today (confirmed, zero hits for `disk_usage`/`disk_quota`/`storage_limit`). Given 172GB free and an early-stage feature, v1 uses a flat cap, not a quota system: `_sync_mcp_mirror` checks the clone's `size_bytes` after cloning/fetching, and if it exceeds a constant (proposed: 2GB), skips embedding/keeps the mirror for `symbol_source`/`search` but logs a warning and marks the row (`mcp_git_mirrors.size_bytes` already captured) so this can be revisited manually if it ever fires. Not a hard reject — a large-but-installed repo shouldn't lose hosted MCP entirely, just the more expensive embedding step. This is intentionally simple; a real per-installation quota system is out of scope (see Non-Goals).

## Branch Scope (known behavior difference from local MCP)

The mirror sync (`_sync_mcp_mirror`) tracks the repo's **default branch only** — it fetches and resets to `origin/<default-branch>` on every push event to that branch. Local MCP, by contrast, reflects whatever's actually checked out on the developer's machine, including an uncommitted feature branch in progress. Hosted MCP cannot see feature-branch or uncommitted work — it's a view of what's merged/pushed to default, refreshed on every push to that branch. This is an acceptable v1 scope (matches "team-shared source of truth" positioning) but should be stated in user-facing docs/onboarding, not left implicit: hosted MCP answers "what does this repo look like on main," not "what am I currently working on."

## Testing Strategy

- Unit tests for `_sync_mcp_mirror` (clone-then-fetch idempotency, atomic-update-under-concurrent-read simulation) and `_reindex_mcp_embeddings` (content-hash skip logic, deletion of stale rows).
- The isolation test suite described above (multi-tenant leakage, concurrent requests).
- Auth middleware tests: missing token, invalid token, free-plan token, valid paid token — each asserted against the exact status code / tool-error behavior specified above.
- Integration test standing up the mounted `streamable-http` app in-process (FastMCP supports this for testing) and driving it with a real MCP client library call for at least one tool from each of the three data-access layers (a pure-query tool, `symbol_source`, and `search_codebase`).
- Manual end-to-end verification against the real deployed server with a real coding agent (Claude Code) pointed at a real test installation, before considering this shippable — consistent with this session's existing practice of never trusting a self-reported "tests pass" without real verification.

## Rollout / Tier Gating

No new gating logic — reuses `installations.plan != "free"`, identical to the AIRview wiki build boundary. No pricing/seat changes implied by this spec; hosted MCP is presented as an included capability of the same paid tiers that already get AIRview, not a new SKU.

## Open Risks (carried forward, not blocking, revisit post-v1)

- Embedding similarity search is a linear scan in Python (no pgvector), matching existing precedent (`evidence_packet_cache`) — fine at current scale, a real bottleneck if any single repo's embedding table grows very large. Worth a follow-up spec if/when that happens.
- The 2-generation-request concurrency cap and 2GB mirror size cap are both first-guess constants, not derived from load testing — expected to be tuned once hosted MCP has real paying users.
- No GPU path is designed for; if Qwen2.5-3B's CPU latency proves unacceptable in practice, the next step would be a dedicated GPU instance for Ollama, which is a real infra/cost decision outside this spec's scope.
