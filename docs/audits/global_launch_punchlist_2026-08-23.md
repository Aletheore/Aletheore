# Global launch punch list (target: mid-September 2026)

Scope decided 2026-08-23: public "we're live" launch, affiliate videos firing
together, Instagram marketing starting at the same time. Payments and the
core product already work end to end. This list is everything left before
that coordinated push, ranked by what actually gates the date.

Affiliate outreach (first 19 candidates, Contact folder screenshots) sent
same day - see Sent folder in support@aletheore.com. Waiting on replies
before minting discount codes via /admin/affiliates.

---

## 1. Load testing - three of four original scope items done (2026-08-24), one gap

Production is a single server (`srv1675832`, per
`docs/operations/DEPLOYMENT-VERIFICATION.md`). Rather than risk hitting it
directly, both load tests ran locally against the real code (`scan_repository()`
for scan-worker, a real `uvicorn app_server.main:app` process for app-server)
resource-matched to prod's exact `docker-compose.yml` limits, with real
Postgres/Redis.

**scan-worker (12-job burst, 8 small + 4 large real repos - one is Aletheore's
own 634-file codebase - each job isolated with zero cache reuse to model a
genuine "new install" cold scan, 2 concurrent workers matching `scan-worker=2`):**
- All 12 succeeded. Total wall time to drain the burst: 63s.
- Peak memory per job: 226-237MB against the actual 1GB per-worker cap -
  only ~23% utilized, well over 4x headroom.
- **Verdict: comfortable margin for this bottleneck at this burst size.**

**app-server (real HMAC-signed `push`/`pull_request` webhook deliveries,
concurrency swept 2 -> 50 -> 200, single uvicorn process matching prod's
exact startup command):**
- 2000 concurrent deliveries (concurrency=200): 100% success (0/2000
  failures), p50=135ms, p99=5.0s, sustained ~366 req/s. CPU never exceeded
  ~72% of the 1-CPU cap; memory peaked at 143MB against the 768MB cap.
- **The real ceiling isn't app-server CPU/memory - it's Postgres write
  contention on the webhook-delivery dedup table.** Counterintuitive finding,
  verified with a controlled fresh-table A/B: widening the default asyncpg
  pool (10 -> 50 connections) made throughput *worse* (366 -> ~103-135 req/s,
  p99 climbing to 6.5s), not better - more concurrent connections increased
  lock/WAL contention faster than they added parallelism, confirmed via
  Postgres's own container CPU staying under 12% throughout (not a raw
  Postgres compute limit). **Do not "fix" this by bumping the pool size** -
  if it ever becomes a real bottleneck, the fix is query/schema-level
  (e.g. moving delivery-cleanup out of the per-request hot path), not a
  bigger pool and not more hardware.
- Even the unmodified default (366 req/s sustained, 0 failures up to 2000
  concurrent deliveries) is orders of magnitude above any realistic
  launch-day webhook volume. **Verdict: not a capacity risk for launch.**

**Free-tier LLM burst (`writing_adapter_chain_for_free_tier` /
`run_with_free_tier_fallback`, `scan_worker/model_tiers.py`), a genuinely
different bottleneck class - external provider rate limits, not local
compute, so neither more CPU nor a second server touches it either way:**
- Deliberately did **not** hit the real Groq/Gemini/OpenAI/OpenRouter APIs -
  hammering shared free-tier quota from a load test risks degrading real
  users' reviews while claiming to test for exactly that failure mode, and
  today's exact headroom isn't independently knowable without burning real
  quota to find it. Instead, `openai.OpenAI` was mocked with per-provider
  rate limits as explicit, stated assumptions (Groq's 6,000 TPM is from
  this doc; the other three are reasoned estimates, not verified live
  numbers) - what's tested is the fallback chain's own correctness under
  real concurrency, using the real code path end to end. The one piece of
  genuinely shared state (OpenAI free-tier's Redis-backed daily-budget
  reservation, `_reserve_openai_free_tier_budget`) ran for real against
  real Redis, not mocked - that's the actual TOCTOU concern the code's own
  comments flag, worth exercising for real.
- **Realistic burst (30 concurrent new-install reviews):** 100% served (28
  by Gemini, 2 by Groq before its tight simulated cap kicked in), 0
  exhausted. Healthy.
- **Deliberately extreme burst (500 simultaneous, well beyond any plausible
  single-second launch spike):** cascade correctly reached all 4 providers
  (Gemini 333, Groq 2, OpenAI-FreeTier 18, OpenRouter 20 - hit its
  simulated 20-req/min cap exactly), but 127/500 (25.4%) got
  `FreeTierFallbackExhausted` once every provider's simulated limit was
  saturated at once. **The real-world question this leaves open is
  Gemini's actual headroom** - it absorbed the overwhelming majority of
  overflow in this simulation because it was assumed far more generous
  than Groq; if that assumption is wrong, the exhaustion rate at a genuine
  burst would look very different. Worth confirming Gemini's actual
  documented free-tier limits before trusting this number as more than
  "the fallback mechanism itself works correctly."
