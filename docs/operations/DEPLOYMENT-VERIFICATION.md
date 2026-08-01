# Deployment Verification

**Purpose:** Define the minimum verification required before treating hosted deployment as current.
**Status:** Active baseline
**Owner:** Arihant Kaul
**Related Documents:** [README.md](README.md), [INCIDENT-RESPONSE.md](INCIDENT-RESPONSE.md), [../../github-app/README.md](../../github-app/README.md)
**Last Updated:** 2026-07-23

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

As of 2026-07-23, read-only inspection found:

- Host: `srv1675832`.
- Deployment path: `/root/aletheore`.
- Remote: `https://github.com/Aletheore/Aletheore.git`.
- Branch: `master`.
- Commit: `ad4f3cdf3b4b3683c81afc0ce3a4151423fc1da4`.
- Services running: `app-server`, `scan-worker`, `scheduler`, `postgres`, `redis`, `caddy`.
- App-native scheduler is running.
- No Ofelia container was observed.
- App server still starts directly with Uvicorn on that commit; PR #15 migration-startup behavior is not yet deployed.
- App/worker images are non-root on that commit, but Python base images are still tag-pinned rather than digest-pinned.
- Compose resource limits from PR #15 are not yet deployed.

## Recovery Rule

If any deployed state differs from repository expectations, treat production as stale until the exact commit and Compose configuration are verified.

