# Global launch punch list (target: mid-September 2026)

Scope decided 2026-08-23: public "we're live" launch, affiliate videos firing
together, Instagram marketing starting at the same time. Payments and the
core product already work end to end. This list is everything left before
that coordinated push, ranked by what actually gates the date.

Affiliate outreach (first 19 candidates, Contact folder screenshots) sent
same day - see Sent folder in support@aletheore.com. Waiting on replies
before minting discount codes via /admin/affiliates.

---

## 1. Load testing - scan-worker and app-server done (2026-08-24), free-tier LLM burst not yet tested

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

**Not yet tested: free-tier Flash Review under a burst of new free
installations hitting Groq/Gemini/OpenAI-nano rate limits simultaneously.**
This is the one part of the original plan still open, and it's a genuinely
different bottleneck class (external provider TPM ceilings, which neither
more local compute nor a second server does anything for) - worth running
before closing this item out.

## 2. Second server as a launch buffer - reconsider given load test results

Original decision: stand up a second server (8GB RAM / 2 vCPUs) for the
first 2 months post-launch as a safety margin. The load test was meant to
settle whether this is needed; results above point away from it for the two
bottlenecks tested so far - neither scan-worker capacity nor app-server
capacity is compute/memory-bound on the current single server, and the one
real bottleneck found (Postgres write contention under app-server load)
would not be fixed by a second server anyway, only by a query-level change.

**Still open before finalizing "no second server":**
- The free-tier LLM-rate-limit burst test above hasn't run yet - if *that*
  turns out to be the real launch-day risk, it's also not something a
  second compute server fixes (it's an external provider ceiling), which
  would argue for skipping the second server entirely rather than just
  deferring the decision.
- If a second server is still wanted purely as basic redundancy (not a
  capacity fix), that's a separate, smaller decision than the one this load
  test was scoped to answer.

**Estimated effort:** half a day to provision and wire into the deploy
process, if still wanted after the free-tier burst test.

## 3. Congruency checks - scope to confirm

Read as: cross-system consistency checks - billing state matches Paddle's
actual state, affiliate commission records match real transactions,
installation plan matches what the dashboard shows the user, free-tier
counts match what's actually been consumed. Not yet scoped precisely;
first task here is nailing down the exact list of "these two things must
always agree" invariants worth checking before launch, then writing checks
for each.

**Estimated effort:** half a day to scope, 1-2 days to implement checks,
depending on what the scoping turns up.

## 4. 16 remaining findings from the second-pass audit - not launch blockers individually

Full detail in `docs/audits/Claude_Audit.md`, findings #19-22 and #24-35
(the 3 highest-severity ones, #17/#18/#23, are already fixed and deployed).
None of these are crashes; all are real, verified, silent-failure or
data-integrity bugs. Grouped by rough priority:

**Worth fixing before launch (real correctness bugs, cheap fixes):**
- #22: `cli audit --no-map-schema` is silently inert
- #24, #25: two check-then-act races in `db.py` (health-check-target/API-token
  limits, and `generate_token`'s racy id re-derivation)
- #26: `cli.py`'s `_fetch_whoami` exception gap
- #19: FastAPI router-prefix collection scope bug in `endpoints.py`

**Lower urgency (narrow trigger conditions, or affect scanning accuracy
more than product correctness):**
- #20 (cross-file endpoint-cache invalidation), #21 (Rust `pub use`), #27
  (Django `+=` routes), #28 (PHP grouped `use{}`), #29 (Java static
  wildcards), #30 (advisory-lock namespace collision), #31-33 (PHP alias/TS
  import-equals/Rust nested `use`), #34 (dashboard comment/ordering
  mismatch), #35 (Python/JS self-import filter gap)

**Estimated effort:** 3-4 days for the first group, the second group can
reasonably slip past launch and get picked off afterward.

## 5. Restore drill - not re-verified recently

`docs/operations/DEPLOYMENT-VERIFICATION.md` flags this as outstanding
across the last several deploys. Before a launch that's supposed to bring
in real paying customers at higher volume, need actual proof a restore
from backup works, not just that the backup file gets created on schedule
(that part was verified 2026-08-10).

**Plan:** spin up a throwaway Postgres instance, restore the latest real
backup into it, verify the data is actually intact and queryable (spot
check a few tables against known values from the live DB).

**Estimated effort:** half a day.

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

**Update 2026-08-24:** scan-worker and app-server load tests both ran, no
structural surprises - see item 1. Second server (item 2) is now leaning
"probably not needed" rather than "needed, just provision it," pending the
free-tier LLM burst test.
