# Graphify Comparison Benchmark — Design

## Goal

Build a real, reproducible, head-to-head benchmark comparing Aletheore's
code-graph/query capability against [Graphify](https://github.com/Graphify-Labs/graphify)
(111k stars, YC S26), a competing "turn your codebase into a queryable
knowledge graph" tool. Lives in the separate `aletheore-benchmarks` GitHub
repo, alongside the existing Repowise comparison, with the same
transparency standard: full methodology, raw results, no API key required
beyond the (cheap) model used to run it.

## Why this exists, and why not just cite their numbers

Graphify's own `BENCHMARKS.md` claims that giving a coding agent their tool
lifts "key-fact coverage" from 70.8% (grep+read baseline) to 82.0% on
`frappe/erpnext` (~1M LOC), at ~140K tokens/query, a 20x token reduction
versus stuffing the whole repo into context. Secondary blog coverage of
Graphify (not their own repo) also circulates a "70%"/"71.5x" figure that
does not appear in their primary source at all — it's unclear what it
actually measures, so it is **not** used anywhere in this benchmark.

Their own reproduction harness (`crosstool/run.py`) and the exact question
set used to produce the 70.8%/82.0% numbers are **not present in their
public repo** — only `BENCHMARKS.md` itself matches a search for "erpnext"
in `Graphify-Labs/graphify`. So this benchmark cannot reproduce their exact
test; it replicates their test's *shape* (same protocol: fixed agent,
grep/read/list baseline, one code-intelligence tool added at a time, graded
coverage, tokens tracked) against an independently-authored question set,
and runs **both tools ourselves** under one harness, one judge, one report.
Comparing a number we measured against a number they self-reported, on
different questions, different days, different judges, is not a controlled
comparison — it's the same scope-mismatch mistake already flagged once on
the Repowise work (folding a rival's uncovered corpus rows into a headline
average). Running both tools ourselves is the only way to avoid repeating
it here.

## Corpus

- **Repo**: `frappe/erpnext`, GPL v3.0 (read-only benchmark use only, no
  redistribution — same posture as every existing corpus in `corpora.json`,
  none of which redistribute source).
- **Size**: ~1.86GB full repo; shallow-clone at the pinned commit only (not
  full history) to keep this practical to run and re-run.
- **Pinned commit**: chosen at implementation time (latest `develop` HEAD as
  of the clone), recorded in the new corpus manifest exactly like
  `corpora.json` records commits for every other corpus. A different
  checkout invalidates the question set's ground truth, same rule as the
  rest of the repo.
- **Scope**: lives in its own new top-level directory,
  `graphify_comparison/`, mirroring how `pr_review/` is already separate
  from the main location-accuracy corpus table in `corpora.json`. **Not**
  added to `corpora.json` in this pass — ERPNext is far larger than every
  existing corpus (single small-to-medium libraries) and folding it into
  the same averages would be its own scope-mismatch bug. Promotable to a
  permanent corpus later, once these numbers land, per an explicit
  follow-up decision — not automatic.

## Question set

