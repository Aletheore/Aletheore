# Reproducibility — what is and is not guaranteed

Read this before citing any number here as settled.

## Versions the published results were produced with

| | version |
|---|---|
| Aletheore | 0.7.2 |
| RepoWise | 0.27.0 |
| Ollama | 0.32.6 |
| Embedding model (both tools) | `nomic-embed-text` (768-dim) |
| Wiki generation + judge model | `deepseek-chat` via api.deepseek.com, 2026-08-12 |
| RepoWise wiki generation model | `deepseek-v4-flash` |
| Python / LanceDB | 3.12.10 / 0.34.0 |
| Corpus | Flask @ `2a8a38b051fc248865730bf3511bf2e2ea325e81` |

Runners call `_bench.check_corpus_commit()` and refuse to score against a
different checkout, because the ground truth was verified against that one.

## What reproduces exactly

**Retrieval (the location benchmark).** Deterministic given the same corpus,
same Aletheore version and the same local embedding model. Same index, same
ranking, same top-k. Re-running changes only latency.

**Ground-truth verification.** `scripts/verify_ground_truth.py` is pure file
inspection: 32/32 or it fails loudly.

**Scoring.** `score.py` / `score2.py` are arithmetic over committed raw results.
Anyone can recompute every published number from `results/*.json` without an API
key, an LLM, or a network.

## What does NOT reproduce exactly

**Anything involving an LLM.** Wiki generation and the architecture judge are
sampled. Judging runs at `temperature: 0` and each question is graded twice with
the systems' positions swapped, which controls position bias — it does not make
the result deterministic.

**We have not measured judge variance.** The architecture scores are single
runs. A repeat run would land near, not on, 2.17 and 2.42, and we do not
currently know the spread. Treat gaps smaller than ~0.2 as inside the noise: on
that basis "RepoWise is ahead on comprehension" is supported, and any precise
gap figure is not.

**Model drift.** `deepseek-chat` is a moving target. The same script in six
months calls a different model. Nothing here pins a model snapshot, so a later
run measures a different system.

**Aletheore version affects the retrieval number.** The committed
`results_aletheore.json` (75.0% top-1) was produced by the code in this PR.
A build that also carries the search-index chunking change — deliberately
excluded here, see AIRVIEW_GAP.md — scores 71.9% instead. Reproduce against the
PR's code, not a later checkout.

## Known scope limits

- One repository, one language, 44 questions.
- **Questions were authored by us.** Mitigated by sourcing from Flask's public
  API/docs and mechanically verifying every anchor, but it is the weakest link
  in the methodology and an independently-authored set would be stronger.
- The architecture arm depends on an LLM judge grading LLM-written prose.
- The independent-judge cross-check (`llama3.1:8b`) covers only the
  raw-code-chunks arm, and tied 16 of 24 pairings — directional, not precise.

## Running it

```bash
git clone https://github.com/pallets/flask /tmp/bench-flask
git -C /tmp/bench-flask checkout 2a8a38b051fc248865730bf3511bf2e2ea325e81

python3 scripts/verify_ground_truth.py          # must print 32/32
cd /tmp/bench-flask && aletheore scan . && aletheore index .
cd -  &&  python3 scripts/run_aletheore.py
python3 scripts/score.py results/results_aletheore.json=ALETHEORE
```

Paths are environment variables, not hard-coded: `BENCH_FLASK`, `BENCH_FLASK_RW`,
`BENCH_OUT`, `BENCH_ENV_FILE`, `GITHUB_APP_PATH`.

The RepoWise half needs an LLM key in `$BENCH_ENV_FILE` and — importantly —
`REPOWISE_EMBEDDER=ollama`. Without it, `repowise search --mode semantic`
silently degrades to full-text and you will benchmark the wrong thing. See
METHODOLOGY.md; that defect invalidated our own first run.
