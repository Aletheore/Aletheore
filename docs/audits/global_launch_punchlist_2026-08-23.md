# Global launch punch list (target: mid-September 2026)

Scope decided 2026-08-23: public "we're live" launch, affiliate videos firing
together, Instagram marketing starting at the same time. Payments and the
core product already work end to end. This list is everything left before
that coordinated push, ranked by what actually gates the date.

Affiliate outreach (first 19 candidates, Contact folder screenshots) sent
same day - see Sent folder in support@aletheore.com. Waiting on replies
before minting discount codes via /admin/affiliates.

---

## 1. Load testing - blocks the date, not started

Production is a single server (`srv1675832`, per
`docs/operations/DEPLOYMENT-VERIFICATION.md`), never load tested. A
coordinated launch is exactly the traffic shape that would find a capacity
problem first - simultaneous signups, simultaneous scans, simultaneous
free-tier Flash Review calls hitting Groq's 6,000 TPM ceiling.

**Plan:**
- Script a realistic signup + scan burst against a staging/local copy of the
  stack (or carefully against prod off-peak, load-test tooling TBD - k6 or
  locust are the obvious choices given this is mostly HTTP + webhook traffic).
- Specifically measure: concurrent installation onboarding, concurrent scan
  jobs (scan-worker queue depth under load), free-tier Flash Review under a
  burst of new free installations hitting Groq/Gemini/OpenAI-nano rate
  limits simultaneously.
- Find the actual breaking point, not just "it seemed fine."

**Estimated effort:** 2-3 days (script + run + analyze), assuming no major
capacity surprises. Longer if the first run finds a real bottleneck that
needs a code fix, not just more hardware.

## 2. Second server as a launch buffer - decided, not provisioned

Decision: stand up a second server (8GB RAM / 2 vCPUs) for the first 2
months post-launch as a safety margin, not a permanent architecture change.
Cheapest real hedge against a load-test surprise or a genuine launch-day
spike that the single-server setup can't absorb.

**Open questions before provisioning:**
- Same provider/region as the current box, or deliberately different for
  basic redundancy?
- Split by function (e.g. move scan-worker replicas there, keep app-server
  on the primary) or a hot standby / load-balanced pair?
- This decision should follow the load test, not precede it - the load test
  tells us whether 8GB/2vCPU is even enough, or whether the bottleneck is
  somewhere load balancing doesn't fix (e.g. Groq's per-key rate limit,
  which more compute does nothing for).

**Estimated effort:** half a day to provision and wire into the deploy
process, once the shape of what it needs to run is known from the load test.

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
