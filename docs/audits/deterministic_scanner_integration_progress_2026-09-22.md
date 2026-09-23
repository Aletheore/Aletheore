# Deterministic Scanner Integration — Progress (2026-09-22)

**Continues:** [`deterministic_scanner_evaluation.md`](deterministic_scanner_evaluation.md) (the original tool survey) and [`deterministic_scanner_integration_scope.md`](deterministic_scanner_integration_scope.md) (the placement plan across MCP/PRs/scan/evidence/AIRview/Docs).

**Purpose of this doc:** a single, complete handoff of what happened in this session (Trivy + PMD integration, a separate deterministic PR check, several real production bugs found and fixed, and a grounding investigation) — for whichever agent or person picks this up next.

## Where things stand: open PRs

None of these are merged yet (deliberately — the user is reviewing them):

- **#763** — `feat/trivy-scanner-integration`: Trivy (`secret,misconfig` scanners), always-on in `static_analysis/_SCANNERS`. Real timing justified always-on (3.15s on `github-app/`, 10.46s full repo). Also fixes a real bug found via testing: Trivy made an unwanted network call to Maven Central even scoped to `secret,misconfig` (not `vuln`) — failed with a real 429 rate-limit against `google/gson`. Fixed with `--offline-scan`, confirmed zero detection loss.
- **#764** — `feat/deterministic-pr-check-run` (based on master): a new, separate, non-plan-gated GitHub Check Run — **"Aletheore Deterministic Scan"** — combining Semgrep/gosec/Bandit/Trivy findings via `history.py`'s new `static_analysis` diff category. Deliberately the *one* check run in `jobs.py` NOT gated behind `plan == "free"`. Also removes the old `find_static_analysis_regressions` (Semgrep+Bearer merged into Flash Review's own LLM findings) — a real, controlled experiment this session measured that merge making Flash Review's recall/precision *worse*, not better, on the 13-case real-PR corpus. Also adds real inline Checks-API annotations (batched at GitHub's real 50-per-request limit), not just a summary block.
- **#765** — `feat/pmd-scanner-integration` (stacked on #763, since both touch `_SCANNERS`): PMD Java code-quality scanning, always-on (2.74s on `google/gson`, 5.0s on `apache/commons-lang`). Real noise found and filtered: unfiltered PMD produced 3,582 violations on gson, 70% from two JUnit-convention rules that aren't bugs; `CloseResource` sampled as a real false positive (flagged an in-memory tree builder). `pmd_scanner.py`'s `_NOISY_RULES` excludes these, evidence-based.

## What was merged and deployed tonight

- **#766** — health-sweep stale-alert false positive fix (see "Real incidents found" below). Deployed to prod, tagged `github-app-deploy-2026-09-22`.
- **#767** — changelog entry for the above deploy.
- **#768** — a new runbook, [`MANUAL-ACCOUNT-CHANGES.md`](../operations/MANUAL-ACCOUNT-CHANGES.md), documenting the credit-reset gap below.

## Real incidents found and fixed live in production

