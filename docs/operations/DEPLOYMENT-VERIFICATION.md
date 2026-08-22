# Deployment Verification

**Purpose:** Define the minimum verification required before treating hosted deployment as current.
**Status:** Active baseline
**Owner:** Arihant Kaul
**Related Documents:** [README.md](README.md), [INCIDENT-RESPONSE.md](INCIDENT-RESPONSE.md), [../../github-app/README.md](../../github-app/README.md)
**Last Updated:** 2026-08-22 (second deploy)
**Snapshot Freshness:** CURRENT as of 2026-08-22 - production was redeployed to `master` (commit `09cfdda`) and re-verified live via SSH the same day.

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

As of 2026-08-22 (second deploy), following a redeploy to `master` (`git reset --hard origin/master` + `docker compose build app-server scan-worker health-worker scheduler` + `docker compose up -d --no-deps --scale scan-worker=2` for those four), live inspection found:

- Host: `srv1675832` (`root@187.127.169.89`).
- Deployment path: `/root/aletheore`.
- Remote: `https://github.com/Aletheore/Aletheore.git`.
- Branch: `master`.
- Commit: `09cfdda8a26793e019ed95162964d5a1f34c1d2d`.
- Working tree: clean aside from an untracked `github-app/backups/` directory (expected - backup script output, not repo content), no local diffs or stashes.
- 6 commits since the first same-day deploy tag (`github-app-deploy-2026-08-22`) - see `github-app/CHANGELOG.md`'s "second deploy" entry. Headline changes: three crash/broken-feature bugs from the second-pass audit (an unguarded-`.decode()` scan-abort, an RRF-fusion `TypeError` crash in AIRview Q&A, and a `NameError` that silently broke AIRview's non-scanned-file fallback - #360), plus a new ops-monitor check alerting when a free-tier provider key goes missing (#357, closing the exact gap this same day's free-tier key-sync incident exposed).
- Passing `--scale scan-worker=2` on the `up -d` command itself (not as a separate follow-up call) recreated both replicas cleanly under their expected names (`scan-worker-1`, `scan-worker-2`) with no orphan - confirms the 2026-08-22 (first deploy) finding: the plain service-name form doesn't reliably recreate every replica, but including `--scale` from the start avoids the problem entirely rather than needing a manual cleanup pass.
- Services running: `app-server`, `scan-worker` (2 replicas), `health-worker`, `scheduler`, `autoheal`, `demo-scan-worker`, `demo-sandbox-runner`, `postgres`, `redis`, `caddy`, `jina-embed` - all `Up`; the four rebuilt services and `scan-worker`'s second replica all reporting Docker-healthcheck `healthy`.
- App server starts via `python scripts/migrate.py && exec uvicorn ...`; this redeploy carried no pending migrations (`no pending migrations` in logs) - a code-only deploy.
- Post-deploy, verified live (not just that the deploy succeeded) that all three #360 fixes and the #357 addition are actually present in the running code: `dashboard._fetch_wiki_file_content_sync` calls `_github_http_client()` not the unimported `get_github_api_client()`; `search_index._rrf_fuse` merges hit dicts (`by_key.get(key, {})`) instead of overwriting; `answer.answer_question` has the `top_score is not None` guard; `scanner.graph` has zero remaining bare `.decode()` calls (43 guarded); `scan_worker.jobs` has `_check_free_tier_provider_keys`. Also re-confirmed the four free-tier provider keys (synced earlier the same day) survived the restart - `has_api_key()` still returns `True` for all four.
- Health checks: internal `http://127.0.0.1:8000/healthz` and public `https://app.aletheore.com/healthz` both return `200 {"status":"ok","checks":{"database":"ok","redis":"ok"}}`.
- No errors, tracebacks, or exceptions found in `app-server`, `scan-worker-1`, `scan-worker-2`, `health-worker`, or `scheduler` logs after restart (targeted grep for `error|traceback|exception`).
- Not re-verified this pass (out of scope, no relevant Dockerfile/script changes in the diff): Docker socket mount absence, non-root users, CPU/mem limits, backup cron execution, base-image digest pinning, restore-drill target availability, disk space. Each was last directly verified in the 2026-08-10 deploy - re-check if any host-level or Dockerfile change touches them.

## Free-Tier Flash Review Provider Keys (live server config, not in git)

`writing_adapter_chain_for_free_tier` in `scan_worker/model_tiers.py` builds its fallback chain
from four env vars (`GROQ_API_KEY`, `GEMINI_API_KEY`, `OPENAI_FREE_TIER_API_KEY`,
`OPENROUTER_API_KEY`), each silently skipped if unset - the code has no way to tell "no key
configured" apart from "operator hasn't gotten to this provider yet", so an empty chain fails
silent, not loud (`jobs.py` logs a warning and returns `False`, no user-facing error, no alert).

As of 2026-08-22, confirmed all four keys are set in production's `github-app/.env` (checked via
`has_api_key()` boolean return values only - the actual values never appear in any command output,
log, or file under version control) and `scan-worker`/`health-worker` have been restarted to pick
them up. Before this, all four had been present in the *local* `.env` for an unknown period but
never synced to the server, meaning every free-tier Flash Review was silently no-op'ing in
production despite the feature's code being fully implemented, tested, and deployed. If this
regresses (e.g. a future server rebuild from a fresh `.env` template), the symptom is the same as
before: free-tier reviews silently stop happening, with only a log line to notice by. Re-verify with
the four `has_api_key()` checks above after any `.env`-affecting change, not just after a code
redeploy - env drift is invisible to `git diff` and to every check in this document above this one.

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

