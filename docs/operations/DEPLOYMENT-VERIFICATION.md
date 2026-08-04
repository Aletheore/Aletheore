# Deployment Verification

**Purpose:** Define the minimum verification required before treating hosted deployment as current.
**Status:** Active baseline
**Owner:** Arihant Kaul
**Related Documents:** [README.md](README.md), [INCIDENT-RESPONSE.md](INCIDENT-RESPONSE.md), [../../github-app/README.md](../../github-app/README.md)
**Last Updated:** 2026-08-04
**Snapshot Freshness:** CURRENT as of 2026-08-04 - production was redeployed to `master` (commit `447d7e9`) and re-verified live via SSH the same day.

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

As of 2026-08-04, following a redeploy to `master` (`git pull --ff-only` + `docker compose build` + `docker compose up -d`), live inspection found:

- Host: `srv1675832`.
- Deployment path: `/root/aletheore`.
- Remote: `https://github.com/Aletheore/Aletheore.git`.
- Branch: `master`.
- Commit: `447d7e940430be07d14f6c84bdf8ea4bc49144c0`.
- Working tree: clean, no local diffs or stashes.
- Services running: `app-server`, `scan-worker`, `scheduler`, `demo-scan-worker`, `demo-sandbox-runner`, `postgres`, `redis`, `caddy`, `ollama` - all `Up`, `postgres` reporting `healthy`.
- App server starts via `python scripts/migrate.py && exec uvicorn ...`; startup logs show `no pending migrations` (schema already current, no errors).
- `demo-scan-worker` has **no** Docker socket mount (`docker inspect` confirms empty `Mounts`); `demo-sandbox-runner` is the sole holder of `/var/run/docker.sock`, publishes no host ports (internal-only on the Compose network), and is reachable from `demo-scan-worker` at `http://demo-sandbox-runner:8090` (verified with a live TCP connect from inside the `demo-scan-worker` container).
- `app-server` and `scan-worker` run as the non-root `aletheore` user; `demo-sandbox-runner` runs as `root` (required to reach the Docker socket - the one service designed to need it).
- CPU/mem limits present: `app-server` 768MiB, `scan-worker` 1GiB, `demo-scan-worker` 512MiB, `demo-sandbox-runner` 256MiB.
- `scripts/backup-postgres.sh` present at the expected path.
- `app-server`/`scan-worker` base images (`Dockerfile.app-server`, `Dockerfile.scan-worker`) are digest-pinned (`python:3.12-slim@sha256:...`), not just tag-pinned.
- Restore drill target database availability was **not** re-verified this pass - out of scope for a routine redeploy; still needs an explicit check per its own runbook item.
- Health checks: internal `http://127.0.0.1:8000/healthz` and public `https://app.aletheore.com/healthz` both return `200 {"status":"ok","checks":{"database":"ok","redis":"ok"}}`. (The bare `aletheore.com` domain resolves to the marketing site's Vercel deployment, not this host - expected, not a routing bug.)
- No errors, tracebacks, or exceptions in `app-server`, `scan-worker`, `scheduler`, `demo-scan-worker`, `demo-sandbox-runner`, or `caddy` logs since restart.
- Disk: 144G available of 193G (26% used).

## Recovery Rule

If any deployed state differs from repository expectations, treat production as stale until the exact commit and Compose configuration are verified.

