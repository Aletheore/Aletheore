# Deployment Verification

**Purpose:** Define the minimum verification required before treating hosted deployment as current.
**Status:** Active baseline
**Owner:** Arihant Kaul
**Related Documents:** [README.md](README.md), [INCIDENT-RESPONSE.md](INCIDENT-RESPONSE.md), [../../github-app/README.md](../../github-app/README.md)
**Last Updated:** 2026-08-27
**Snapshot Freshness:** CURRENT as of 2026-08-27 - production was redeployed to `master` (commit `12baf31`) and re-verified live via SSH the same day.

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

As of 2026-08-27 (third deploy), following a redeploy to `master` (`git fetch` + `git reset --hard origin/master` + `docker compose build app-server scan-worker scan-worker-2 health-worker scheduler` + `docker compose up -d --no-deps --force-recreate` for those five), live inspection found:

- Host: `srv1675832` (`root@187.127.169.89`).
- Commit: `12baf31`.
- Working tree: clean aside from the expected untracked `github-app/backups/` directory.
- 2 commits since the previous deploy tag (`github-app-deploy-2026-08-27-2`): #428 fixed
  `_PUSHOVER_KEY_PATTERN`'s bare `$` (which, without `re.MULTILINE`, matches just before a single
  trailing newline as well as true end-of-string) to `\Z`, so a 30-character Pushover key with a
  copy-paste trailing newline is now correctly rejected instead of silently accepted; #430 fixed
  `git_intel/incremental.py`'s `fold()` iterating `commits` in caller order (newest-first, matching
  real `git log`) while building `recent_commits` with `insert(0, ...)` - the two compounded to put
  the *oldest* commit at `recent_commits[0]` instead of the newest, which `jobs.py`'s
  `_commit_attachment_from_graph`/`_owner_attachment_from_graph` read directly as "the latest
  commit" for health-check-failure correlation and likely-owner inference on the hosted path.
  #430's own CI catalogued and fixed three test fixtures with an oldest-first mirror-image ordering
  bug that had been masking this; a fourth fixture (`github-app/tests/test_correlation.py`) was
  found and fixed the same way after the first CI run on this session's push still failed against
  it, verified locally against a real Postgres round-trip before re-pushing. See
  `github-app/CHANGELOG.md` for the full per-PR writeup.
- Rebuilt the same five services as the previous deploy (`admin.py` changed for #428;
  `git_intel/incremental.py` - part of the shared `aletheore` package `scan_worker` installs -
  changed for #430) - `demo-scan-worker` and `demo-sandbox-runner` again left untouched, neither's
  own source changed.
- Services running: same set as the previous snapshot, all `Up`; all five rebuilt services
  reporting Docker-healthcheck `healthy` within seconds of recreation.
- No pending migrations - `app-server`'s startup log shows `no pending migrations`.
- Post-deploy, verified live (not just that the deploy succeeded) by executing directly inside the
  running containers: `app_server.admin`'s live `_PUSHOVER_KEY_PATTERN` source contains `\Z`;
  `aletheore.git_intel.incremental.fold`'s live source contains `reversed(commits)`.
- Health checks: internal `/healthz` returns `200 {"status":"ok","checks":{"database":"ok","redis":"ok"}}`.
- No errors, tracebacks, or exceptions in `app-server`, `scan-worker`, `scan-worker-2`,
  `health-worker`, or `scheduler` logs in the 2 minutes after restart.
- Not re-verified this pass (no relevant Dockerfile/host changes): Docker socket mount absence,
  non-root users, CPU/mem limits, backup cron execution, base-image digest pinning, restore-drill
  target availability, disk space - each last directly verified 2026-08-10 (restore drill itself
  upgraded 2026-08-24, see below).

## 2026-08-27 (second deploy) Snapshot

As of 2026-08-27 (second deploy), following a redeploy to `master` (`git reset --hard origin/master` + `docker compose build app-server scan-worker scan-worker-2 health-worker scheduler` + `docker compose up -d --no-deps --force-recreate` for those five), live inspection found:

- Host: `srv1675832` (`root@187.127.169.89`).
- Commit: `d90bd87`.
- Working tree: clean aside from the expected untracked `github-app/backups/` directory.
- 20 commits since the previous deploy tag (`github-app-deploy-2026-08-27`) - the headline changes:
  #426 caps `ProcessPoolExecutor` parallel-parse worker count to the real available CPU quota
  (cgroup-aware, not raw `os.cpu_count()`); #427 redesigns the marketing site and the hosted
  dashboard's sign-in/repo-picker/overview with a light glass theme (dark mode changed from
  OS-auto to an explicit `data-theme="dark"` opt-in); #429 fixes FastAPI endpoint-mapping missing
  the mount prefix entirely for `include_router(module.router, prefix=...)`-style calls (only bare
  identifiers were handled before), which fed both the dashboard's endpoint list and the hosted
  health-check monitor with wrong paths; plus #409's `stop_grace_period` fix (`docker-compose.yml`,
  30m30s on scan-worker/scan-worker-2, 11m on health-worker) and #410's spend-cap check-then-act
  race fix were both already live at the previous deploy's commit but are included here for
  completeness. See `github-app/CHANGELOG.md` for the full per-PR writeup.
