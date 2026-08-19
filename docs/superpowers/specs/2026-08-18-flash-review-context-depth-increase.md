# Spec: raise Flash Review's context-depth caps (paid-tier improvement)

## Why

Flash Review is currently 100% paid-only (`run_flash_review_job` returns early on
`installation["plan"] == "free"`, `github-app/scan_worker/jobs.py:1388`) — this spec does not
change that, does not add a free tier, and does not touch plan routing at all. It exists because
the context-depth caps that already gate Flash Review's context-building functions
(`build_code_evidence_context`, `build_referenced_symbol_context`, `build_blast_radius_context`,
`fetch_review_file_context`) were sized conservatively — most were set well before this project's
own recent benchmark work established how much real headroom the actual production models have.
Real numbers, verified before writing this spec:

- Primary model: GPT-5.6 Luna, 1,050,000-token context window, pricing flat up to 272,000 tokens
  (2x input/output rate above that threshold).
- Fallback model (used when OpenAI is unavailable — `model_tiers.py:89`): `deepseek-v4-pro`,
  1,000,000-token context window.
- Current worst-case production prompt (existing caps): ~56,000 tokens. New worst case (this spec's
  numbers): ~112,000 tokens. Both comfortably under the 272,000-token pricing threshold on Luna,
  and nowhere near either model's real window.
- Cost impact: at Luna's $0.20/1M input token rate, worst-case input cost per review goes from
  ~$0.011 to ~$0.022 - against a monthly abuse-ceiling cap of `PLAN_MONTHLY_PRICE_USD["air"]
  (29.99) * CAP_FRACTION_OF_PRICE (0.5)` = ~$15.00 per installation (`app_server/llm_cost.py:42-50`,
  `82-86`). Negligible relative to that ceiling.

This is a **paid-tier-only improvement to context that already exists** - more of a PR's changed
symbols get analyzed, more real callers get checked before concluding "no confirmed caller found",
more of a large PR's changed files fit in full, more referenced (not-changed) definitions are
available as evidence. No new feature, no new external dependency, no plan-based branching added.

## Exact changes

Six constants, all doubled (uniform 2x - keeps the reasoning and verification simple, and the
combined worst-case math above already accounts for the full 2x set together, not each in
isolation):

| constant | file | current | new |
|---|---|---|---|
| `MAX_CONTEXT_FILES` | `github-app/scan_worker/github_api.py:7` | `15` | `30` |
| `MAX_CONTEXT_FILE_BYTES` | `github-app/scan_worker/github_api.py:8` | `40_000` | `80_000` |
| `MAX_CONTEXT_TOTAL_BYTES` | `github-app/scan_worker/github_api.py:9` | `200_000` | `400_000` |
| `MAX_REFERENCED_SYMBOLS` | `github-app/scan_worker/flash_review.py:357` | `8` | `16` |
| `MAX_REFERENCED_SYMBOL_BYTES` | `github-app/scan_worker/flash_review.py:358` | `20_000` | `40_000` |
| `MAX_BLAST_RADIUS_SYMBOLS` | `github-app/scan_worker/flash_review.py:244` | `5` | `10` |
| `MAX_BLAST_RADIUS_CANDIDATES` | `github-app/scan_worker/flash_review.py:245` | `20` | `40` |
| `MAX_BLAST_RADIUS_CALLERS_SHOWN` | `github-app/scan_worker/flash_review.py:246` | `5` | `10` |

That's it. Just the numeric literals. Do not change any of the surrounding logic, do not rename
anything, do not touch `build_code_evidence_context` or `build_change_impact_context` (not in
scope - their output is already small and deterministic, not gated by a byte/count cap like the
ones above).

**Do not touch `model_for_plan`, `writing_adapter_for_plan`, or any plan-based branching.** Those
are out of scope for this spec - a separate, later piece of work, not part of this change.

## Required test fix (not optional - this test will silently stop testing what it claims to)

`github-app/tests/test_flash_review.py`, `test_build_blast_radius_context_caps_candidates_checked`
(around line 1848) hardcodes the *old* cap value directly:

```python
imported_by_list = [f"caller_{i}.py" for i in range(30)]
...
def fake_fetch_file_content(candidate_path: str) -> str | None:
    call_count[0] += 1
    if call_count[0] <= 20:
        return f"def call_{call_count[0]}():\n    handler()\n"
    return None
...
assert call_count[0] <= 20
```

With `MAX_BLAST_RADIUS_CANDIDATES` raised to 40, this test's 30-candidate fixture no longer exceeds
the cap at all - it would keep passing, but would stop actually testing the capping behavior it
exists to verify (nothing would ever get capped, since 30 < 40). Fix by importing the real constant
and using it instead of the literal 20, and sizing the fixture to genuinely exceed it:

```python
from scan_worker.flash_review import MAX_BLAST_RADIUS_CANDIDATES, build_blast_radius_context

imported_by_list = [f"caller_{i}.py" for i in range(MAX_BLAST_RADIUS_CANDIDATES + 10)]
...
    if call_count[0] <= MAX_BLAST_RADIUS_CANDIDATES:
        return f"def call_{call_count[0]}():\n    handler()\n"
    return None
...
assert call_count[0] <= MAX_BLAST_RADIUS_CANDIDATES
```

This way the test keeps testing the real capping behavior regardless of what the constant's value
is, instead of re-encoding today's specific number and silently going stale the next time it
changes.

Search the rest of `test_flash_review.py` for any other hardcoded literal matching one of the eight
old values above used as an assertion (not inside a `monkeypatch.setattr(...)` call, which
correctly sets its own independent test-scoped value and is not affected by this change) before
concluding this is the only one - do not assume it's the only occurrence without checking.

## Verification (mandatory - do this for real, not as a claim)

1. Run the full `github-app` test suite. Report the real pass/fail counts.
2. Real-data check, not synthetic: pick the 2-3 largest real cases from
   `benchmarks/pr-review-benchmark/cases/` (by diff size or file count - `swebench-matplotlib-25775`
   and `009-cobra-completions-args-mutation` are good candidates, they had the largest measured
   context sizes in this project's own recent corpus measurement). For each, actually build the full
   context Flash Review would send (`fetch_review_file_context` equivalent + `build_code_evidence_context`
   + `build_change_impact_context` + `build_referenced_symbol_context` + `build_blast_radius_context`,
   same as `run_mixed_repo_ab.py`'s `_run_case` already assembles them - reuse that pattern, don't
   reinvent it) with the new caps, and report the real total character/token count. Confirm it's
   comfortably under 272,000 tokens (leave real margin - this project has been burned before by
   estimating from one sample instead of measuring the real worst case across the corpus).
3. Report the exact new worst-case total alongside the exact old worst-case total (recompute the
   old one for real too, don't just quote the ~56,000 estimate above) so there's a real before/after
   number, not just a claim that it's "still safe."