- **The Redis-backed daily budget stayed correctly bounded under real
  concurrent access in both runs** (574,000/2,400,000 tokens after the
  500-burst, zero overshoot) - the atomic-INCRBY design holds up under
  genuine thread-level concurrency, not just in isolation.
- **Verdict: the fallback mechanism itself is sound and degrades gracefully
  (errors out cleanly for the unservable fraction, doesn't corrupt shared
  state) even at an artificially extreme burst size; a realistic burst size
  shows no problem at all.** Not a launch blocker.
- **Follow-up checked (2026-08-24): can't be resolved further.** Google no
  longer publishes a static per-model RPM/RPD free-tier table on
  `ai.google.dev/gemini-api/docs/rate-limits` (confirmed live, page last
  updated 2026-08-18) - the page now just says "View your active rate
  limits in AI Studio," which is account-specific and sits behind auth we
  don't have for a generic "Gemini's free tier" number. Third-party
  aggregator sites still quote static RPM/RPD figures, but they read as
  stale/scraped SEO content, not an authoritative source - not citing them
  here. The assumed number in this test's simulation is therefore still an
  assumption, not a measured or sourced fact; doesn't change the "fallback
  mechanism is sound" verdict either way, since that held even at an
  artificially extreme burst size regardless of the exact ceiling. Also
  noted in passing, untouched: the code pins `gemini-3.5-flash`
  (`scan_worker/model_tiers.py:302`, `src/aletheore/cli.py:106`) while
  Google's current stable default is `gemini-3.7-flash` - not evaluated
  further, just flagged for whoever next touches model pins.

