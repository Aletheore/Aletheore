# Retrieval regression gate (Bench-CI)

Automated check on pull requests that touch `src/aletheore/search_index.py`
or `src/aletheore/scanner/graph.py`: builds a real search index over a real
open-source repo, runs Aletheore's real search against a set of hand-verified
questions, and fails the check if retrieval quality (MRR) drops by more than
`--max-mrr-drop` (default 0.02) versus `baseline.json`.

## Scope, honestly

This is **one corpus (Zod), not the four** (Flask/Gin/Serde/Zod) that
[aletheore-benchmarks](https://github.com/Aletheore/aletheore-benchmarks)
publishes numbers for. That repo's full run depends on infrastructure this
gate deliberately doesn't take on: a local multi-repo checkout workspace, and
running against every published corpus on every relevant PR. One real corpus
that actually runs in CI beats a four-corpus design that doesn't run at all.

Zod was chosen because it's the corpus the barrel-file rank penalty
(`search_index.py`'s `_is_barrel_file`) specifically targets - the questions
and pinned commit are vendored from `aletheore-benchmarks` (MIT, same org).

## Files

- `fixture.json` - the pinned repo, commit, and question files.
- `questions/zod.json`, `questions/zod_vocab.json` - hand-verified
  question → ground-truth-file pairs, vendored from `aletheore-benchmarks`.
- `build_fixture.py` - clones the pinned commit and runs the real
  `aletheore scan` + `aletheore index` CLI over it. Idempotent: a rerun with
  the fixture already built is a no-op, which is what makes the CI cache
  (keyed on `fixture.json`'s commit) actually save time.
- `score.py` - runs every question through `search_index()`, computes
  top-1/3/5 and MRR (same definitions as `aletheore-benchmarks`' own
  `score_retrieval_matrix.py`, so the numbers are directly comparable), and
  gates on a real drop versus `baseline.json`.
- `baseline.json` - the real, measured score to gate against.

## Running locally

```bash
cd benchmarks/retrieval-regression
python3 build_fixture.py   # clones Zod, scans+indexes it (local Ollama, no API key)
python3 score.py           # scores and gates
```

Override the workspace location with `BENCH_CI_WORKSPACE` (defaults to
`.fixture-cache/` inside this directory, gitignored).

## Updating the baseline

Only after a deliberate, reviewed ranking change - never to silence a real
regression:

```bash
python3 score.py --update-baseline
```