1. **Trivy's unwanted Maven network call** (folded into #763) — see above.
2. **Bearer diff-scoping attempt, reverted** — tried adding Bearer to the new Deterministic Scan check via a diff-scoped pass (materializing only the PR's changed files, to dodge Bearer's real 300s+ full-repo cost). Measured *worse* accuracy: isolating this repo's own `jobs.py` (even with sibling files for context) made Bearer report 11 false-positive `os_command_injection` findings the full-repo scan correctly suppresses. Reverted; real fix (async full-scan + Check Run status update instead of shrinking scope) is designed but not built — see `[[project_bearer_joern_async_deterministic_scan_fix]]` in memory.
3. **Health-sweep stale-alert false positive** (fixed, #766, deployed) — Aletheore's own dogfood install (`Aletheore/Aletheore`, installation_id `147514632`) had its plan changed `air` → `flash` via a **direct database write**, not a real Paddle webhook. `list_health_check_targets_all` is deliberately AIR-exclusive, so its one `health_check_targets` row went silently ineligible — correct, self-healing behavior. But `run_health_sweep_staleness_check_job` only measures time since `endpoint_health`'s last write, which froze at the moment of the downgrade and only grew (confirmed live: 1 day 15.5 hours) — re-alerting via email every 6 hours indefinitely for a fully-expected state. Fixed: the staleness job now skips alerting when there are currently zero eligible targets.
4. **Same manual DB switch also drained LLM spend credit** — `base_credit_allotment_usd` stayed at `18.00` (air's old allotment) on a fully-drained `$0` balance, because only the real Paddle webhook path (`reset_billing_period_credit`, traced and confirmed correct) resets credit on a plan change — a direct SQL write skips it entirely. Effect: `run_flash_review_job` silently no-op'd (~36ms, no comment, no error) for every PR on this repo. **Fixed directly in prod**: reset to flash's real `$5.00` allotment. Root cause and procedure documented in #768's runbook so this doesn't silently recur.
5. **A real 422 error posting one Flash Review inline comment on #764** — `github-app/scan_worker/jobs.py:1282`, `httpx.HTTPStatusError: 422 Unprocessable Entity` from GitHub's API. The review's own summary comment said "4 finding(s) posted" but only 3 landed — a real bug (likely a finding whose cited line isn't actually part of the diff's hunks, which GitHub rejects for inline comments) plus a secondary honesty gap in the summary count. **Not yet fixed** — flagged, not investigated further this session.

## Ops actions taken directly on production tonight

- IndieRouter rotated Aletheore's API key and raised the rate limit to their max (120) plus gave ₹1,000 (~$11-12) in credits. Restarted `app-server`/`scan-worker`/`scan-worker-2`/`health-worker`/`scheduler` to pick up the new key. Checked the codebase for any concurrency/throttle setting tuned around the old rate limit — none exists, so this is pure headroom, nothing to adjust in code.
- Manually reset the dogfood install's `base_credit_allotment_usd`/`base_credit_remaining_usd` to flash's real `$5.00` (see incident 4 above).
- Manually enqueued `run_flash_review_job` for the then-open PRs (#763, #764, #765, #768) after the credit reset, to confirm Flash Review actually fires again — all four completed with real LLM spend logged, real reviews posted (see incident 5 for the one real gap found).

## The grounding investigation (result: no changes made)

The user asked whether Flash Review's grounding (`_validate_findings` in `flash_review.py` — drops findings whose cited line is outside the diff, whose quoted content doesn't match, or whose named symbol doesn't appear in context) should be loosened, since a real PR (#765) had two findings dropped as "outside the diff."

- Retrieved the actual dropped findings' file/line was possible (only logged, not the text) — a fresh re-run of that same diff produced two *different* findings that both survived grounding: one real (a timeout-message imprecision, confirmed true against the code) and one false (a claim about PMD's `ruleset` field format, directly contradicted by real PMD 7.27.0 output already captured this session).
- Ran a real, controlled 4-way A/B comparison on the 13-case real-PR corpus (`martian_real_gold` / `pr_review/real_pr_recall_corpus`):

  | Config | Recall | Precision |
  |---|---|---|
  | Bare + grounding (3-trial avg — represents production once #764 ships) | **58.3%** | **39.4%** |
  | Bare + no grounding | 52.3% (23/44) | 33.0% (33/100) |
  | With-evidence + grounding (original) | 54.5% (24/44) | 30.8% (28/91) |
  | With-evidence + no grounding | 56.8% (25/44) | 34.5% (38/110) |

  On the config that matters (bare, since that's what ships post-#764), removing grounding made **both** recall and precision worse. **Conclusion: no changes to grounding.** Scripts live in `martian_real_gold/run_bare_no_grounding.py` and `run_no_grounding.py` (scratchpad, not committed) if this needs re-running later.

## Tool-integration list: what's left

From [`deterministic_scanner_evaluation.md`](deterministic_scanner_evaluation.md)'s full candidate list:

| Tool | Status |
|---|---|
| Semgrep, gosec, Bandit | Already shipped (pre-existing), always-on |
| Trivy | **#763, open, not merged** |
| PMD | **#765, open, not merged, stacked on #763** |
| Bearer, Joern, SonarQube | Already shipped (pre-existing), opt-in |
| **Graudit** | Not started — **gated on GPL-3.0 legal sign-off**, a decision for the user, not attempted |
| **YASA-Engine** | Not started — real friction found tonight: getting-started docs live entirely on an external wiki (not the repo), needs a Node.js build-from-source (no quick binary), and its taint analysis is architecturally the same "needs whole-program context" shape that broke Bearer's diff-scoping attempt. Needs a dedicated session with real build-environment setup, not a quick add. See `[[project_yasa_engine_needs_dedicated_session]]` in memory. |
| Error Prone, Infer, SpotBugs, Phasar | Build-dependent (javac/bytecode/LLVM IR), scan-only, not PR-review-compatible without real work — not attempted |
| Reviewdog | Not a detector — real value would be upgrading #764's Check Run to use it instead of the native Checks API annotations already built; not needed since Aletheore already normalizes every scanner's output itself |
| LiSA | Low priority, deprioritized |

## What the next agent should do

1. Wait for the user to review #763/#764/#765 before merging (do not merge without explicit instruction).
2. If asked to continue the tool list: Graudit needs the user's legal call first; YASA-Engine needs a real, dedicated session (build it, find real docs, test on a toy example) before any integration attempt — don't rush it the way Bearer's diff-scoping attempt was rushed and had to be reverted.
3. The real 422 inline-comment-posting bug on #764 (incident 5 above) hasn't been investigated — worth a look before or after merge.
4. Re-run the Martian benchmark against the actual deployed prod code (not a local branch) once #763/#764/#765 are reviewed and merged, per the user's own plan — for a true apples-to-apples number.