- Rebuilt all five app-relevant services this time (`app-server` included, unlike the previous
  deploy) since both `app_server/frontend.py` and `app_server/demo_scan_api.py` changed alongside
  `scan_worker/jobs.py` and `scan_worker/live_wiki.py` - `demo-scan-worker` and
  `demo-sandbox-runner` were left untouched since neither's own source changed (they build from
  separate Dockerfiles, confirmed via `docker-compose.yml`, not assumed).
- Services running: same set as the previous snapshot, all `Up`; all five rebuilt services
  reporting Docker-healthcheck `healthy` within a minute of recreation.
- No pending migrations - `app-server`'s startup log shows `no pending migrations`.
- Post-deploy, verified live (not just that the deploy succeeded) by executing directly inside the
  running containers, not by re-reading the repo: `app_server.frontend`'s live source contains
  `data-theme="dark"` (the explicit opt-in, replacing the old `@media (prefers-color-scheme: dark)`
  auto-follow) and the light-glass sign-in background (`rgba(255, 255, 255, 0.65)`); `app_server.demo_scan_api`
  imports cleanly; `scan_worker.jobs` has `_run_scan`.
- Health checks: internal `/healthz` returns `200 {"status":"ok","checks":{"database":"ok","redis":"ok"}}`.
- No errors, tracebacks, or exceptions in `app-server`, `scan-worker`, `scan-worker-2`,
  `health-worker`, or `scheduler` logs in the 2 minutes after restart (targeted grep for
  `error|traceback|exception`).
- Not re-verified this pass (no relevant Dockerfile/host changes): Docker socket mount absence,
  non-root users, CPU/mem limits, backup cron execution, base-image digest pinning, restore-drill
  target availability, disk space - each last directly verified 2026-08-10 (restore drill itself
  upgraded 2026-08-24, see below).

## 2026-08-27 (first deploy) Snapshot

As of 2026-08-27 (first deploy), following a redeploy to `master` (`git pull origin master` + `docker compose build scan-worker scan-worker-2 health-worker scheduler` + `docker compose up -d --no-deps --force-recreate` for those four - `app-server` deliberately left untouched, see below), live inspection found:

