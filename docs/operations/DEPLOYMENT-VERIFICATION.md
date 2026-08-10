# Deployment Verification

**Purpose:** Define the minimum verification required before treating hosted deployment as current.
**Status:** Active baseline
**Owner:** Arihant Kaul
**Related Documents:** [README.md](README.md), [INCIDENT-RESPONSE.md](INCIDENT-RESPONSE.md), [../../github-app/README.md](../../github-app/README.md)
**Last Updated:** 2026-08-10
**Snapshot Freshness:** CURRENT as of 2026-08-10 - production was redeployed to `master` (commit `1659182`) and re-verified live via SSH the same day.

## Purpose

This runbook prevents repository state from being confused with production state.

## Required Checks

Before claiming a hardening change is live, verify:

- The server checkout path and remote.
- The deployed branch and commit.
- The working tree status.
- Running Compose services.
- Container startup commands.
- App server, worker, scheduler, PostgreSQL, Redis, and Caddy health.
- Absence of Docker socket mounts.
- Non-root app and worker users.
- CPU and memory limits for app server and scan worker.
- Migration runner execution before app startup.
- Backup script availability.
- Restore drill target database availability.

## Current Server Snapshot

As of 2026-08-10, following a redeploy to `master` (`git reset --hard origin/master` + `docker compose build app-server scan-worker health-worker scheduler` + `docker compose up -d --no-deps` for those four), live inspection found:

- Host: `srv1675832`.
- Deployment path: `/root/aletheore`.
- Remote: `https://github.com/Aletheore/Aletheore.git`.
- Branch: `master`.
- Commit: `1659182c146601a0817059b09d1d70cd99b25889`.
- Working tree: clean, no local diffs or stashes.
- Services running: `app-server`, `scan-worker`, `health-worker`, `scheduler`, `autoheal`, `demo-scan-worker`, `demo-sandbox-runner`, `postgres`, `redis`, `caddy`, `ollama` - all `Up`; `app-server`, `scan-worker`, `health-worker`, `scheduler`, `autoheal`, and `postgres` reporting Docker-healthcheck `healthy`. All four rebuilt services show `RestartCount: 0` since redeploy.
- App server starts via `python scripts/migrate.py && exec uvicorn ...`; this redeploy carried 5 pending migrations (`035_data_deletion_log.sql` through `039_telemetry_retention_index.sql`), all applied cleanly on startup with no errors (`applied 5 migration(s)` in logs). Confirmed live in Postgres afterward: `data_deletion_log`, `webhook_deliveries`, and `installation_access_log` tables exist; `cli_telemetry_events_occurred_at_idx` index exists; `installations.llm_suggestions_enabled` column exists.
- This redeploy shipped: self-serve data deletion (Settings -> Delete all data, and identically on uninstall) with a plan-independent purge that covers free-plan installations, not just paid seats; AIR evidence schema validation; webhook replay/duplicate protection for GitHub and Paddle; an MCP consent boundary (`ALETHEORE_MCP_ALLOW`) gating tools that transmit repository evidence externally; and body-size/rate-limit/retention controls on the two ingestion endpoints (`/v1/telemetry`, `/v1/runtime-events`).
- The two new abuse controls were verified live against the public endpoint post-deploy, not just via test suite: an oversized `/v1/telemetry` POST returned `413`; a chunked request with no `Content-Length` returned `411`; a valid small request returned `200` and inserted normally.
- The data-deletion fix (purging PII for free-plan installations, not just paid seats) was verified pre-deploy via the test suite and a red/green check against a local test database, not re-exercised against a real production installation - deleting a live customer's data to prove the code path would be destructive and out of proportion to what the test suite already demonstrates.
- `demo-scan-worker` has **no** Docker socket mount (`docker inspect` confirms empty `Mounts`); `demo-sandbox-runner` is the sole holder of `/var/run/docker.sock`, publishes no host ports (internal-only on the Compose network) - unchanged this pass, not re-verified with a fresh TCP connect since neither service nor its Dockerfile was touched by this redeploy.
- `app-server` and `scan-worker` run as the non-root `aletheore` user (re-confirmed live via `docker compose exec ... whoami`); `demo-sandbox-runner` runs as `root` (required to reach the Docker socket - the one service designed to need it, unchanged).
- CPU/mem limits present and re-confirmed via `docker inspect`: `app-server` 768MiB, `scan-worker` 1GiB. `demo-scan-worker`/`demo-sandbox-runner` limits not re-checked this pass - neither was touched by this redeploy.
- `scripts/backup-postgres.sh` present at the expected path (re-confirmed).
- `app-server`/`scan-worker` base image digest-pinning not re-verified this pass - neither Dockerfile changed in this redeploy's diff (confirmed via `git diff` against the prior deployed commit), so the prior verification stands.
- Restore drill target database availability was **not** re-verified this pass - out of scope for a routine redeploy; still needs an explicit check per its own runbook item.
- Health checks: internal `http://127.0.0.1:8000/healthz` and public `https://app.aletheore.com/healthz` both return `200 {"status":"ok","checks":{"database":"ok","redis":"ok"}}`.
- No errors, tracebacks, or exceptions in `app-server`, `scan-worker`, `health-worker`, or `scheduler` logs since restart.
- Disk: 129G available of 193G (34% used).

## Paddle Webhook Destination (live account config, not in git)

The set of events Paddle actually delivers to `/webhooks/paddle` is configured on Paddle's side
(notification destination `ntfset_01kyksktbmvr49pyygmxa3vfjz`), not in this repository - adding a
new event handler in `app_server/webhooks/paddle.py` does **not** make Paddle start sending that
event type. Confirmed via the Paddle API (`notificationSettings.list`/`.get`) that this destination
is currently subscribed to `subscription.canceled`, `subscription.created`, `subscription.paused`,
`subscription.resumed`, `subscription.updated`, and `transaction.completed`.

`transaction.completed` was added 2026-08-10 alongside the affiliate-commission feature - it was
initially missing (the destination predates that handler and was never updated), which would have
made the entire commission-recording code path permanently unreachable in production despite
passing all tests, since tests exercise the handler directly rather than real Paddle delivery. This
is now a standing check: any new webhook handler for an event type not already in the list above
needs both the code AND this destination's `subscribed_events` updated, verified live via
`notificationSettings.get`, not assumed from the code change alone.

## Recovery Rule

If any deployed state differs from repository expectations, treat production as stale until the exact commit and Compose configuration are verified.

## Deploy History

This file is a snapshot of *current* production state — it gets overwritten on every redeploy, so
it never shows what was live last week. For that, every production deploy is tagged
`github-app-deploy-YYYY-MM-DD` (append `-N` for a second same-day deploy), and
[`../../github-app/CHANGELOG.md`](../../github-app/CHANGELOG.md) has a dated, human-readable entry
per deploy. `git tag -l 'github-app-deploy-*'` lists every tracked deploy point;
`git log <prev-tag>..<tag>` gives the exact commit range for any one of them.