**Gap found dogfooding (Aletheore Flash review on PR #368, 2026-08-24) - now
closed (2026-08-24):** the original plan's four scope items were concurrent
installation onboarding, concurrent scan jobs, and free-tier LLM burst under
simultaneous new free installs - **concurrent installation onboarding was
never actually tested as its own path.** What was tested is two proxies for
it, not the thing itself: scan-worker capacity used `scan_repository()`
directly (bypassing the `installation` webhook, `handle_installation_event`,
and `run_initial_scan_job`'s full pipeline entirely), and app-server
throughput used `push`/`pull_request` events, never a signed `installation`
event. A real onboarding burst also triggers `run_live_wiki_full_build_job`/
`run_live_docs_full_build_job` for paid installs - full LLM builds, a much
heavier load than the incremental updates the free-tier burst test covered.

Timing reason this moved up: the first real affiliate deal closed the same
day (a creator, ~12.8K followers, $70/reel) with free paid-plan access
promised within days - a real fresh installation about to hit exactly this
untested path for the first time in the wild.

**Now run for real** (real signed `installation`(`created`) webhook
deliveries via HMAC-SHA256, against a real `uvicorn app_server.main:app`
process, real Postgres + Redis, two real `rq` workers on the `scans` queue
matching prod's `scan-worker=2`, 10 concurrent simulated new installations -
6 free, 4 paid to exercise the wiki/docs full-build branch - each cloning a
real local git repo built from this repo's own real source files):
- Faked only what a load test genuinely can't have: no real GitHub App
  installation exists to authenticate as, so repo enumeration, installation
  token exchange, default-branch sha lookup, and the raw `git clone`
  target were redirected to local `file://` repos and dummy tokens: same
  "can't hit what isn't real" category as the free-tier test's own mocking,
  not a new precedent. The LLM client
  (`aletheore.adapters.openai_compatible.OpenAI`) was mocked the same way
  the free-tier burst test mocked it - real retry/spend-budget code ran
  for real around a placeholder response, not real generated content.
- **10/10 webhook deliveries succeeded**, fired concurrently, ~62ms each
  (0.11s wall for all 10 - HMAC verification and routing are cheap).
- **10/10 real clone+scan+DB-write pipelines completed**, 38s wall to
  drain the full burst across 2 real concurrent workers (real `git clone`
  from local repos, real `_run_scan`, real `repo_history` rows).
- **All 4 paid installs' wiki AND docs full builds reached a real terminal
  "ready" status** - found and fixed one test-harness gap along the way:
  Docs' full-build path fetches each module's source via a *separate*
  GitHub Contents API call (`fetch_file_content`, not covered by the clone
  mock above); the first run correctly surfaced a real `401 Unauthorized`
  for all 4 (caught per-module, no crash, no hang, clear error message,
  correctly aggregated to `status=failed` since 0/N modules succeeded -
  the code's own error handling is sound here, this was a test-mock gap,
  not a product bug). Re-mocked that one additional call and reran: clean
  `ready` for all 4, no errors in either worker's logs.
- **Per-installation LLM spend tracking (`llm_spend`) stayed correctly
  isolated under real concurrent access** - 4 distinct, plausible,
  non-overlapping dollar amounts for the 4 paid installations building
  simultaneously across 2 processes, no cross-installation bleed, no
  negative/overshot values.
- **Verdict: the real, previously-untested pipeline (signed webhook -> real
  enqueue -> real clone/scan -> conditional full LLM builds) holds up
  cleanly under a realistic concurrent-onboarding burst.** Not a launch
  blocker; closes the one gap item 1 above was honestly left open on.

## 2. Second server as a launch buffer - leaning "not needed" after all three load tests

Original decision: stand up a second server (8GB RAM / 2 vCPUs) for the
first 2 months post-launch as a safety margin. The load test was meant to
settle whether this is needed; all three pieces (scan-worker, app-server,
free-tier LLM burst - item 1) now point away from it: none of the three
bottlenecks tested is compute/memory-bound on the current single server,
and the one real bottleneck found (Postgres write contention under
app-server load) would not be fixed by a second server anyway, only by a
query-level change. The free-tier LLM path is the one where "more compute"
was never going to be the fix regardless (external provider ceilings), and
its own fallback mechanism held up fine under a deliberately extreme
simulated burst.

**Checked and closed (2026-08-24):**
- The free-tier test's one real gap - Gemini's *actual* free-tier rate
  limit was assumed, not measured. Checked against Google's current docs:
  they no longer publish a static per-model table (account-specific,
  behind AI Studio auth as of 2026-08-18). Can't be resolved further
  without account access we don't have; doesn't change the "mechanism is
  sound" conclusion either way, since that held even at an artificially
  extreme burst size regardless of the exact ceiling. See item 1 for the
  full detail.
- If a second server is still wanted purely as basic redundancy (not a
  capacity fix), that's a separate, smaller decision than the one this load
  test was scoped to answer - worth deciding on its own merits if desired.

**Estimated effort:** half a day to provision and wire into the deploy
process, if still wanted after the free-tier burst test.

## 3. Congruency checks - scoped and audited (2026-08-24)

Five real, code-grounded invariants (dashboard plan display isn't a
separate one - it reads `installations.plan` directly, congruent by
construction):

1. `installations.plan` vs Paddle's actual current subscription status.
2. `installations` existence vs GitHub's actual installation list (no
   ghost row left by a missed `installation.deleted` webhook).
3. Paddle webhook destination's subscribed events vs what `paddle.py`
   actually handles.
4. `affiliate_commissions` vs real Paddle transactions (correct 15%-of-
   collected-total math, traceable to a real transaction).
5. `affiliate_referrals` vs the discount code genuinely used at checkout.

**Audited against live Paddle + live GitHub + live DB, all 3 real
installations that currently exist:**

- **Invariant 1, `ArihantK15` (free, has stored Paddle ids):** holds.
  Confirmed against live Paddle - the subscription genuinely is
  `status: canceled` (a failed payment converted it to free), so
  `plan=free` is exactly correct.
- **Invariant 1, `Aletheore/Aletheore` (plan=`air`):** initially flagged -
  neither its stored `paddle_subscription_id` nor `paddle_customer_id`
  resolve in live Paddle. **Confirmed intentional, not a bug**: this
  installation (and previously the founder's personal account, before its
  own trial's failed payment downgraded it - see the row above) was
  manually granted `air` for free, for dogfooding. Documented here so a
  future congruency check doesn't flag it as drift: this specific
  installation's paid access is *not* expected to be backed by a live
  Paddle subscription.
- **Invariant 2:** holds. All 3 DB installations confirmed still active on
  GitHub (`suspended_at` null for all three).
- **Invariant 3:** holds for the real ("platform" traffic-source)
  destination - its subscribed events exactly match what `paddle.py`
  handles. Found a second, stray "simulation"-traffic-source destination
  (`ntfset_01kzpj06abyhg1jc0b8pr6gne2`) also active and pointed at the
  real prod webhook URL. It couldn't actually inject fake data into
  production (it signed with its own distinct secret, which the app
  didn't recognize, so anything from it would have been rejected by
  `verify_paddle_signature`) - low severity, but dead/confusing config.
  **Deleted** (2026-08-24); confirmed via `notificationSettings.list` that
  only the real "platform" destination remains.
- **Invariants 4 & 5:** not yet exercisable - 0 affiliate commissions and
  0 referrals exist in production, since no discount codes have been
  minted yet (waiting on affiliate replies, per the top of this file).
  Code logic read and looks structurally sound (idempotent on
  `paddle_transaction_id`, correct 15%-of-collected-total math via
  `_handle_transaction_completed`) - real end-to-end verification has to
  wait until a real referred customer actually converts.

**Verdict: the invariants that could be checked right now all hold, once
the one genuinely intentional exception is accounted for.** No launch
blockers. The one cleanup item found (the stray simulation notification
destination) is already resolved; the remaining two invariants simply
can't be verified yet for lack of real data to check against.

## 4. 16 remaining findings from the second-pass audit - not launch blockers individually

Full detail in `docs/audits/Claude_Audit.md`, findings #19-22 and #24-35
(the 3 highest-severity ones, #17/#18/#23, are already fixed and deployed).
None of these are crashes; all are real, verified, silent-failure or
data-integrity bugs. Grouped by rough priority:

**Worth fixing before launch (real correctness bugs, cheap fixes) - DONE
(2026-08-24, PR #370, TDD'd tests for all five):**
- #22: `cli audit --no-map-schema` is silently inert
- #24, #25: two check-then-act races in `db.py` (health-check-target/API-token
  limits, and `generate_token`'s racy id re-derivation)
- #26: `cli.py`'s `_fetch_whoami` exception gap
- #19: FastAPI router-prefix collection scope bug in `endpoints.py`

**Lower urgency (narrow trigger conditions, or affect scanning accuracy
more than product correctness) - still open, deferred past launch:**
- #20 (cross-file endpoint-cache invalidation), #21 (Rust `pub use`), #27
  (Django `+=` routes), #28 (PHP grouped `use{}`), #29 (Java static
  wildcards), #30 (advisory-lock namespace collision), #31-33 (PHP alias/TS
  import-equals/Rust nested `use`), #34 (dashboard comment/ordering
  mismatch), #35 (Python/JS self-import filter gap)

**Estimated effort:** first group done. The second group can reasonably
slip past launch and get picked off afterward.

## 5. Restore drill - done and passed (2026-08-24)

Copied the latest real backup (`aletheore_app_2026-08-24T03-00-01Z.dump`,
35.7MB) off the production server via `scp`, confirmed byte-identical
transfer (`md5sum` matched between server and local copy before touching
it), restored it into a fresh, empty, throwaway local Postgres 16 container
(matching prod's Postgres version) via `pg_restore`, then verified against
live production rather than just checking the restore "looked" successful:

- All 49 tables restored, no `pg_restore` errors.
- Row counts for 8 spot-checked tables (`installations`, `api_tokens`,
  `repo_history`, `affiliates`, `affiliate_referrals`, `sessions`,
  `sent_emails`, `schema_migrations`) matched live production exactly.
- Actual values matched too, not just counts: all 3 `installations` rows
  identical (id/login/plan); the restored snapshot's most-recent
  `repo_history` row (by exact timestamp) confirmed to still exist in
  live prod's full history - proving real continuity between the backup
  and the live database, not coincidentally-equal counts. Live prod's 3
  newest `repo_history` rows postdate the 03:00 UTC backup (from today's
  deploy activity) - expected and correctly absent from the restored copy.
- Ran the app's real `scripts/migrate.py` against the restored DB:
  **"no pending migrations"** - the restored schema is genuinely current
  with what the running application code expects, not just structurally
  similar.
- Spot-checked a large `evidence` JSONB blob (273KB) for corruption:
  valid `jsonb_typeof`, real `aletheore_version` field intact.
- Local copy and throwaway container both destroyed immediately after
  verification - the dump contains real production data (installation
  logins, session state) and wasn't left lying around.

**Verdict: the backup-and-restore path genuinely works**, not just "the
file gets created on schedule" (verified separately, 2026-08-10).

---

## Rough sequencing against 3 weeks

1. Week 1: load test (item 1) - everything else about capacity depends on
   what this finds. Restore drill (item 5) in parallel, it's independent.
2. Week 1-2: provision second server if the load test says we need it
   (item 2). Start on the higher-priority audit fixes (item 4, first group).
3. Week 2: congruency checks (item 3) once scoped. Finish remaining
   priority audit fixes.
4. Week 3: buffer for whatever the load test or congruency checks turned up
   that wasn't anticipated, plus affiliate onboarding (discount codes for
   anyone who replied) and final go/no-go check.

Everything here assumes no major surprises. The load test is the one item
that could genuinely move the date if it finds something structural - it
should run first, not last.

**Update 2026-08-24:** all three load-testing pieces (scan-worker,
app-server, free-tier LLM burst) ran - see item 1. No structural surprises.
Second server (item 2) is now leaning "probably not needed" rather than
"needed, just provision it" - fully settled, with the Gemini rate-limit
follow-up checked and closed (unresolvable further without AI Studio
account access, doesn't change the conclusion).
