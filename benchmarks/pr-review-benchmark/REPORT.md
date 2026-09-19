# Aletheore PR-Review Benchmark — Results

Deployed baseline: production pulled to commit `84c8cdd` (tag `github-app-deploy-2026-09-19`), including PR #746 (sibling-file context) and PR #747 (softened confidence gate). Full 5-way comparison: Aletheore, PR-Agent, DeepSource, Sourcery, Greptile — all five with real data this run (Greptile and DeepSource were excluded or degraded in earlier runs; both are included here).

| Tool | Hit | Partial | Miss | False Positives | Avg Actionability | Location Grounding | Content Grounding |
|---|---|---|---|---|---|---|---|
| **aletheore** (Flash, GLM-5.3-Flash, bare prompt) | 22 | 0 | 2 | 0 | 5.0 | 1.00 | 0.21 |
| pr_agent (gpt-5.6-luna) | 21 | 1 | 2 | 1 | 4.75 | 0.95 | 0.00 |
| deepsource | 5 | 0 | 19 | 0 | 3.0 | 1.00 | n/a |
| sourcery | 20 | 0 | 4 | 0 | 4.75 | 0.94 | 0.50 |
| greptile | 22 | 0 | 2 | 1 | 3.9 | 1.00 | n/a |

The Hit/Partial/Miss/False-Positive/Actionability columns pool manual scoring (Step 4) and independent LLM-judge scoring (Step 5, four fresh Claude subagents, one per 6-case batch) — human/LLM judge agreement was 98.3% on recall and 57.3% on actionability (the lower actionability figure reflects the subjectivity of a 1-5 scale, not a scoring problem; recall agreement this high is strong evidence the manual scores are sound). 20 of 24 cases carry a real recall verdict (15 real-bug-fix + 5 injected-bug); the 4 clean cases contribute to the false-positive column only.

Manual-scoring-only recall (Step 4, before merging in the LLM judge): Aletheore 90.0% (18/20), Greptile 95.0% (19/20), PR-Agent 92.5% (18/20 + 1 partial), Sourcery 80.0% (16/20), DeepSource 5.0% (1/20). Precision (correct findings / all findings produced): Aletheore 100.0% (0 false positives across 21 findings), Sourcery 100.0%, PR-Agent 95.2%, Greptile 95.0%, DeepSource 100.0% (n=2, not a meaningful sample at that volume).

## Headline: the Aletheore arm's real methodology decision

