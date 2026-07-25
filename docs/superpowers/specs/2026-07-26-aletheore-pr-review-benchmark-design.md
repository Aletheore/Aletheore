# Aletheore PR-Review Benchmark — Design Spec

## Overview

Aletheore's core marketing claim is "evidence-grounded" — every claim a generated report makes
must trace back to a specific field in the deterministic scan evidence (`air.json`), enforced in
code today by `src/aletheore/citation_verifier.py`'s file-existence check on `file:line`
citations, and by the mandatory "what evidence doesn't cover" discipline documented in
`src/aletheore/manual/part-7-perspectives.md`. That claim is currently an assertion, not a
demonstrated fact to a prospective buyer. This spec defines a public benchmark that tests it
directly against real AI PR-review competitors, on real and reconstructed bugs, scored blind.

This is a one-time dated snapshot, not a product feature or an ongoing service — it lives
alongside the codebase as a benchmark artifact, reusing existing code
(`citation_verifier.py`'s logic) rather than extending the shipped product.

## Goals

- Produce a reproducible comparison of Aletheore against **Qodo/PR-Agent** and **DeepSource**
  (named) and **CodeRabbit** (anonymized, for ToS reasons — see Competitor Lineup) across
  ~25 test cases spanning 4-5 languages.
- Score every tool on recall, false-positive rate, citation/grounding accuracy, and
  actionability — not just the headline metric Aletheore is expected to win on.
- Publish the full methodology, raw outputs (except CodeRabbit's, see below), and scoring
  sheet alongside the report, so the result is independently checkable rather than taken on
  faith.
- Reuse `citation_verifier.py`'s existing file-existence logic as the automated half of the
  grounding score, extended with a real line-bounds check against the actual checkout (this
  benchmark has filesystem access competitors' evidence schema doesn't need to support).

## Non-Goals

- **No fully-automated, reusable benchmarking harness** (rejected as "Option B" during design
  — a YAML-driven orchestrator with a scoring dashboard is a second product-shaped effort;
  scripts here exist only to remove copy-paste error from a semi-automated, otherwise manual
  process).
- **No Greptile or SonarQube entries.** SonarQube/SonarCloud has an explicit anti-benchmarking
  clause (AUP §5) identical in effect to CodeRabbit's; Greptile's ToS bars using the platform
  "to develop or offer a competing product," which is a plausible reading and not worth the
  risk for a fourth entry when Qodo/DeepSource already anchor the named comparison.
- **No Graphite entry.** Graphite's AI reviewer (formerly "Diamond," now folded into
  "Graphite Agent") has no benchmarking restriction found and could be a clean named addition
  later, but a fifth entry isn't needed to hit this benchmark's goals — deferred, not excluded
  for cause.
- **No ongoing tracked leaderboard.** This is a single dated snapshot; periodic re-runs as
  competitors' products change is a real future idea, explicitly deferred, not committed to
  here.
- **No formal speed/cost benchmarking** — qualitative notes only, not a scored dimension.
- **No second anonymized entry** invented solely to make CodeRabbit's entry less identifiable
  by elimination. That residual "soft tell" is accepted and disclosed (see Known Limitations)
  rather than solved by adding an entry the benchmark doesn't otherwise need.

## Competitor Lineup & Legal Handling

ToS research (2026-07-26) found:

| Tool | Benchmarking restriction | Treatment |
|---|---|---|
| CodeRabbit | Explicit: bars disclosing benchmark results without written consent (ToS §4.2) | Anonymized as "Tool D" or similar |
| SonarQube/SonarCloud | Explicit: same effect (Acceptable Use Policy §5) | Excluded entirely |
| Greptile | No explicit benchmark ban, but bars using the platform "to develop or offer a competing product" | Excluded (gray area, not worth the risk for a 4th entry) |
| DeepSource | No restriction found | Named directly |
| Graphite (AI reviewer, "Diamond" deprecated → now "Graphite Agent") | No restriction found | Not included in v1 (see Non-Goals); clean if added later |
| Qodo/PR-Agent | Apache 2.0 OSS, self-hosted, no account/ToS at all | Named directly |

CodeRabbit handling specifics: install on a test repo to generate real output, capture it
privately, but publish only the anonymized *scored summary* — never the raw transcript,
screenshots of its real UI, verbatim branding/footer text, or any other detail (model choice,
pricing, links) that would identify it. The raw CodeRabbit output is excluded from the public
commit for this reason (see Publication & Reproducibility).

## Test Corpus & Ground Truth

~25 test cases across Python, TypeScript/JS, Go, and Java (Aletheore's strongest scanner
languages), split three ways:

1. **~15 real bug-fix reconstructions.** Find a merged bug-fix commit in a popular OSS repo
   (clear `fix:`/"Fixes #N" message, ideally a linked issue), check out the state immediately
   before that fix, and submit that state as the "PR under test." The actual fix commit is the
   ground truth. This mirrors the methodology Greptile's own published benchmark used (50
   PRs/5 repos, bugs reconstructed from real bug-fix commits) — a known, citable precedent,
   not a method invented for this exercise.
