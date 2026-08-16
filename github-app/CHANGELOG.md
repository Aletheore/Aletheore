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

## 2026-08-16

- **jina-embed now runs llama.cpp against a Q8_0 GGUF quantization of jinaai/jina-embeddings-v2-base-code,
  replacing the raw PyTorch/HuggingFace `transformers` backend.** Earlier the same day, two production
  incidents against that backend (a request that never finished within the 60s timeout, then an
  OOM kill under a mem_limit already raised to accommodate it) traced back to comparing the wrong
  things: nomic-embed-text, which this service replaced, was served by Ollama's llama.cpp engine -
  quantized weights, hand-tuned CPU kernels - while jina-embed ran unquantized in eager-mode PyTorch.
  Measured directly on this host against a real 133k-char, 38-chunk flask source sample: the old
  backend hadn't finished the first fifth of the batch after 164s before OOM-killing; llama.cpp
  finished the same batch in 24.55s (~5,400 chars/s) at a 375MB peak, against the old backend's
  2.44GB+. Quantization cost was checked directly too - 0.9997 cosine similarity against the
  full-precision embedding on the same input. `jina-embed`'s `mem_limit` comes back down from the
  emergency-raised 6000m to 2000m on this evidence.
- **`jina-embed` runs 2 independent model instances (`JINA_EMBED_INSTANCES=2`) instead of 1 instance
  parallelizing across 2 threads.** Same total CPU budget, differently spent: a single instance
  splitting one embedding call across threads pays real synchronization overhead inside llama.cpp's
  matmul kernels, while N single-threaded instances processing N requests concurrently pay none of
  that - pure task parallelism. Measured locally (4 concurrent streams of real `apache/thrift` source,
  `--cpus=2` both configurations): 2x1 threads finished in 223.27s against 1x2's 238.83s, ~6.5% faster
  on identical CPU. More importantly, it reduces queueing delay under concurrent load specifically -
  every caller (two `scan-worker` replicas, `demo-scan-worker`, hosted index builds) previously queued
  behind one locked instance even when their requests were otherwise fully independent, which
  contributed to a real `ReadTimeout` on a thrift-scale request (see the char-cap entry below).
  Memory checked under real concurrent load, not assumed: peaked at 620MiB, comfortably inside the
  existing 2000m limit.
- **`HOSTED_EMBED_MAX_CHARS` lowered from 130,000 to 60,000.** 130,000 was reasoned from a single
  isolated request measurement (24.55s, zero concurrent load) and caused a real failure: a thrift
  index build hit `ReadTimeout` at exactly 60.1s and lost 22 minutes of progress, because real latency
  under concurrent load is compute time plus queueing delay, not compute time alone. 60,000 leaves
  ~37s of margin against the 60s timeout at the measured ~2,630 chars/s real-world throughput, still
  3x the original 20,000 baseline. Should be revisited once the multi-instance change above has real
  concurrent-load evidence behind it, rather than another single-request extrapolation.

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
