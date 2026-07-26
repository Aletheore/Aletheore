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

- Produce a reproducible, **named, 3-way comparison** of Aletheore against **Qodo/PR-Agent**
  and **DeepSource** across ~25 test cases spanning 4-5 languages. (CodeRabbit was in the
  original scope, anonymized for ToS reasons — dropped entirely on 2026-07-26; see Competitor
  Lineup and the amendment note below.)
- Score every tool on recall, false-positive rate, citation/grounding accuracy, and
  actionability — not just the headline metric Aletheore is expected to win on.
- Publish the full methodology, raw outputs, and scoring sheet alongside the report, so the
  result is independently checkable rather than taken on faith.
- Reuse `citation_verifier.py`'s existing file-existence logic as the automated half of the
  grounding score, extended with a real line-bounds check against the actual checkout (this
  benchmark has filesystem access competitors' evidence schema doesn't need to support).

## Non-Goals

- **No fully-automated, reusable benchmarking harness** (rejected as "Option B" during design
  — a YAML-driven orchestrator with a scoring dashboard is a second product-shaped effort;
  scripts here exist only to remove copy-paste error from a semi-automated, otherwise manual
  process).
- **No Greptile, SonarQube, or CodeRabbit entries.** SonarQube/SonarCloud has an explicit
  anti-benchmarking clause (AUP §5); CodeRabbit has an equivalent clause (ToS §4.2) and was
  dropped entirely on 2026-07-26 after its Free-plan rate limits also made a fair, repeatable
  comparison impractical in practice, independent of the legal question (see amendment note
  below). Greptile's ToS bars using the platform "to develop or offer a competing product,"
  which is a plausible reading and not worth the risk for a fourth entry when Qodo/DeepSource
  already anchor the named comparison.
- **No Graphite entry.** Graphite's AI reviewer (formerly "Diamond," now folded into
  "Graphite Agent") has no benchmarking restriction found and could be a clean named addition
  later, but a fifth entry isn't needed to hit this benchmark's goals — deferred, not excluded
  for cause.
- **No ongoing tracked leaderboard.** This is a single dated snapshot; periodic re-runs as
  competitors' products change is a real future idea, explicitly deferred, not committed to
  here.
- **No formal speed/cost benchmarking** — qualitative notes only, not a scored dimension.
- **No anonymized entry at all.** With CodeRabbit dropped entirely, every remaining tool is
  named directly; there is no ToS-driven reason left to hide any tool's identity from the
  scorer (see amendment note below).

## Competitor Lineup & Legal Handling

ToS research (2026-07-26) found:

| Tool | Benchmarking restriction | Treatment |
|---|---|---|
| CodeRabbit | Explicit: bars disclosing benchmark results without written consent (ToS §4.2) | **Dropped entirely** (see amendment below) — not anonymized, not included |
| SonarQube/SonarCloud | Explicit: same effect (Acceptable Use Policy §5) | Excluded entirely |
| Greptile | No explicit benchmark ban, but bars using the platform "to develop or offer a competing product" | Excluded (gray area, not worth the risk for a 4th entry) |
| DeepSource | No restriction found | Named directly |
| Graphite (AI reviewer, "Diamond" deprecated → now "Graphite Agent") | No restriction found | Not included in v1 (see Non-Goals); clean if added later |
| Qodo/PR-Agent | Apache 2.0 OSS, self-hosted, no account/ToS at all | Named directly |

**Amendment (2026-07-26): CodeRabbit dropped entirely.** The original plan anonymized
CodeRabbit as "Tool D" to work around its ToS §4.2 restriction. In practice, CodeRabbit's
Free-plan rate limits made it impossible to get a fair, repeatable run across the full corpus
regardless of the legal handling — so it was removed from the comparison rather than worked
around. This also removes the need for any anonymization/blind-labeling machinery in the
pipeline: with only named, ToS-unrestricted tools left (Aletheore, Qodo/PR-Agent, DeepSource),
findings are scored and published under real tool names throughout (see Scoring Rubric &
Judging).

Aletheore's actual comparable feature in this benchmark is its hosted GitHub App's **Flash
Review** (`deepseek-v4-flash`, hardcoded server-side), which posts a PR comment from
`aletheore[bot]` — not the CLI's whole-repo `aletheore audit` command, which is a separate,
non-comparable feature that doesn't run against a single PR's diff the way every competitor
in this lineup does.

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
    llm_judge.py            # independent LLM scoring pass over the same, real-name-keyed
                             # findings (named comparison — no anonymization step)
  results/
    raw/<case-id>/<tool>.json          # all three tools' raw output, real-name-keyed
    scored/<case-id>.yaml               # your manual scoring sheet (authoritative)
    llm_judged/<case-id>.json           # independent LLM judge's scoring pass
  REPORT.md
  METHODOLOGY.md