2. **~6 injected bugs.** Deliberately introduce a known bug pattern (SQL injection via string
   concatenation, off-by-one, missing null/None check, race condition, hardcoded secret) into
   an otherwise-clean PR, targeting categories underrepresented in the real set. Ground truth
   (file, line, category, expected finding) is written down *before* any tool runs against the
   case — pre-registered, not scored after the fact from whatever a tool happened to flag.
3. **~4 clean PRs with no real bug**, added during design beyond what was originally scoped,
   because this is the direct test of false-positive/hallucination behavior on code with
   nothing wrong with it — central to the grounding claim this benchmark exists to test.

Each case directory records: which repo/commit, the PR diff, and the ground-truth document.

## Architecture (Execution Pipeline)

New top-level directory `benchmarks/pr-review-benchmark/`:

```
benchmarks/pr-review-benchmark/
  cases/<case-id>/
    repo.txt              # repo + base commit / PR-under-test pointer
    pr.diff
    ground_truth.md        # pre-registered: file, line, category, expected finding
  scripts/
    run_case.py            # clones repo, applies PR, invokes each tool, dumps raw JSON output
    check_citations.py      # extends citation_verifier.py's file-existence check with a
                             # real line-bounds check against the actual checkout
    anonymize.py            # relabels tool identities -> Tool A/B/C/D, writes a sealed mapping
  results/
    raw/<case-id>/<tool>.json          # CodeRabbit's raw output excluded from public commit
    scored/<case-id>.md                # blind scoring sheet
    mapping.sealed.json                 # tool<->label mapping, opened only after scoring locks
  REPORT.md
  METHODOLOGY.md
```

**Model parity.** Where the model is under our control (Aletheore's audit step, PR-Agent's
bring-your-own-key config), both are pinned to the same underlying model, so the comparison
isolates grounding architecture rather than which LLM is smarter. DeepSource's and
CodeRabbit's models are opaque and not controllable — disclosed explicitly as "this measures
the product as a real user gets it," not an apples-to-apples model comparison.

**Nondeterminism.** Single run per case for cost/time reasons, except a ~5-case spot-check
subset run 3× to report a variance sanity-check rather than presenting single noisy LLM runs
as if they were stable.

## Scoring Rubric & Blind Judging

Per case, per tool:

- **Recall** — hit / partial / miss on the real or injected bug.
- **False positives** — count and list of non-issues raised, with particular weight on the 4
  clean-PR cases where any finding at all is a false positive by construction.
- **Citation/grounding accuracy** — automated via `check_citations.py`: does each cited
  file:line actually exist and fall within the real file's line count in the checkout used for
  that case.
- **Actionability** — manual, 1-5 scale: is the finding specific enough to act on, or generic
  advice that could apply to any codebase.

**Blind process:** `anonymize.py` relabels tool identities to A/B/C/D before any case is
scored; the sealed mapping for a given case is only reopened after that case's scoring is
locked, removing the single biggest credibility risk — the benchmark's own author
unconsciously favoring Aletheore's output during judgment calls.

## Publication & Reproducibility

`REPORT.md` (adaptable into a `website/` page later) plus the full
`benchmarks/pr-review-benchmark/` directory committed to this repo: cases, ground truth, raw
outputs (CodeRabbit's excluded per the legal handling above — only its anonymized scored
summary is published), scoring sheets, and `METHODOLOGY.md` recording exact run dates, model
versions, and the specific product versions/plans of DeepSource, CodeRabbit, and Qodo/PR-Agent
used, since all three will keep changing after this snapshot is taken.

Framing: report all four scoring dimensions honestly for every tool, including where a
competitor wins one. Headline framing emphasizes grounding/citation accuracy as Aletheore's
differentiator, but the scorecard itself is never edited to hide an unfavorable result — a
mixed outcome reads as more credible than a clean sweep.

## Known Limitations (stated in the report up front, not discovered later)

- **CodeRabbit's anonymized entry is a soft tell.** A single unnamed "Tool D" next to three
  named tools is guessable by elimination. Accepted rather than solved by adding an
  unnecessary second anonymized entry.
- **Single-run scoring on ~20 of 25 cases** — LLM nondeterminism means an individual case
  result is noisier than the spot-checked subset; the report states this plainly rather than
  presenting all cases as equally stable.
- **DeepSource is architecturally different from the other three.** It's closer to static
  analysis plus AI-assisted autofix than a pure LLM PR-reviewer in the same shape as Aletheore,
  Qodo/PR-Agent, or CodeRabbit. It stays in the comparison, but the report calls this out
  explicitly as a caveat on that entry specifically, rather than implying full architectural
  parity across all four.
