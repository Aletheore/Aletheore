# Methodology

- **Run date:** TBD at execution time
- **Aletheore version/model:** TBD (hosted Flash Review, GitHub App; hardcoded to `deepseek-v4-flash` server-side as of 2026-07-26 — see `github-app/scan_worker/model_tiers.py`)
- **Qodo/PR-Agent version/model:** TBD (CLI, BYOK; configured to `deepseek/deepseek-v4-flash` for model parity with Aletheore)
- **DeepSource plan/version:** TBD
- **LLM judge model:** TBD (must be a different provider/family than the models above)
- **Corpus:** 25 cases — 15 real bug-fix reconstructions, 6 injected bugs, 4 clean PRs, across Python/TypeScript/Go/Java
- **Known limitations:** see `docs/superpowers/specs/2026-07-26-aletheore-pr-review-benchmark-design.md`'s
  "Known Limitations" section — reproduced in full in the published report, not just linked.
