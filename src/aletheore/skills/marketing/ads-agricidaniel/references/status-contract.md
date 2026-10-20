# Repository status contract

Use the deterministic core for `ads status` and `ads next`; do not infer
readiness from prose or make network calls.

```bash
python -m claude_ads_core status --root . --as-of YYYY-MM-DD
python -m claude_ads_core next --root . --as-of YYYY-MM-DD
```

Optionally pass `--release-gate PATH` for an exact release-gate report. The
`artifact-order-v1` policy selects exactly one result in this order: unverified
load-bearing claim, stale load-bearing claim, first declared maturity blocker,
first canonical failed release check, first disabled scoring profile, first
disabled capability, then lowest unsatisfied maturity level. Repository
manifests and the optional report are the only evidence. Missing or malformed
required inputs fail closed.
