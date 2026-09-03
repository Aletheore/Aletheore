# Aletheore Security-Scanner Benchmark

Measures precision/recall of Aletheore's two deterministic security
scanners — `aletheore_secrets` (regex/entropy credential detection,
`src/aletheore/secrets.py`) and `aletheore_vulnerabilities` (OSV.dev-backed
dependency CVE lookup, `src/aletheore/vulnerabilities.py`) — against a
labeled ground-truth corpus. These are the scanners behind Aletheore's
"evidence-backed" findings claim; unlike `benchmarks/pr-review-benchmark/`
(which scores LLM-synthesized PR review comments), neither had a
systematic accuracy measurement before this benchmark — only reactive bug
fixes had touched them.

Both scanners are fully deterministic (no LLM calls), so unlike
`pr-review-benchmark`, this benchmark has no paid-API cost and no LLM
judge: ground truth is exact-match against a labeled corpus, not a
judged score.

A synthetic pilot corpus (11 secrets cases, 5 vulnerabilities cases)
gives exact-match ground truth; a real-repo validation run (20 real
open-source repos, 21,430 files) gives a real false-positive-rate signal
the synthetic corpus can't provide on its own — and found 6 real product
gaps in `secrets.py`, all now fixed with regression tests. See
`REPORT.md` for full results and analysis.

## Layout

```
secrets/
  cases/<id>/repo/...          fixture file tree to scan
  cases/<id>/ground_truth.yaml expected finding (or none)
  scripts/fixtures.py          placeholder -> fake-secret expansion (see below)
  scripts/run_benchmark.py     runs find_secrets() against every case, scores it

vulnerabilities/
  cases/<id>/repo/...          a single dependency manifest (real historical
                                package/version pinned to a real, publicly
                                documented CVE, verified against OSV.dev
                                directly at corpus-build time)
  cases/<id>/ground_truth.yaml expected finding (or none)
  scripts/run_benchmark.py     runs check_vulnerabilities() against every
                                case, scores it (hits the real OSV.dev API)

real-repo-validation/
  fetch_repos.sh                downloads 20 real OSS repos (11 already
                                 vetted in pr-review-benchmark's own
                                 corpus, 9 more added to cover missing
                                 ecosystems) at pinned commits, into a
                                 scratch dir - never checked in here
  run_real_repos.py             runs both scanners against those real
                                 trees for a real-scale FP-rate signal
                                 the synthetic corpora can't provide
  results-2026-09-03.json       raw output from the run described in
                                 REPORT.md's "Real-repo validation"
```

## Why secrets fixtures store placeholders, not literal values

`secrets/cases/*/repo/**` files contain tokens like
`__BENCHMARK_AWS_ACCESS_KEY__`, not literal fake credentials. Storing a
real-shaped secret (even a fabricated one) directly in the repo trips
GitHub's push protection — exactly the problem
`pr-review-benchmark/scripts/fixtures.py`'s docstring documents for its
Stripe-key case. `secrets/scripts/fixtures.py` generalizes that same
fix: the runner copies each case's `repo/` into a tempdir and expands
placeholders there, so the committed corpus never contains anything a
secret scanner (GitHub's or Aletheore's own) would match, while the code
actually scanned always does.

## Running

```bash
python3 secrets/scripts/run_benchmark.py
python3 vulnerabilities/scripts/run_benchmark.py
```

Each prints a per-case verdict table plus recall / false-positive
numbers. No setup beyond `pip install -e src` (for the `aletheore`
package) and `pyyaml`.

For the real-repo validation run (see `REPORT.md`), from an empty
scratch directory:

```bash
cp <this-dir>/real-repo-validation/{fetch_repos.sh,run_real_repos.py} .
./fetch_repos.sh
python3 run_real_repos.py
```

## Known limitations (pilot)

1. **Small pilot, not a full corpus.** All 7 secret patterns and all 10
   vulnerability ecosystems now have at least one pilot case
   (`private_key_header` and three `generic_credential_assignment`/
   `private_key_header` false-positive shapes were added after the
   real-repo run found real gaps in them; crates.io/RubyGems/Packagist/
   NuGet/Gradle/Swift were added after being proven correct via the
   real-repo run first, then given a dedicated hand-verified-CVE case
   each). The secrets pilot has 5 true-negative cases now (up from 1);
   vulnerabilities has 7 (up from 1, one per ecosystem) - still enough to
   catch a broken parser, not enough for a statistically meaningful
   per-ecosystem false-positive-rate claim on its own.
2. **The vulnerabilities benchmark's ground truth *is* OSV.dev**, the
   same source `check_vulnerabilities` queries live. This isn't an
   independent oracle the way the secrets corpus's hand-authored ground
   truth is — it mainly tests Aletheore's manifest-parsing and
   OSV-integration correctness (does it parse this pom.xml/go.mod/
   package.json/requirements.txt right, query the right ecosystem name,
   surface the result), not "does OSV.dev's data agree with reality."
   A case's verdict can also drift if OSV.dev's own database changes
   after corpus-build time (recorded per-case in each `ground_truth.yaml`
   description).
3. **Live network dependency.** The vulnerabilities run hits the real
   OSV.dev API; a case reports `ERROR` (excluded from recall/FP
   denominators) rather than a false verdict if OSV.dev is unreachable.
