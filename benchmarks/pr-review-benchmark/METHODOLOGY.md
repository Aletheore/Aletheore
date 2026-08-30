# Methodology

- **Run date:** 2026-08-30 (final, post-deploy 3-way run)
- **Deployed baseline:** production commit `35e18f8`, tag `github-app-deploy-2026-08-30` — includes every fix from this session (#462 through #476), confirmed live before this run started.
- **Aletheore AIR:** `gpt-5.6-luna` (generation) + `deepseek-v4-flash` (dual-agent verification). Scored via direct `review_diff()` invocation (real diffs, real file content, `verify_with_second_model=True`), not live webhook — see REPORT.md's Known Limitations #2 for why.
- **Aletheore Flash:** `gpt-5.6-luna` generation only, `verify_with_second_model=False`. Same direct-invocation methodology as AIR, new arm added to isolate verification's real effect on recall vs. precision.
- **PR-Agent:** explicitly configured to `gpt-5.6-luna` (`--config.model=gpt-5.6-luna --config.custom_model_max_tokens=128000`), not its own `gpt-5.5` default — true model parity with Aletheore. 4 of 24 cases refreshed fresh this run; 20 reused from an earlier same-day run under identical config (see REPORT.md limitation #3).
- **LLM judge:** none this run — see REPORT.md's Known Limitations #1. Manual (Step 4) scoring only, read against real finding message content.
- **Corpus:** 24 of 25 cases — 15 real bug-fix reconstructions, 5 injected bugs, 4 clean diffs, across Python/TypeScript/Go/Java. Case `020` excluded (corpus fixture issue). DeepSource excluded (real quota exhaustion, unrelated to this run).
- **Prior runs, both superseded:** an initial run compared Aletheore's free tier (weak fallback models, no verification) against PR-Agent's `gpt-5.5` default — an unintentionally unfair comparison on both model tier and model cost/capability. A same-day follow-up corrected the model to Luna-vs-Luna but ran against pre-deploy code via direct invocation only. This run is the first to combine correct model parity with a confirmed-deployed baseline.
- **Known limitations:** see REPORT.md's "Known Limitations" section, reproduced there in full.
