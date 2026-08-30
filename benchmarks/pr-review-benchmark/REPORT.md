# Aletheore PR-Review Benchmark — Results

**Run: 2026-08-30.** Aletheore (real AIR-tier: `gpt-5.6-luna` generation + `deepseek-v4-flash` dual-agent verification) vs. PR-Agent (Qodo), explicitly reconfigured onto the same `gpt-5.6-luna` model instead of its own default (`gpt-5.5`), for true model parity. 24 of 25 corpus cases (case `020` excluded — a corpus fixture issue, unrelated to either tool, not yet re-verified against a live push). DeepSource excluded this run (its GitHub App had exhausted its analysis quota on the test account partway through an earlier same-day run and never reconnected — a real infrastructure gap, not a methodology choice).

Scored two ways: a manual pass reading every finding's actual message content against `cases/<id>/ground_truth.yaml` (not file:line proximity alone — a citation near the right line with unrelated content is scored a miss), and an independent LLM judge (Claude, a different model family than either tool under test), dispatched blind — one fresh subagent per case, given only the ground truth and the findings, with no awareness of this benchmark, either tool's identity beyond its name, or which run this is.

This run **supersedes an earlier same-day comparison** that unintentionally pitted Aletheore's free tier (no OpenAI, no verification pass) against PR-Agent's `gpt-5.5` default (a stronger, ~20x more expensive reasoning model) — an unfair baseline, corrected here. See `METHODOLOGY.md` for full run details.

## Overall

| Tool | Hit | Partial | Miss | False Positives | Avg Actionability | Location Grounding | Content Grounding |
|---|---|---|---|---|---|---|---|
| aletheore | 16 | 0 | 8 | 0 | 4.94 | 0.96 | n/a |
| pr_agent | 6 | 0 | 18 | 10 | 4.86 | 0.13 | 0.00 |

**Location grounding** — the cited file exists and the cited line is inside it. A static analyser reporting its own AST positions clears this by construction, so a rate of 1.0 here is close to uninformative on its own.

**Content grounding** — text the finding quotes verbatim really appears near the line it cites. This is the bar Aletheore's Flash Review enforces on itself in production, applied identically to every tool here. Findings that quote nothing verbatim can't be scored at this level and are excluded from its denominator rather than counted as passes or failures — PR-Agent's 0.00 reflects that its findings are prose explanations, not verbatim-quoting citations, not that every one is wrong; several of its "miss" findings on real_bug_fix cases were independently judged correct in content by the LLM judge below despite failing this stricter, citation-specific check.

## By category

| Category | Cases | Aletheore hit/miss | Aletheore FPs | PR-Agent hit/miss | PR-Agent FPs |
|---|---|---|---|---|---|
| Real bug-fix reconstructions | 15 | 13 / 2 | 0 | 4 / 11 | 6 |
| Injected bugs | 5 | 3 / 2 | 0 | 2 / 3 | 2 |
| Clean diffs (false-positive test) | 4 | 0 / 4* | 0 | 0 / 4* | 2 |

\* On clean diffs there is no ground-truth issue to hit, so "miss" here means correct silence for Aletheore (0 findings, 0 false positives on all 4) and 2 spurious findings for PR-Agent (cases `023`, `024`) — see the false-positive column, not the hit/miss column, for the real signal on this category. The independent LLM judge in fact scored these as "hit" (interpreting a correct-silence tool as having correctly identified no issue exists) — a genuine rubric ambiguity on clean cases worth resolving before the next run, not an error in either scoring pass; see Known Limitations.

## Human/LLM judge agreement

- **Recall agreement: 83.3%** (20 of 24 cases, the human manual score and the blind LLM judge's score agreed on hit/partial/miss)
- **Actionability agreement: 58.3%** (looser by nature — a 1–5 scale has more room to differ than a 3-way categorical call)

The disagreements are informative, not noise to explain away:
- The clean-case "hit" interpretation above (4 cases) accounts for most of the recall disagreement — a real rubric ambiguity, not a judge error.
- Case `009` (cobra completions args mutation): manually scored PR-Agent's finding as a clean hit; the LLM judge scored it "partial," reading its explanation (framed around `SetArgs`/completion re-use rather than the exact backing-array-mutation mechanism) as less directly on-point than the manual read credited it.

## Known limitations

- **Single run, not an average.** This corpus has a documented noise floor from an earlier, unrelated investigation on these same 25 cases: a model's raw proposal rate swung from 56% to 4% across two identical reruns (see `SWEBENCH_RUN_STATUS.md`). A gap this large (13 vs. 4 on real-bug-fix cases) is very unlikely to be pure noise in aggregate, but any single case can flip on a rerun — case `013` (Gson equals/hashCode) is a miss for both tools here despite being independently, verifiably fixable by Aletheore's prompt (see `flash_review.py`'s paired-method-consistency addition, verified working on this exact case in isolation the same day, just not reproduced in this specific run).
- **This run's Aletheore side bypassed the live GitHub-fetch path.** To capture clean per-tool-per-provider token counts, Aletheore's review was invoked directly (`review_diff()`) rather than through the real webhook, which also means three same-day fixes to that fetch layer (GitHub-omitted-patch reconstruction, evidence-context byte budgeting, diff total-size budgeting) were not exercised by this specific run — they're shipped and unit-tested, just not end-to-end validated here.
- **Scoring is not blind to tool identity.** CodeRabbit was dropped from this comparison entirely (ToS), leaving a named, not anonymized, comparison between Aletheore and PR-Agent. The independent LLM judge above is the mitigation for reviewer bias, not a blind-labeling process.
- **The LLM judge is a second opinion, not ground truth.** The manual score is what the headline table reports; the judge's role is the published agreement rate and a check against reviewer fatigue, not a replacement authority — see the clean-case rubric ambiguity above for a concrete instance of the two passes reading the same rubric differently.
- **DeepSource is architecturally different** from the other two tools (closer to static analysis than an LLM PR-reviewer in the same shape as Aletheore or PR-Agent) and is excluded from this specific run for an unrelated infrastructure reason (quota), not a judgment about its architecture.

Full methodology, model versions, and run timing: see `METHODOLOGY.md`.