- Host: `srv1675832` (`root@187.127.169.89`).
- Commit: `3b89249`.
- Working tree: clean aside from the expected untracked `github-app/backups/` directory.
- 5 commits since the previous deploy tag (`github-app-deploy-2026-08-26-2`) - three real bugs in
  `scan_worker/jobs.py`, found by a proactive dual-pass audit (this session plus a second,
  independent Claude session auditing the same file for a fresh set of eyes) rather than a bug
  report: #405 (free-tier Flash Review falsely claiming a diff was reviewed clean, and advancing
  `last_reviewed_sha`, when every free-tier provider actually failed mid-review), #406 (PR scans
  permanently polluting the persisted default-branch git graph with unmerged commits via
  `_sync_persistent_git_graph`), and #407 (the direct sibling of #406 - `_sync_code_graph` had the
  identical unconditional-`GRAPH_BRANCH="default"`-write bug, corrupting the durable code graph).
  See `github-app/CHANGELOG.md`'s 2026-08-27 entry for the full writeup.
- Only `scan-worker`, `scan-worker-2`, `health-worker`, and `scheduler` were rebuilt - all four
  share `Dockerfile.scan-worker` and actually execute `scan_worker/jobs.py`, and (same gotcha as
  every prior multi-image deploy) compose tags each as its own separately-built image despite the
  shared Dockerfile, so each needed an explicit rebuild. `app-server` also bundles a copy of
  `scan_worker/` in its image, but its own source was grepped directly: every reference to these
  job functions is a string literal handed to RQ's `queue.enqueue(...)` (job name resolved and
  imported by the *worker* process that dequeues it, never by `app-server` itself) - confirmed no
  rebuild was needed for this fix to take effect.
- Services running: same set as the 2026-08-24 snapshot below, all `Up`; the four rebuilt services
  reporting Docker-healthcheck `healthy` within seconds of recreation.
- No pending migrations - a code-only deploy, and `app-server` (the only service that runs
  `scripts/migrate.py`) wasn't even restarted this time.
