# Before-Launch Fixes

Audit of everything shipped since the last checkpoint: PR #291 through #299
(`37db9b1..16bd077` on `master`) — free-tier Flash Review routing, the
citation-matching fixes, AIRview/Docs LLM call-volume cuts, shipping
"compact" as the Flash Review default, CLI telemetry removal, pricing page,
and changelogs.

Method: 5-agent multi-angle review, then every finding below was
independently re-verified by reading the actual source and, where relevant,
the exact diff that introduced it. One candidate finding (a claimed Redis
hard-dependency in the free-tier adapter chain) was investigated and
**ruled out** — `get_redis_client()` and `Redis.from_url()` are lazy, and
`run_with_free_tier_fallback` catches `Exception` broadly per-provider, so a
Redis blip degrades gracefully. It's omitted below since it isn't real.

Ranked most severe first.

---

## 1. Free-tier Flash Review monthly cap is check-then-act, not atomic — can overshoot

**RESOLVED 2026-08-20.** Fixed together with Batch 3 finding #1 and finding
#4 below — see the note there for the shared fix (atomic
`reserve_flash_review_count`/`reserve_llm_spend` in `scan_worker/db.py`,
wired into `run_flash_review_job` in place of the lock-then-later-write
pattern). PR: `fix/installation-spend-cap-atomic-reservation`.


**File:** `github-app/scan_worker/jobs.py:1449-1451` (check) vs `:1679` (increment)

The count check is locked:
```python
with installation_spend_lock(settings.database_url, installation_id):
    if get_flash_review_count_this_month(...) >= MAX_FREE_TIER_FLASH_REVIEWS_PER_MONTH:
        return
```
but `increment_flash_review_count()` doesn't run until line 1679, inside a
**separately re-acquired** lock, after the entire unlocked review (diff
fetch, LLM call, GitHub round-trips) completes. Two PRs pushed to the same
free-tier installation close together — landing on different scan-worker
replicas — can both read `count=149` (under the 150 cap), both pass, and
both proceed. This overshoots `MAX_FREE_TIER_FLASH_REVIEWS_PER_MONTH` by
however many land in the window.

The adjacent comment (jobs.py:1440-1448) explicitly claims this "closes...
the same class of race" as `model_tiers._reserve_openai_free_tier_budget` —
but that fix is a genuinely atomic `INCRBY` reservation *before* the call,
not a locked read-then-later-write. The comment overstates what was fixed;
the TOCTOU gap is real.

**Fix:** reserve the count atomically the same way the OpenAI daily-token
budget does (e.g. an atomic increment against a Redis/DB counter at
check-time, released/trued-up on completion) instead of locking only the
read.

---

## 2. OpenAI free-tier daily token reservation is never released or trued-up on a failed call

**RESOLVED 2026-08-20.** Confirmed the fallback chain itself was never the
problem — `run_with_free_tier_fallback` already correctly moves on to the
next provider (Groq → Gemini → OpenAI-FreeTier → OpenRouter, in that
order) when an OpenAI-FreeTier attempt fails, so no individual review
actually fails from this. The bug is narrower and quieter than that: the
leak is in OpenAI-FreeTier's own Redis-tracked daily token counter, which
stays permanently decremented by 130,000 per failed attempt regardless of
how the chain recovers — after enough real failures in a day, that
provider silently drops out of the chain for the rest of the day against
zero real usage.

Added `on_call_failed: Callable[[], None] | None = None` to
`OpenAICompatibleAdapter.__init__`, invoked in `simple_completion`'s
`except` branch (not when `_ensure_budget_for_next_call` itself declines
up front — that path already self-releases). Wired
`on_call_failed=lambda: _true_up_openai_free_tier_reservation(redis_conn, 0)`
for the OpenAI-FreeTier adapter specifically in
`writing_adapter_chain_for_free_tier`. Deliberately a dedicated hook, not
a reuse of `on_usage(0, 0)`: that would misrepresent a failed call as a
completed one to any other `on_usage` consumer.

Regression tests: `src/tests/test_openai_compatible_adapter.py`'s
`test_simple_completion_calls_on_call_failed_when_the_call_fails`,
`test_simple_completion_does_not_call_on_call_failed_on_success`,
`test_simple_completion_does_not_call_on_call_failed_when_budget_is_declined_up_front`;
`github-app/tests/test_model_tiers.py`'s
`test_openai_free_tier_on_call_failed_releases_the_reservation` and
`test_openai_free_tier_simple_completion_releases_reservation_on_a_failed_call`
— confirmed all 5 fail without the fix and pass with it. PR:
`fix/openai-freetier-reservation-leak`.

**Files:** `src/aletheore/adapters/openai_compatible.py:305-332`,
`github-app/scan_worker/model_tiers.py:95-101`

`simple_completion` reserves `OPENAI_FREE_TIER_RESERVATION_TOKENS` (130,000)
via `_ensure_budget_for_next_call()` **before every attempt** (line 306).
`on_usage` — the only thing that calls `_true_up_openai_free_tier_reservation`
to correct the placeholder to real usage — fires only at line 330-331,
strictly *after* a successful response. Any exception during the call
(line 326-329) turns into `AdapterInvocationError` without `on_usage` ever
running.

After ~18 consecutive failed OpenAI-FreeTier attempts in a day
(18 × 130k ≈ 2.34M, against the 2.4M `OPENAI_FREE_TIER_DAILY_TOKEN_CAP`),
the counter is artificially exhausted and the provider gets excluded for
the rest of the day despite having consumed zero real tokens. This is
plausible in practice — a rotated/expired key or an outage produces exactly
this failure pattern, and it's silent (just fewer providers in the
fallback chain, easy to miss).

**Fix:** release the reservation (call `_true_up_openai_free_tier_reservation`
with `real_total_tokens=0`, or a dedicated release helper) in the
`except Exception` branch of `simple_completion`, not only on success.

---

## 3. A fully-exhausted free-tier fallback still burns one of the installation's monthly review slots

**RESOLVED 2026-08-20.** `_run_flash_review` now returns whether a real
review actually ran; `run_flash_review_job` releases the count/spend
reservation in a `finally` block whenever it didn't (free-tier fully
exhausted, no provider keys configured, or any other exception before
completion) — see finding #1's resolution note.


**Files:** `github-app/scan_worker/flash_review.py:941-959`,
`github-app/scan_worker/jobs.py:1679`

When every free-tier provider fails, `review_diff` catches
`FreeTierFallbackExhausted` and sets `raw_output = "[]"` by design — "no
findings, not a crash" (flash_review.py:944-959). `jobs.py` has no way to
distinguish that from a genuinely clean diff at line 1679, so
`increment_flash_review_count()` runs unconditionally. A review that
produced nothing because of a full free-tier infrastructure outage still
consumes one of the installation's 150 monthly free-tier slots — compounding
with #1 in an outage window.

**Fix:** thread a signal (e.g. have `review_diff` return whether it
degraded via `on_free_tier_exhausted`, which is already wired) up to
`_run_flash_review` and skip the count increment (though probably still
record $0 spend) when the review never actually ran.

---

## 4. Docs incremental-update spend recording lost its lock — real regression from PR #294

**RESOLVED 2026-08-20.** The fix went to the actual shared root cause
(`_IncrementalSpendBudget`, used by managed audits, AIRview full builds,
and this docs-incremental path alike) rather than re-adding a lock to this
one call site: `can_start_next_call()` now reserves atomically per call via
`reserve_llm_spend` (a single UPSERT...WHERE...RETURNING, re-reading the
live DB total every time, same primitive `model_tiers._reserve_openai_free_tier_budget`
already used for the OpenAI daily-token cap) instead of comparing an
in-memory `current_spend` snapshot taken once when the budget object was
created. `record_usage()` then trues up the reservation to the real cost.
Proven under genuine concurrent load, not just unit-mocked: see
`test_scan_worker_db.py`'s `test_reserve_llm_spend_is_atomic_under_real_concurrency`
(30 real threads racing a $5 cap in $0.50 reservations against a real
Postgres instance — exactly `sum(cap/reserve)` succeed, never more).
PR: `fix/installation-spend-cap-atomic-reservation`.


**File:** `github-app/scan_worker/jobs.py:3719-3727` (confirmed against the
exact diff in commit `ecb4508`)

Before:
```python
spend_accumulator = {"total": 0.0}
...
with installation_spend_lock(dsn, installation_id):
    record_llm_spend(dsn, installation_id, spend_accumulator["total"], monthly_cap=monthly_cap, feature="docs_incremental")
```
one accumulator, written once, under a lock. After:
```python
spend_budget = _IncrementalSpendBudget(dsn, installation_id, update_model, current_spend, monthly_cap, feature="docs_incremental")
def _on_usage(prompt_tokens, completion_tokens):
    spend_budget.record_usage(prompt_tokens, completion_tokens)
```
`_IncrementalSpendBudget.record_usage` (jobs.py:2933-2938) calls
`record_llm_spend` directly, per module, **with no lock at all** — and its
`can_start_next_call()` cap check compares against `current_spend`, a
snapshot taken once at job start (line 3720) plus an in-process
`spent_this_job` counter. Two concurrent jobs against the same installation
(a Docs incremental update overlapping a Flash Review or AIRview build)
each hold stale snapshots and can't see each other's spending mid-run,
letting them jointly overshoot `monthly_cap`. This pattern already exists
elsewhere (`_run_managed_audit`, AIRview full-build path) — it isn't unique
to Docs — so the fix should be systemic, not local to this call site.

**Fix:** either have `record_usage()` take `installation_spend_lock` itself
around the DB write, or re-read `current_spend` from the DB under a lock at
each `can_start_next_call()` check instead of trusting an in-process
snapshot.

---

## 5. `openai_compatible.py`'s hardcoded cap-exceeded message misreports the OpenAI free-tier daily cap as a monthly spend cap