```

**Model parity.** Where the model is under our control (Aletheore's Flash Review, hardcoded
server-side to `deepseek-v4-flash`, and PR-Agent's bring-your-own-key config, pointed at the
same model), both use the same underlying model, so the comparison isolates grounding
architecture rather than which LLM is smarter. DeepSource's model is opaque and not
controllable — disclosed explicitly as "this measures the product as a real user gets it,"
not an apples-to-apples model comparison for that entry specifically.

**Nondeterminism.** Single run per case for cost/time reasons, except a ~5-case spot-check
subset run 3× to report a variance sanity-check rather than presenting single noisy LLM runs
as if they were stable.

## Scoring Rubric & Judging

Per case, per tool:

- **Recall** — hit / partial / miss on the real or injected bug.
- **False positives** — count and list of non-issues raised, with particular weight on the 4
  clean-PR cases where any finding at all is a false positive by construction.
- **Citation/grounding accuracy** — automated via `check_citations.py`: does each cited
  file:line actually exist and fall within the real file's line count in the checkout used for
  that case.
- **Actionability** — manual, 1-5 scale: is the finding specific enough to act on, or generic
  advice that could apply to any codebase.

**Named process.** With CodeRabbit dropped, no tool identity needs to be hidden for legal
reasons, so scoring is done directly against real tool names (`aletheore`, `pr_agent`,
`deepsource`) — there is no anonymization/relabeling step in the pipeline. The residual bias
risk this removes protection against (the benchmark's own author unconsciously favoring
Aletheore's output during judgment calls) is instead mitigated by the second, independent
scoring pass below.

**Dual judging.** Every case gets two independent scoring passes against the rubric above,
blind to each other (the LLM judge never sees your scores, and vice versa), though neither is
blind to tool identity:

1. **Your manual review** — authoritative for the published headline scorecard.
2. **An independent LLM judge** (`llm_judge.py`) — a model not itself under test (a different
   provider/family than whichever model powers Aletheore's Flash Review and PR-Agent's config
   in this run, recorded in `METHODOLOGY.md`) scores the same findings against the same
   ground truth and rubric.

The human/LLM agreement rate per rubric dimension is published alongside the headline
scorecard as an additional credibility signal; a case where the two diverge is called out
explicitly in the report rather than smoothed over or quietly dropped.

## Publication & Reproducibility

`REPORT.md` (adaptable into a `website/` page later) plus the full
`benchmarks/pr-review-benchmark/` directory committed to this repo: cases, ground truth, all
three tools' raw outputs (no exclusions — no ToS-restricted tool remains in scope), scoring
sheets, and `METHODOLOGY.md` recording exact run dates, model versions, and the specific
product versions/plans of DeepSource and Qodo/PR-Agent used (and Aletheore's own deployed
commit/model), since all three will keep changing after this snapshot is taken.

Framing: report all four scoring dimensions honestly for every tool, including where a
competitor wins one. Headline framing emphasizes grounding/citation accuracy as Aletheore's
differentiator, but the scorecard itself is never edited to hide an unfavorable result — a
mixed outcome reads as more credible than a clean sweep.

## Known Limitations (stated in the report up front, not discovered later)

- **Single-run scoring on ~20 of 25 cases** — LLM nondeterminism means an individual case
  result is noisier than the spot-checked subset; the report states this plainly rather than
  presenting all cases as equally stable.
- **DeepSource is architecturally different from the other two.** It's closer to static
  analysis plus AI-assisted autofix than a pure LLM PR-reviewer in the same shape as Aletheore
  or Qodo/PR-Agent. It stays in the comparison, but the report calls this out explicitly as a
  caveat on that entry specifically, rather than implying full architectural parity across all
  three.
- **Scoring is not blind to tool identity.** With CodeRabbit dropped, the comparison is named
  throughout rather than anonymized; the benchmark's own author could unconsciously favor
  Aletheore's output during manual scoring. The independent LLM judge's agreement rate is the
  mitigation for this, not a blind-labeling process (see Scoring Rubric & Judging).
- **The LLM judge is a second opinion, not ground truth.** It has its own potential blind
  spots (e.g., over-weighting confident phrasing) and is not presented as equivalent in
  authority to the manual review — the human score is what the headline scorecard reports;
  the LLM judge's role is the published agreement-rate signal and a check against reviewer
  fatigue, not a replacement for the manual pass.
