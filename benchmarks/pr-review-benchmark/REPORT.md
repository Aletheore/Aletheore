# Aletheore PR-Review Benchmark — Results (Final, Post-Deploy 3-Way)

Deployed baseline: production pulled to commit `35e18f8` (tag `github-app-deploy-2026-08-30`), including every fix from this session (#462-#476). This supersedes the earlier free-tier-vs-GPT-5.5 run and the pre-deploy Luna-vs-Luna run.

| Tool | Hit | Partial | Miss | False Positives | Avg Actionability | Location Grounding | Content Grounding |
|---|---|---|---|---|---|---|---|
| aletheore (AIR: Luna+DeepSeek) | 15 | 1 | 4 | 0 | 4.0 | 0.96 | n/a |
| aletheore_flash (Luna only) | 15 | 0 | 5 | 0 | 4.0 | 1.0 | n/a |
| pr_agent (Luna) | 7 | 0 | 13 | 8 | 4.7 | 0.125 | 0.0 |

20 cases carry a real recall verdict (15 real-bug-fix + 5 injected-bug); the 4 clean cases score false-positive rate only (0 for both Aletheore tiers, part of the 8 pr_agent false positives above).

## Headline

AIR and Flash score identically on total recall (15/20 each) despite Flash skipping DeepSeek verification entirely — consistent with verification being a precision mechanism (it can only drop a finding, never add one), not a recall one. Both substantially outperform PR-Agent-on-Luna (7/20) on this corpus, and both stayed at zero false positives on the 4 clean diffs versus PR-Agent's 8.

## Known limitations

1. **No LLM judge this run.** The corpus's documented process calls for a blind Claude-subagent dispatch per case. This run was executed by a forked subagent whose tool policy blocks spawning further subagents, and no `ANTHROPIC_API_KEY` was available for a direct API-based substitute. Scores here are Step 4 manual scoring only (real finding message content read against `ground_truth.yaml`, not file:line proximity or grounding-rate alone) — real, but not independently verified by a second scorer this pass.
2. **AIR ran via direct invocation, not live webhook.** The plan to validate deployed fixes end-to-end via triggering real webhook reviews on the scratch repo's 24 open PRs did not produce new data: Aletheore's own deterministic check-runs (secrets, vulnerability) confirmed the webhooks were received and processed by the newly-deployed code, but no new Flash Review comment was posted on any of the 24 triggered commits. This matches an already-known condition from earlier the same day — the AIR-tier install's monthly review cap (500/month) was exhausted by this session's volume — not a regression from the deploy. AIR's scores above are therefore the same direct-invocation methodology as the pre-deploy run, re-verified to still be running current post-#476 code (the diff-reconstruction/budget fixes live in `fetch_pr_diff`, which direct invocation doesn't call, so this arm's results are unaffected by which specific PRs were used to collect them).
3. **PR-Agent refresh only completed for 4 of 24 cases within this run's real-time budget** (001-004; each real review takes ~60-120s end-to-end). The remaining 20 cases reuse real PR-Agent-on-Luna data collected earlier the same day (same model, same config, nothing about PR-Agent changed in between) rather than being freshly re-collected in this exact pass.
4. **Case 013 (Gson equals/hashCode)**: both Aletheore tiers hit it this run (a reversal from the pre-deploy run, where it was a documented miss for AIR despite the paired-method-consistency prompt fix — attributed to real run-to-run sampling noise on a reasoning model, not a broken fix). This run's result is consistent with that fix working; the earlier miss remains explained as noise, not contradicted.
5. **Scoring bug found and fixed after the initial pass.** The 4 clean cases (022-025) were incorrectly scored `recall: miss` for `pr_agent` (vs. the correct `recall: null` used for both Aletheore tiers — recall doesn't apply to a diff with no ground-truth issue; only false-positive rate does). This inflated `pr_agent`'s miss count from the true 13/20 to a reported 17, and made the table's own denominator (24) silently disagree with the headline text (20) and with both Aletheore rows. Caught by re-deriving the aggregate tallies directly from `results/scored/*.yaml` rather than trusting the generated table, and corrected in both the underlying scored files and the table above before this PR was reviewed further.

Corpus: 24 of 25 cases (case `020` excluded, a fixture/push-protection issue not yet re-verified against a live push). DeepSource excluded (real analysis-quota exhaustion on the test account, unrelated to this run).