**RESOLVED 2026-08-20.** Added `budget_exceeded_message: str = "the
monthly LLM spend cap would be exceeded"` to `OpenAICompatibleAdapter.
__init__` (the correct default for every other `before_llm_call` wiring,
e.g. `jobs.py`'s `spend_budget.can_start_next_call`), used in both
`_ensure_budget_for_next_call` (the `simple_completion` path) and
`invoke`'s equivalent check. The OpenAI-FreeTier adapter construction in
`writing_adapter_chain_for_free_tier` now passes
`budget_exceeded_message="the daily free-tier token allowance would be
exceeded"`. Regression tests:
`src/tests/test_openai_compatible_adapter.py`'s
`test_simple_completion_raises_the_default_budget_message_when_before_llm_call_declines`,
`test_simple_completion_raises_a_custom_budget_message_when_before_llm_call_declines`,
`test_invoke_raises_the_custom_budget_message_when_before_llm_call_declines`;
`github-app/tests/test_model_tiers.py`'s
`test_openai_free_tier_budget_exceeded_message_names_the_daily_allowance_not_the_monthly_cap`
— confirmed all fail without the fix and pass with it. Full `src/tests`
(1,297) and `github-app` (verified clean in isolation after ruling out
unrelated cross-session DB contention) suites green. PR:
`fix/openai-freetier-cap-message`.

**File:** `src/aletheore/adapters/openai_compatible.py:464-469`

```python
def _ensure_budget_for_next_call(self) -> None:
    if not self._has_budget_for_next_call():
        raise AdapterInvocationError(
            f"{self.name} stopped before starting the next model call because "
            "the monthly LLM spend cap would be exceeded"
        )
```
`model_tiers.py` wires `before_llm_call=lambda: _reserve_openai_free_tier_budget(redis_conn)`
into the OpenAI-FreeTier adapter — this is a **daily token-count** cap, not
a monthly dollar cap. When it trips, the error text says "monthly LLM
spend cap," and on total free-tier exhaustion this reaches on-call via
`_send_ops_alert` (jobs.py:1605). An engineer paged with "monthly spend cap
exceeded" would misdiagnose a healthy daily-allowance rollover as a billing
problem.

**Fix:** parameterize the message (or give the OpenAI-FreeTier adapter its
own `before_llm_call` error text) so it says "daily free-tier token
allowance" when that's actually what tripped.

---

## 6. Subsystem cache lookups became strictly sequential, working against this PR's own goal

**RESOLVED 2026-08-20.** `generate_subsystems` now runs the per-cluster
cache lookups through `_run_concurrently` (same bounded pool as the write
phase), gathering `(brief, cluster, name, cached_record)` results in
input order before partitioning into `records_by_id`/`targets` — order
and failure-propagation semantics unchanged (`_run_concurrently` already
guarantees both). Regression test:
`github-app/tests/test_live_wiki.py`'s
`test_generate_subsystems_overlaps_cache_lookups_instead_of_serializing`
(12 clusters, 150ms cache-lookup latency each) — confirmed it measures
~1.85s without the fix (fully sequential) and comfortably under 0.9s with
it. Full `github-app` suite green. PR:
`fix/live-wiki-cache-lookup-concurrency`.

**File:** `github-app/scan_worker/live_wiki.py:845` (confirmed against the
`ecb4508` diff)

Before: one thunk per cluster did cache-lookup-then-write, and all thunks
ran concurrently via `_run_concurrently` (`MAX_GENERATION_WORKERS = 6`).
After: the cache lookup was pulled out into a plain `for brief in briefs:`
loop (line 845) that runs to completion *before* the batched-write phase
starts; only the write phase is still concurrent. On a repo with 30-40
subsystem clusters, that's 30-40 sequential blocking cache round-trips
added to the front of every build — working against the stated goal of
cutting call volume / build time in the same PR. (This doesn't add extra
LLM calls, just serialized latency ahead of them.)

**Fix:** run the cache-lookup phase concurrently too (same
`_run_concurrently` helper, just for lookups instead of full
build-and-write), then partition into `records_by_id` / `targets` from the
gathered results.

---

## 7. Duplicated free-tier/cache wiring in `_run_flash_review`

**RESOLVED 2026-08-20.** Confirmed by direct comparison the two blocks
had grown byte-identical except for one line (`code_evidence_context=
code_evidence_context`), and `review_diff`'s own signature already
defaults `code_evidence_context: str = ""` — so passing the empty string
explicitly is behaviorally identical to omitting it. Collapsed the
`if code_evidence_context: ... else: ...` split into a single
unconditional `review_diff(...)` call, simpler than the audit's suggested
kwargs-dict extraction since the two branches turned out to have no real
divergence left to build a dict around. Verified via existing
`test_jobs.py -k "flash_review or review_diff"` (26 passed, unchanged
behavior) and full `github-app` suite. PR: `fix/flash-review-wiring-dedup`.

**File:** `github-app/scan_worker/jobs.py:1626-1670`

The two `review_diff()` call sites (with vs. without
`referenced_symbol_context`) duplicate ~25 lines of identical free-tier
chain / cache-lookup / cache-write wiring, including an identical
explanatory comment about why free-tier skips the cache. Any future change
to how free-tier interacts with the cache has to be made twice; the
duplicated comment already signals the two are meant to stay in lockstep,
which is exactly the setup for silent drift.

**Fix:** extract the shared kwargs into a dict/builder and pass
`referenced_symbol_context` (or its absence) as the one real variable.

---

## 8. Subsystem and file-page batch-write-with-retry logic duplicated near-verbatim

**RESOLVED 2026-08-20.** Extracted a generic `_run_batched_with_retry`
helper (chunk targets, run chunks concurrently, retry only what didn't
resolve, up to N rounds) that both `_generate_subsystem_records_for_targets`
and `_generate_file_pages_for_targets` now share. The two callers'
per-round accumulation genuinely differs (subsystem only keeps a first
success; file-pages must remember the *last* result with a usable detail
across failed retries, for salvage — a later retry that fails outright
with no detail must not erase an earlier round's salvageable one), so the
helper takes an `on_round_result(target_id, result)` callback rather than
trying to force one accumulation policy on both — each caller owns its
own merge logic, the helper only owns the loop/chunk/concurrency/retry
mechanics. Regression test:
`test_generate_file_pages_batched_salvage_survives_a_later_detail_less_retry`
— confirmed it fails when that "don't erase" guard is broken and passes
with it. Full `test_live_wiki.py` (61 passed) and `github-app` suite
green. PR: `fix/live-wiki-batch-write-dedup`.

**File:** `github-app/scan_worker/live_wiki.py:635-711` (`_write_subsystem_batch`
/ `_generate_subsystem_records_for_targets`) vs `:1145-1230`
(`_write_file_page_batch` / `_generate_file_pages_for_targets`)

Both pairs implement the identical shape — loop up to N attempts, chunk
remaining targets, run chunks concurrently, merge results by id, shrink
`remaining` to failures — with separately-named, unsynchronized
batch-size/attempt constants and only the per-item payload/result type
differing. A future change to retry policy or failure logging has to be
made twice by hand.

**Fix:** generic-ize over the target/result type (a small
`TypeVar`-parameterized helper) so both call sites share one
implementation.

---

## 9. New health-fix cooldown reinvents a cooldown primitive that already exists in this codebase

**RESOLVED 2026-08-21.** Replaced `was_recently_down`'s Postgres
row-history query (a DB round-trip per down-flip against
`endpoint_health`) with the same Redis key-+-TTL cooldown shape
`_send_ops_alert` already uses. Added
`_health_fix_suggestion_cooldown_key`/`_recently_suggested_a_fix`/
`_mark_fix_suggestion_sent` (built on the existing `_set_with_expiry`
primitive) and threaded `redis_conn` through both call sites
(`_run_health_check_sweep_for_target`, `run_health_check_down_retry_job`).
Deleted the now-unused `was_recently_down` from `db.py`. Regression test:
`test_sweep_skips_fix_suggestion_when_endpoint_was_recently_down` updated
to pre-populate the real cooldown key in a `_FakeRedis` instance (rather
than mocking a function that no longer exists), directly exercising the
Redis-backed mechanism. Full `github-app` suite (1,449 passed, 8 skipped)
green. PR: `fix/health-fix-cooldown-redis`.

**File:** `github-app/scan_worker/db.py:628` (`was_recently_down`, a bespoke
Postgres query against `endpoint_health`)

`jobs.py`'s `_send_ops_alert` (line 2614) already implements "a Redis key +
TTL gating a repeat alert" as the shared cooldown mechanism other
ops-alert sources use (referenced directly in comments elsewhere in this
same diff, e.g. jobs.py:1602-1604). The new health-fix cooldown instead
adds a separate, single-purpose SQL query tied to `endpoint_health`'s row
history — slower (a DB round-trip per check vs. a Redis read) and
functionally redundant. The next feature needing "don't re-fire an
expensive action for a flapping condition" now has two incompatible
cooldown implementations to pick between.

**Fix:** back the health-fix cooldown with the same Redis-key-+-TTL
primitive `_send_ops_alert` uses, or factor that primitive out so both
call sites share it.

---

## Suggested priority before launch

1, 2, 3 are real correctness/financial-integrity gaps (cap overshoot, phantom
budget consumption, phantom review-count consumption) — fix before launch,
they compound with each other under any concurrent-load or outage scenario.
5 is a one-line message fix that avoids paging on-call with the wrong
diagnosis. 4 is a real regression reintroducing a race that used to be
locked — fix alongside 1/2/3 since it's the same class of bug. 6, 7, 8, 9
are maintainability/perf cleanups, not launch blockers, but cheap to take
now while the code is fresh.

---
---

# Batch 2 — PRs #273, #274, #279–#284, #286, #287, #290

(`d89f1de..37db9b1` on `master`.) Deterministic blast-radius context, the
scanner performance fix, the diff-marker collision fix (#283), the git-intel
malformed-timezone crash fix (#281), Flash Review's 5 new deterministic
semantic checks (#279), the ops-alert cooldown (#286), context-depth caps
(#287), hosted-embed concurrency/char-cap tuning (#273/#274), and the live
demo display fix (#290). Dependency-bump and pure-docs commits in this range
were skipped.

Same method as Batch 1: multi-angle review, then every finding below
independently re-verified by reading the actual source (not just trusting
the review agent's report — this run hit a session API limit partway
through and several of its own verification sub-agents were cut off with no
conclusion, so I re-derived and re-checked every item that didn't already
have a completed verification).

**Three things worth naming on their own before the item-by-item list:**

- **The same unfixed marker-collision bug in three more places.** #283
  fixed a real file-marker collision in `flash_review.py`'s
  `_diff_valid_lines`, but the identical unguarded `--- X ---` regex match
  exists unfixed in three other places that parse the same diff format —
  two of them in production code (`semantic_checks.py`'s
  `_diff_hunks_by_file`, and `flash_review.py`'s own
  `build_change_impact_context`), one in the benchmark harness. The fix
  landed in one of four call sites that needed it. Findings 1-3 below.
- **Two deterministic semantic checks that silently get defeated for
  their most realistic trigger case.** The resource-leak check (finding 4)
  cites the `open()` line, which the downstream grounding filter then
  drops as "too far from the diff" whenever open and the removed `close()`
  are more than ~8 lines apart — the single most common real shape of this
  bug. The iterator-reuse check (finding 6) scans the whole file instead
  of the hunk, directly contradicting that module's own documented design
  ("every condition below is checked against that hunk's own
  removed/added lines, never the whole file's"). Both ship a check that
  looks like coverage but silently isn't there for the realistic case —
  worse than no check, since it's false confidence.
- **A real regression in the just-shipped ops-alert cooldown fix (#286).**
  Keying the cooldown on `source` alone (finding 8) means
  `_check_backup_freshness`'s three distinct, differently-worded,
  differently-severe alert conditions share one cooldown key — so one
  firing silently suppresses the other two for 15 minutes. The fix for
  "same condition re-alerts forever" introduced "different conditions get
  conflated," for the one call site with more than one condition per
  source.

---

## 1. `semantic_checks.py`'s diff parser has the same file-marker collision that #283 just fixed in `flash_review.py` — unfixed

**RESOLVED 2026-08-20** (branch `fix/diff-marker-collision-sibling-parsers`).
Fixed together with findings #2 and #3 below — all three got the identical
`prev_blank` boundary guard `_diff_valid_lines` already used. Regression
test: `test_semantic_checker_survives_a_removed_line_shaped_like_a_file_marker`
in `test_flash_review.py`.


**File:** `github-app/scan_worker/semantic_checks.py:89-93` (`_diff_hunks_by_file`)

```python
file_match = _FILE_MARKER_RE.match(line)
if file_match:
    current_file = file_match.group(1)
    current_hunk = None
    continue
```
matches `_FILE_MARKER_RE` (`^--- (.+) ---$`) on every line, unconditionally.
Compare `flash_review.py`'s `_diff_valid_lines` (line 708-711), fixed by
#283 specifically for this collision:
```python
prev_blank = True  # start-of-text counts as a boundary
...
file_match = _FILE_MARKER_RE.match(line)
if file_match and prev_blank:
```
A real removed/added source line that happens to read `-- something ---`
renders, once diffed, as the raw line `--- something ---` — indistinguishable
from a genuine file-section marker without the boundary guard. In
`_diff_hunks_by_file` this resets `current_file`/`current_hunk` mid-parse,
silently truncating the current file's hunk or misattributing subsequent
removed/added lines to the wrong file — which then feeds wrong evidence
into every deterministic check in this module (resource-leak, bounds-clamp,
iterator-reuse, SQL-injection, etc.), the exact class of bug #283 was
written to close, just not ported to this sibling parser.

**Fix:** add the same `prev_blank` boundary guard here.

---

## 2. `flash_review.py`'s own `build_change_impact_context` has the same unfixed collision, in the same file as the fix

**RESOLVED 2026-08-20** — see finding #1's resolution note. Regression
test: `test_build_change_impact_context_survives_a_removed_line_shaped_like_a_file_marker`.


**File:** `github-app/scan_worker/flash_review.py:373-374`

```python
if raw_line.startswith("--- ") and raw_line.endswith(" ---"):
    current_file = raw_line[4:-4]
    continue
```
No `prev_blank` guard, no boundary check at all — even less guarded than
semantic_checks.py's version (a plain substring/prefix-suffix check, not
even a regex anchor). Same collision: a diff line whose content happens to
start with `--- ` and end with ` ---` flips `current_file` mid-parse, so
subsequent removed/added lines and the `_CHANGE_IMPACT_PATTERNS` matches
built from them get attributed to the wrong file in the change-impact
signals block shown to the reviewing model. This is the third occurrence
of the identical unfixed pattern (with #1 above and #3 below) — the fix
landed in exactly one of four call sites that needed it.

**Fix:** same `prev_blank`-style guard, or better, have all diff-format
parsers in this codebase share one hardened tokenizer instead of each
reimplementing the `--- {file} ---` / `@@ ... @@` scan separately.

---

## 3. Benchmark harness has the same unfixed collision — silently drops real diff lines from evaluation

**RESOLVED 2026-08-20** — see finding #1's resolution note. This one's
collision was a different shape (filtering real git-diff header lines by
prefix, not matching the internal `--- {file} ---` marker), so the fix is
a `seen_first_hunk` positional guard instead of `prev_blank`, but the same
underlying principle: real headers only ever appear in a fixed position,
never scattered through hunk bodies. Regression tests in the new
`benchmarks/pr-review-benchmark/tests/test_evaluate_semantic_checks.py`.


**File:** `benchmarks/pr-review-benchmark/scripts/evaluate_semantic_checks.py:73`
(`git_diff_to_review_format`)

```python
if line.startswith(("index ", "--- ", "+++ ")):
    continue
```
Converts real `git diff` output into Aletheore's internal `--- {file} ---`
review format. A genuine removed source line rendered as `-` + `-- old
constant` = `--- old constant` matches `startswith("--- ")` and is silently
dropped from `body_lines` before it ever reaches `find_semantic_regressions`
— indistinguishable from a real `--- a/path` header line. This is
benchmark/eval tooling, not production, so it doesn't affect real Flash
Reviews — but it does mean this harness silently under-reports what the
deterministic checks actually catch whenever a benchmark case's diff
happens to touch a line shaped like a `---`-delimited comment or literal,
which is a determinism/precision problem for a tool whose whole job is
producing a trustworthy accuracy number.

**Fix:** same boundary-guard fix, applied here too.

---

## 4. Resource-leak findings cite the `open()` line, which the grounding filter then drops for any function longer than ~16 lines

**RESOLVED 2026-08-20** (branch `fix/semantic-checks-hunk-scoping`). Now
cites `hunk.new_start` (always inside the diff by construction) instead of
`open_line`; the open() location is still surfaced, just in the `issue`
text rather than as the finding's citation. Regression test:
`test_semantic_checker_cites_the_hunk_not_the_far_away_open_call_for_a_resource_leak`
(open() call 15+ lines from the removed close, well outside grounding
tolerance, asserts the cited line is the hunk's, not the open call's).


**File:** `github-app/scan_worker/semantic_checks.py:305-320`
(`_resource_leak_findings`)

```python
open_match = next((m for m in _RESOURCE_OPEN_RE.finditer(source) if m.group(1) == var), None)
open_line = _line_number(source, open_match.group(0)) if open_match else None
...
findings.append(_finding(file, open_line, f"{var} is opened here, but the changed code removed its Close() call..."))
```
The finding cites where the resource was **opened**, not where the diff
actually removed the `Close()` call (which is where `hunk.new_start`/
`hunk.new_end` are). These findings flow into the same grounding filter as
every other finding (`flash_review.py:892-904`, `find_semantic_regressions`
→ `_validate_findings`), which drops anything more than
`DIFF_LINE_TOLERANCE` (= 8) lines from any diff-touched line
(`_line_is_near_diff`, flash_review.py:743-744). The single most common
real shape of this exact bug — a resource opened near the top of a
function and its `Close()` removed further down, e.g. during a refactor —
gets silently discarded by the very validation step this check's own
findings are supposed to pass through, whenever open and the removed
close are more than ~16 lines apart. This defeats the check for its most
realistic trigger case, silently (no log, no different UX — it just never
appears).

**Fix:** cite the line of the removed `Close()` call (available via the
hunk, e.g. `hunk.new_start`) instead of the open line, or if the open
line is more informative for a human, cite the close-call line as
`finding["line"]` and mention the open line only in the `issue` text.

---

## 5. Bounds-clamp findings can cite an unrelated line for a common variable name

**RESOLVED 2026-08-20.** Added `_line_number_near_hunk` (scoped to the same
`DIFF_HUNK_TOLERANCE` window every proximity check in this module already
uses) and swapped it in at every citation call site in the file, not just
this one — 8 call sites total shared the identical whole-file-first-match
bug. Regression test:
`test_semantic_checker_cites_the_hunk_not_an_earlier_unrelated_assignment_for_a_removed_bounds_clamp`.


**File:** `github-app/scan_worker/semantic_checks.py:400-408`
(`_removed_bounds_clamp_findings`)

```python
findings.append(_finding(file, _line_number(source, f"{var} =") or hunk.new_start, ...))
```
`_line_number` (line 119-123) does a linear top-to-bottom scan of the
**whole file** and returns the line of the *first* substring match — not
the occurrence inside the hunk that actually triggered the check, and
with no scoping to `hunk`'s range at all. For a common variable name
(`result`, `count`, `total`, `value`) that's assigned earlier anywhere
else in the same file — extremely common in any file longer than a
handful of functions — this cites that unrelated earlier line instead of
the real one. The `or hunk.new_start` fallback only ever fires when
**no** match exists anywhere in the file, which is the rare case, not the
common one this is presumably guarding against.

**Fix:** track the removed/added lines' actual line numbers when
building `_Hunk` (or scan only within `hunk.new_start..hunk.new_end`
in `source`) instead of a whole-file substring search.

---

## 6. Iterator double-consumption check violates this same module's own stated design — scans the whole file, not the hunk

**RESOLVED 2026-08-20** — see finding #4/#5's resolution notes (same PR).
Both `assignments` and `uses` are now matched against a hunk-scoped window
instead of the whole file. Regression test:
`test_semantic_checker_does_not_flag_a_common_variable_name_used_correctly_elsewhere_in_the_file`
(two unrelated functions each correctly use a common variable name once;
without the fix, the whole-file scan summed both into a false "consumed
twice" finding).


**File:** `github-app/scan_worker/semantic_checks.py:177-191`

```python
if "yield" in dependency:
    assignments = re.findall(rf"\b(\w+)\s*=\s*{re.escape(name)}\s*\(", source)
    for variable in assignments:
        uses = re.findall(
            rf"\bfor\s+\w+\s+in\s+{re.escape(variable)}\b|\b(?:sum|list|tuple|set)\([^\n]*\b{re.escape(variable)}\b",
            source,
        )
        if len(uses) >= 2 and any(name in line for line in added_lines):
            return _finding(...)
```
Both `assignments` and `uses` are matched against `source` — the whole
file — not `removed_lines`/`added_lines`/a hunk-scoped window. This
directly contradicts the docstring on the enclosing function, four lines
above where this block starts: *"One reference, one call site, one nearby
hunk - every condition below is checked against that hunk's own
removed/added lines, never the whole file's"* (line 145-147). A common
variable name (e.g. `results`) reused across two genuinely unrelated
functions — one already consuming a one-shot iterator correctly elsewhere
in the file, another touched by the current diff and calling a different
`yield`-based dependency — can trip `len(uses) >= 2` on the whole-file
count and fire a false "consumes the iterator twice" finding tied to
unrelated code.

**Fix:** scope both `assignments` and `uses` regex scans to the hunk's
own removed/added lines (or a small window around it), matching every
other check in this function.

---

## 7. `_looks_like_test_file` reimplements an existing, better utility — weaker in both directions

**RESOLVED 2026-08-21.** Deleted `_looks_like_test_file`, imported and
called `aletheore.dead_code.is_test_file` (the same anchored-regex utility
`jobs.py` already uses) at its one call site. Regression tests:
`test_fetch_review_file_context_does_not_mislabel_a_production_file_starting_with_test`
(`src/testing_utils.py` no longer false-positives as a test file) and
`test_fetch_review_file_context_labels_a_tests_directory_jsx_spec_file_correctly`
(`__tests__/Button.spec.tsx` — previously missed entirely — now correctly
labeled). Full `test_flash_review.py` (155 passed) and `github-app` suite
green. PR: `fix/flash-review-test-file-detection`.

**File:** `github-app/scan_worker/flash_review.py:356-362`

```python
def _looks_like_test_file(path: str) -> bool:
    lowered = path.lower()
    return (
        "/test" in lowered
        or lowered.startswith("test_")
        or lowered.endswith(("_test.py", ".test.js", ".spec.js", ".test.ts", ".spec.ts"))
    )
```
vs. the already-imported-elsewhere `aletheore.dead_code.is_test_file`
(used in this very file's neighbor, `jobs.py:25,3542,3693`), backed by
anchored regexes:
```python
TEST_PATH_PATTERNS = [
    re.compile(r"(^|/)test_[^/]+\.py$"),
    re.compile(r"(^|/)[^/]+_test\.py$"),
    re.compile(r"(^|/)[^/]+\.test\.[jt]sx?$"),
    re.compile(r"(^|/)[^/]+\.spec\.[jt]sx?$"),
    re.compile(r"(^|/)(tests?|__tests__)/"),
]
```
`"/test" in lowered` is an unanchored substring check — `src/testing_utils.py`,
`api/testimonials.py`, any real production file whose path segment merely
*starts with* "test", false-positives into "test file content" treatment
(flash_review.py:155) instead of full content. In the other direction, a
real test file under `__tests__/` (no literal `/test` substring — it's
`__tests__/`, not `.../test...`) or a `.tsx`/`.jsx` spec/test file (not in
the `endswith(...)` tuple, which only covers `.js`/`.ts`) is missed
entirely. `is_test_file` already handles all of this correctly and is one
import away.

**Fix:** delete `_looks_like_test_file`, call `is_test_file` from
`aletheore.dead_code` instead (already a dependency of this package).

---

## 8. Ops-alert cooldown is keyed on `source` alone — one call site fires three different conditions under the same key

**RESOLVED 2026-08-21.** Gave each of `_check_backup_freshness`'s three
conditions its own `source` suffix (`.missing_dir`, `.no_dump`, `.stale`)
instead of a shared `"ops_monitor.backup_freshness"`. Went with folding
the condition into `source` itself (not a separate `variant` param only
affecting the cooldown key) after finding a second reason it was
necessary: `send_error_alert` (`app_server/error_alerts.py`) has its own
*independent* in-memory cooldown also keyed on `source` alone
(`f"{source}:{type(error).__name__}"`) — a `variant`-only fix would have
left that second cooldown still conflating the three conditions.
Regression test:
`test_check_backup_freshness_missing_dir_and_stale_backup_both_alert_within_cooldown`
— confirmed only 1 of 2 alerts fired without the fix (second silently
suppressed) and both fire with it. Full `github-app` suite green. PR:
`fix/ops-alert-backup-freshness-key`.

**File:** `github-app/scan_worker/jobs.py:2614-2629` (`_send_ops_alert`)
and `:2721-2751` (`_check_backup_freshness`)

```python
cooldown_key = f"ops_monitor:alert_cooldown:{source}"
if redis_conn.get(cooldown_key) is not None:
    return
```
`_check_backup_freshness` calls `_send_ops_alert` three times, all with
the identical `source="ops_monitor.backup_freshness"`, for three genuinely
different, differently-worded, differently-severe conditions: backup
directory missing (line 2724-2729), no backup dump found (2734-2739), and
latest backup stale (2745-2751). Because the cooldown key is `source`
alone, whichever of the three fires first sets a 15-minute
(`OPS_ALERT_COOLDOWN_SECONDS = 900`) cooldown that silently suppresses the
other two if they occur within that window — e.g. the backup directory
goes stale (alert fires, cooldown set), then genuinely becomes
*unavailable* five minutes later (a worse, differently-worded condition),
and on-call never hears about it until the first alert's cooldown expires.
This is a real regression introduced by the same PR (#286) that fixed the
original repeat-alert-spam problem — it fixed the "same condition keeps
re-firing" bug by introducing a "different conditions get conflated"
bug, for the one call site with more than one condition per source.

**Fix:** fold the specific condition into the cooldown key, e.g.
`source=f"ops_monitor.backup_freshness.{condition}"` for each of the three
call sites, or extend `_send_ops_alert` to accept a `variant` argument
that's included in `cooldown_key` but not in the alert's `source` field.

---

## 9. Blast-radius context can't receive the exact per-file line mapping that's already computed and available

**RESOLVED 2026-08-21.** Added a `diff_patches` parameter to
`build_blast_radius_context`, forwarded to `_diff_valid_lines(diff_text,
diff_patches)` (which already supported an optional `patches` argument —
just never received one from this call site). Threaded the
already-computed `diff_patches` from `jobs.py:1535` through the one call
site at `jobs.py:1602`. Regression test:
`test_build_blast_radius_context_forwards_diff_patches_to_valid_lines_computation`
(spies on `_diff_valid_lines` to confirm the value is actually forwarded)
— confirmed it fails without the fix (`TypeError: unexpected keyword
argument`) and passes with it. Full `test_flash_review.py` (156 passed)
suite green. PR: `fix/blast-radius-diff-patches`.

**File:** `github-app/scan_worker/flash_review.py:248-253, 272`
(`build_blast_radius_context`), `github-app/scan_worker/jobs.py:1552-1554`

`build_blast_radius_context` doesn't accept a `diff_patches` parameter and
internally calls `_diff_valid_lines(diff_text)` with a single argument
(line 272), forcing the text-parsing fallback path
(`flash_review.py:705-712`) instead of the more precise per-file
`_patch_valid_lines` path that's used everywhere `patches` is actually
supplied. Meanwhile `diff_patches` is already computed once at
`jobs.py:1498` and passed into `review_diff` at both of its call sites
(`jobs.py:1645, 1667`) — but the `build_blast_radius_context` call at
`jobs.py:1552-1554` has no way to forward it, since the function's
signature doesn't accept it. Not a crash risk (the fallback path already
has the #283 boundary-marker fix), but a real precision/consistency gap:
blast-radius citation-line computation uses a strictly less accurate line
mapping than the rest of the same review pipeline, for no reason other
than the parameter not being threaded through.

**Fix:** add a `diff_patches` parameter to `build_blast_radius_context`
and pass the already-computed value from `jobs.py:1552`.

---

## 10. `_first_commit_at`'s epoch fallback poisons repo age for the whole repo, not just the bad commit

**RESOLVED 2026-08-21.** `_first_commit_at` now filters out
`_EPOCH_UTC`-valued dates before taking `min()`, only falling back to the
epoch value in the genuinely-unrecoverable case where every root commit's
date is unparseable. `_EPOCH_UTC`'s own comment (in `incremental.py`)
already explains why it's deliberately "very old" — correct for staying
out of the way of a *most-recent* ranking, but exactly wrong fed into a
`min()` that's finding the *oldest* value. Regression test:
`test_analyze_git_repo_age_excludes_a_root_commit_with_a_malformed_date`
— confirmed it reports `repo_age_days=20648` (~56.5 years, matching the
bug exactly) without the fix and the correct 43 days (from the remaining
real root commit) with it. Full `test_git_intel.py` (25 passed) and
`src/tests` suite green. PR: `fix/first-commit-epoch-fallback`.

**File:** `src/aletheore/git_intel/analyzer.py:313-327` (`_first_commit_at`),
`src/aletheore/git_intel/incremental.py:97-110` (`parse_commit_date`)

`parse_commit_date` has a three-tier fallback for a malformed date string
— ISO parse, then a UTC re-parse of just the 19-char date/time prefix
(the actual fix from #281, for a malformed *timezone offset* specifically)
— and only falls all the way back to `_EPOCH_UTC` (1970-01-01) when even
that fails:
```python
def _first_commit_at(repo_path: Path) -> datetime:
    ...
    dates = []
    for sha in root_shas:
        ...
        dates.append(parse_commit_date(date_result.stdout.strip()))
    return min(dates)
```
`_first_commit_at` collects a date for **every** root commit (a repo with
merged unrelated histories can have several) and takes `min()` across all
of them. If even one root commit's date is malformed badly enough to hit
the epoch fallback, `min()` picks 1970-01-01 as the repo's founding date
regardless of what every other root commit says — corrupting
`repo_age_days` (`analyzer.py:349`) for the entire repo (reports it as
~56 years old) rather than just excluding or flagging that one bad
commit. #281's own fix already establishes that malformed-timezone root
commits are a real, reachable production case (that's what it was written
to survive without crashing); this is the next-order bug in the same
function — surviving without crashing, but silently producing a wrong
answer that presumably feeds ownership/hotspot/maturity signals derived
from repo age.

**Fix:** exclude epoch-fallback dates from the `min()` (e.g. filter
`dates` to only non-fallback values before taking `min`, falling back to
`now` or skipping the age computation entirely if every root commit's
date is unparseable) rather than letting one bad commit set the floor for
the whole repo.

---

## Suggested priority — Batch 2

1-3 are the same root-cause bug (unguarded `--- X ---` marker match) in
three places; fixing #1 and #2 (both production code, on the same review
path as the flagship citation-accuracy work from PR #292/#293) should
happen together — they're the same five-minute fix, copy-pasted three
times over instead of shared. #4 and #6 both silently defeat a
deterministic check for its most realistic trigger shape — same severity
class as #1-3, since a shipped-but-silently-broken check is worse than no
check (false confidence). #8 is a real regression in the exact PR meant
to fix alerting, worth a same-day fix since it's actively degrading
on-call signal right now. #5, #7, #9, #10 are real but lower-frequency or
lower-blast-radius; still worth fixing before or shortly after launch.

---
---

# Batch 3 — PRs #246–#271

(`a2cac8a..d89f1de` on `master`.) The spend-lock concurrency cluster
(#251/#252/#253 and its follow-ups), a prior "audit findings batch" (#258),
`referenced_symbol_context` (#254), hosted embeddings routing through Jina
(#257) plus the full char-cap/concurrency tuning cluster (#259-271), MCP
gap-closing (#249), AIRview concurrency (#248), the C# dependency-graph fix
(#250), query-ownership (#256), and the crash-looping prod fix (#246).

Method unchanged from Batch 2: multi-angle review, then every finding
independently re-verified by reading the actual source and, for several,
tracing execution by hand line-by-line rather than trusting the
description. This is the batch with the most financial- and
privacy-sensitive findings so far — flagged as requested.

**Worth naming up front:**

- **The paid-tier dollar spend cap has the exact same race as Batch 1's
  free-tier count cap** (finding 1) — I'd only written up the free-tier
  side in Batch 1; this batch's review independently found the same
  architectural gap applies to the actual dollar cap that gates every paid
  installation. `installation_spend_lock`'s own docstring
  (`db.py:340-344`) still claims it "serializes the check/run/record
  cycle," but the code no longer does that, and `record_llm_spend`
  (`db.py:211-244`) never re-checks the cap before writing. Two of these
  bugs, same root cause, now confirmed on both the count side and the
  dollar side.
- **A silent embedding data-corruption bug** (finding 2): when the hosted
  embedding call fails on literally the *first* batch of a run, the local
  fallback re-embeds and duplicates that same batch, producing more
  vectors than input texts — which then silently misaligns the
  hash-to-vector mapping downstream with no error raised anywhere.
- **A real MCP consent bypass** (finding 3): `aletheore_answer` never
  forwards the operator's `EFFECT_EXTERNAL` decision down to the embedding
  call, unlike its two sibling tools that this same diff updated to do it
  correctly — so withholding external-transmission consent for this one
  tool has no effect if a hosted token is configured.

---

## 1. The paid-tier monthly dollar spend cap has the same check-then-act race as Batch 1's free-tier count cap — extends that finding to real money

**RESOLVED 2026-08-20.** Both this and Batch 1 findings #1/#3/#4 (the same
underlying architecture issue, on the count side, the exhausted-fallback
side, and the AIRview/Docs incremental-budget side respectively) were fixed
together in one pass: `github-app/scan_worker/db.py` gained
`reserve_flash_review_count`/`release_flash_review_count_reservation` and
`reserve_llm_spend`/`release_llm_spend_reservation` — atomic
`INSERT...ON CONFLICT...WHERE...RETURNING` reservations (the same shape as
the already-atomic `check_and_reserve_flash_review_attempt`), replacing the
lock-then-later-write pattern everywhere it appeared. `run_flash_review_job`
reserves both caps atomically before `_run_flash_review` starts, not after
it finishes; a `finally` block releases the reservation if the review never
actually ran. `_IncrementalSpendBudget` (managed audits, AIRview/Docs
builds) reserves per call the same way instead of trusting a stale
snapshot. Verified under genuine concurrent load against a real Postgres
instance (60 threads racing a count cap of 20, 30 threads racing a $5
spend cap in $0.50 reservations) — see `test_scan_worker_db.py`'s two
`*_is_atomic_under_real_concurrency` tests. Full github-app suite (1,410
tests) green. PR: `fix/installation-spend-cap-atomic-reservation`.


**Files:** `github-app/scan_worker/jobs.py:1423` (lock-scope comment),
`~1453-1461` (check), `~1674-1679` (record);
`github-app/scan_worker/db.py:337-344` (`installation_spend_lock`
docstring), `:211-244` (`record_llm_spend`)

`installation_spend_lock`'s own docstring says it exists to "serialize the
check/run/record cycle per installation so scaling scan-worker to multiple
replicas later can't let concurrent jobs for the same installation both
pass the cap check before either has recorded its cost" — but the actual
lock usage in `jobs.py` was narrowed (documented in a separate, adjacent
comment at line 1423-1434) to wrap only the quick check and, separately,
the post-hoc record — never the run in between. `record_llm_spend` then
unconditionally adds `cost_usd` via an atomic UPSERT with no re-check
against `monthly_cap` (the parameter is used only to log a warning past
`WARN_FRACTION_OF_CAP`, "it has no effect on what gets recorded" per its
own docstring) and no rollback path.

With `docker-compose.yml` running multiple scan-worker replicas on the
same queue, two Flash Reviews for the same paid installation running
concurrently can both read `current_spend < monthly_cap`, both proceed,
and both later add their cost unconditionally — overshooting the
installation's actual dollar cap by up to `(N-1) * cost_of_one_review` for
N concurrent reviews. No test simulates overlapping jobs for the same
installation, so nothing currently catches this. This is the same
underlying architecture issue as Batch 1 finding #1, just confirmed here
to apply to the dollar cap that gates *every* paid customer, not only the
free-tier review count.

**Fix:** same as Batch 1 finding #1/#4 — either hold one lock across
check+record with the expensive work happening outside a *reservation*
(atomic reserve-then-true-up, like the OpenAI free-tier token fix already
does), or re-check the cap inside the locked record step and roll back /
skip recording if it would push spend past the cap.

---

## 2. Hosted-embedding fallback duplicates the failed batch when the *first* batch fails — silent vector/hash misalignment

**RESOLVED 2026-08-20.** Fixed alongside findings #3 and #4 below.
`_embed_in_batches`'s fallback span-rebuild changed from `range(start, ...)`
to `range(end, ...)`, so the batch already embedded locally is no longer
re-queued. Regression tests: `src/tests/test_search_index.py`'s
`test_a_402_falls_back_to_local_and_says_why` (strengthened to assert
`len(vectors) == 1`) and the new
`test_hosted_failure_on_the_first_of_several_batches_does_not_duplicate_it`
(10 texts, `batch_size=5`, asserts `len(vectors) == 10` and no duplicate
texts across the local-fallback batches). Full `src/tests` suite (1,294
tests) and full `github-app` suite green. PR:
`fix/embed-batch-consent-affiliate-audit`.

**File:** `src/aletheore/search_index.py:960-988` (`_embed_in_batches`)

```python
while span_index < len(spans):
    start, end = spans[span_index]
    span_index += 1
    batch = texts[start:end]
    if use_hosted:
        try:
            vectors.extend(embed_texts_hosted(batch, token, repo_id=repo_id))
            ...
            continue
        except HostedEmbeddingUnavailableError as exc:
            if vectors:
                raise
            ...
            use_hosted = False
            spans = [(s, min(s + batch_size, len(texts))) for s in range(start, len(texts), batch_size)]
            span_index = 0
    vectors.extend(embed_texts(batch))   # <-- falls through here even after the except block ran
```
Traced by hand for 10 texts, `batch_size=5`: first hosted call on
`texts[0:5]` fails, `vectors` is still empty so the `if vectors: raise`
guard doesn't fire. The except block rebuilds `spans` from `range(start,
...)` — `start=0`, the start of the *batch that just failed* — instead of
`range(end, ...)`, and resets `span_index = 0`. Execution then falls
through the bottom of the `if use_hosted:` block (no `continue`) straight
into `vectors.extend(embed_texts(batch))`, which locally embeds
`texts[0:5]` — correct, that's the intended immediate fallback for the
batch that just failed. But because `spans` was rebuilt starting from `0`
and `span_index` reset to `0`, the very next loop iteration reads
`spans[0] = (0, 5)` again and embeds `texts[0:5]` a **second** time
locally. Result: 15 vectors for 10 input texts. Downstream,
`_embed_stale_by_hash`'s `dict(zip(stale_hashes, fresh_vectors))`
(line 1041) silently zips the misaligned/extra vectors against hashes,
attaching wrong embeddings to the back half of the batch with no error or
warning anywhere — a search index built from this run would return
plausible-looking but wrong results for an unknown subset of chunks, with
nothing in the logs to indicate it happened.

**Fix:** rebuild `spans` from `range(end, ...)`, not `range(start, ...)`
— the batch that already got embedded (successfully, by the local
fallback) should not be included in the remaining-work list.

---

## 3. `aletheore_answer` never forwards MCP hosted-transmission consent — silently ignores `EFFECT_EXTERNAL`

**RESOLVED 2026-08-20.** `answer_question` gained an `allow_hosted: bool =
True` parameter, forwarded to `search_index(..., allow_hosted=allow_hosted)`;
`_register_answer_tool` now takes `effects` and its `aletheore_answer`
handler calls `answer_question(..., allow_hosted=EFFECT_EXTERNAL in
effects)`, mirroring `aletheore_search_codebase`/`aletheore_index`.
Regression tests: `src/tests/test_answer.py`'s
`test_answer_question_forwards_allow_hosted_to_search_index` and
`test_answer_question_allow_hosted_defaults_to_true`;
`github-app/tests/test_mcp_server.py`'s
`test_aletheore_answer_tool_forbids_hosted_embeddings_by_default` and
`test_aletheore_answer_tool_permits_hosted_embeddings_when_external_is_allowed`
(mirroring the existing `aletheore_index` consent tests). PR:
`fix/embed-batch-consent-affiliate-audit`.

**Files:** `src/aletheore/mcp_server.py:624-627` (`aletheore_answer`) vs.
`:592-606` (`aletheore_search_codebase`, same diff, same file);
`src/aletheore/answer.py:20-27` (`answer_question`)

```python
def aletheore_answer(question: str, k: int = 5) -> str:
    ...
    return _toon_result(answer_question(repo_path, question, answer_adapter, k=k))
```
vs. its sibling, updated by this same diff to do it correctly:
```python
def aletheore_search_codebase(query: str, k: int = 10, language: str | None = None) -> str:
    ...
    search_index(repo_path, query, k=k, language=language, allow_hosted=EFFECT_EXTERNAL in effects)
```
`answer_question` calls `search_index(repo_path, question, k=k)` with no
`allow_hosted` argument (`answer.py:27`), so `search_index`'s (and
transitively `_embed_in_batches`'s) default of `allow_hosted=True` applies
regardless of what the MCP operator actually granted.
`TOOL_REQUIRED_EFFECTS["aletheore_answer"]` (`mcp_server.py:672`) is only
`{EFFECT_NETWORK}` — deliberately not `EFFECT_EXTERNAL` — so an operator
can grant this tool network access without granting consent to transmit
repository content externally, exactly as intended for the other two
hosted-embedding-calling tools this same diff updated. For
`aletheore_answer` specifically, that consent boundary is a no-op: if
`ALETHEORE_API_TOKEN` is set, the question text (and the chunks retrieved
to answer it) go to Aletheore's hosted endpoint either way.

**Fix:** thread `allow_hosted=EFFECT_EXTERNAL in effects` through
`answer_question` → `search_index`, the same way
`aletheore_search_codebase` and `aletheore_index` already do.

---

## 4. Affiliate commission payouts can mark a reversed (refunded/charged-back) commission as paid, corrupting the audit trail

**RESOLVED 2026-08-20.** Added `AND NOT reversed` to
`mark_commissions_paid`'s UPDATE, matching both `list_affiliates_with_totals`
queries. Regression tests: `github-app/tests/test_affiliates.py`'s
`test_mark_commissions_paid_does_not_touch_a_reversed_commission` and
`test_mark_commissions_paid_still_pays_unreversed_commissions_alongside_a_reversed_one`.
Full `github-app` suite green. PR:
`fix/embed-batch-consent-affiliate-audit`.

**File:** `github-app/app_server/affiliates.py:132-141`
(`mark_commissions_paid`) vs. `:107-129` (`list_affiliates_with_totals`,
same file, same diff)

```python
async def mark_commissions_paid(pool: asyncpg.Pool, affiliate_id: int) -> int:
    result = await pool.execute(
        "UPDATE affiliate_commissions SET paid = true WHERE affiliate_id = $1 AND NOT paid",
        affiliate_id,
    )
```
`list_affiliates_with_totals`'s totals, updated by this same diff to
handle the new `reversed` column correctly, both filter it:
```sql
COALESCE((SELECT SUM(amount_usd) FROM affiliate_commissions c
          WHERE c.affiliate_id = a.id AND NOT c.paid AND NOT c.reversed), 0) AS total_owed_usd,
COALESCE((SELECT SUM(amount_usd) FROM affiliate_commissions c
          WHERE c.affiliate_id = a.id AND c.paid AND NOT c.reversed), 0) AS total_paid_usd
```
`mark_commissions_paid`'s UPDATE has no `AND NOT reversed` at all. A
commission that gets reversed (via the webhook path added in this same
diff, on a Paddle refund/chargeback) before an admin clicks "mark paid" is
already correctly excluded from `total_owed_usd` — but it's still matched
by the broader unpaid-only UPDATE and gets flipped to `paid = true`, after
which it's *also* excluded from `total_paid_usd` (same `NOT reversed`
filter). The row silently disappears from both totals while sitting in
the database marked as paid — a commission that was never actually paid
out, with no trace in either report. No test exercises the
reversed-then-marked-paid interaction.

**Fix:** add `AND NOT reversed` to `mark_commissions_paid`'s UPDATE, same
as both totals queries.

---

## 5. Marketplace webhook's one-time initial build isn't retry-safe, unlike the Paddle path this same diff hardened

**RESOLVED 2026-08-20.** Gave `marketplace.py`'s one-time build the same
independent `claim_paid_setup` gate the Paddle path already has, replacing
the `transitioned_to_paid` gate. Regression test:
`github-app/tests/test_marketplace_webhook.py`'s
`test_crash_after_plan_write_still_runs_setup_on_retry` (mirrors
`test_webhooks_paddle.py`'s equivalent) — confirmed it fails without the
fix (0 enqueues instead of 2) and passes with it. Full `github-app` suite
green. PR: `fix/marketplace-webhook-retry-safety`.

**Files:** `github-app/app_server/webhooks/marketplace.py:64-90` vs.
`github-app/app_server/webhooks/paddle.py:147` (`claim_paid_setup`,
backed by migration `052_paid_setup_completion.sql`, same diff)

```python
transitioned_to_paid = False
if new_plan != "free":
    transitioned_to_paid = await claim_free_to_paid_plan(pool, installation_id, new_plan)
    ...
if transitioned_to_paid:
    queue.enqueue("scan_worker.jobs.run_live_wiki_full_build_for_installation_job", ...)
```
Paddle's webhook handler in this same diff was changed to gate its
equivalent one-time build on an independent `claim_paid_setup` claim
specifically so the build stays retry-safe if the handler crashes after
the plan transition commits but before the job enqueues (`should_run_paid_setup
= plan != "free" and await claim_paid_setup(pool, installation_id)`).
`marketplace.py` still gates purely on `transitioned_to_paid`, i.e. on
`claim_free_to_paid_plan`'s return value. If that call commits (plan is
now paid) but the handler crashes before `queue.enqueue` runs — e.g. inside
`add_installation_member` a few lines later — GitHub retries the same
webhook delivery (the handler re-raises on exception specifically so
GitHub will retry), but `claim_free_to_paid_plan` now returns `False`
since the plan is no longer `"free"`, so `transitioned_to_paid` is `False`
on the retry and the initial Live Wiki/Docs build is permanently skipped
for that installation.

**Fix:** give the Marketplace path the same independent `claim_paid_setup`
gate the Paddle path now has, instead of relying on the plan-transition
claim doubling as the build-trigger claim.

---

## 6. MCP's generated ownership tool can never forward a target — the query it wraps just gained per-file support

**RESOLVED 2026-08-20** (PR #302). Independently re-confirmed the same
session by a blind MCP-comparison test agent that hit this exact wall
unprompted (see `before_launch_fixes.md`'s "Fixed — gaps found via MCP
tool comparison testing" section). Fixed by adding an `optional_target_kinds`
set to `_register_query_wrapper_tools` so `ownership` gets a
`target: str | None = None` signature instead of the old binary
required/none split.

**File:** `src/aletheore/mcp_server.py:290-303`
(`_register_query_wrapper_tools`)

```python
if requires_target:
    def tool(target: str) -> str:
        ...
        return _toon_result(func(evidence, target))
else:
    def tool() -> str:
        ...
        return _toon_result(func(evidence, None))
```
Whether a query's generated MCP tool accepts a `target` parameter at all
is decided by a static `requires_target` flag per query, not by whether
the underlying query function can use one. `find_ownership` was extended
elsewhere in this diff (`query.py:88-90`, backing `git.file_ownership`) to
return per-file ownership when given a target, matching what the CLI's
`_query` already forwards unconditionally — but `find_ownership` wasn't
added to whatever set of query names has `requires_target=True`, so the
generated `aletheore_ownership` MCP tool has no `target` parameter and
always calls `func(evidence, None)`. The file-scoped ownership data
documented in `docs/AIR-SCHEMA.md` is fully computed and present in
evidence, just structurally unreachable through the MCP tool surface —
an MCP-driven agent can never ask "who owns this specific file," only get
repo-wide ownership, unlike the CLI which already supports it.

**Fix:** add `find_ownership` (or whatever its query name is) to the
`requires_target=True` set alongside the other target-capable queries.

---

## 7. Two sequential blocking Redis calls inside an async request handler

**RESOLVED 2026-08-21.** Wrapped both `is_rate_limited` calls in
`asyncio.to_thread`, matching the pattern this same file already uses for
the actual Jina embedding call. Kept them sequential rather than
pipelining into one Redis round-trip: skipping the second, repo-scoped
check when the installation-wide one already trips the limit is a real
short-circuit worth keeping. Regression test:
`test_rate_limit_checks_are_offloaded_to_thread` — confirmed it fails
without the fix (0 of 2 expected `asyncio.to_thread` calls) and passes
with it. Also had to make an existing test
(`test_embedding_provider_call_is_offloaded_to_thread`) dispatch by which
function it's given rather than assume it's the only `asyncio.to_thread`
call in the request — not a functional regression, just a stale
assumption from before this fix. Full `test_embeddings_api.py` (20
passed) and `github-app` suite green. PR:
`fix/embeddings-api-rate-limit-async`.

**File:** `github-app/app_server/embeddings_api.py:179-190`
(`create_embeddings`)

```python
rate_limited = is_rate_limited(...)   # installation-wide bucket
...
rate_limited = is_rate_limited(...)   # repo-scoped bucket
```
Neither call is awaited — `is_rate_limited` uses the synchronous redis-py
client and blocks via `pipe.execute()` — inside an `async def` request
handler. Two blocking Redis round-trips run back-to-back with no data
dependency forcing that order, each one stalling the asyncio event loop
(and therefore every other concurrent request on the same worker) for its
full duration. Not a correctness bug, but a real latency/throughput issue
on the hot embeddings-ingest path that this same diff otherwise spent
significant effort hardening (Jina routing, char-cap tuning, concurrency
caps).

**Fix:** either pipeline both checks into one Redis round-trip, or move
them off the event loop via `asyncio.to_thread` (already used elsewhere
in this same file for the actual embedding call at line 233).

---

## 8. Removing the Java/C# pre-parsed tree cache doubles tree-sitter parse cost for both languages

**RESOLVED 2026-08-21.** Restored `java_pre_parsed`/`csharp_pre_parsed`
caches populated during each language's pre-pass (which already has to
parse every file to read its package/namespace), and the main loop now
reuses `(source, tree)` from the cache instead of re-reading and
re-parsing `.java`/`.cs` files from scratch. Also found and corrected two
existing tests
(`test_build_module_graph_reparses_java/csharp_without_retaining_prepass_trees`)
that had been written to *assert* the double-parse as an intentional
"CPU for bounded memory" trade-off — the pre-parsed tree is only ever
held from the pre-pass to the main loop within the same function call,
never retained for the scan's whole lifetime, so caching it costs
nothing extra in memory; the trade-off the tests described was never
real. Both renamed and now assert exactly 1 read+parse per file instead
of 2. Regression tests confirmed both fail (2 reads) without the fix and
pass (1 read) with it. Full `test_graph.py`/`test_graph_java.py`/
`test_graph_csharp.py` (95 passed) and `src/tests` suite green. PR:
`fix/java-csharp-preparsed-cache`.

**File:** `src/aletheore/scanner/graph.py` (main per-file extraction loop,
~2280-2295)

Confirmed via `git diff a2cac8a..d89f1de -- src/aletheore/scanner/graph.py`:
this diff removes `java_pre_parsed: dict[Path, tuple[bytes, Tree]]` and
`csharp_pre_parsed: dict[Path, tuple[bytes, Tree]]` (populated during the
mandatory Java/C# pre-pass that builds `java_source_roots` and
`csharp_type_owners`) along with the main loop's
`if language_name == "java" and path in java_pre_parsed: source, tree =
java_pre_parsed[path]` reuse check — with no replacement caching
mechanism. The main per-file loop now unconditionally does
`source = path.read_bytes(); tree = parser.parse(source)` for every file,
including `.java`/`.cs` files that the pre-pass already parsed once to
build cross-file type/import data. Every Java or C# file in a scanned repo
now gets parsed by tree-sitter twice per scan instead of once — a silent
throughput regression on exactly the languages that already require two
passes, on top of this project's own tracked scan/indexing-speed
concerns.

**Fix:** restore the cache (or thread the pre-pass's already-built
`(source, tree)` through to the main loop directly) so Java/C# files are
parsed once per scan, same as every other language.

---

## Suggested priority — Batch 3

1 and 2 are the most consequential: 1 is a real-money cap-overshoot race
on every paid installation (same class as Batch 1's #1/#4, now confirmed
on the dollar side too — worth fixing all four of these together as one
piece of work), and 2 is silent data corruption in the search index with
no error signal, which is worse than a crash. 3 and 4 are both "the
consent/audit boundary this same diff built correctly for sibling code
paths has one gap" — quick, targeted fixes, high trust-impact if shipped
as-is (a consent bypass and a payout-audit-trail bug are both the kind of
thing that surfaces badly in a support ticket, not a stack trace). 5 is a
narrow but real retry-safety gap on a payment-adjacent path. 6, 7, 8 are
real but lower-urgency — a missing feature-surface (6), a latency
regression (7), and a performance regression (8) — worth fixing before or
shortly after launch rather than blocking it.

---
---

# Batch 4 — PRs #195–#230 (partial, methodology note below)

(`be14225..8983b1e` on `master`, 31 commits.) The original scan-cache
trust-boundary closures (#195/#197/#199), MCP/dashboard evidence
validation (#196), the DNS-rebinding fix (#204), the prompt-injection
guard rollout (#202), the Paddle authorization-claim fix (#218), the
affiliate payout referral-count multiplier bug (#217), spend-cap gating
for AIRview/Docs and fix-suggestion calls (#207/#220), and two prior
"audit findings batch" PRs (#221/#223).

**Methodology note (superseded 2026-08-21):** the original pass above hit
the account session limit twice and was done as a single-pass, non-exhaustive
review targeted at the highest-stakes commits only. A proper 4-way
parallel multi-agent pass (matching Batches 1-3's methodology) has since
covered the full 31-commit range, each finding independently re-verified
by direct source reads and, where applicable, live regex/function tests
before being recorded — see the findings below finding #1. This batch is
now exhaustive to the same standard as Batches 1-3.

**The one finding from this batch is worth reading closely — it's a false
sense of security, not a bug in the traditional sense:**

## 1. The regression test written to prevent the spend-lock TOCTOU race can't actually detect it — and the race it was meant to prevent has since reopened

**RESOLVED 2026-08-20.** Both underlying issues fixed together: the race
itself (see Batch 1 #1/#3/#4 and Batch 3 #1's resolution notes — atomic
reservation replaced the lock-then-later-write pattern entirely, so there's
no "was the lock held" question left to get wrong), and the two weak tests
named below were deleted and replaced with
`test_flash_review_job_reserves_the_cap_before_running_the_review`,
`test_flash_review_job_releases_reservation_when_the_review_never_runs`,
and `test_flash_review_job_does_not_release_reservation_after_a_successful_review`
in `test_jobs.py`, plus two genuine concurrency tests in
`test_scan_worker_db.py` that fire real threads at the reservation
functions against an actual Postgres instance and assert the exact right
number succeed — the kind of proof a fake-lock unit test could never
provide.

**Files:** `github-app/tests/test_jobs.py:1845-1902`
(`test_flash_review_job_records_spend_and_count_while_the_lock_is_still_held`)
and `:1905-1959`
(`test_flash_review_job_releases_lock_during_the_review_itself`)

PR #220 (2026-08-12) found F25 from a prior audit — "record_llm_spend and
increment_flash_review_count land outside installation_spend_lock" — did
**not** reproduce at the time, and added a structural regression test
specifically to keep it that way: `..._while_the_lock_is_still_held`
asserts that when `record_llm_spend`/`increment_flash_review_count` run,
a tracking fake lock reports `held == True`.

The tracking fake is the problem:
```python
@contextmanager
def _tracking_spend_lock(*args, **kwargs):
    lock_state["held"] = True
    try:
        yield
    finally:
        lock_state["held"] = False
```
It just flips a shared boolean on enter/exit of **any** `with
installation_spend_lock(...):` block — it has no notion of *which*
acquisition is active, so it cannot distinguish "the same lock, held
continuously from the cap check through to the record" (the invariant
#220 actually intended to protect) from "the lock was released after the
check, the review ran unlocked, and a **new, separate** acquisition was
taken just before the record call" (a real TOCTOU gap — two concurrent
reviews for the same installation can each pass the check before either
records). Both shapes make the assertion `held == True` pass identically.

This is not a hidden gap — the very next test's own comment says so
explicitly: *"This asserts the lock is free during the expensive part,
which the F25 test above doesn't check (it only checks
record/increment)."* Read together, the two tests establish two
individually-true, separately-necessary properties (lock is held at
record time; lock is free during the review) but never the one property
that actually matters — that the check and the eventual record are part
of one atomic acquisition, not two. That's exactly the gap Batch 1
finding #1 and Batch 3 finding #1 describe as currently real in
production `jobs.py` (separate `with installation_spend_lock(...):`
blocks for the check and for the record, with the expensive unlocked
review in between) — the F25 test is green right now, in the exact
codebase where that race has already reopened, and would stay green even
if it got worse.

**Fix:** this doesn't need a new test so much as a stronger fake — give
`_tracking_spend_lock` (or a shared test helper) an acquisition counter
or identity token, and add an assertion that the acquisition active at
record time is the *same* acquisition that was active at check time (or,
more directly: once the real fix from Batch 1/3 finding #1 lands — an
atomic reserve-then-true-up instead of a lock — replace this test with
one that verifies the reservation is atomic, e.g. by simulating two
overlapping calls and asserting only one can pass the cap check for the
same remaining budget).

---

## 2. The fix-suggestion LLM spend cap check and record are two separate lock acquisitions — the exact TOCTOU race PR #220 fixed has reopened for this call site

**RESOLVED 2026-08-21.** Migrated `_fix_suggestion_attachment` off the two-
separate-lock-acquisitions shape onto `_IncrementalSpendBudget` — the same
atomic `reserve_llm_spend`/`record_llm_spend` primitive every other real
LLM call site in this file already uses. `can_start_next_call()` now gates
the call atomically before the GitHub fetch even happens; `record_usage`
(wired as the adapter's `on_usage`) trues the flat reservation up to the
real cost afterward. Also updated `_llm_spend_cap_reached`'s docstring,
which previously named this function as the reference example of the "keep
locking around it" shape — no longer accurate. New regression test
(`test_fix_suggestion_attachment_reserves_spend_atomically_against_concurrent_calls`
in `github-app/tests/test_jobs.py`) fires two concurrent calls under a cap
that only fits one reservation, synchronized with a barrier so both
threads' cap-check reads land in the same instant — verified via
`git stash` that it fails (2 succeed instead of 1) on the pre-fix code and
passes on the fix. Also updated two pre-existing tests in
`test_correlation.py` whose mocks predated this migration (added a
`reserve_llm_spend` mock to `_patch_fix_suggestion_spend_gate`, and fixed
`test_fix_suggestion_attachment_records_spend_when_model_succeeds`'s
assertion, which now correctly expects the true-up *delta* from the flat
reservation instead of the raw cost). Full `github-app` suite (1455+2
passed, 8 skipped) and `src` suite (1304 passed) green. PR:
`fix-suggestion-spend-race`.


**File:** `github-app/scan_worker/jobs.py:2072-2112` (`_fix_suggestion_attachment`)

```python
with installation_spend_lock(dsn, installation_id):
    cap_reached, monthly_cap = _llm_spend_cap_reached(dsn, installation_id, plan)
if cap_reached:
    return None

app_jwt = generate_app_jwt(settings.github_app_id, settings.github_app_private_key)
token = _token_sync(installation_id, app_jwt)
client = get_github_api_client()
file_content = fetch_file_content(client, token, repo_full_name, source_file)
...
raw = _health_fix_suggestion_adapter(on_usage=_on_usage).simple_completion(...)
with installation_spend_lock(dsn, installation_id):
    record_llm_spend(
        dsn, installation_id, spend_accumulator["total"], monthly_cap=monthly_cap,
        feature="health_fix_suggestion",
    )
```

PR #220 originally fixed this (no cap check at all) by wrapping the entire
check-through-record sequence in one lock acquisition. PR #258 later
deliberately split this into two separate acquisitions, with the GitHub
fetch and the real LLM call unlocked in between, to reduce lock
contention — reopening the exact check-then-act race Batch 1 finding #1
and Batch 3 finding #1 already fixed for every other LLM surface in this
file. This call site is reachable both from `POST /v1/runtime-events`
(rate-limited to 300/hour per installation) and from
`run_health_check_down_retry_job`'s retry path, so two concurrent
invocations can both pass the cap check before either records spend, each
making a real, billed LLM call, overshooting the monthly cap. Unlike
flash review and AIRview/Docs, this call site was never migrated to the
atomic `reserve_llm_spend`/`release_llm_spend_reservation` primitive PR
#304 introduced to close this bug class everywhere else.

**Fix:** migrate to the same atomic reservation pattern (`reserve_llm_spend`
before the call, `release_llm_spend_reservation` if it never completes) or
`_IncrementalSpendBudget`, matching every other LLM call site in this file.

---

## 3. AIRview live-wiki full-build and incremental-update spend caps have the same check-then-act race — never migrated to the atomic reservation fix that covers the sibling Docs paths

**RESOLVED 2026-08-21.** Migrated both functions off the two-separate-lock
shape onto `_IncrementalSpendBudget`, matching Docs. Unlike Docs (one LLM
call per module, gated by a per-module loop check in `jobs.py`), Wiki
internally batches multiple clusters into single LLM calls
(`live_wiki.py`'s `_run_batched_with_retry`), so there's no 1:1 alignment
between "clusters" and real network calls a loop check could gate. Instead
wired `before_llm_call=spend_budget.can_start_next_call` directly into the
adapters: added a `before_llm_call` pass-through parameter to
`_live_wiki_naming_adapter`, `_live_wiki_full_build_writing_adapter`, and
`_live_wiki_update_writing_adapter` (they already accepted `on_usage`;
`writing_adapter_for`/`writing_adapter_for_plan` already accept
`before_llm_call`). This gates every real network call regardless of
internal batching, and also covers `_attach_wiki_file_pages`'s calls for
free, since it reuses the same `writing_adapter` instance the caller
constructed. Two new regression tests (one per function) fire two
concurrent builds for different repos under the same installation, with a
cap that only fits one reservation, synchronized with a double-wait
barrier so both threads' cap-check reads land at the same instant —
verified via `git stash` that both fail (2 succeed instead of 1) on the
pre-fix code and pass on the fix. Full `github-app` suite (1458 passed, 8
skipped) and `src` suite (1304 passed) green. PR: `wiki-spend-race`.


**File:** `github-app/scan_worker/jobs.py:3279-3323` (`run_live_wiki_full_build_job`),
`:3419-3463` (`_maybe_update_live_wiki`)

```python
with installation_spend_lock(dsn, installation_id):
    cap_reached, monthly_cap = _llm_spend_cap_reached(dsn, installation_id, plan)
if cap_reached:
    ...
    return
...
records = live_wiki.generate_subsystems(...)   # unlocked, can be many LLM calls
_attach_wiki_file_pages(evidence, records, writing_adapter, fetch_line_count)
with installation_spend_lock(dsn, installation_id):
    record_llm_spend(dsn, installation_id, spend_accumulator["total"], ..., feature="airview_full_build")
```

Same shape as finding #2 and Batch 1/3 finding #1: cap checked under one
lock acquisition, real spend happens completely unlocked
(`live_wiki.generate_subsystems` can issue many cluster-generation LLM
calls), lock re-acquired only to record the total at the end.
`run_live_wiki_full_build_for_installation_job` fans out one queued job
per repo for the same installation, so two repos under one paid
installation whose full builds land close together (e.g. both due for the
48h catch-up sweep at the same tick) each read spend as under the cap
before either has recorded anything, and both proceed — overshooting the
monthly cap by both builds' combined cost. PR #304 ("installation
spend/count cap check-then-act race — atomic reservation") migrated the
sibling **Docs** paths (`run_live_docs_full_build_job`,
`_maybe_update_live_docs`) onto `_IncrementalSpendBudget`'s atomic
`can_start_next_call()`/`record_usage()` (confirmed: `_run_docs_build_for_modules`
checks `spend_budget.can_start_next_call()` before every module) but never
touched the Wiki paths, which still use the old lock-then-later-record
shape. `installation_spend_lock`'s own docstring states its purpose is
exactly to prevent this.

**Fix:** wire `_live_wiki_naming_adapter`/`_live_wiki_full_build_writing_adapter`/
`_live_wiki_update_writing_adapter` to accept and forward `before_llm_call`
(they already accept `on_usage`; `writing_adapter_for`/`writing_adapter_for_plan`
already support `before_llm_call`), construct an `_IncrementalSpendBudget`
per call the same way the Docs paths do, and pass
`before_llm_call=spend_budget.can_start_next_call` — this gates every real
network call the adapter makes regardless of how many clusters
`generate_subsystems` batches into one call, which a per-item loop check
(the Docs approach) can't do cleanly here since Wiki's batching happens
inside `live_wiki.py`, not per-iteration in `jobs.py`.

---

## 4. FastAPI cross-file router-mount prefix map is keyed by bare variable name with no file/import scoping — produces phantom cross-router endpoint paths

**RESOLVED 2026-08-21.** `cross_file_router_mounts` is now keyed by
`(defining_file, router_name)` instead of bare `router_name`. Found the
codebase already has a real, tested Python import resolver
(`_resolve_python_module`/`_resolve_python_from_import` in
`scanner/graph.py`, plus `_python_source_roots` for monorepo/src-layout
handling) - reused it rather than writing a second one. New
`_resolve_router_definition_file` follows a `from <module> import router
[as alias]` statement in the file where `include_router(router, ...)` is
called, resolves the module to a real file via the existing resolver, and
uses that as the defining-file half of the key; falls back to the current
file's own path when no such import binds the name (the pre-existing,
correct same-file case). `_extract_flask_fastapi_routes` now filters
`external_router_mount_prefixes` to `defining_file == rel_path` before
building its per-file lookup dict, so two different files' routers
imported under the same conventional bare name no longer collide. Verified
via a manual reproduction (two routers both named `router`, imported
un-aliased into two different mounting files) that the bug was real on
master - each file's routes were reported at BOTH prefixes - and that the
fix produces exactly the correct prefix per file. New regression test
`test_map_api_endpoints_does_not_cross_contaminate_same_named_routers_in_different_files`
in `src/tests/test_endpoints.py`, verified via `git stash` that it fails
without the fix and passes with it. Updated two pre-existing tests whose
fixtures relied on an unrealistic same-name reference with no resolvable
import statement. Full `src` suite (1305 passed) and `github-app` suite
(1456 passed, 8 skipped) green. PR: `fastapi-router-scoping`.


**File:** `src/aletheore/endpoints.py:1219-1226` (collection), `:59-86`
(`_collect_fastapi_include_prefixes`)

```python
cross_file_router_mounts: dict[str, list[str]] = {}
for path in _iter_source_files(repo_path, ignored_paths):
    if path.suffix != ".py":
        continue
    source = path.read_bytes()
    tree = parsers["py"].parse(source)
    for router, prefixes in _collect_fastapi_include_prefixes(tree.root_node, source).items():
        cross_file_router_mounts.setdefault(router, []).extend(prefixes)
```

`_collect_fastapi_include_prefixes` keys its return dict by the raw source
text of `include_router(X, prefix=...)`'s first positional argument —
`router = source[positional[0].start_byte:positional[0].end_byte].decode()`
— with no file scoping at all. `router` is the idiomatic FastAPI variable
name, and the common "each submodule registers itself" pattern
(`router = APIRouter()` + `include_router(router, prefix="/users")` in
`users.py`; the identical shape with `prefix="/items"` in `items.py`)
reuses that name across files. The merged `cross_file_router_mounts["router"]`
ends up holding both files' prefixes, and `_extract_flask_fastapi_routes`
receives the *entire* merged dict for every file it processes — so a repo
with two or more FastAPI routers named `router` in different files gets
phantom endpoint entries, each file's routes reported under prefixes that
belong to a different file's router entirely. This directly undermines
the accuracy of the deterministic API-endpoint evidence this project's
whole pitch depends on.

**Fix:** key `cross_file_router_mounts` (and pass through per-file) by
`(defining_file, router_name)` instead of `router_name` alone, so a
same-named router in a different file never merges.

---

## 5. Secret-scanner's boundary tightening silently misses `object.ATTRIBUTE = "secret"` assignments

**RESOLVED 2026-08-21.** Added `.` to the left-boundary character class:
`(?:^|[\s_.-])`. Independently verified the minimal fix against all
relevant cases before applying it, including rejecting a subagent's
proposed alternative (a negative lookbehind `(?<![A-Za-z0-9_])`) after
confirming by direct regex testing that it would regress the working
`MY_PASSWORD=` case. New regression test
`test_find_secrets_detects_dotted_attribute_credential_assignment` in
`src/tests/test_secrets.py` covers `self.PASSWORD=`/`cfg.API_KEY=`
(now match) and `MYPASSWORD=` (still correctly doesn't match) - verified
via `git stash` that it fails without the fix and passes with it. Full
`src` suite (1305 passed) and `github-app` suite (1456 passed, 8 skipped)
green. PR: `secret-scanner-boundary`.


**File:** `src/aletheore/secrets.py:83-84` (`generic_credential_assignment`)

```python
r"(?i)(?:^|[\s_-])(PASSWORD|SECRET|API_KEY)\s*[:=]\s*"
r"['\"]?([A-Za-z0-9+/=_.-]{16,})['\"]?(?=\s|$|[,#;)])"
```

Directly verified with the live regex: `self.PASSWORD='mysecretvalue1234567890'`
and `cfg.API_KEY='abcd1234abcd1234abcd'` both fail to match — the `[\s_-]`
left-boundary class allows whitespace, `_`, and `-` before the keyword but
not `.`, so any dotted attribute-assignment credential (`self.PASSWORD =`,
`cfg.API_KEY =` — one of the most common hardcoded-credential shapes in
object-oriented Python/JS/Java code) is silently invisible to this
scanner. Confirmed this is a real regression, not an intentional
trade-off: the boundary class already special-cases `_`/`-` specifically
to keep matching compound names like `MY_PASSWORD` while still rejecting
`MYPASSWORD` (no separator at all) — `.` was simply omitted from that same
class, not deliberately excluded.

**Fix:** add `.` to the boundary character class:
`(?:^|[\s_.-])` — verified this single-character change correctly matches
all of `self.PASSWORD=`, `cfg.API_KEY=`, `PASSWORD=`, `MY_PASSWORD=`,
`MY-SECRET=`, while still correctly rejecting `MYPASSWORD=`.

---

## 6. Unpinned PEP 508 dependency with an environment marker is silently dropped from CVE/license/dead-dependency scanning — reopens the exact blind spot this function exists to close

**RESOLVED 2026-08-21.** Deleted the `has_marker` special case; unpinned
now always falls through to `version = "*"` regardless of a marker,
matching the markerless path. Confirmed via `git blame`/PR history this
was a real regression, not an intentional carve-out: PR #230's own commit
message states its explicit goal was "or no version at all" being kept
rather than dropped, with no marker exception mentioned - the `has_marker`
guard silently preserved the old pre-#230 behavior for exactly the
marker-qualified case, contradicting both #230's stated intent and this
function's own adjacent comment.

Caught mid-implementation that a pre-existing test
(`test_parse_pip_pins_reads_pep621_pyproject_dependencies`, predating
#230) asserted the *opposite* - that an unpinned, marker-qualified
dependency (`tzdata; sys_platform == 'win32'`) should be dropped. Full
`src` suite run surfaced this as a real failure, not a false positive:
verified via git log that this test predates #230 and was never
reconciled with #230's later "keep unpinned declarations too" fix.
Updated it to assert `("tzdata", "*", "PyPI")` is now kept, consistent
with every other unpinned dependency (queried by package name only via
`_query_batch`, per #230). New regression test
`test_parse_pep508_dependency_keeps_unpinned_marker_qualified_dependency`
added; verified via `git stash` that it fails without the fix and passes
with it. Full `src` suite (1306 passed) and `github-app` suite (1459
passed, 8 skipped) green. PR: `pep508-marker-dependency`.


**File:** `src/aletheore/vulnerabilities.py:42-57` (`_parse_pep508_dependency`)

```python
has_marker = ";" in dependency
...
version_match = re.search(r"(?:==|>=|~=)\s*([0-9][^,\s]*)", specifiers)
# A lower bound is the most useful stable approximation for a range. Keep
# unpinned declarations too: downstream checks can query the package as a
# whole instead of silently pretending the dependency was absent.
if version_match is None and has_marker:
    return None
version = version_match.group(1) if version_match else "*"
```

The comment states the intended behavior — keep unpinned declarations
rather than silently dropping them — but the code directly contradicts it
whenever the dependency string also carries a PEP 508 environment marker
(`; python_version < "3.10"`, an idiomatic and common shape). Directly
verified: `_parse_pep508_dependency("typing_extensions")` (markerless,
unpinned) correctly returns `("typing-extensions", "*", "PyPI")`, but
`_parse_pep508_dependency('typing_extensions; python_version < "3.10"')`
(marker, unpinned) returns `None` — silently dropped. This feeds the pin
list `_parse_python_dependencies` builds from `pyproject.toml`'s
`[project.dependencies]`, so any marker-qualified unpinned dependency is
invisible to CVE scanning, license checking, and unused-dependency
detection — the exact three consumers this function's own docstring
context says were previously blind to real dependencies.

**Fix:** delete the `has_marker` special case entirely; always fall
through to `version = "*"` when unpinned, regardless of whether a marker
was present, matching the markerless path's already-correct behavior.

---

## 7. Module-overview chunk boundary uses the wrong symbol when a class precedes the first function, leaking already-indexed symbol source into the "unreachable" head chunk

**RESOLVED 2026-08-21.** Changed `head_end` to compute from
`min(s["start_line"] for s in symbols)` instead of assuming `symbols[0]`
is positionally first. New regression test
`test_build_chunks_module_head_stops_at_a_class_that_precedes_the_first_function`
in `src/tests/test_search_index.py` reproduces the real trigger shape (a
class before the first function) and asserts the head chunk stops before
the class and never contains its declaration; verified via `git stash`
that it fails without the fix (head chunk swallowed the class, `end_line`
6 instead of 2) and passes with it. Full `src` suite (1307 passed) and
`github-app` suite (1459 passed, 8 skipped) green. PR:
`search-index-chunk-boundary`.

This closes out Batch 4 (7/7 findings resolved, 0 remaining).


**File:** `src/aletheore/search_index.py:450, 500`

```python
code_symbols = module["symbols"]["functions"] + module["symbols"]["classes"]
...
symbols = code_symbols + constants
...
head_end = min(symbols[0]["start_line"] - 1, MODULE_CHUNK_MAX_LINES)
```

`functions` and `classes` are two separate lists from the AST walk, each
individually in file order, but concatenating them as functions-then-classes
does not yield a file-order-sorted list. Whenever a class textually
precedes the file's first function, `symbols[0]` is the first function,
not the file's true first symbol. Confirmed with a real example already
in this repo: `github-app/app_server/url_validation.py` has `class
UnsafeURLError` at line 6 and its first function `_is_disallowed_ip` at
line 13 — `symbols[0]` resolves to line 13, so `head_end = 12`, and
`lines[:12]` (the computed "head"/module-overview chunk) swallows the
entire `UnsafeURLError` class declaration and body, content that is also
separately indexed as its own class chunk. This directly contradicts the
surrounding comment's own claim that the head only covers what "symbol
extraction structurally cannot reach," and dilutes the module-overview
chunk's actual docstring/import content with unrelated duplicated symbol
source — degrading the retrieval-quality gains PR #208 was built to
measure.

**Fix:** compute `head_end` from `min(s["start_line"] for s in symbols) - 1`
instead of assuming `symbols[0]` is positionally first, or sort `symbols`
by `start_line` once before use.

---

**Three things checked and found still solid — reported for completeness,
not as findings:**

- **The scan-cache trust-boundary fix is actually complete.** #195 closed
  it for the hosted scan-worker, #197 found and closed a second instance
  in the public demo sandbox, #199 found and closed a third in the GitHub
  Action — three fixes for one bug class, the same pattern Batch 2 found
  *unfixed* three more times for the diff-marker collision. I checked
  whether a fourth instance exists: `ALETHEORE_DISABLE_LOCAL_SCAN_CACHE`
  is set in exactly four places (`action.yml` x2, `jobs.py`,
  `demo_sandbox_entrypoint.sh`), and `subprocess.run(["aletheore", "scan",
  ...])` — the only way an untrusted checkout gets scanned — appears in
  exactly one place (`jobs.py:479`), already covered. The three remaining
  in-process `scan_repository()` callers (`cli.py`, `watch.py`,
  `mcp_server.py`) are all local-machine, single-user contexts where the
  cache's original trust assumption still holds. No gap found.
- **The prompt-injection guard (#202) held up across every prompt added
  since.** #202 ported `_INJECTION_GUARD`-style framing into 7 LLM
  prompt sites; I checked every `*_SYSTEM_PROMPT` constant that exists
  now, including two added later (`BATCH_SUBSYSTEM_WRITING_SYSTEM_PROMPT`,
  `BATCH_FILE_PAGE_WRITING_SYSTEM_PROMPT` in `live_wiki.py`, from the
  batching work) and `live_docs.py`'s `COMBINED_SYSTEM_PROMPT` (from the
  generate+polish merge). All three correctly append the guard. No gap
  found.
- **The Paddle authorization-claim fix (#218) is intact and unregressed.**
  `webhooks/paddle.py` still derives `installation_id` exclusively via
  `unsign_checkout_installation_id(installation_token, ...)` on a signed,
  TTL'd, purpose-salted token — never a raw `custom_data.installation_id`
  — confirmed against the current source, not just the original diff.

---
---

# Batch 5 — PRs #203, #231–#246

(`759b915`, `d0c347a..a2cac8a` on `master` — the full gap between Batch 4,
which covered up to `8983b1e`, and Batch 3, which started at `a2cac8a`.
This range was previously unaudited.) Covers the schema-mapping/ER-diagram
feature (#203), retrieval-quality fixes (demoting docs/demo/benchmark
paths #231, multi-prefix router endpoint compounding #232, Java visibility
from Java's own modifiers #233, .NET/Java test-project naming exclusions
#234, narrowing Java/C# declaration-only-file demotion #236, language-named
query routing #237), and the embedding-cache/scan-worker churn (TEI
replacing Ollama #238, a TEI memory-limit hotfix #239, a same-day revert
back to Ollama #241, the per-repo checkout lock + scan-worker scaled to two
replicas #242, the production switch to self-hosted jina-embed #244, and
the app-server crash-loop fix #246).

**Three things worth naming up front:**

- **A brand-new paid feature's flagship output silently corrupts on
  ordinary SQL comments.** #203's schema-mapping module claims
  comment-awareness throughout (its own docstring and commit message say
  so), but three of its lower-level parsing helpers don't consistently
  reuse the one function that actually implements it — an inline `--`
  comment inside a `CREATE TABLE` column list silently drops the real
  column that follows it and fuses its text into a bogus column instead
  (finding 1), the same gap lets an unbalanced `(` inside a comment
  swallow an entire subsequent table with zero trace (finding 2), and a
  `;` inside a `/* */` block comment prematurely ends a statement
  (finding 3). All three were reproduced by actually running the parser,
  not inferred from reading.
- **A product whose job is enumerating reachable API surface for security
  review can silently drop a real, reachable, unauthenticated-by-default
  endpoint from its own map.** #232 fixed multi-prefix compounding, but a
  router mounted once with no `prefix=` at all alongside one prefixed
  mount loses the unprefixed endpoint entirely (finding 4) — the same
  failure class the PR was written to close, just for a combination its
  own regression test didn't cover.
- **Two retrieval-quality fixes regress on close variants of the exact
  phrasing they were built to fix**, both confirmed by direct
  reproduction: #234's `.NET` test-project exclusion also hard-excludes
  any ordinary word ending in "-tests" (`Contests`, `Protests`, `Attests`
  — finding 5), and #237's language-aware routing declines detection on
  the plain phrasing "in C++" / "in C#" because of a regex word-boundary
  collision with its own unambiguous cpp/csharp patterns (finding 6) —
  giving up the exact benchmark gain the PR measured, on the shorter,
  more natural version of the same query.

**Method:** unchanged from Batches 1-4 — every finding below was verified
against current source (not the historical diff), and wherever the
underlying function is callable directly, by actually running it with a
crafted input and pasting the real output. Given #238-#246's revert churn
(Ollama → TEI → Ollama → jina-embed), findings 7-8 were specifically
checked against the state of the code *after* `a2cac8a`, not any
intermediate commit — confirmed the live embedder today is jina-embed,
with no `ollama`/`tei` service or Dockerfile left anywhere in the tree.

---

## 1. A `--` comment inside a `CREATE TABLE` column list silently drops the real column that follows it

**RESOLVED 2026-08-21.** Fixed together with findings 2-3 below - all
three shared one root cause (comment-skipping not consistently reused
across the module's scanning helpers) and one fix: a shared
`_comment_end()` helper, reused by `_split_top_level`,
`_tokenize_column_definition`, `_read_parenthesised_body`, and
`_skip_to_statement_end`. Regression tests written first (confirmed
failing against the unfixed code), one per finding, in
`src/tests/test_schema_map.py`. Full `src/` suite (1312 tests) green.
PR: `fix/schema-map-comment-parsing` (#338).

**File:** `src/aletheore/schema_map.py:176-214` (`_split_top_level`),
`:217-258` (`_tokenize_column_definition`)

Both functions are literal-aware (handle `'...'`) and paren-depth-aware,
but neither recognizes `--` or `/* */` comments — comment text is scanned
as if it were ordinary column-definition source. Given:

```sql
CREATE TABLE things (
    id serial PRIMARY KEY,
    -- deprecated
    name text
);
```

reproduced output (`extract_schema`, run against the current working
tree):
```json
"columns": [
  {"name": "id", "type": "SERIAL", "primary_key": true, ...},
  {"name": "--", "type": "DEPRECATED NAME TEXT", "primary_key": false, ...}
]
```
The real `name text` column never appears — it's absorbed into a bogus
`"--"` column's `type` field, with no entry in `unsupported` and no error
of any kind. This is not a contrived edge case: an inline comment on its
own line inside a `CREATE TABLE` body is one of the most ordinary real-
world SQL authoring styles. It silently corrupts the flagship deliverable
of this PR (the ER diagram and the AIR `schema` section every downstream
consumer — dashboard, MCP, LLM prompts — reads).

A variant with a comma inside the comment (`-- deprecated, do not use this
column`) is worse still — it produces two garbage pseudo-columns and still
drops the real column.

**Fix:** strip comments (both `--` and `/* */`, literal- and
dollar-quote-aware, same rules `_Cursor.skip_whitespace_and_comments`
already implements) from the column-list body before/while splitting, not
just from the top-level statement stream.

---

## 2. An unbalanced paren inside a `--` comment inside a column list swallows a whole subsequent table into one, silently

**RESOLVED 2026-08-21** — see finding 1's resolution note.

**File:** `src/aletheore/schema_map.py:408-430` (`_read_parenthesised_body`)

This function tracks paren depth character-by-character to find the
balanced `)` that closes a `CREATE TABLE (...)` body, but — like finding
1's functions — has no comment awareness. Given:

```sql
CREATE TABLE users (
    id serial PRIMARY KEY,
    -- see issue (#123 for context, not closed here
    name text NOT NULL
);

CREATE TABLE orders (
    id serial PRIMARY KEY,
    user_id integer REFERENCES users(id)
);
```

the stray `(` inside the comment bumps `depth` to 2 with nothing to bring
it back down until the *next* real `)` in the file — which turns out to
be inside `orders`' `REFERENCES users(id)`, not `users`' own closing
paren. Reproduced result: the `orders` table disappears entirely, its
`user_id` FK relation is never recorded, and the rest of the migration
file gets crammed into one bogus column's type string on `users` — a
whole table plus its relation vanish from the schema with zero trace in
`unsupported`. Referencing an issue/PR number in a comment
(`-- see (#123`, `-- fixes (JIRA-456`) is a common pattern, and any
comment with one more `(` than `)` anywhere before the real closing paren
triggers this.

**Fix:** same as finding 1 — make comment-skipping part of the
paren-balance scan, not just the top-level statement scan.

---

## 3. A `;` inside a `/* ... */` block comment prematurely ends a statement

**RESOLVED 2026-08-21** — see finding 1's resolution note.

**File:** `src/aletheore/schema_map.py:112-139` (`_skip_to_statement_end`)

This function explicitly special-cases `--` comments (line 136-138) but
has no equivalent branch for `/* */` block comments. Given:

```sql
ALTER TABLE widgets ADD COLUMN status text /* values: active; inactive; deleted */ DEFAULT 'active';
```

the first `;` inside the block comment is treated as depth-0 statement
end. Reproduced result: the column change is recorded with a mangled
type (comment fragment fused in), the real `DEFAULT 'active'` is lost,
and the remainder of the comment plus the real terminator show up as
bogus `unsupported` entries instead. The module's own docstring and this
PR's commit message claim comment-aware statement splitting "so a
`DEFAULT ';'` does not terminate a statement early" — true for `--`
comments, false for `/* */` block comments.

**Fix:** add a `/*` branch to `_skip_to_statement_end` mirroring the
existing `--` branch.

**Test-suite gap (context for findings 1-3):** `src/tests/test_schema_map.py`
has one comment-related test, but it only places a comment *before* a
`CREATE TABLE` statement at the top level — the position
`_Cursor.skip_whitespace_and_comments` already handles correctly. No test
puts a comment *inside* a column list (findings 1-2) or exercises a block
comment containing a semicolon (finding 3) — the exact "tests only the
happy path" pattern that let all three ship.

---

## 4. FastAPI router mounted with an implicit (no-`prefix=`) mount alongside a prefixed mount silently drops the implicit mount's endpoint

**RESOLVED 2026-08-21.** `_collect_fastapi_include_prefixes` now records an
explicit empty-string mount for a prefix-less `include_router` call
instead of skipping it - `compose()`'s existing `if mount_prefix:` check
already treats an empty string as "nothing to join," so this alone fixed
the fan-out with no change needed on the consumer side. Regression test
written first (confirmed failing against the unfixed code) in
`src/tests/test_endpoints.py`. Full `src/` suite (1312 tests) green.
PR: `fix/endpoints-implicit-router-mount` (#339).

**File:** `src/aletheore/endpoints.py:117-123` (collection) and `:265`
(fan-out)

PR #232 fixed the case where a router mounted at two or more *explicit*
prefixes was compounding them into one wrong path. But
`_collect_fastapi_include_prefixes` only records a mount at all when the
`include_router(...)` call carries an explicit `prefix=` keyword
argument:

```python
prefix_arg = next(
    (arg for arg in args.named_children if arg.type == "keyword_argument"
     and source[...].decode() == "prefix"),
    None,
)
if prefix_arg is None:
    continue
```

`app.include_router(router)` — no `prefix=` at all, an extremely common
FastAPI pattern for mounting a router unprefixed alongside also mounting
it under a versioned or admin prefix elsewhere — contributes *nothing* to
the mounts list for that router. If the same router also has one
explicitly-prefixed mount elsewhere, `mount_prefixes` is non-empty (from
that other call), so the fan-out's `if mount_prefixes` branch is taken and
the `else` branch that would emit the implicit/root-mounted path never
runs — the router's root-mounted endpoint is silently dropped, exactly
the failure mode PR #232 was written to fix, just for the
prefix-vs-no-prefix combination its own regression test didn't cover (the
test only exercises two *explicit* prefixes).

**Reproduction:** a router mounted via `app.include_router(router)` and
`app.include_router(router, prefix="/admin")` — `map_api_endpoints`
returns only `/admin/{user_id}`; the root-mounted `/{user_id}` endpoint
never appears. (The general N-prefix case does work correctly for 3+
*explicit* prefixes — no hardcoded 2-prefix assumption.)

For a product whose job is enumerating reachable API surface for security
review, a real, reachable, unauthenticated-by-default endpoint silently
missing from the map is a meaningful gap, not just a search-quality nit.

**Fix:** don't gate collection on `prefix_arg is not None` — record an
explicit `""` (or a sentinel) mount for a prefix-less `include_router`
call too, so `mount_prefixes` is non-empty whenever the router has *any*
recorded mount, and the implicit mount's own unprefixed path is included
in the fan-out alongside the explicit ones.

---

## 5. `_is_test_path`'s .NET-suffix fix excludes any directory whose name is an ordinary English word ending in "-tests" (Contests, Protests, Attests)

**RESOLVED 2026-08-21.** Fixed together with findings 6 and 9 below (same
file, bundled into one PR since each is independent but narrow). Checked
against the segment's original case via a new `_has_dotnet_test_suffix`
helper instead of the already-lowered `parts` list, which had destroyed
the case-transition/separator signal that distinguishes "UnitTests" from
"Contests". Regression test written first, confirmed failing against
the unfixed code. Full `src/` suite (1317 tests) green.
PR: `fix/search-index-retrieval-regressions` (#340).

**File:** `src/aletheore/search_index.py:274`

```python
if any(part.endswith("tests") or part.endswith(".test") for part in parts):
    return True
```

Added in #234 to catch `.NET`-style test-project names (`UnitTests`,
`AutoMapper.DI.Tests`). The comment reasons through the one collision it
checked for — `"latest"` doesn't end in `"tests"` — but `endswith("tests")`
also fires on any ordinary word ending in the same five letters:
`Contests`, `Protests`, `Attests`, `Retests`. Unlike #231's demotion, this
function's callers treat a `True` result as a **hard exclusion**
(`search_index.py:387`) — the file is dropped from the index entirely,
not merely ranked lower.

**Reproduction:**
```python
_is_test_path("src/Contests/ContestController.cs")   # -> True  (wrong)
_is_test_path("src/Protests/ProtestTracker.java")     # -> True  (wrong)
_is_test_path("src/Attests/Foo.cs")                   # -> True  (wrong)
_is_test_path("src/Requests/PullRequests.cs")         # -> False (safe)
```
A contest-judging platform's `Contests/` directory, a civic app's
`Protests/` tracker, or an attestation service's `Attests/` module would
have every file under that directory silently excluded from retrieval —
the same failure class already documented and fixed once in this codebase
for `_looks_like_test_file`'s unanchored substring match (Batch 2 finding
#7).

**Fix:** require a word boundary/separator before the `"tests"` suffix
(a preceding `.`, `-`, `_`, or a case transition), rather than a bare
`str.endswith` check.

---

## 6. Language-aware query routing declines detection on the exact "in C++" / "in C#" phrasing it was built to handle

**RESOLVED 2026-08-21** — see finding 5's resolution note (same PR,
`#340`). Added a `(?![+#])` negative lookahead to the cued bare-"c"
pattern so it no longer matches inside "C++"/"C#", which the already-
correct unambiguous patterns claim first.

**File:** `src/aletheore/search_index.py:1295-1298`

```python
_CUED_QUERY_LANGUAGES = (
    ...
    (rf"\bc\s+{_LANGUAGE_CUE}\b|\bin\s+c\b", "c"),
)
```

`\bin\s+c\b` is meant to catch a bare "C" used as a language reference. But
`\b` is a boundary between a word and *non*-word character — and `+`/`#`
are both non-word characters, so `\bin\s+c\b` also matches inside
`"in C++"` and `"in C#"`. Both already match unambiguously via
`_UNAMBIGUOUS_QUERY_LANGUAGES`'s `c\+\+|cpp` / `c#|csharp` patterns, so a
query naming C++ (or C#) plainly, with no other language mentioned,
populates `found` with *two* entries, and the `len(found) != 1` guard
(correctly designed to decline genuinely two-language queries) fires and
returns `None` — the language filter never activates.

**Reproduction:**
```python
_detect_query_language("where is this implemented in C#")               # -> None
_detect_query_language("where is TBinaryProtocol implemented in C++")   # -> None
_detect_query_language("explain the C++ port of this")                 # -> 'cpp'  (has a cue word after)
```
The shipped regression test only exercises `"in the C++ library"` — the
word `"the"` breaks the `\bin\s+c\b` adjacency, so the test never hits
this path. The shorter, at-least-as-natural phrasing — the exact wording
apache/thrift's own cross-language benchmark questions use ("Where is
TBinaryProtocol implemented in C++?") — silently falls back to unfiltered
search, giving up the top-3 60%→73.3% / top-5 60%→93.3% gain this PR
measured for precisely the C++/C# rows of that benchmark.

**Fix:** exclude `+`/`#` from counting as a word boundary for this check
(e.g. `\bin\s+c\b(?![+#])`), or run the cued "c" pattern only after
checking whether an unambiguous cpp/csharp match already claimed the same
span.

---

## 7. Nothing in CI actually boots the app-server/scan-worker images — the exact defect class that caused #246 can silently ship again

**RESOLVED 2026-08-21.** Added a smoke-test step to
`container-security.yml` that runs each built image's real entry-module
import chain inside the container (app-server with dummy required env
vars - settings are only required, not connected to, at import time, so
no real Postgres/Redis needed; scan-worker and jina-embed need none).
Verified two ways: confirmed all three current images pass with real
`docker run`, and confirmed the smoke test actually catches the #246
defect class by simulating the original bug (removing the scan_worker
COPY line) and observing the same `ModuleNotFoundError` a real prod
container hit. PR: `fix/ci-smoke-test-docker-images` (#342).

**File:** `.github/workflows/container-security.yml:14-61`,
`github-app/Dockerfile.app-server:12-13`,
`.github/workflows/github-app-tests.yml:58-67`

#246's root cause (confirmed against the `a2cac8a` diff): #243 added
`dashboard.py` imports from `scan_worker.github_api` and
`scan_worker.live_wiki`, but `Dockerfile.app-server` never had a
`COPY github-app/scan_worker ./scan_worker` line — only `app_server`,
`scripts`, `migrations`. The image built fine (Python doesn't validate
imports at `docker build` time) and only failed at container *start*,
when uvicorn actually imported the module — latent since #243 merged,
only surfacing on the first app-server redeploy since then. It was
hotfixed live on the prod host before the fix landed in git.

The fix itself is correct and confirmed present today — both
`Dockerfile.app-server:12-13` and `Dockerfile.scan-worker:12-13` now
explicitly `COPY` both `app_server` and `scan_worker`. But nothing added
to CI catches the *class* of bug:

- `container-security.yml` builds each image purely to hand it to Trivy/
  Anchore — it never runs the built image, so a missing-package
  `ImportError` at process start would still pass this workflow green,
  exactly as it did the first time.
- `github-app-tests.yml` runs `pytest` directly against the checked-out
  source tree via `pip install -e .` — it never builds or runs either
  Dockerfile, so every import that a Docker-built image would fail on is
  trivially present when running from source.
- No workflow in the repo runs `docker run` or hits `/healthz` against
  either built image.

The very next time `app_server` (or `scan_worker`, which app-server now
also imports) gains a new cross-package import, the same silent,
CI-green, first-real-redeploy crash-loop can happen again, with no
automated check standing between a merged PR and finding out on the prod
host.

**Fix:** add a cheap smoke-test step to `container-security.yml` (or a
new lightweight job) that `docker run`s the built `app-server` and
`scan-worker` images — even just importing the entry module inside the
container, or hitting `/healthz` after a short wait — so a missing
package fails CI instead of prod.

---

## 8. The embedding-cache purge has no runtime safety net of its own — a future embedder switch can silently mix incompatible vectors again

**RESOLVED 2026-08-21.** Added a `model`/`embedder` column to both cache
tables and a `CURRENT_EMBEDDER` constant in `embedding_client.py` (the
one place a real embedder switch already has to touch), tagged on every
write and filtered at the SQL level on every lookup - a mismatched or
NULL (pre-migration) row now simply never matches, no purge migration
needed on the next switch. Verified with a real-Postgres integration
test per cache module proving a row written under one embedder identity
never matches a lookup under a different one. Full `github-app` suite
(1456 tests, including `test_jobs.py`) green.
PR: `fix/embedding-cache-model-identity` (#343).

**File:** `github-app/migrations/049_purge_cache_for_embedder_switch.sql`,
`github-app/migrations/012_evidence_packet_cache.sql:4-16`,
`github-app/migrations/013_flash_review_cache.sql:4-16`,
`github-app/scan_worker/packet_cache.py:34-42`,
`github-app/scan_worker/flash_review_cache.py:28-36`

`049_purge_cache_for_embedder_switch.sql`'s own comment names the exact
risk migration 018→jina-embed was written to close: nomic and jina are
both 768-dim, so an old row would pass the array-length check in
`packet_cache.py`'s cosine similarity and get compared against new jina
query vectors — meaningless, since they're different embedding spaces —
hedging only that downstream re-validation "would likely" (not "always")
catch a resulting false match. Confirmed this hedge is the *only*
protection:

- Neither cache table has an embedder-identity column —`model_used`
  records the LLM used for the writing-stage/review call, not the
  embedding model.
- `_cosine_similarity` in both cache modules checks only vector-length
  equality before comparing — any two same-dimension vectors from *any*
  embedder are compared as if commensurate.
- The downstream re-validation this leans on (`live_wiki.py:507-533`)
  only checks that citations in the cached prose resolve against current
  evidence — it never checks the cached content is semantically related
  to the current query. A wrong-embedder hit that happens to clear
  `SIMILARITY_THRESHOLD = 0.92` sails through as long as its citations
  happen to still resolve.
- Migrations only run from `app-server`'s own entrypoint;
  `scan-worker`'s entrypoint never runs `migrate.py`, and
  `docker-compose.yml` has no `depends_on` from `scan-worker`/
  `scan-worker-2` on `app-server`. Nothing in the stack guarantees the
  `TRUNCATE` finishes before a scan-worker replica starts writing cache
  rows again during a rolling deploy.

Not an active bug against jina-embed today — the switch is weeks old and
stable, and nothing in the subsequent tuning history (#257-#267)
suggests a wrong-embedder hit ever actually happened. But the mechanism
that would prevent it next time is entirely deploy-time discipline
(remembering to write a `TRUNCATE` migration that happens to land before
new code writes anything) with zero code-level enforcement — still live
in the current schema and compose file for whichever embedder change
comes next.

**Fix:** add a `model`/`embedder_name` column to both cache tables,
populate it on every write, and filter (or zero the similarity of) rows
that don't match the currently-configured embedder at lookup time — makes
the cache self-healing on an embedder switch without needing a purge
migration at all.

---

## 9. Java/C# declaration-only-file demotion still fires when the only non-interface type in the file is an abstract class, not a concrete one

**RESOLVED 2026-08-21** — see finding 5's resolution note (same PR,
`#340`). Excluded `abstract` from `_JAVA_CSHARP_CLASS_DECL`'s modifier
alternation.

**File:** `src/aletheore/search_index.py:294-297, 353-354`

#236's own commit message states the fix's intent precisely: "demote a
Java/C# file only when it has no **concrete** class alongside its
interface." But `_JAVA_CSHARP_CLASS_DECL` lists `abstract` as one of the
allowed leading modifiers it matches — so a file containing only an
interface and an `abstract class` (no instantiable implementation at
all, functionally still pure contract) is treated identically to a file
with a genuine concrete implementation, and the file-level demotion never
applies.

**Reproduction:**
```python
src = '''
public interface Shape { double area(); }
public abstract class AbstractShape implements Shape {
    public abstract double area();
    public String describe() { return "shape"; }
}
'''
_is_declaration_only_file("Shape.java", "java", src)  # -> False (arguably should be True)
```
(The two boundary cases the audit asked about directly — interface-only,
and interface plus a genuine concrete class — both work correctly.)

Narrower and lower-stakes than the other findings in this batch (it
affects ranking within a Java/C# corpus, not correctness of extraction or
a hard exclusion), but it does contradict the fix's own stated boundary
condition.

**Fix:** exclude `abstract\s+` from `_JAVA_CSHARP_CLASS_DECL`'s modifier
alternation (or separately check a matched `class` line doesn't have
`abstract` among its modifiers).

---

## Checked and found solid

- **#231's `_is_auxiliary_path`** (`search_index.py:1216-1218`) matches on
  exact, lowercased path *segments* against a fixed set, not a substring —
  confirmed `docs_generator.py` (a filename, not a directory segment) and
  a hypothetical `benchmark_runner.py` core-code file are *not* demoted.
  This is a demotion (rank penalty), not an exclusion, so even a genuine
  false-positive directory name would only cost rank, not visibility. The
  marker list is necessarily incomplete relative to every possible
  docs/demo directory name a repo could use — a coverage gap measured
  empirically, not an anchoring bug.
- **#233's `_java_is_public`** was tested directly against a real
  tree-sitter parse covering every boundary condition: explicit
  visibility, package-private defaults, annotation-preceded methods,
  interface methods (implicit/static/default), enum methods, and
  top-level type visibility. All matched expected Java semantics exactly.
  Two gaps the commit message explicitly discloses as unfixed (C#'s
  `is_public` computed the old way; Java never extracting
  `constructor_declaration`/`annotation_type_declaration`) are still
  present but were never in scope for this PR and are already disclosed.
- **#242's per-repo checkout lock is correctly scoped and leak-safe.**
  `repo_checkout_lock` (`scan_worker/db.py:477-512`) is a plain blocking
  `pg_advisory_lock`/`pg_advisory_unlock` pair on a dedicated, non-pooled
  connection, released in a `finally` regardless of exception — a crashed
  holder's session-scoped lock releases automatically on connection
  close, so there's no leak path. No separate check-then-acquire step, so
  no TOCTOU gap. Traced every other lock this codebase takes
  (`installation_spend_lock`) and confirmed it's never acquired from
  inside a `repo_checkout_lock` block or vice versa — no lock-ordering
  cycle available for a deadlock.
- **#239's TEI memory-limit saga is moot against current code.** TEI is
  fully gone from the tree. The mem_limit question was re-litigated twice
  more after this range (#261, #263), both citing concrete, on-host
  measurements rather than a repeat of the original under-provisioned
  guess. Nothing about the current jina-embed limit looks undersized by
  the same pattern that caused #239's OOM-kills.

---

## Suggested priority — Batch 5

Findings 1-3 are the most severe: a brand-new paid feature's core data
(schema/ER-diagram) silently corrupts on ordinary, common SQL comment
styles, with zero error trace — fix together, same root cause (comment-
skipping not consistently reused across the module's scanning helpers).
Finding 4 is next: a security-relevant enumeration product silently
dropping a real endpoint from its own map is a meaningful gap, not a
nit. Findings 5-6 are real quality regressions on the exact cases their
own PRs were built to fix — findings 5 is a hard exclusion (worse than
6's soft fallback-to-unfiltered-search). Findings 7-8 are both real but
currently-dormant hardening gaps — neither is an active bug today, but
both are exactly the mechanism that would let a class of bug this range
already hit once (#246, a live crash-loop) happen again silently. Finding
9 is narrow and lower-stakes. Worth closing 1-4 before or very close to
launch; 5-9 are real but can follow shortly after.

---
---

# Fixed — gaps found via MCP tool comparison testing (2026-08-20)

Not part of the PR-history audit above. Found by running paired Aletheore-vs-Repowise
MCP tool comparison agents against this repo (dead code, security surface, hotspots/
ownership, and the spend-cap question from Batches 1-3) as an internal quality check,
then fixed one by one, each with a regression test. Full suite (1289 tests) green after
all four.

1. **`aletheore_dead_code` false-positived on RQ string-dispatch and pytest
   `conftest.py`.** 3/3 spot-checked "unreachable module" findings
   (`scan_worker/jobs.py`, `scan_worker/demo_scan.py`, `src/conftest.py`) were live
   code, invisible to static import analysis because they're invoked via
   `queue.enqueue("module.func", ...)` string dispatch or pytest's filename-based
   auto-discovery. Fixed in `src/aletheore/dead_code.py`: added `conftest.py` to
   `ENTRY_POINT_FILENAMES` (matching `wiki_mapping.py`'s existing
   `_DEMOTED_BASENAMES` convention, which already special-cased it elsewhere in
   this same codebase), and added `_referenced_by_dotted_string` — checks whether
   an otherwise-unreachable module's dotted path appears as a quoted string
   literal anywhere else in the repo before flagging it dead.

2. **`aletheore_ownership` had no target parameter at all** (Batch 3 finding #6,
   independently re-confirmed by a fresh agent that hit the same wall unprompted).
   Fixed in `src/aletheore/mcp_server.py`: `_register_query_wrapper_tools` now
   recognizes an `optional_target_kinds` set (`{"ownership"}`) and generates a
   `target: str | None = None` signature for those, instead of the previous
   binary "always required" / "never accepted" split — `find_ownership` already
   branched on `target` internally, it just could never receive one from MCP.

3. **`aletheore_layer_violations` returned "inconclusive" for this repo.** Not a
   code bug — `detect_layer_violations`'s built-in `LAYER_FOLDER_MARKERS` only
   recognizes classic Clean-Architecture folder names (`domain`, `application`,
   `infrastructure`, etc.), which this repo doesn't use. The already-wired
   `.aletheore.json` → `layer_markers` override for exactly this case was just
   never populated. Set it to `{"aletheore": 0, "github-app": 1}` (verified via
   `grep` that `src/aletheore/` never imports from `github-app/`, so this
   boundary is unambiguous) and re-scanned: now reports `convention_detected:
   true`, 2 layers, 0 violations — a real result instead of an empty one.

4. **Secrets scanner missed a real API key shape.** `github-app/.env:6`
   (`GEMINI_API_KEY=AQ.Ab8R...`) went undetected — the newer Google AI Studio key
   format contains a literal `.` that the `generic_credential_assignment`
   pattern's value character class didn't include, truncating the match to 2
   characters (under the 16-char minimum) and silently dropping the whole
   finding rather than just leaving it unredacted. Fixed in
   `src/aletheore/secrets.py`: added `.` to the value character class.

---

## `Claude_Audit.md` / `immediate_issue_PRs.md` verification pass — 2026-08-21

Two separate audit documents (16 findings + 4 "immediate" duplicates/subsets)
sitting in the repo root, untracked, none marked resolved in the documents
themselves. Independently re-verified every finding against current master
rather than trusting either document's dated claims — the standing discipline
this whole file has followed.

**Result: 15 of 16 `Claude_Audit.md` findings, and all 4 `immediate_issue_PRs.md`
items, were already resolved** on master by the time each was checked — by
PR #250, #258, #283, #284, #304, #305, #315, and (for 3 of the 5
`installation_spend_lock` sites in finding #1) by this session's own Batch 4
work (PRs #331/#332). Confirmed each by reading the actual current
implementation, not by trusting the audit's line numbers or the commit
messages' claims:

- #1 (spend lock held across LLM work, 5 sites) — all 5 now use
  `_IncrementalSpendBudget`'s atomic per-call reservation instead of holding
  a lock across minutes of work; Docs sites were already correct, Wiki sites
  and fix-suggestion fixed today (Batch 4 findings #2/#3 above).
- #2 (free→paid gate race across webhook events) — `claim_free_to_paid_plan`/
  `claim_paid_setup` (`app_server/db.py`) now atomically gate both the plan
  transition and the one-time setup, shared across Paddle and Marketplace.
- #3 (stale 5s→60s replay-window comment) — comment already says 60s.
- #4 (`_is_safe_next_path` control chars) — `any(ord(char) < 0x20 ...)` guard
  already present.
- #5 (`/v1/managed-audit` body-size cap + materialize-before-limit) —
  endpoint is in `MAX_BODY_BYTES_BY_PATH`; the decode-before-quota-check
  ordering was deliberately kept, now with an explicit comment explaining
  why (moving the scan-slot reservation earlier would burn a slot on a
  malformed request). Residual: no dedicated per-request rate limiter beyond
  the business quotas — minor, not pursued.
- #6 (embeddings rate-limit key + unlocked spend check) — bucket is now
  `installation-wide AND per-repo`, closing the caller-controlled-rotation
  gap; the "unlocked spend check" is moot since hosted embeddings run on
  self-hosted Jina (no metered per-call dollar cost to protect).
- #7 (coarse "administers" access-level permission) — `_has_real_admin_permission`
  now gates both the billing portal and the initial-seat claim; this is
  PR #315, visible in this session's own starting git log.
- #8 (affiliate commissions never reversed on refund) — `adjustment.created`
  and refund-shaped `transaction.updated` now call `reverse_commission`.
- #9 (`clear_api_key` permission care) — both it and `_save_key` now share
  `_write_credentials`, the fd+fchmod+write helper.
- #10 (citation pattern left boundary) — `(?<![\w./-])` already present.
- #11 (crash mid-webhook drops plan change) — `claim_webhook_delivery` now
  reclaims a stale (>15 min) claim, the exact recovery path the finding
  asked for.
- #12 (ChatOps trigger substring match) — line-anchored
  (`line.strip().startswith`) plus a bot-author filter both present.
- #13 (diff `--` marker collision) — `_diff_valid_lines` already tracks
  `prev_blank` and only treats a marker match as a real boundary there.
- #14 (19 unpruned tree traversals) — `_iter_pruned_tree` already shared
  across all six detectors.
- #16 (no file-size guard before parsing) — `MAX_SOURCE_FILE_BYTES` already
  enforced at all three `read_bytes()` sites in `graph.py`.

**#15 was the one real gap, now fixed** (PR `java-csharp-prepass-memory`):
`build_module_graph`'s Java/C# pre-pass caches `dict[Path, tuple[bytes, Tree]]`
for every file of that language simultaneously (measured independently at
82MB RSS on a 512-file C# repo, extrapolating to ~1.6GB on 10,000 files -
tree-sitter trees run ~37x their source size). The main loop reused each
entry via a plain dict lookup rather than releasing it once consumed, so the
whole cache stayed pinned in memory until `build_module_graph` returned -
i.e. until every other file in the repo, any language, had also been
processed. Changed the main loop's lookup to `.pop(path)` for both
`java_pre_parsed` and `csharp_pre_parsed`, releasing each entry's memory as
soon as it's consumed instead of holding it for the rest of the scan. Does
not reduce the pre-pass's own peak (structurally required - it must see
every file's package/namespace before it can infer a source root at all),
only how long that peak is sustained afterward.

New regression tests (one per language,
`test_build_module_graph_releases_a_{java,csharp}_prepass_tree_once_the_main_loop_consumes_it`)
use `sys.getrefcount()` on the specific `Tree` object as a direct, reliable
proxy for "is the pre-parsed cache still holding a reference to this" -
weakrefs aren't supported on tree-sitter's `Tree` type, and RSS measurement
is real but flaky/slow for a unit test. Verified by hand (and via `git
stash`) that reverting the `.pop()` fix changes the asserted refcount (4
without the fix, 3 with it) - both tests fail without the fix and pass with
it. Full `src` suite (1310 passed) and `github-app` suite (1459 passed, 8
skipped) green.

# Finding outside the batch audits — retrieval index silently mismatched embedders (2026-08-21)

**RESOLVED 2026-08-21.** Not from a PR-range audit - surfaced while
re-verifying a published retrieval-latency claim on the website
(`website/benchmarks.html`: "RepoWise retrieval is faster in-process - 68ms
against our 125ms"). Chasing an apparent Aletheore latency regression led
to a real, live retrieval-quality bug instead: a local repo's index, built
with Ollama's `nomic-embed-text` (768-dim), got queried with a vector from
Aletheore's own hosted embedding endpoint (`jina-embeddings-v2-base-code`,
also 768-dim) because a valid hosted credential existed on the machine and
`search_index()` (as of 0.9.0, `src/aletheore/search_index.py`) correctly
prefers hosted embeddings when available - a deliberate, separate
correctness fix. The existing `IndexDimensionMismatchError` guard only
compared vector length, so two different models sharing a dimension passed
silently. Measured directly: querying the mismatched pair returned 25%
top-1 accuracy against ground truth instead of the ~72-75% the same index
gave when queried with the matching provider - coherent-looking, plausible
file paths, just wrong, with zero error raised.

**Fix** (PR `fix/search-index-embedder-identity`, #347):
`_embed_in_batches`/`_embed_stale_by_hash`/`_reusable_vectors` now also
report which embedder actually produced a set of vectors
(`"hosted:<model>"` or `"local:<model>"`), stamped into every index row
alongside the vector. `build_index`'s reuse guard checks embedder identity
in addition to dimension before reusing an unchanged chunk's old vector,
and `search_index` raises the existing `IndexDimensionMismatchError` when
the query's embedder differs from the index's, even on matching dimension.
An index built before this column existed falls through safely to the old
dimension-only behavior (`_table_embedder` returns `None`, both checks
skip) - self-healing on the next rebuild, the same pattern as PR #343's
embedding-cache identity fix (Batch 5 finding 8).

Verified: 3 new TDD tests confirmed failing against the unfixed code,
passing after; full `src` suite green (1329 passed, excluding two
pre-existing, unrelated stray duplicate test files -
`src/tests/test_endpoints 2.py` - already failing identically with this
fix's changes stashed out); a real, non-mocked end-to-end smoke test
against local Ollama and a real hosted build both confirmed the `embedder`
column is stamped correctly and a self-consistent search still succeeds
normally (no false positive).

Separately: the website's "68ms vs 125ms" latency claim itself was
re-verified and holds - re-run with current versions (Aletheore 0.9.0,
RepoWise 0.27.0) on a fresh, faithful, local-only flask corpus gave
Aletheore 52.9ms mean / 50.5ms median (down from 125.1/115.2 published,
not a regression) against RepoWise 61.8ms mean / 60.6ms median (matching
its own 68ms/67ms published number) - Aletheore is now faster than
RepoWise on this metric, not slower. No website correction needed for the
number itself, though the "Where we lose" callout is now stale and worth
revisiting.

# Follow-up — formalized the retrieval-speed benchmark, then found AIRview's comprehension loss was already fixed and unpublished (2026-08-22)

**RESOLVED 2026-08-22.** Direct continuation of the finding above. Two
separate pieces of work, both landed in `aletheore-benchmarks` (PR-less
repo, commit `5ca96ec`):

**1. Made the retrieval-speed re-verification reproducible, not ad hoc.**
The 52.9ms/125ms numbers above came from scratchpad scripts, not anything
committed. Fixed `run_aletheore.py` to pin `allow_hosted=False` explicitly
(closing the exact credential-contamination gap that caused the false
"regression" two findings back - it printed nothing before, silently
routing through a hosted network call whenever the runner had a saved
credential). Added the missing `run_repowise_inprocess.py` - RepoWise's
"68ms in-process" figure had never had a backing script; the only
committed script always subprocesses the CLI and pays a ~2.5-3.5s `import
lancedb` tax per query, measuring something else entirely (the "via CLI"
column). Final committed numbers: **Aletheore 40.5ms mean vs RepoWise
52.5ms mean, in-process** - Aletheore faster, not slower.

**2. While scoping a fresh AIRview-vs-RepoWise comprehension run (user
wanted to compare AIRview written by deepseek-v4-flash vs gpt-5.6-luna),
discovered the published comprehension numbers (`AIRVIEW_GAP.md`: AIRview
1.21/3 vs RepoWise 2.54) are stale by months, not current.** The fix
`AIRVIEW_GAP.md`'s own "Cause 2" section called for -
`related_symbols`, giving AIRview real `(name, line)` citation targets for
cross-file material instead of bare path lists - was written, measured to
work (beat RepoWise for the first time in testing), and **shipped to
production already**: it's commit `a2cac8a`, squash-merged under an
unrelated PR title ("app-server image was missing scan_worker package,
crash-looping prod #246"), which is exactly why nobody noticed and the
benchmark doc was never updated. Confirmed live on master:
`AIRVIEW_PROMPT_VERSION = "5"`, `related_symbols` fully implemented in
`live_wiki.py`, all 62 `test_live_wiki.py` tests passing.

The benchmark pipeline to re-verify this was itself incomplete - no
committed script built the RepoWise-side rows for the architecture question
set the published numbers were judged on (`arch_context2.json`). Added
`build_repowise_arch_context.py`, and parameterized `build_airview.py`
(now supports either deepseek-v4-flash or gpt-5.6-luna as the writer,
matching `model_tiers.py`'s real adapter selection) and
`build_airview_ctx3.py` so both arms can be built and scored without
clobbering each other.

**Re-run today, full 12-question architecture set, 3 judge repeats per
question (was a single unrepeated pass when 1.21/2.54 was published):**
- AIRview (deepseek-v4-flash): **1.88 vs RepoWise 1.99** - a 0.11 gap,
  smaller than the judge's own measured noise floor on this run (mean
  spread 0.50, max 1.50). A statistical tie, not a loss.
- AIRview (gpt-5.6-luna, the real production writer per `model_tiers.py`):
  **1.53 vs RepoWise 2.08** - a 0.55 gap, well outside that run's noise
  floor (mean spread 0.33). A real loss. Not yet root-caused - surprising
  given Luna is the primary model specifically because it beat DeepSeek on
  real-world coding/PR-review benchmarks elsewhere.

Answers the question that started this: on this specific comprehension
benchmark, deepseek-v4-flash held up better than gpt-5.6-luna for AIRview,
not just as well.

`AIRVIEW_GAP.md` updated in place with a dated note (original diagnosis
kept for its reasoning, headline number marked stale, reproduction
commands included) rather than rewritten. The website's public "2.13 vs
2.35" comprehension table and the "Where we lose" callout citing it are
now confirmed stale on two fronts (this finding and the retrieval-speed
one above) and were deliberately left untouched - both are live marketing
copy, not something to silently rewrite without review.

**Open threads, not yet actioned:**
- Why Luna loses this specific comprehension benchmark more decisively
  than DeepSeek, given the opposite holds elsewhere.
- Whether/how to update the website's public comprehension table now that
  both of its headline numbers are known-stale.

# Follow-up — extended to 4 more corpora, shipped the AIRview writer-model switch (2026-08-22)

**RESOLVED 2026-08-22.** Direct continuation of the finding above. User's
call before trusting the single-Flask tie: "let's run this test with 4
more corpuses before we publish to settle it."

**Generalized the benchmark pipeline** (`aletheore-benchmarks` commit
`00e3321`) to run against any already-indexed corpus, not just Flask -
`build_repowise_arch_context.py`/`build_airview.py`/`build_airview_ctx3.py`
now take `BENCH_CORPUS`, and `questions/architecture_generic.json`
(pre-existing, corpus-agnostic, previously unused for this exact
comparison) removed the need to author new per-corpus questions.

**Results, deepseek-v4-flash writer, 5 corpora, full 12-question set, 3
judge repeats:**
| corpus | language | AIRview | RepoWise | verdict |
|---|---|---|---|---|
| flask | Python | 1.88 | 1.99 | tie |
| automapper | C# | **0.38** | 2.29 | severe loss - see below |
| axios | JavaScript | **2.28** | 1.61 | clear win |
| fmt | C++ | **2.04** | 1.64 | win |
| jq | C | 1.93 | 1.76 | tie/lean win |

**automapper investigated directly, not silently averaged in.** Its
clustering produced 119 subsystems for 512 files (3.9 files/subsystem)
against every other corpus's 9-13/subsystem - 2-3x more fragmented, on the
same clustering code every corpus shares. Each retrieval bundle for its
questions stitches together many tiny disconnected fragments instead of a
few substantial ones, which plausibly explains the 72/72 RepoWise sweep
independent of which model wrote the prose. A real, separate defect
(over-fragmented clustering on certain codebases, dense namespace/generic-
heavy C# in this case) - not evidence against deepseek-v4-flash. Not yet
fixed; flagged in `AIRVIEW_GAP.md` for its own investigation.

**Excluding automapper as the confirmed outlier: deepseek-v4-flash
averages 2.03 vs RepoWise's 1.75 across four languages** - a real lead,
not a toss-up, and considerably stronger than the single-Flask tie
suggested on its own.

**Shipped the production model switch** (PR `feat/airview-deepseek-writer`,
#352): `writing_adapter_for` in `model_tiers.py` gained a `_prefer_luna`
flag (default `True`, unchanged everywhere else) and a new
`writing_adapter_for_airview` wrapper that always sets it `False`. Wired
into all three AIRview writing surfaces in `jobs.py`
(`_live_wiki_naming_adapter`, `_live_wiki_full_build_writing_adapter`,
`_live_wiki_update_writing_adapter`). The full-build path was previously
plan-dependent via `writing_adapter_for_plan` (Luna falling back to
untested `deepseek-v4-pro`); now every plan gets the specific model that
was actually benchmarked, `deepseek-v4-flash`. PR review, managed audits,
and Docs are untouched - not what this benchmark measured, stay on Luna.

Verified: `test_model_tiers.py` (35 passed, 3 new) and `test_jobs.py` (199
passed, 1 pre-existing skip, tests updated to assert AIRview never selects
Luna even when `OPENAI_API_KEY` is configured) both green.

**Open threads carried forward:**
- automapper's clustering-fragmentation bug (119 subsystems for 512
  files) - real, unfixed, worth its own investigation.
- Why Luna loses this comprehension benchmark specifically, given the
  opposite holds on real-world coding/PR-review benchmarks elsewhere -
  still unexplained.
- Whether/how to update the website's public comprehension table - the
  5-corpus data is now considerably stronger evidence than what's
  currently published, but publishing is still a separate decision from
  fixing the code.

# Follow-up — fixed the automapper clustering bug, verified no regression elsewhere (2026-08-22)

**RESOLVED 2026-08-22.** Closes the first open thread above. User's call:
"for the clustering, thing we need a language specific fix for sure."

**Root cause was not language-specific.** Investigated via
`systematic-debugging` before touching anything: AutoMapper's dependency
graph already had healthy edge density (76.8% of modules with imports,
4.18 edges/module - matching gson's 4.11, better than flask's 3.80),
ruling out the C# import-extraction gap a prior audit (`Claude_Audit.md`
item C, MSBuild `<Using>` parsing) had already fixed. The real cause:
**420 of 513 dependency-graph nodes (82%) were test files**
(`UnitTests`/`IntegrationTests`/`AutoMapper.DI.Tests`), and
`build_clusters` (`src/aletheore/architecture.py`) had no concept of
excluding them - every test file joined a modularity community same as
real source, fragmenting 512 files into 119 near-singleton subsystems.

**Fix** (PR `fix/clustering-excludes-test-files`, #353): `build_clusters`
now excludes test paths before clustering, reusing `search_index.py`'s own
`_is_test_path` (single source of truth, no duplicated detection logic) -
deferred inside the function body rather than a top-level import, since a
top-level import broke `test_importing_cli_does_not_eagerly_load_heavy_dependencies`
(dragged `lancedb`/`openai` into every CLI command's import path; caught
by CI, fixed in a follow-up commit before merge). Verified empirically:
119 clusters -> 8 on the real corpus, with two substantial real subsystems
(38 and 30 modules).

**Re-tested end-to-end, not just at the cluster level:**
- automapper: **0.38 vs RepoWise 2.29 -> 2.08 vs RepoWise 1.78** - a 72/72
  RepoWise sweep reversed to a 44/72 AIRview lead. The 0.30 gap sits
  inside this run's own noise floor, so not statistically airtight alone,
  but going from catastrophic to competitive on identical material is
  strong direct confirmation.
- **Did the fix hurt anything else?** User asked directly - checked, not
  assumed. Cluster counts dropped for every corpus (flask 12->4, axios
  71->27, fmt 32->11, jq 18->11 nearly unchanged). Fully re-ran the two
  most exposed:
  - flask: 1.88/1.99 (tie) -> 1.96/1.75 (still a tie, slightly better)
  - axios: 2.28/1.61 (win, gap 0.67) -> 2.12/1.85 (win, gap 0.27)

  axios's gap narrowed, but a meaningful part is judge noise, not
  regression: RepoWise's own score moved 1.61->1.85 on byte-identical
  material between the two runs - the same order of magnitude as this
  benchmark's own previously-measured judge drift on identical bytes
  (~0.21, `JUDGE_NOISE.md`). **No corpus flipped from a win or tie into a
  loss.** fmt/jq checked at the cluster level only, not re-judged
  end-to-end - jq is low-risk (barely changed), fmt unverified.

Verified: new test (`test_build_clusters_excludes_test_files`) confirmed
failing against the unfixed code, passing after. Full `src/` suite green
(1332 passed). `github-app` tests referencing clusters green (75 passed,
7 pre-existing skips).

**Open threads carried forward:**
- fmt's comprehension score not re-verified end-to-end after the fix
  (cluster-level check only).
- Why Luna loses this comprehension benchmark specifically - still
  unexplained.
- Whether/how to update the website's public comprehension table.

# Follow-up — closed the last open thread: published the current numbers (2026-08-22)

**RESOLVED 2026-08-22.** fmt re-verified end-to-end (1.92 vs RepoWise 1.72
- still a win, narrower than the pre-fix 2.04/1.64, same judge-noise
pattern as axios/flask). Final 5-corpus averages, current code, deepseek-
v4-flash writer:

| corpus | language | AIRview | RepoWise |
|---|---|---|---|
| flask | Python | 1.96 | 1.75 |
| axios | JavaScript | 2.12 | 1.85 |
| automapper | C# | 2.08 | 1.78 |
| fmt | C++ | 1.92 | 1.72 |
| jq | C | 1.93 | 1.76 |
| **average** | | **2.00** | **1.77** |

Aggregate preference count across all 360 judged pairs: Aletheore 198
(55.0%), RepoWise 144 (40.0%), tie 18 (5.0%).

**Published both corrected figures** (Aletheore PR `docs/publish-current-benchmark-numbers`,
#354; `aletheore-benchmarks` commit `482ffe2`):
- Website `benchmarks.html`'s "Where we lose" callout - removed the two
  bullets that were no longer true (RepoWise comprehension win, RepoWise
  speed win), added the current 2.00-vs-1.77 comprehension figure framed
  honestly ("roughly at parity, leaning ahead," most per-corpus gaps
  inside judge noise - not a decisive win), kept the two still-real
  weaknesses (vocabulary-phrasing gap, AIRview fallback coverage)
  untouched.
- `aletheore-benchmarks/README.md` - same two sections, old figures kept
  in place and clearly marked superseded rather than deleted, matching
  this repo's own reproducibility discipline.

Old 2.13-vs-2.35 and 68ms-vs-125ms figures retired everywhere they were
published, with the history (and the real bug each one uncovered) linked
rather than hidden.

**Open threads still carried forward:**
- Why Luna loses this comprehension benchmark specifically, given the
  opposite holds on real-world coding/PR-review benchmarks - still
  unexplained.

## CLI/UX gap sweep, round 2 - 2026-08-22

Same recipe as PR #346 (the update-notice gap): fork per subsystem
instructed to actually run commands and observe real behavior rather than
theorize, findings independently re-verified against live source before
touching anything. Four subsystems audited this round: MCP, AIRview, Docs
export, Endpoint Monitoring/healthcheck.

**MCP** - clean. All 6 install targets (`cursor`, `vscode`, `kiro`,
`opencode`, `codex-cli`, `claude-code`) verified end-to-end against real
scratch repos; every printed message matched the file actually written.
Tool/resource docstrings, `--target` help text, and the README's "30/31
tools" count all verified accurate against `build_server()`. No fix
needed. (Flagged one gap outside MCP scope while surveying `cli.py` - see
sponsor panel below.)

**Docs export** - clean. Paid-gating, build-status banners (failed/
partial/none), `docsSymbolCount` regex, GitHub heading-anchor
slugification, and `_maybe_sync_docs_to_repo` wiring all traced
end-to-end against real evidence and found consistent with what's
claimed in the dashboard copy. No fix needed.

**Fix 1 - `audit`'s sponsor panel contradicted its own consent prompt**
(PR `sponsor-panel-contradicts-audit-consent`, #348, merged): `cli.py`'s
`_sponsor_panel()` printed "No accounts, no tracking - nothing leaves
this machine" unconditionally after every `audit` run - including paths
that just sent evidence to a third-party API (with explicit consent) or
invoked an already-authenticated local CLI adapter, directly
contradicting the consent prompt shown moments earlier in the same
invocation. Every adapter in `KNOWN_ADAPTERS` sends data externally one
way or another, so the claim was never true wherever the panel fired.
Line dropped. Regression test verified fail-without/pass-with via `git
stash`; full `test_cli.py` suite green (154 passed).

**Fix 2 - `healthcheck` exited 0 with no summary even on total outage**
(PR `healthcheck-exit-code-and-summary`, #349, merged): `_healthcheck()`
unconditionally `return 0`'d after iterating results, with no aggregate
count anywhere in the output. Reproduced live: every endpoint in this
repo's own evidence printed UNREACHABLE against an unlistened port, and
the process still exited 0 - a script or CI job checking the exit code,
or a human skimming a long list, got zero signal the target was
completely down. Now prints "N of M endpoint(s) reachable" and exits 1
when every checked (non-skipped) endpoint is unreachable. Regression
test verified fail-without/pass-with; full suite green (154 passed).

**Fix 3 - AIRview banner claimed a "fast model" for updates that stopped
being true 2026-08-09** (PR `airview-banner-model-claim`, #350, merged):
the dashboard banner said "Built once by a frontier model, kept current
by a fast one," but `model_tiers.resolve_model()` has routed both the
full build and every incremental update to the same model (Luna, when
`OPENAI_API_KEY` is configured) since the 8/9 routing change - confirmed
by tracing `model_for_plan`/`resolve_model` call sites in `jobs.py`
directly. Paying customers were shown a stale, false description of what
runs their updates. Reworded to not name a specific model, so the claim
can't drift the same way again. Regression test asserts on
`frontend.WIKI_HTML` directly, verified fail-without/pass-with; full
`test_frontend_js_syntax.py` suite green (11 passed, 1 skipped).

## Endpoint monitoring — real integration test, 2026-08-22

User flagged endpoint monitoring as "the only [subsystem] we haven't
tested properly" after the CLI/MCP/Docs live-verification round. Audited
the hosted health-check sweep (`_run_health_check_sweep_for_target` +
friends in `github-app/scan_worker/jobs.py`) and found the gap was real
but different from the CLI-gap species: the *decision logic* (23 existing
tests - reachability flips, down-retry escalation, SSRF re-validation,
shape-change, latency, rotation) is thoroughly unit-tested, but every one
of those tests mocks away every I/O boundary
(`DATABASE_URL=postgresql://unused`, `list_health_check_targets_all`/
`get_last_endpoint_health`/`insert_endpoint_health`/
`_enqueue_health_down_retry` all monkeypatched). Nothing had ever proven
the pieces are wired together correctly against real Postgres, real
Redis/RQ, and a real HTTP endpoint - an argument-name or -order mismatch
between `jobs.py` and `scan_worker/db.py` would pass every existing test
and only surface in production.

**Fix** (PR `healthcheck-integration-audit`, #351, merged): added one
integration test that runs the real sweep against real Postgres, real
Redis, and a real local HTTP server that starts reachable and then goes
down - including draining the down-retry chain through a real RQ
`ScheduledJobRegistry`, the same way `health_worker.py`'s actual
`Worker(..., work(with_scheduler=True))` does it in production, not by
calling the retry job directly with hand-written args. Docker Desktop
wasn't running locally; started it and the existing
`aletheore-test-pg`/`aletheore-test-redis` containers to get a real DB/
Redis to test against.

Verified the test has real teeth: deliberately swapped the `method`/
`path` argument order in the real `insert_endpoint_health` call inside
`jobs.py`, confirmed the test failed with a clean assertion error, then
reverted the mutation (confirmed clean via `git status`/`git diff`
before committing).

**No product bug found** - the pipeline is correctly wired end-to-end.
Full `test_jobs.py` suite green (166 passed, 9m23s), full
`test_scan_worker_db.py` green (102 passed), new test stable across two
consecutive local runs before pushing, and passed for real in CI against
GitHub Actions' own Postgres/Redis services on both Python 3.11 and 3.12.

Process note: hit the shared-stash-across-worktrees race again mid-fix
(three parallel `git stash push`/`pop` calls across three worktrees
non-deterministically swapped two files between the sponsor-panel and
AIRview-banner worktrees, since `git stash` is a single LIFO stack shared
by the whole repo, not per-worktree). Caught via `git status --short` in
each worktree before committing, diagnosed by diffing each modified
file's actual content, and fixed by reverting the misplaced file in each
worktree and reapplying the correct edit directly. All fail-without/
pass-with verification was then redone sequentially (one worktree at a
time) to avoid re-triggering the race - this is the safe pattern going
forward whenever more than one worktree needs a stash-based verification
in the same operation.
