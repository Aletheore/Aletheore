# Aletheore GitHub App — Foundation Design

**Status:** Draft, pending review
**Date:** 2026-07-17

## Problem

Today, getting Aletheore's PR-diff automation (new secrets, layer violations, dependency
vulnerabilities since the base branch) requires a maintainer to hand-write a GitHub Actions
workflow using `action.yml`. That's real friction for adoption, and it's also the only
mechanism that could plausibly justify using the KVM4 server (paid through ~2028, freed up by
Procta's pause) for Aletheore — a GitHub App needs a persistent webhook receiver; nothing else
in Aletheore's current roadmap does.

This spec covers the App's **foundation only**: install it on a repo, get automatic PR comments
and a free hosted dashboard, with GitHub Marketplace handling billing plumbing. It deliberately
does **not** cover the two paid capabilities decided alongside it — managed `audit` runs (shared
LLM key) and team/risk features (Slack/Teams alerts, branch-protection policy) — those get their
own specs once this foundation is real and shipped, since they both build on infrastructure this
spec creates (the webhook receiver, the job queue, the installation/plan records).

## Goals

- A registered GitHub App (`Aletheore`) installable on any repo, subscribed to `pull_request`,
  `installation`, `installation_repositories`, and `marketplace_purchase` webhook events.
- On every PR open/push: clone base + head refs, run the existing `aletheore scan` +
  `aletheore diff` CLI unchanged, post the result as a PR comment — output identical in kind to
  today's `action.yml`, but zero-config (no workflow YAML to write).
- A free hosted dashboard per installed repo (`aletheore.com/app/<org>/<repo>`) showing the
  latest scan's dependency graph, clusters, and trend charts across stored history — the hosted
  equivalent of the local `aletheore dashboard`, viewable by teammates without installing
  anything.
- GitHub Marketplace billing wired up structurally (installation → plan tracking), even though
  no paid feature gates on it yet in this spec — so the later paid-feature specs don't need to
  touch this plumbing again.
- Hosted on the KVM4 server at `aletheore.com` (or a subdomain), using the same Docker Compose +
  Caddy pattern already proven running Procta.

## Non-Goals

- **No managed `audit` runs** (agentic LLM report using a shared key) — separate spec, builds on
  this one's job queue.
- **No team/risk features** (Slack/Teams alerts, branch-protection/merge-blocking policy) —
  separate spec, also builds on this one.
- **No custom billing UI.** GitHub Marketplace handles plan selection and checkout entirely;
  this spec only consumes the `marketplace_purchase` webhook.
- **No code execution of the scanned repo's own code.** The scanner is static analysis only,
  same as today — cloning is for reading source, not running it.
- **No permanent storage of source code.** Only derived evidence (JSON) is ever persisted.

## Product Behavior

**Free tier**, on every repo the App is installed on:
- Automatic PR comments on open/synchronize, mirroring `action.yml`'s output.
- The hosted dashboard, populated from the same scans that produce the PR comments.

**Paid tier**: no gated capability in this spec. `installations` records a `plan` field from
`marketplace_purchase` so future specs can gate on it without a schema change.

## Architecture

### Components

- **`app-server`** (FastAPI, matching the existing Python/FastAPI stack already used across
  Aletheore's tooling and Procta): receives GitHub webhooks, verifies signatures, enqueues jobs,
  serves the hosted dashboard pages and a small JSON API for them, handles
  `marketplace_purchase`/`installation` events synchronously (they're cheap — DB writes only).
- **`scan-worker`**: consumes the job queue, does the actual clone → scan → diff → comment work.
  Runs as a separate process/container from `app-server` so a slow or stuck scan never blocks
  webhook responsiveness — this directly mirrors Procta's existing `worker`/
  `autosave-worker` split in `docker-compose.yml`, the same pattern already proven on this box.
- **Redis**: job queue between `app-server` and `scan-worker` (same role Redis already plays in
  Procta's stack — no new technology to learn).
- **Postgres**: `installations`, `repo_history` tables (below).
- **Caddy**: reverse proxy, TLS, same config style as Procta's `Caddyfile`.

### Webhook signature verification

Every incoming webhook is verified against the App's webhook secret using HMAC-SHA256 over the
raw request body, compared to the `X-Hub-Signature-256` header with a constant-time comparison.
Requests that fail verification are rejected with 401 before any further processing — this is
the App's only public entry point, so it's the one surface that must never trust its input by
default.

### `pull_request` event flow

1. `app-server` verifies the signature, checks the event is `opened` or `synchronize`, and
   enqueues a job (`{installation_id, repo_full_name, pr_number, base_sha, head_sha}`) — nothing
   else happens synchronously, so the webhook response returns immediately.
2. `scan-worker` picks up the job, creates a **job-scoped temp directory**
   (`/tmp/aletheore-jobs/<uuid>/`, one per job, never reused) and does the clone under it:
   - Exchange the installation id for a short-lived installation access token (GitHub Apps API).
   - Shallow-clone base and head refs into `base/` and `head/` under the job dir, using the
     installation token for auth (works for private repos without storing any long-lived
     credential per repo).
   - Run `aletheore scan base/` and `aletheore scan head/` (existing CLI, unchanged).
   - Run `aletheore diff` between the two evidence files (existing CLI, unchanged).
3. **Comment upsert, not insert**: search existing PR comments for a hidden marker
   (`<!-- aletheore-diff -->`) in the body. If found, `PATCH` that comment with the new diff. If
   not, create it. This prevents comment spam from repeated pushes to the same PR — a real gap
   in a naive "always post a new comment" design, since every `synchronize` event fires again.
4. On any failure in steps 2-3 (clone timeout, scan error, giant/malformed repo, network issue),
   post (or upsert) a plain-language failure comment — "Aletheore couldn't complete this scan:
   `<short reason>`" — rather than doing nothing. Silent failure is indistinguishable from the
   product being broken.
5. **Cleanup runs in a `finally`-equivalent, unconditionally** — the job-scoped temp directory is
   deleted whether the job succeeded, failed, or crashed. No source code is retained past this
   step; only the produced evidence JSON moves on to step 6.
6. On success, write the head evidence JSON as a new row in `repo_history` (see Storage below).

### `installation` / `installation_repositories` events

- `installation.created` / `installation_repositories.added`: upsert a row in `installations`
  for the account, with `plan = 'free'` until a `marketplace_purchase` says otherwise.
- `installation.deleted`: delete the `installations` row **and** all `repo_history` rows for
  that installation's repos. Uninstalling should actually remove stored data, not just stop
  future scans — this matters for any privacy claim the marketing site makes.

### `marketplace_purchase` event

Handles the full lifecycle, not just the initial purchase:
- `purchased`: set `installations.plan` to the purchased plan id.
- `changed`: update `installations.plan` to the new plan id.
- `cancelled`: set `installations.plan` back to `free`. Any future paid-gated feature must check
  this field live, not cache a stale "is paid" flag, so a cancellation takes effect immediately.

Handling is idempotent: the same delivery id retried by GitHub (their webhooks do retry) results
in the same end state, not a duplicate side effect — enforced by `UPSERT ... ON CONFLICT` on
`(installation_id)` rather than `INSERT`.

### Tenant isolation

Because multiple repos' clones land on one shared box, every job gets its own UUID-named temp
directory, created fresh and deleted unconditionally at job end. Jobs never share a directory,
so a crash or slow job in one tenant's scan cannot leak into or block another tenant's job. Job
concurrency on `scan-worker` is bounded (a fixed worker pool size, tuned to the KVM4's actual
CPU/memory headroom once this is running) so one large repo can't starve the others.

## Storage

Postgres, two tables:

```sql
CREATE TABLE installations (
    installation_id BIGINT PRIMARY KEY,
    account_login   TEXT NOT NULL,
    plan            TEXT NOT NULL DEFAULT 'free',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE repo_history (
    id              BIGSERIAL PRIMARY KEY,
    installation_id BIGINT NOT NULL REFERENCES installations(installation_id) ON DELETE CASCADE,
    repo_full_name  TEXT NOT NULL,
    scanned_at      TIMESTAMPTZ NOT NULL,
    evidence        JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX repo_history_lookup ON repo_history (installation_id, repo_full_name, scanned_at DESC);
```

`repo_history` mirrors the local CLI's `history.py` exactly in spirit: one full evidence
snapshot per scan, rotated to the most recent 20 per `(installation_id, repo_full_name)` (delete
the oldest beyond 20 after each insert — same `keep=20` default already used locally). Real
snapshot sizes measured against this repo's own history are 92–184KB, so 20 snapshots per repo
is a few MB, not a storage concern. `ON DELETE CASCADE` from `installations` means an
`installation.deleted` webhook's cleanup is a single `DELETE FROM installations WHERE
installation_id = $1` — `repo_history` rows go with it automatically.

## Hosted Dashboard

`aletheore.com/app/<org>/<repo>` — server-rendered page (or a small SPA, implementation detail
for the plan) reading the most recent N rows from `repo_history` for that repo, rendering the
same dependency graph / cluster graph / trend-chart views the local `aletheore dashboard`
already renders from local history. No auth gate in this spec (dashboard is free and the
installation implies the repo owner wants it visible) — **except** for private repos, where the
page must check the requester has read access to the underlying GitHub repo (via the GitHub API,
using the viewer's own OAuth if they're logged in) before rendering anything. Public repos render
with no auth required.

## Deployment (KVM4)

Directly reuses the operational pattern already proven running Procta on this exact box:

- `docker-compose.yml`: `app-server`, `scan-worker` (replicable, same shape as
  `proctor-browser_worker_N`), `redis`, `postgres`, `caddy`.
- `Caddyfile`: a new `aletheore.com { }` (or `app.aletheore.com { }`) block, reverse-proxying to
  `app-server`, TLS via Caddy's automatic HTTPS — same mechanism already configured for
  `app.procta.net`.
- Secrets (GitHub App private key, webhook secret, Postgres credentials) in a `.env` file on the
  server, never committed — same discipline already established and enforced for Procta's
  secrets.
- The domain `aletheore.com` is already owned; DNS needs an A/CNAME record pointed at the KVM4
  server's IP once deployed.

## Privacy Posture

Worth stating plainly on the marketing site rather than leaving implicit: installing the App is
a different trust boundary than running the local CLI. Source code is transiently fetched (clone
→ scan → discard, typically seconds) to produce evidence; **only derived evidence, never source
code, is ever persisted.** This is the honest, precise version of "nothing leaves your machine"
adapted for a hosted feature that inherently has to touch a machine that isn't the user's own —
better to say this clearly than to imply the App works identically to the CLI's local-only
promise.

## Testing

- **Webhook signature verification**: valid signature passes, tampered body/invalid signature
  rejected with 401.
- **`pull_request` flow**: a real small test repo (or a fixture pair of base/head refs) produces
  the same diff output as invoking `aletheore scan` + `aletheore diff` directly — i.e., the App
  doesn't reimplement scan/diff logic, it only orchestrates the existing CLI, so this test proves
  the orchestration is faithful, not that scan/diff itself is correct (already covered by
  Aletheore's existing test suite).
- **Comment upsert**: two consecutive `synchronize` events on the same PR result in exactly one
  bot comment (edited), not two.
- **Failure path**: a forced scan failure (e.g. an unreadable repo) results in a posted failure
  comment, not silence.
- **Cleanup**: job temp directory is confirmed absent after both a successful and a forced-failed
  job run.
- **`marketplace_purchase` idempotency**: replaying the same delivery id twice results in one
  `installations` row in the expected end state, not an error or a duplicate.
- **`installation.deleted` cascade**: confirms `repo_history` rows are actually gone after
  deletion, not just the `installations` row.
- **History rotation**: inserting a 21st snapshot for a repo leaves exactly 20 rows, the oldest
  one gone.

## Open Questions For The Implementation Plan

- Exact framework choice for the dashboard's frontend (server-rendered templates vs a small SPA)
  — implementation detail, doesn't affect this design.
- Whether `scan-worker` pool size needs to be configurable from day one or can be a hardcoded
  constant tuned once real usage exists.
