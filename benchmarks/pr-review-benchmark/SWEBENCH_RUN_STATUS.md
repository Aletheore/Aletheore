# SWE-bench citation-grounding run — status / handoff

**Started:** 2026-08-15. **Owner:** Arihant + Claude (this session). Read this
top to bottom before touching anything if you're picking this up cold.

## Final result 2026-08-15: the benchmark's real signal is LLM proposal-rate
## noise, not the bug we fixed

Two full re-runs of the identical 25-case corpus (`--allow-empty` retriggers,
byte-identical diffs both times), both through fresh LLM calls (semantic
cache was not the confound - see below):

- **Run A** (before `_lookup_valid_lines`, PR #253): model proposed *something*
  in 14/25 cases (56%), 3 survived to be posted, 5 of the rejections were
  confirmed via production logs to be the exact-path-match bug (correct
  citations, wrong rejection reason).
- **Run B** (after PR #253, same corpus, same diffs): model proposed
  *something* in only 1/25 cases (4%) - 24/25 came back "No issues found in
  this diff" (the model itself proposed nothing, not "proposed and
  rejected"). The one proposal that did happen (PR #37) was legitimately
  out-of-scope (2 of 6 changed files were skipped from review context).

**The fix is confirmed correct** (code-reviewed, unit-tested, and its logic
is unrelated to how often the model proposes anything) - but this pair of
runs cannot demonstrate that on its own, because the model's raw proposal
rate swung from 56% to 4% between two runs of the *same* diffs. That's
LLM sampling variance dwarfing the effect being measured, the same
"[[project_judge_noise_floor]]" problem already documented for the judge -
single-run deltas on n=25 aren't evidence here either. Don't re-run this
specific comparison expecting a stable before/after number; the noise floor
is the finding.

**What would actually settle it**: replay the *same* cached raw model
output (captured from Run A, before grounding) through both the old and new
`_validate_findings` in a local test, deterministically, no LLM call
involved. That isolates the grounding-filter's effect from the model's own
variance. Not done in this session - Run A's individual raw proposals were
only ever recovered in fragments from grep'd production logs (5 of the 14),
not saved wholesale.

## Why this exists

We wanted a real number for "what fraction of Aletheore Flash Review's PR
citations are actually grounded in the code" (not `n=1`, which is all the
*existing* 3-case benchmark run had produced — see
`benchmarks/pr-review-benchmark/README.md` for that harness). The user
explicitly chose to source cases from **SWE-bench Verified** instead of
hand-authoring more cases, specifically *because* it's an external dataset
we don't control — "for actual results with no control from our side, will
show our worst side." Don't quietly swap back to a hand-picked or
convenience sample; the whole point is the sample is externally fixed.

## What "done" looks like

1. 25 real SWE-bench Verified bugs, each opened as a real PR, each with a
   real `aletheore[bot]` Flash Review comment.
2. Run `scripts.check_citations.verify_findings_against_checkout` (via
   `normalize_aletheore`, **after** the fix below) against all 25 to get a
   real location-grounding rate and content-grounding rate.
3. Report the number honestly, including the cases with zero findings
   (that's real data too, not a gap in coverage).

## Critical methodology bug found 2026-08-15 (post-deploy, post-retrigger)

After the `installation_spend_lock` scope fix (see below) was deployed, all
"coverage" checks this session — including ones reported as "17/25 done",
"25/25 done" — polled with:
```
gh api .../issues/$n/comments --jq '... | contains("aletheore-flash-review") ...'
```
**This is a false positive.** `upsert_pr_comment` edits the SAME comment in
place across every retrigger, and the `aletheore-flash-review` HTML marker is
present in the comment body **whether the current content is a completed
review OR a "Aletheore couldn't complete this flash review: canceling
statement due to lock timeout" failure message.** A PR whose *first*
retrigger succeeded and was later overwritten by a *second* retrigger's
failure still matches this check. Verified directly: **15 of 25 PRs'
current comment is a failure message**, not a real review, despite this
session having reported "25/25" complete.

**The fix**: check actual body content, not marker presence —
`grep -q "couldn't complete"` → FAILED, else look for either "No issues
found in this diff." / "No issues held up" / a `` `file:line` `` citation
bullet → REAL_REVIEW. Any future coverage check in this effort MUST use
content, not marker presence.

**Root cause of the underlying failures** (separate from the lock-scope bug,
confirmed via production Postgres logs): checkpoints on the prod host
(`root@187.127.169.89`, `github-app-postgres-1`) take 60-160s to write out,
recurring roughly every 5 minutes (`checkpoint_timeout` default), and every
observed `LockNotAvailable` failure timestamp falls inside a checkpoint
window. This is real infra signal, tracked separately (see task chip "Fix
Postgres checkpoint I/O causing lock timeouts" / spawned task
`task_f80ae561`) — not something more retriggering permanently fixes, just
reduces the odds of hitting per attempt.

## State as of last update

- [x] Downloaded SWE-bench Verified (500 instances, 12 Python repos) →
      `/tmp/swebench_verified.parquet` (not committed; re-download if gone,
      see "How to reproduce the sample" below).
- [x] Drew a **reproducible random sample of 25** (`pandas.sample(n=25,
      random_state=42)`) → `/tmp/swebench_sample_25.json`. Distribution:
      9 django, 5 scikit-learn, 4 sphinx, 4 sympy, 1 each
      astropy/matplotlib/xarray. **Seed is 42 — if this file is lost,
      re-run the sample command below and you'll get the identical 25.**
- [x] Validated the case-construction approach end-to-end, by hand, on one
      instance (`sympy__sympy-23534`) before scaling — confirmed the
      reverse-diff construction correctly reintroduces the real bug.
- [x] Fixed a real bug in `scripts/normalize.py`'s `normalize_aletheore`:
      it was checking a finding's *suggested fix* against the *current*
      code for content-grounding, which can only fail by construction (see
      git log / the Codex instructions file that was handed off for this —
      search for "normalize_aletheore" in recent commits to confirm this
      landed; if not, the instructions are preserved in this session's
      transcript and need to be re-applied before trusting any
      content-grounding number from this run).
- [x] Created `Aletheore/pr-review-benchmark-sandbox` (private) — replaces
      the old personal-account `ArihantK15/proctor-browser` scratch repo.
      **Not needed anymore**: confirmed the Aletheore GitHub App is
      installed **org-wide** on `Aletheore` (`repository_selection: all`,
      installation id 147514632) so any new repo under the org gets Flash
      Review automatically, no manual install step.
- [x] Forked the 7 source repos under the `Aletheore` org (durable push
      targets — can't push synthetic commits to the real upstream repos):
      `Aletheore/astropy`, `Aletheore/django`, `Aletheore/matplotlib`,
      `Aletheore/xarray`, `Aletheore/scikit-learn`, `Aletheore/sphinx`,
      `Aletheore/sympy`.
- [ ] **In progress, 21/25 PRs opened as of last check** (astropy ×1,
      django ×9, matplotlib ×1, scikit-learn ×5, sphinx ×4, xarray ×1 —
      all confirmed live on `Aletheore/pr-review-benchmark-sandbox` PR
      #1-#21). Currently cloning sympy for the final 4 instances. The
      Monitor watching for `PR:|ERROR|SKIP` lines went quiet even though
      it's working: Python block-buffers stdout when piped through `tee`,
      so log lines land in bursts, not real time. Don't assume "no
      output" means stuck — check `gh pr list --repo
      Aletheore/pr-review-benchmark-sandbox --state all` and `ps aux |
      grep run_swebench` for real signs of life first.
      `scratchpad/run_swebench_cases.py` running in the
      background (Bash task id `bmz4c15zm`, log at
      `.../tasks/bmz4c15zm.output`; a Monitor (task `b2md9z70s`) is
      tailing it for `PR:|ERROR|SKIP|Built [0-9]+/` lines). This builds
      all 25 cases into
      `benchmarks/pr-review-benchmark/cases/swebench-<short-id>/` and
      opens a real PR on `Aletheore/pr-review-benchmark-sandbox` for each.
      **Check `/tmp/swebench_cases_built.json` for the authoritative
      per-case result once it finishes** (case_id, instance_id, fork,
      fixed_commit, pr_url for each successfully-built case).
- [x] All 25 cases built, all 25 PRs opened — confirmed via
      `gh pr list --repo Aletheore/pr-review-benchmark-sandbox --state all`
      (PRs #1-#25). Authoritative record: `/tmp/swebench_cases_built.json`.
- [ ] **Waiting on production queue backlog, not a bug.** Each PR enqueues
      2 jobs (`run_pr_scan_job` + `run_flash_review_job`,
      `github-app/app_server/webhooks/pull_request.py`). 25 PRs × 2 = 50
      jobs landed on top of whatever else was already queued (66 total
      seen in `rq:queue:scans` at last check). Only 2 worker processes
      handle that queue (confirmed alive via `redis-cli HGETALL
      rq:worker:<id>` — both `state: busy`, recent heartbeats, not
      crashed), and a real Flash Review run has been measured at 5m50s in
      production (see the comment on `FLASH_REVIEW_JOB_TIMEOUT_SECONDS` in
      `pull_request.py`). At 2 concurrent workers this could realistically
      take 30-90+ minutes to fully drain. **Don't mistake "no aletheore[bot]
      comments yet" for broken** — check `docker exec
      github-app-redis-1 redis-cli LLEN rq:queue:scans` (declining number =
      draining) and worker `state`/`last_heartbeat` before assuming
      anything is stuck.
- [ ] A Monitor (task `b8mw95n5c`) is polling queue depth + comment count
      across all 25 PRs every 90s and will report `ALL_DONE` or
      `QUEUE_DRAINED`. If that Monitor is gone (session restart), just
      re-run the same poll loop, or manually check comment counts:
      `for n in $(seq 1 25); do gh api repos/Aletheore/pr-review-benchmark-sandbox/issues/$n/comments --jq '[.[] | select(.user.login=="aletheore[bot]")] | length'; done`
- [ ] Not started: running the grounding check across all 25 and reporting
      the real number.

## How the case construction actually works (read before changing anything)

The **existing 3 hand-authored `real_bug_fix` cases'** convention (see
`cases/001-flask-cli-key-quote/repo.txt` + `pr.diff`):
`repo.txt`'s `base_commit` is the **fixed** state (a real, upstream commit
after the bug was fixed). `pr.diff` is the **inverse of the real fix** —
applying it to `base_commit` reintroduces the bug, and that's the tree
that gets reviewed as "the PR."

SWE-bench's own schema is the opposite: its `base_commit` is the **buggy**
(pre-fix) state, and its `patch` field is the real fix diff. There is no
single canonical "fixed-state commit hash" in SWE-bench's data — it's
implied by `base_commit + patch`, not a real commit that exists anywhere.

So for each sampled instance, `scripts/run_swebench_cases.py`:
1. Clones the real upstream repo (cached per-repo under
   `scratchpad/swebench-run/source-clones/`, reused across all instances
   from the same repo — e.g. all 9 django instances share one clone).
2. Checks out SWE-bench's real `base_commit` (buggy).
3. `git apply`s SWE-bench's real `patch` (the fix) → now at the fixed
   state.
4. Commits that locally, and pushes it to the matching `Aletheore/<repo>`
   fork on a branch named `swebench/<instance_id>` — **this pushed commit
   is what `repo.txt`'s `base_commit` points at**, matching the existing
   convention exactly, just automated instead of a real historical commit
   hash.
5. `pr.diff` = `git diff HEAD HEAD~1` (fixed → buggy) computed from that
   same local commit, before it's discarded.
6. `ground_truth.yaml`'s `expected_file`/`expected_line` are parsed from
   **the reverse diff's `+` side** (the buggy/PR-under-review state's line
   numbers), **not** from SWE-bench's original `patch` (whose `+` side is
   the *fixed* state's numbering — using that would point at the wrong
   line entirely). This was a real bug caught and fixed before the first
   real run; if expected_line looks systematically wrong later, check this
   first.
7. Copies the case into `Aletheore/pr-review-benchmark-sandbox`'s
   `benchmark-sandbox/<case-id>/` (mirrors the existing convention from
   the old proctor-browser cases) and opens a PR there.

## How to reproduce the sample (if `/tmp` files are gone)

```bash
curl -sL "https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified/resolve/main/data/test-00000-of-00001.parquet" \
  -o /tmp/swebench_verified.parquet
python3 -c "
import pandas as pd
df = pd.read_parquet('/tmp/swebench_verified.parquet')
sample = df.sample(n=25, random_state=42).sort_values('repo')
sample.to_json('/tmp/swebench_sample_25.json', orient='records', indent=2)
"
```

## How to check on / resume the background run

```bash
# Live progress
tail -100 "/private/tmp/claude-501/-Users-arihantkaul-Documents-GitHub-Veridion/416e5ceb-2227-4fe5-bff0-4239553ed740/tasks/bmz4c15zm.output"

# Once finished, the authoritative per-case result:
cat /tmp/swebench_cases_built.json
```

If the background process died (e.g. session ended) before finishing all
25, **it's safe to just re-run** `scratchpad/run_swebench_cases.py` — it's
idempotent per-instance (force-pushes branches, `git checkout -B`, skips a
case if the golden patch doesn't apply). Cases already fully built (files
exist under `benchmarks/pr-review-benchmark/cases/swebench-*/`) will just
get rebuilt identically since the source data is fixed by the seed.

## Real bug found and fixed after the first full run (read this before trusting anything below the first measurement)

The first citation-grounding measurement across all 25 PRs came back
**0 citations in every single case**. That looked like either a
remarkable result or a broken pipeline — it was the latter.

**Root cause**: `run_swebench_cases.py`'s `open_sandbox_pr()` checked out
the buggy state in the source clone and had a comment claiming it
"materializes the actual (buggy) checkout tree under the case dir" —
but the code never actually copied those files into the sandbox PR
directory. Confirmed via `gh pr diff --name-only` on PR #25: every PR
only ever contained `repo.txt`, `pr.diff`, `ground_truth.yaml`,
`ground_truth.md` — 4 metadata files, zero real source code. Flash Review
correctly reviewed what it was shown (text/YAML files) and correctly
found nothing wrong with them. **That result measured nothing about
citation grounding — it measured this bug.**

**First fix attempt was also wrong**, and caught before pushing: rsync'ing
the entire cloned repo into the sandbox dir produced a 1,968-file, 839K-
insertion "PR" for what should be a one-line sympy bug. Checked this
against the real hand-authored case 001's actual PR
(`gh pr view 213 --repo ArihantK15/proctor-browser`): 1 changed file, 1069
additions. The existing convention only ever materializes the file(s) the
patch actually touches, at their real relative path — not the whole repo.

**Actual fix** (now in `run_swebench_cases.py` and
`fix_and_repush_cases.py`, both in `scratchpad/`): parse `diff --git a/X
b/X` lines from each SWE-bench patch to get the touched-file list, copy
only those files (at their buggy-state content) into
`benchmark-sandbox/<case-id>/<same-relative-path>`. Validated on
`swebench-sympy-23534` before scaling to all 25 again: 1 file changed, 925
insertions, confirmed via `gh pr diff 25 --name-only` and grepping the
diff body for the actual missing `cls=cls` bug. All 25 cases touch 1-3
files (`fix_and_repush_cases.py`'s preview output), consistent with real
PRs.

**Lesson, stated plainly since it cost a full wasted measurement cycle**:
this file's own earlier "How the case construction actually works"
section was itself under-specified — it never said explicitly that only
the *touched* files get copied in, not the whole tree, because the person
writing it (me) hadn't actually checked what the real methodology does
before describing it. Don't trust a methodology description in this file
without cross-checking it against an actual real PR when something
downstream looks off.

**Since each fix push is a `synchronize` action** (in
`pull_request.py`'s `ENQUEUE_ACTIONS`), it re-triggers Flash Review
automatically on all 25 PRs — no need to reopen anything. Repushed all 25
(24 actually changed; `swebench-sympy-23534` correctly no-op'd since it
was already fixed and validated first). All pushed clean, 1-3 files per
case, no warnings.

**Gotcha caught before it wasted a second measurement**: the first
attempt at a "wait for the re-review" poll checked "does any
`aletheore[bot]` comment exist" — which was already true for all 25 from
the *first, broken* review, so it would have reported done instantly
without actually waiting for anything. Fixed by capturing each PR's
comment `updated_at` as a baseline *before* the repush
(`/tmp/swebench_comment_baseline.txt`) and polling for that timestamp to
change, not just for a comment to exist. If re-deriving this baseline,
capture it immediately after the repush script finishes, before checking
progress.

## Second round: 21/25 genuinely re-reviewed, 3 hit a real production bug, 1 needed no retrigger

After the repush, polling (correctly, against the pre-repush baseline)
showed 21/25 with a genuinely new review and the queue empty. 4 were
missing:

- **PR #25** (`swebench-sympy-23534`): not actually missing — its baseline
  already reflected the fresh review from the earlier single-case
  validation (before the 25-batch even ran), and the batch script
  correctly no-op'd it (`nothing to commit`, identical content). Verified
  by reading its comment directly: real review, real grounding line
  (`_Grounding: 0 of 1 proposed finding(s) held up against this diff._`).
  No action needed.
- **PRs #1, #2, #3** (`swebench-astropy-14309`, `swebench-django-12262`,
  `swebench-django-12155`): genuinely never got a new review. Root cause
  found in production logs, not guessed: `run_flash_review_job` failed
  outright with `psycopg.errors.LockNotAvailable: canceling statement due
  to lock timeout` inside `installation_spend_lock`
  (`scan_worker/db.py:329`) — pushing 24 synchronize events for the same
  installation in quick succession created enough lock contention that
  several Flash Review jobs couldn't acquire the per-installation
  spend-lock in time and failed hard (no PR comment, no retry). **This is
  a real production bug**, tracked as task #205 (`Fix
  installation_spend_lock timeout under burst Flash Review load`) —
  separate from and not blocking this benchmark.

**Retrigger mistake, caught and fixed before it corrupted the
measurement**: first attempt appended a `retrigger: <timestamp>` line to
`ground_truth.md` to force a new `synchronize` event. This *worked* (new
review landed) but polluted the diff — Flash Review correctly flagged the
timestamp line itself as a benchmark-metadata leak into the diff, which
is a real, correctly-grounded finding, but not what this case is supposed
to measure. Fixed by reverting to the clean commit (`git reset --hard
HEAD~1` + force-push) and retriggering with `git commit --allow-empty`
instead — same effect (new synchronize event) with zero diff content
change. **If retriggering again for any reason, always use `--allow-empty`,
never a real file edit.**

As of the last check, PRs #1/#2/#3 are mid-flight again after the clean
retrigger: PR #3's comment has the evidence-diff half but not yet the
flash-review half (job still running); PR #2 looks like a genuine clean
re-review (`No issues held up... 0 grounded`, no mention of the old
pollution); PR #1 still showed the *old, polluted-content* review as of
the last direct check, meaning its clean retrigger hadn't been picked up
by a worker yet. A Monitor (task `by72gwrmn`) is polling
`/tmp/swebench_retrigger_baseline.txt` against fresh checks — importantly
it also verifies the `aletheore-flash-review` HTML comment marker is
present, not just that the comment changed, since PR #3 demonstrated a
comment can update with only the evidence-diff half landed.

## Root cause fixed at the source (not another retrigger)

After finding 14/25 PRs missing a flash-review comment (not the 3-4
originally thought — every burst of retrigger pushes recreated the same
concurrency spike), the user correctly redirected: stop hammering the
symptom with more spaced retriggers, fix the actual bug.

Root cause, confirmed in production logs (not guessed): `installation_spend_lock`
(`github-app/scan_worker/db.py:317`, 5s `ADVISORY_LOCK_TIMEOUT`) wrapped
the entire `_run_flash_review` call in `jobs.py` — a real LLM call plus
several GitHub API round-trips, measured up to 5m50s in production — when
the lock's actual job (per its own docstring) is just to make the spend
check-then-record cycle atomic. Any review queued behind another for the
same installation had no chance to wait; it failed outright with
`psycopg.errors.LockNotAvailable`.

**Fixed** in `github-app/scan_worker/jobs.py`: the lock is now acquired
twice, briefly — once for the initial cap check, once (inside
`_run_flash_review`) just for the final spend-record write — never around
the expensive work between them. Verified this preserves the original F25
invariant (`test_flash_review_job_records_spend_and_count_while_the_lock_is_still_held`
passes unchanged) and added a new regression test
(`test_flash_review_job_releases_lock_during_the_review_itself`) for the
actual gap that was missing coverage. Full test suite: 146/146 in
`test_jobs.py`, 702 passed / 3 failed (all 3 confirmed environmental, no
local redis/postgres in this sandbox, none reference `jobs.py`) across the
whole `github-app/tests/` suite.

**PR:** https://github.com/Aletheore/Aletheore/pull/251 (also bundles the
`normalize_aletheore` fix and this 25-case corpus, already committed
separately in the same branch for clean history). Same broad-lock pattern
also confirmed in `run_managed_audit_pr_job`/`run_managed_audit_api_job` —
not fixed here, tracked separately (task #205) so this PR stays scoped to
what's actually tested and verified.

**Once merged and deployed**, re-run the whole SWE-bench pipeline clean
from scratch — the fix should mean **zero** lock-timeout failures even
with all 25 PRs' worth of jobs landing at once, no spacing needed:

```bash
cd scratchpad
python3 run_swebench_cases.py   # rebuilds all 25 cases + opens 25 fresh PRs
# wait for Flash Review (should now succeed cleanly, no retriggers needed)
python3 measure_swebench_citations.py   # the real number
```

Before re-running, the 25 PRs from THIS run (`Aletheore/pr-review-benchmark-sandbox`
#1-#25, several polluted by mid-flight retrigger attempts before the
proper fix) should probably be closed/ignored rather than reused — cleaner
to start over once the fix is live, since several of those PRs have messy
git history from the retrigger churn documented above.

## Clean re-run in progress (post-fix)

Fix deployed to production and verified live:
- `scan-worker` rebuilt from `origin/master` at `112093a` (PR #251 merged)
  and restarted with `docker compose up -d --no-deps --scale scan-worker=2
  scan-worker` — confirmed both containers running the new code
  (`grep -c 'never around the expensive work' /app/scan_worker/jobs.py`
  returns 1 on both).
- Found and cleaned up a real deploy hygiene issue while verifying: an
  orphaned container `github-app-scan-worker-2-1` (different image,
  `github-app-scan-worker-2`, not the current `scan-worker` service at
  all) was still running 20h-old code alongside the two freshly-deployed
  replicas — leftover from before this got consolidated into one scaled
  `scan-worker` service. Stopped and removed; confirmed exactly 2 workers
  registered on the `scans` queue in Redis afterward (`rq:workers:scans`).

Old messy PRs (#1-#25 on `Aletheore/pr-review-benchmark-sandbox`, several
polluted by mid-flight retrigger attempts before the fix) all closed. The
already-correct case branches (`case-swebench-*`, unchanged — only the
production bug needed fixing, not the case content) were reopened as
fresh PRs **#26-#50** on the same repo. `/tmp/swebench_cases_built.json`
updated to point each case's `pr_url` at its new PR number.

A Monitor (task `bnjx8hdhz`) is polling `queue_depth` and flash-review
completion count across PRs #26-#50 every 45s. Expectation this time:
**zero** `LockNotAvailable` failures even with all 25 landing close
together, since the lock no longer holds through the review itself.

## Next steps once all 25 PRs exist and Flash Review has commented

1. Fetch each PR's `aletheore[bot]` issue comments (same pattern as
   `scratchpad/measure_citations.py` from earlier this session — adapt the
   `CASES` dict to read from `/tmp/swebench_cases_built.json` instead of
   the 3 hardcoded cases).
2. Run `normalize_aletheore` (make sure the suggestion-stripping fix is
   actually in `scripts/normalize.py` before trusting content-grounding
   numbers) → `verify_findings_against_checkout` per case.
3. Aggregate: total findings, location-grounding rate, content-grounding
   rate (with the checkable/uncheckable split reported honestly — don't
   collapse "uncheckable" into either verified or unverified).
4. Report the real number, including cases that got zero findings — that's
   real signal (Flash Review declining to comment on a diff isn't a gap in
   the measurement).

## Known open items / things NOT done yet

- The `normalize.py` suggestion-stripping fix was handed to Codex as
  instructions, not necessarily applied by this session directly — verify
  it actually landed (`git log -- benchmarks/pr-review-benchmark/scripts/normalize.py`)
  before trusting any content-grounding number.
- No comparison against repowise has started. The user mentioned wanting
  "correct answer token vs repowise" as a follow-on use for this same
  SWE-bench sandbox once citation grounding is done — that's a separate,
  not-yet-scoped piece of work, not part of this run.
- `benchmarks/pr-review-benchmark`'s original 3-case results (flask ×2,
  requests) are unrelated to this SWE-bench run and shouldn't be conflated
  with it in the final report — keep them as two clearly separate results
  if both get reported (different corpora, different sample sizes,
  different selection methodology).
