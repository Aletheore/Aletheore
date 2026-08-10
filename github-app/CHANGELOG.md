# github-app Changelog

Notable changes to the hosted GitHub App backend (`github-app/`), by deploy date. This is
separate from the root [`CHANGELOG.md`](../CHANGELOG.md), which tracks versioned releases of the
`aletheore` CLI package in `src/` — the backend has no version number of its own and ships
continuously via pull + rebuild, so its history is tracked here by date instead.

**Convention:** each production deploy is tagged `github-app-deploy-YYYY-MM-DD` (append `-N` for a
second same-day deploy). `git tag -l 'github-app-deploy-*'` gives the full list of deploy points;
`git log <prev-tag>..<tag>` gives the exact commit range for any one of them. For a live,
re-verified snapshot of exactly what's running in production right now, see
[`docs/operations/DEPLOYMENT-VERIFICATION.md`](docs/operations/DEPLOYMENT-VERIFICATION.md) — this
file is the history; that one is the current state.

## 2026-08-10

- Affiliate program: manually-onboarded creators get a Paddle discount code (10% off first month)
  that doubles as the attribution key. `subscription.created` now records a referral on free ->
  paid signup when the code matches a known affiliate; `transaction.completed` (previously
  unhandled) records 15% recurring commission per billing cycle. New internal admin routes
  (`/admin/affiliates`) behind a dedicated `AFFILIATE_ADMIN_TOKEN` for onboarding, reporting, and
  marking commissions paid. Payouts stay manual and off-platform.
- Self-serve data export (JSON snapshot of repos, findings, members, token labels, health
  targets), an admin action audit log covering member/token/webhook/setting changes, and an
  email-OTP requirement on top of the existing typed confirmation for delete-all-data — closing a
  stolen-session-cookie gap the typed confirmation alone didn't cover.
- Suppressed 45 secrets-scanner findings on our own repo that were all synthetic values in test
  fixtures or old planning docs — dogfooding noise, not real secrets.
- Self-serve data deletion extended to free-plan installations (previously paid-plan only), AIR
  evidence schema validation, webhook replay/duplicate protection (GitHub + Paddle), an MCP
  consent boundary gating tools that transmit repo evidence externally, and body-size/rate-limit
  controls on the two unauthenticated ingestion endpoints (`/v1/telemetry`, `/v1/runtime-events`).

## 2026-08-09

- Reliability: heartbeat-based hang detection with auto-restart, Docker healthchecks + an autoheal
  watchdog, and a staleness alert on the health-check sweep itself.
- Extra seat price bump to $4.99/mo with its LLM cap allowance decoupled to $3.00, fixing a
  zero-margin gap.
- Dismiss/mute findings on the hosted dashboard (per-repo, identity-keyed, survives re-scans).
- `.aletheore.json` repo config: ignored paths, disabled checks, severity threshold — the
  mechanism later used (2026-08-10) to clean up our own dogfooding noise.
- PR review and AIRview writing surfaces routed onto GPT-5.6 Luna; AIRview full-build frequency
  capped.
- Public security/trust page; rate limiting on `/auth/login` and `/auth/callback`; alerting on
  unhandled exceptions in our own backend.
- Self-serve Paddle billing portal link and LLM-spend/Flash-review usage surfaced in the
  dashboard.
- Hosted AI-generated Docs: single downloadable markdown export, optional commit of the reference
  into the customer's own repo, dashboard polish.
- Transactional email (welcome, payment-failed, subscription-canceled, branded templates) and a
  weekly usage digest, both via Resend; fixed a send timing out on httpx's 5s default.
- Flash Review: fixed a too-tight `job_timeout` silently killing most reviews; stopped
  double-fetching file content on every review.

## 2026-08-08

- Closed a redirect-following SSRF-adjacent gap and a queue-contention issue in health
  monitoring.
- Public status page shipped; two live monitoring bugs found in the process, fixed.
- Blocking Paddle API calls and `get_settings()` re-reads taken off the request hot path.
- Two real bugs found dogfooding our own scanner: worktree-corrupted dead-code detection, a Flash
  Review escaping false positive.

## 2026-08-07

- Hosted AI-enhanced Docs: per-symbol AI descriptions, nesting fix, 48h catch-up sweep for
  installations that missed a build window.
- Blocking GitHub API calls taken off the single event loop.
- Dashboard: stopped leaking raw Paddle error strings to users, explained what API tokens are for,
  fixed a no-op on an empty label.

## Earlier

Not tracked here — see `git log` for anything before 2026-08-07. This file starts from the point
the gap (no changelog for continuously-deployed backend changes) was identified and closed.
