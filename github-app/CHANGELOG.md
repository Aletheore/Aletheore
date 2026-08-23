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

## 2026-08-23

- **AIRview/Docs incremental updates were sharing the PR/push scan job's 300s `job_timeout`,**
  and RQ's watchdog killed the whole scan job mid-flight on large repos once the writing stage
  ran long - "Work-horse terminated unexpectedly" in the `scans` queue. Decoupled both updates
  into their own separately-timed jobs (`run_live_wiki_incremental_update_job` /
  `run_live_docs_incremental_update_job`, 600s each), enqueued instead of called inline, reloading
  evidence from the DB rather than passing it through the queue (#364).
- **The ops/error alert cooldown was 15 minutes, not the agreed 6 hours.** Confirmed against the
  live inbox: `ops_monitor.failed_jobs.scans` re-alerted roughly every 15-30 minutes throughout
  2026-08-22 while the timeout bug above kept the `scans` queue's failed-jobs count continuously
  above threshold. Both `OPS_ALERT_COOLDOWN_SECONDS` and `error_alerts._ALERT_COOLDOWN_SECONDS`
  were still at the original 900s from #286; bumped both to 6 hours so a persisting issue gets one
  alert and periodic reminders, not one every cycle (#365). Cleared 5 stale entries from the
  `scans` `FailedJobRegistry` post-deploy (2 an orphan-container artifact from the first 08-22
  deploy, already explained in that day's changelog entry; 3 the timeout bug above) so the new
  cooldown didn't start by re-alerting on already-resolved history.

## 2026-08-22 (second deploy)

- **Three crash/broken-feature bugs from the second-pass audit, all live in production:**
  one invalid-UTF-8 byte anywhere in a scanned repo aborted the *entire* scan
  (39 unguarded `.decode()` calls in the scanner, all now `errors="ignore"`); AIRview's
  Q&A path crashed with a `TypeError` on its own best-case (dual-retriever) match, because
  RRF fusion silently dropped the vector distance score on any chunk found by both
  retrievers; AIRview's fallback for non-scanned files (docs, configs, Dockerfiles) called
  a function name that was never imported (`NameError`, silently caught) and 404'd on every
  request (#360).
- **Nothing alerted when a free-tier provider key went missing.** The gap found and fixed
  earlier today (see the "config, not a code deploy" entry below) had no monitoring - a new
  ops-monitor check now alerts per-provider (Groq/Gemini/OpenAI-FreeTier/OpenRouter) within
  minutes instead of staying silent for weeks (#357).

## 2026-08-22

24 commits accumulated since the 8/21 deploy tag and shipped together in this one:

- **AIRview writing surface now always uses deepseek-v4-flash, never GPT-5.6 Luna**, regardless of
  `OPENAI_API_KEY` availability - a benchmark re-run (5 language corpora, blind judge) found
  DeepSeek beats Luna specifically for this comprehension-writing surface, the opposite of what
  holds for PR review and coding benchmarks elsewhere, so the switch is scoped narrowly to AIRview
  via a new `writing_adapter_for_airview` (#352).
- **Architecture clustering no longer counts test files as subsystems.** Reproduced on
  AutoMapper/AutoMapper: 82% of the dependency graph was test files, fragmenting what should have
  been a handful of subsystems into 119 near-singleton clusters. Fixed by excluding test paths
  before clustering, the same filter already used for retrieval (#353).
- Spend-cap check-and-record was two separate lock acquisitions for both fix-suggestion and
  AIRview live-wiki spend budgets - a race that could let concurrent calls both pass the cap
  check before either recorded usage. Fixed via atomic reservation (#331, #332).
- AIRview banner still claimed incremental updates use a fast model after that had changed (#350).
- Healthcheck sweep exited 0 and printed no summary even when every endpoint was unreachable
  (#349); CI never actually booted the app-server/scan-worker images before this - the same class
  of gap that caused the #246 crash-loop incident could have shipped silently again (#342).
  Real end-to-end integration test added for the health-check sweep (#351).
- Audit's sponsor panel claimed nothing left the machine after evidence was actually sent out
  (#348).
- Local search index now detects an embedder swap even when the new embedder happens to produce
  the same dimensionality (#347); three other retrieval-quality regressions in `search_index.py`
  fixed (#340); embedding-cache rows now carry the embedder identity that produced them, closing
  the gap the above fix needed (#343, migration `049_purge_cache_for_embedder_switch.sql`).
- Flash review similarity cache retained raw PR diffs indefinitely instead of expiring them
  (#344).
- Three CLI UX gaps found while auditing for more issues like the update-notice one (#346); bare
  CLI invocation now surfaces an available update (#345); `mcp-install` prints a copyable
  `claude mcp add` command for Claude Code (#341).
- FastAPI router mounted implicitly alongside a prefixed mount lost its own unprefixed endpoint
  (#339); router-mount prefixes could cross-contaminate between files (#333).
- Schema-mapper silently corrupted on ordinary SQL comments (#338).
- Secret scanner missed dotted-attribute credential assignments (#334).
- Java/C# pre-parsed trees stayed pinned in memory for the whole scan instead of being released
  (#337).
- Module-overview chunk boundary used the wrong "first" symbol (#336).
- Unpinned marker-qualified PEP 508 dependency was silently dropped (#335).
- Benchmark numbers on the public site updated to the current, re-verified figures: 40.5ms mean
  retrieval latency (was 125ms) vs RepoWise's 52.5ms, and 2.00 vs 1.77 average comprehension score
  across 5 language corpora (#354).

## 2026-08-22 (config, not a code deploy)

- **Free-tier Flash Review provider keys added to production.** `writing_adapter_chain_for_free_tier`
  (the Groq -> Gemini -> OpenAI-FreeTier -> OpenRouter fallback chain, hardened across #304/#314/
  #316/#319/#344) has been fully implemented and deployed for weeks, but production's `.env` never
  actually had `GROQ_API_KEY`/`GEMINI_API_KEY`/`OPENAI_FREE_TIER_API_KEY`/`OPENROUTER_API_KEY` set -
  every provider silently skipped, the chain built empty, and every free-tier Flash Review no-op'd
  with only a log warning (`jobs.py`'s `if not free_tier_chain: ... return False`), no user-facing
  error. Found while re-verifying this session's other changes were actually live. Keys were present
  locally in `github-app/.env` but had never been synced to the server - copied over (values never
  passed through any tool output or log), `scan-worker` (both replicas) and `health-worker`
  restarted, confirmed live via `has_api_key()` boolean checks (never the raw values) returning
  `True` for all four providers. This is a config change, not a code deploy - no new commit or
  `github-app-deploy-*` tag for it, same as the Paddle webhook destination note below.

## 2026-08-19

- **AIRview and Docs generation cut down to a fraction of their prior LLM call volume.**
  Subsystem and file-page writing for AIRview now batch 5 items per call instead of one, with
  per-item retry only on the items that fail (batch size chosen conservatively - a prior
  experiment merging more content into single prompts silently dropped subsystem coverage from
  83 files to 14 on Flask). Docs' incremental-update path had zero per-call spend gating (a large
  push had no dollar ceiling at all); it now uses the same `_IncrementalSpendBudget` gate the
  full-build path already had, and its two-call-per-module generate/polish pass merged into one.
  Estimated worst case for a full AIRview build: ~$8.40 -> ~$1-2; a large Docs push: uncapped ->
  hard-capped by the shared monthly spend budget.
- **Docs no longer re-describes every symbol in a file on every push that touches it.** A new
  `content_hash` column on `docs_symbols` lets a symbol be skipped if its source snippet is
  unchanged since it was last described - previously, touching one function in a 20-function file
  re-sent all 20 to the LLM, every push, with no memory of prior generations.
- **Health-fix-suggestion no longer re-fires on a flapping endpoint.** A 30-minute cooldown
  (`was_recently_down`) means one down/up/down incident produces one LLM-generated suggestion, not
  a fresh one on every flip - matching how Sentry groups repeat issues rather than re-alerting on
  each occurrence.
- **AIR's PR review cap raised from 300 to 500/month; free tier held at 150.** This is a
  usage-promise ceiling, not a cost-protection measure - the separate dollar-based spend cap is
  unaffected and remains the real defense against a pathological per-review cost.
- **Flash Review now defaults to "compact" evidence (diff + Aletheore's own context, no raw
  file-content dump) instead of including full file contents in the prompt.** A 3-run real
  Luna-generates/DeepSeek-verifies benchmark (see `aletheore-benchmarks/pr_review/README.md`,
  Experiment 4) found compact held 96.7-97.7% independently-verified accept rate across every run
  while full-context swung 85.7-100% and never once won, using a fraction of the prompt tokens.
  Citation verification still uses the real fetched file content; only the prompt-facing blob is
  dropped.
- **Removed the CLI's anonymous usage-ping endpoint (`/v1/telemetry`) from the backend.** It was
  the single most exposed, unauthenticated write path in the service. See the root
  [`CHANGELOG.md`](../CHANGELOG.md)'s 0.8.13 entry for the CLI-side half of this change.

## 2026-08-18

- **Fixed a diff-parser collision that silently dropped real findings.** `_diff_valid_lines`
  recovers per-file boundaries from a `--- {filename} ---` marker `fetch_pr_diff` inserts into the
  flattened diff text. A deleted line whose content is `-- x ---` (any SQL/Lua/Haskell comment, or
  a `--- section ---` divider in any language) arrives in that text as `--- x ---` after the diff's
  own `-` prefix — indistinguishable from a real marker. The real change following it got
  attributed to a phantom file and dropped from the valid-line set, surfacing as "No issues found
  in this diff." Fixed by requiring a marker to sit at an actual file boundary (start of text, or
  immediately after a blank line — the only place a real one is ever placed).
- **Scanner walked the repo tree 19 times per scan, six of them unpruned.** Six detectors (migration
  dirs, docker-compose, kubernetes manifests, terraform files, helm charts, declared env vars) each
  called `repo_path.rglob(...)` independently and filtered `IGNORED_DIRS` out afterward — walking
  into `node_modules`/`.git`/`vendor` on every scan and discarding what they found. Replaced with
  one shared pruned `os.walk`; verified byte-identical output before and after, ~7.75x faster on
  this repo.
- **Ops-monitor alerts re-fired on every ~3-minute check with no cooldown.** A month-old,
  since-fixed health-check-sweep bug (`c34aa6c`) had left 186 failed jobs sitting in the "scans"
  queue's `FailedJobRegistry` that nothing ever cleared — the alert had been silently re-firing on
  that same stale condition ever since, 918 emails accumulated in Spam before this was caught.
  Fixed with a Redis cooldown centralized in `_send_ops_alert` itself, so every ops-alert source
  gets it automatically. Cleared the 349 stale failed jobs on production after verifying each
  failure category first — some already fixed by this same deploy, the rest resolved
  historical/external incidents (GitHub's own API outage, not a code bug).
- **Raised Flash Review's context-depth caps for the paid tier.** Doubled eight constants governing
  how much of a PR's changed-file content, referenced definitions, and blast-radius symbols get
  analyzed — real headroom was there and unused (current worst case ~56,000 tokens against Luna's
  1,050,000-token window, pricing flat to 272,000). One real corpus case
  (`swebench-django-14434`) had its changed file entirely excluded from review under the old
  per-file byte cap; it's included now.

## 2026-08-17

- **`app-server`'s own httpx client to `jina-embed` raised from a 60.0s timeout to 120.0s.**
  It was the actual binding constraint underneath the whole hosted-embedding path: the CLI's own
  client to `app-server` (`src/aletheore/search_index.py`) has used a 120.0s timeout all along, but
  `app-server` was cutting itself off against `jina-embed` at half that budget. Raised alongside real
  per-token timing evidence measured directly against `jina-embed` post-multi-instance (#267, 2
  instances): 88,859 tokens took 124.70s, close to the new ceiling rather than an untested
  extrapolation past it.
- **`/v1/embeddings` now caps concurrent hosted-embed requests, not just requests per hour.**
  The existing rate limiter throttled request *count per window*, which did nothing about several
  requests landing on `jina-embed` at the same moment - it runs `JINA_EMBED_INSTANCES=2`, each
  single-threaded, so a third concurrent request just queues behind a lock until one frees up,
  inside whatever's left of the 120s timeout above. A Redis sorted-set semaphore now admits at
  most `MAX_CONCURRENT_HOSTED_EMBED_REQUESTS` (default 2, matched to `JINA_EMBED_INSTANCES`) at
  once, refusing the rest with 429 and a short `Retry-After` (3s, not the rate limiter's hour) -
  self-healing against a crashed holder via a TTL slightly above the 120s request timeout, so a
  process that dies mid-request leaks its slot for at most that long, never permanently. The CLI's
  `embed_texts_hosted` now retries on 429 (bounded, capped sleep) before falling back to local
  embeddings or failing an in-progress index build, so the common case - a momentary capacity blip
  under concurrent load - costs a short wait instead of degrading the result.

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