- ~15 graded, factual questions about ERPNext's real architecture and
  implementation (e.g. "which file/function owns the Sales Invoice
  doctype's validation hook?", "where is X computed?"), each with an
  objectively-checkable expected key fact or small set of key facts —
  written independently by us, not reverse-engineered from Graphify's
  (unpublished) question set.
- Stored as `graphify_comparison/questions.json` (question text + expected
  key fact(s) + short rationale for why that's the correct fact), same
  shape as the existing `ground_truth.json`/`ground_truth.yaml` pattern in
  `pr_review/`.
- 15 questions, not their thin 6 — more statistical power, consistent with
  this project's normal per-corpus question count (`n=15` is already the
  norm for most corpora in `corpora.json`).

## Harness

A small custom tool-calling agent loop (~150 lines), no new framework
dependency — same spirit as `run_repowise.py`'s direct subprocess-shelling
pattern, and DeepSeek's OpenAI-compatible API is already wired into this
codebase (`aletheore.adapters.openai_compatible.OpenAICompatibleAdapter`).

**Model throughout (agent loop and judge): `deepseek-v4-flash`** — the
cheapest model available to the project, per explicit direction. If this
comparison can't stay cheap on the cheapest model, that's itself a finding
worth reporting, not a reason to reach for a pricier one.

**Three conditions, same agent, same question set, same model, run once
each (see Judge below for repeats)**:

1. **Baseline** — grep/read/list tools only, no code-intelligence tool.
   Mirrors Graphify's own 70.8% baseline framing exactly.
2. **+Aletheore** — baseline tools plus the `aletheore query` capability,
   wired as a callable tool that shells out to the real `aletheore query`
   CLI via subprocess, same invocation style `run_repowise.py` already uses
   for the rival tool in the existing comparison (not the MCP server —
   no plumbing benefit for a benchmark script, and it keeps both tools'
   integration symmetric: CLI subprocess call in, stdout parsed out).
3. **+Graphify** — baseline tools plus `graphify query` /
   `graphify explain` / `graphify path`, installed fresh via `pip install
   graphify` (Apache 2.0, no API key needed to build the graph — graph
   construction itself costs zero LLM credits per their own docs, so this
   doesn't affect the token/cost comparison).

Each (question, condition) run records: the agent's final answer text,
total tokens spent (prompt + completion, summed across every tool-calling
turn in that run), and wall-clock time.

## Judge

Copied directly from `pr_review/blind_judge.py`'s established pattern,
same project, same judge-noise-floor finding already documented there:

- `deepseek-v4-flash` (matching the harness model, per the cost direction
  above — `blind_judge.py`'s existing precedent used `deepseek-v4-pro` for
  a different, unrelated benchmark; this one intentionally uses the
  cheaper model throughout).
- **Anonymized, single-candidate-per-call** scoring — the judge is never
  told which tool (or "no tool") produced an answer, and scores exactly
  one condition's answer against the graded key fact(s) per call, not
  multiple arms in one call (a design already tried and found unreliable —
  53 of 97 instances silently omitted a requested label — documented in
  `pr_review/README.md`).
- **2 runs per (question, condition)**, per the documented judge-noise
  floor (0.2–0.375 drift observed on identical input in prior work) —
  report both individual runs and their mean, not a single-run number
  presented as ground truth.
- Retry-once on a missing/unparseable score; one bad (question, condition,
  run) triple logs and continues, never aborts the whole run.

## Output / reporting

New `graphify_comparison/README.md` section (linked from the main
`aletheore-benchmarks` README's table of contents, same as every other
section):

- A results table: coverage % and mean tokens/query, per condition
  (baseline / +Aletheore / +Graphify), with the two individual judge runs
  shown alongside the mean.
- Full methodology as written above (why we run both tools ourselves, why
  the "70%/71.5x" secondary-source figure is not used, why ERPNext isn't in
  `corpora.json` yet).
- Total real cost of the run (both tools, both judge passes) — expected to
  be small given `deepseek-v4-flash` throughout, but reported exactly, not
  estimated.
- Raw per-question, per-condition, per-run results published in
  `graphify_comparison/results/`, so every number in the table is
  recomputable without re-running anything, matching the rest of the repo.
- If Aletheore loses on any question or condition, that result is
  published as-is — same "we publish the rows we lose" standard as the
  Repowise comparison.

## Out of scope for this pass

- Promoting ERPNext to a permanent `corpora.json` entry (location accuracy,
  architecture explanation, vocabulary-regime questions) — explicit
  follow-up decision after these numbers land, not automatic.
- Any comparison against Graphify's memory/LOCOMO benchmarks (mem0,
  supermemory, etc.) — that's a different product category (conversational-
  agent memory), not comparable to what either Aletheore or Graphify's code
  intelligence layer does.
- Matching Graphify's exact (unpublished) question set — infeasible, and
  arguably less defensible than an independently-authored one anyway.
