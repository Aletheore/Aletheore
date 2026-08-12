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

## 2026-08-12

- AIRview depth: file-level reference pages. Each important file now gets a sectioned page
  (Overview / Why it exists / How it works / Key symbols / Gotchas) hanging off the existing
  `files` JSON column, so there is no migration and no new table. The dashboard renders it as a
  collapsed "Reference" disclosure per file. Measured against RepoWise's wiki with a blind,
  order-swapped judge: the gap closed from 1.33 to roughly 0.2, at about one seventh their token
  cost. Harness and raw results at
  [Aletheore/aletheore-benchmarks](https://github.com/Aletheore/aletheore-benchmarks).
- Subsystem prose may now cite any file in the repository, not only files inside its own cluster.
  `verify_citations` already validated repo-wide, so the restriction added no safety while
  blocking exactly the cross-cutting explanations (request flow, lifecycle) that span subsystems.
- **The wiki's file list no longer depends on the model finishing its output.** It was whatever
  the model echoed back, so a large enough prompt silently shrank it — on Flask, records went from
  83 files to 14, stranding 23 already-generated file pages, since a page can only attach to a
  file entry that exists. The list is now built from the scan for every file in the brief and the
  model's prose merged onto it. Invented files are still dropped. This would have hit any large
  repository.
- File pages salvage instead of discarding. One unverifiable citation used to throw away a whole
  page of otherwise-verified prose; the offending lines are now removed and the remainder
  re-verified, dropping the page only if less than 60% survives. Subsystems already degraded this
  way; file pages now match. 28 of 31 pages kept on Flask.
- Clusters made entirely of tests, examples or docs no longer get a subsystem or an LLM call.
  Measured across eight repositories, 334 subsystem clusters became 87 — 74% fewer calls. serde
  was the extreme case at 160 -> 10. Mixed clusters are kept, and a repo that is all tests keeps
  everything rather than producing an empty wiki.
- The AIRview cache key now carries a prompt version. It depended only on the scan, so editing any
  writing prompt would have silently served pages written by the previous prompt forever.
- Dashboard markdown for generated pages escapes before promoting any tag, so repository content
  reaching the browser through a model's output cannot become live HTML.

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