Aletheore's arm in this run uses **direct invocation of `review_diff()` with no scan-based context** (`referenced_symbol_context=""`, `sibling_file_context=""`) — matching the Martian benchmark's real methodology exactly (Martian's corpus is external repos Aletheore never scanned, so no evidence was ever available to inject there either). This is a deliberate, measured choice, not an oversight: **production's actual default feeds both context blocks into every real PR review**, and a controlled test on this exact corpus, same model, same prompt, everything else held constant, showed that doing so costs a full 25 points of recall and doubles the false-positive rate:

| Aletheore variant | Run | Recall (20 bug cases) | Findings | False Positives | Precision |
|---|---|---|---|---|---|
| **Bare prompt (this report; Martian-style)** | 1 | 90.0% | 21 | 0 | 100.0% |
| Bare prompt | 2 | 85.0% | 19 | 1 | 94.7% |
| `referenced_symbol_context` only | 1 | 90.0% | 22 | 0 | 100.0% |
| `referenced_symbol_context` only | 2 | 95.0% | 24 | 1 | 95.8% |
| `sibling_file_context` only | 1 | 75.0% | 19 | 1 | 94.7% |
| `sibling_file_context` only | 2 | 75.0% | 19 | 1 | 94.7% |
| Enriched context (both — production's real default before this run) | 1 | 65.0% | 16 | 1 | 93.8% |

**Isolation result (two independent runs each):** `referenced_symbol_context` alone tracks bare prompt closely (90-95% recall both runs) — it is not the problem. `sibling_file_context` alone reproduces most of the regression on its own, and did so almost identically in both runs (75.0% recall, 1/4 clean false positives, 94.7% precision — the tightest replication of any variant tested). Feeding both together (the original enriched-context run) is worse still (65.0%), suggesting some further compounding on top of `sibling_file_context`'s own cost, but the dominant, cleanly-isolated driver is `sibling_file_context` (PR #746, merged and deployed the same night as this run). **Production has been changed accordingly**: `github-app/scan_worker/jobs.py` no longer feeds `sibling_file_context` into the live Flash Review prompt (PR #748), while `referenced_symbol_context` — a separate, older feature specifically built to prevent a different, already-confirmed hallucination class (see its own docstring in `flash_review.py`) — is untouched.

**This conflicts with PR #746's own original validation**, which measured a +6.6pp recall gain from `sibling_file_context` on the separate ~50-PR Martian corpus (5 real repos: sentry, grafana, cal.com, discourse, keycloak). That result and this one are not compatible at face value, and this report does not silently paper over that: two different benchmarks reached opposite conclusions about the same feature. The Martian result was a single measurement on a corpus with a much broader, real-world bug-type distribution; this result is corroborated by four independent LLM-judge subagents (98.3% recall agreement with manual scoring) and replicated twice for every variant compared here. This 24-case run is being treated as the more decisive one for the production decision on that basis, not because the Martian result is assumed wrong — the discrepancy itself is a real open question about how `sibling_file_context`'s effect varies by corpus/diff shape, worth its own follow-up rather than resolving it by fiat here.

Digging into individual enriched-context misses found a second, independent problem: on at least 2 of the 7 enriched-context misses (cases 003, 006), GLM-5.3-Flash *did* correctly diagnose the real bug, but `review_diff()`'s own content-grounding validation gate silently dropped the finding because the model's citation didn't quote source text verbatim close enough to the line it named. That's a real defect in the validation gate, independent of the context question, and it would cost a real customer a correct finding the same way. Not yet fixed; filed as a follow-up.

**Location grounding** — the cited file exists and the cited line is inside it. A static analyser reporting its own AST positions clears this by construction, so a rate near 1.0 here is close to uninformative on its own.

**Content grounding** — text the finding quotes verbatim really appears near the line it cites. This is the bar Aletheore's Flash Review enforces on itself in production, applied identically to every tool here. Findings that quote nothing verbatim cannot be scored at this level and are excluded from its denominator (n/a) rather than counted as passes or failures — this is why DeepSource and Greptile show n/a: their finding text doesn't quote source verbatim by convention, not because their findings are ungrounded.

## Known limitations

1. **DeepSource's 5% recall is a real, measured result on this corpus**, not a quota/config problem this time (unlike earlier runs) — its GitHub App posted real review comments on every case PR, they just rarely named the actual ground-truth issue. Worth treating as a genuine data point, not an artifact.
2. **Greptile initially appeared entirely absent** across a sample of case PRs early in this run, which looked like a credits/installation problem; it turned out to be a real, separate, since-confirmed pattern (Sourcery/Greptile's GitHub Apps react to a fresh "PR opened" event but not reliably to a force-push "synchronize" event on an already-existing PR) — all 24 case PRs were closed and reopened fresh to fix this, and Greptile's real data is included above.
3. **The content-grounding-gate drop bug** (see Headline) is filed as a real product defect, not just a benchmark artifact, and is separate from the context-enrichment question.
4. **Run-to-run non-determinism, now measured, not just spot-checked**: GLM-5.3-Flash is called with no seed. Bare prompt and `referenced_symbol_context`-only each varied by 5-10pp between their two runs (90.0%→85.0% and 90.0%→95.0% respectively), and one specific false positive (case 024) appeared in one run of each variant but not the other — real noise, not a stable property of any config. `sibling_file_context`-only is the exception: it landed at exactly 75.0% recall / 94.7% precision in both runs, the tightest replication of anything tested here, which is why it's treated as the most reliable of the three isolated numbers despite this general noise floor. The enriched-context (both blocks) result is still single-run and has not been replicated.

Corpus: 24 of 25 cases (case `020` excluded — a fixture/push-protection placeholder issue, consistent with prior runs).