- Post-deploy, verified live (not just that the deploy succeeded) by importing `scan_worker.jobs`
  directly inside the running `scan-worker` container and inspecting real source via
  `inspect.getsource`, not by re-reading the repo: `run_pr_scan_job`'s source contains no call to
  `_sync_persistent_git_graph(` or `_sync_code_graph(` (both #406 and #407's fixes), and
  `_run_flash_review`'s source contains the `if free_tier_exhausted["value"]: return False` bail-out
  added by #405, ahead of the comment-posting/`set_last_reviewed_sha` calls it used to reach
  unconditionally.
- Health checks: internal `/healthz` returns `200 {"status":"ok","checks":{"database":"ok","redis":"ok"}}`.
- No errors, tracebacks, or exceptions in `scan-worker`, `scan-worker-2`, `health-worker`, or
  `scheduler` logs in the 5 minutes after restart (targeted grep for `error|traceback|exception`).
- Not re-verified this pass (no relevant Dockerfile/host changes, and `app-server` wasn't touched):
  Docker socket mount absence, non-root users, CPU/mem limits, backup cron execution, base-image
  digest pinning, restore-drill target availability, disk space - each last directly verified
  2026-08-10 (restore drill itself upgraded 2026-08-24, see below).

## 2026-08-24 Snapshot

As of 2026-08-24, following a redeploy to `master` (`git reset --hard origin/master` + `docker compose build app-server scan-worker health-worker scheduler` + `docker compose up -d --no-deps --scale scan-worker=2` for those four), live inspection found:

- Host: `srv1675832` (`root@187.127.169.89`).
- Commit: `23a94ab`.
- Working tree: clean aside from the expected untracked `github-app/backups/` directory.
- 6 commits since the previous deploy tag (`github-app-deploy-2026-08-23`) - two real fixes plus
  docs. #369: live-wiki/docs incremental update jobs could reload evidence from a *different,
  newer* scan than the one that enqueued them (`get_latest_evidence` read "whatever's newest
  right now" instead of the exact row persisted by the enqueuing scan) - found by our own Flash
  Review dogfooded on #364 the day before. Fixed by threading the specific `repo_history` row id
  through the queue and reloading by that exact id (`get_evidence_by_id`). #370: the concurrency-
  relevant two of five remaining second-pass-audit findings - health-check-target and API-token
  creation were check-then-act under concurrent requests (#24), and `generate_token` re-derived
  its id via a racy re-query instead of using `create_api_token`'s own return value (#25) - fixed
  with the same advisory-lock-wrapped CTE pattern `add_installation_member_within_seat_limit`
  already used, new lock namespaces 4/5 deliberately chosen to avoid the existing namespace-3
  collision (a separate, unfixed finding, #30). The other three findings in #370 (#19, #22, #26)
  live in the `aletheore` CLI package, not this backend - they ship with the next PyPI release,
  not this deploy. See `github-app/CHANGELOG.md`'s 2026-08-24 entry for the full writeup.
- `--scale scan-worker=2` on `up -d` again recreated both replicas cleanly under their expected
  names, no orphan.
- Services running: same set as the 2026-08-23 snapshot below, all `Up`; the four rebuilt services
  and `scan-worker`'s second replica reporting Docker-healthcheck `healthy`.
- No pending migrations - a code-only deploy.
- Post-deploy, verified live (not just that the deploy succeeded): `scan_worker.db.get_evidence_by_id`
  exists and is callable; both `run_live_wiki_incremental_update_job`/`run_live_docs_incremental_update_job`
  take a `history_id` parameter; `app_server.db.add_health_check_target_within_limit`/
  `create_api_token_within_limit` exist with `HEALTH_CHECK_TARGET_LOCK_NAMESPACE == 4`/
  `API_TOKEN_LOCK_NAMESPACE == 5`; `app_server.admin.generate_token`'s source confirmed calling
  `create_api_token_within_limit` and no longer referencing `list_api_tokens` - all checked by
  importing directly / inspecting source in the running containers, not by re-reading the repo.
- Health checks: internal and public `/healthz` both return `200 {"status":"ok",...}`.
- No errors, tracebacks, or exceptions in `app-server`, `scan-worker-1`, `scan-worker-2`,
  `health-worker`, or `scheduler` logs in the 5 minutes after restart.
- Not re-verified this pass (no relevant Dockerfile/host changes): Docker socket mount absence,
  non-root users, CPU/mem limits, backup cron execution, base-image digest pinning, disk space -
  each last directly verified 2026-08-10. **Restore drill upgraded beyond "target availability"
  this same day (2026-08-24) - see the dedicated section below**, a real restore-and-verify, not
  just confirming a target database is reachable.

## 2026-08-23 Snapshot

As of 2026-08-23, following a redeploy to `master` (`git reset --hard origin/master` + `docker compose build app-server scan-worker health-worker scheduler` + `docker compose up -d --no-deps --scale scan-worker=2` for those four), live inspection found:

- Host: `srv1675832` (`root@187.127.169.89`).
- Commit: `f992751`.
- Working tree: clean aside from the expected untracked `github-app/backups/` directory.
- 5 commits since the previous deploy tag (`github-app-deploy-2026-08-22-2`) - triggered by a user
  report of the `ops_monitor.failed_jobs.scans` alert repeatedly hitting `support@aletheore.com`.
  Root-caused to two compounding bugs, both fixed this deploy: AIRview/Docs incremental updates
  sharing the PR/push scan job's 300s `job_timeout` and getting killed mid-flight by RQ on large
  repos (#364), and the ops/error alert cooldown being 900s (15min) instead of the intended 6
  hours (#365) - see `github-app/CHANGELOG.md`'s 2026-08-23 entry for the full writeup.
- `--scale scan-worker=2` on `up -d` again recreated both replicas cleanly under their expected
  names, no orphan.
- Services running: same set as the 2026-08-22 snapshot below, all `Up`; the four rebuilt services
  and `scan-worker`'s second replica reporting Docker-healthcheck `healthy`.
- No pending migrations - a code-only deploy.
- Post-deploy, verified live (not just that the deploy succeeded): `scan_worker.jobs.OPS_ALERT_COOLDOWN_SECONDS == 21600`,
  `app_server.error_alerts._ALERT_COOLDOWN_SECONDS == 21600`, `LIVE_WIKI_INCREMENTAL_UPDATE_JOB_TIMEOUT_SECONDS ==
  LIVE_DOCS_INCREMENTAL_UPDATE_JOB_TIMEOUT_SECONDS == 600`, and both `run_live_wiki_incremental_update_job` /
  `run_live_docs_incremental_update_job` exist and are callable - all checked by importing directly
  in the running `scan-worker` container, not by re-reading source.
- Inspected the `scans` queue's `FailedJobRegistry` directly (not assumed): found 5 stale entries
  predating this deploy (2 from an already-explained orphan-container artifact of the first
  2026-08-22 deploy, 3 from the timeout bug just fixed) - cleared all 5 so the new 6h cooldown
  didn't start by re-alerting on already-resolved history. Confirmed both monitored queues
  (`scans`, `health`) at `depth=0 failed=0` after clearing.
- Watched a live `run_ops_monitor_job` execution in `scan-worker`'s logs mid-verification (it runs
  every ~3min on the `scans` queue): one alert legitimately fired during the window before the
  stale registry was cleared (Resend `POST /emails` returned `200 OK`), then set a ~6h Redis
  cooldown key (`ops_monitor:alert_cooldown:ops_monitor.failed_jobs.scans`, confirmed via `TTL`)
  - the exact "one alert, then quiet" behavior #365 was meant to produce, observed directly rather
  than inferred from the diff.
- Health checks: internal and public `/healthz` both return `200 {"status":"ok",...}`.
- No errors, tracebacks, or exceptions in `app-server`, `scan-worker-1`, `scan-worker-2`,
  `health-worker`, or `scheduler` logs after restart, aside from the one expected ops-alert log
  line above.
- Not re-verified this pass (no relevant Dockerfile/host changes): Docker socket mount absence,
  non-root users, CPU/mem limits, backup cron execution, base-image digest pinning, restore-drill
  target availability, disk space - each last directly verified 2026-08-10.

## 2026-08-22 (second deploy) Snapshot

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

## Restore Drill (2026-08-24)

Previously only "the backup file gets created on schedule" (2026-08-10) and "the restore-drill
target database is reachable" (last checked with every deploy above) had been verified - neither
proves a restore actually *works*. This is the first real restore-and-verify:

Copied the latest real backup (`aletheore_app_2026-08-24T03-00-01Z.dump`, 35.7MB) off the
production server via `scp`, confirmed byte-identical transfer (`md5sum` matched server vs. local
copy before touching it), restored into a fresh, empty, throwaway local Postgres 16 container
(matching prod's Postgres version) via `pg_restore`. Verified against live production, not just
that the restore "looked" successful:

- All 49 tables restored, zero `pg_restore` errors.
- Row counts for 8 spot-checked tables matched live production exactly
  (`installations`, `api_tokens`, `repo_history`, `affiliates`, `affiliate_referrals`, `sessions`,
  `sent_emails`, `schema_migrations`).
- Actual values matched too: all 3 `installations` rows identical (id/login/plan); the restored
  snapshot's most-recent `repo_history` row confirmed (by exact timestamp) to still exist in live
  prod's full history - proving real continuity, not coincidentally-equal counts.
- Ran the app's real `scripts/migrate.py` against the restored DB: **"no pending migrations"** -
  the restored schema is genuinely current with what the running application code expects.
- Spot-checked a 273KB `evidence` JSONB blob for corruption: valid `jsonb_typeof`, real
  `aletheore_version` field intact.
- Local copy and throwaway container both destroyed immediately after verification - the dump
  contains real production data and wasn't left lying around.

**The backup-and-restore path genuinely works.** Re-run this drill if the backup script, Postgres
major version, or schema-migration tooling changes in a way that could affect restorability.

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

