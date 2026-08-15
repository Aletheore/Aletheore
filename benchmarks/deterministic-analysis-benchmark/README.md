# Aletheore Deterministic Analysis vs. Bare LLM — Runbook

This benchmark tests a narrower, harder-nosed question than `../pr-review-benchmark/`'s PR-review comparison: not "does Aletheore's LLM wrapper catch more bugs than a bare model call" (that investigation's answer, recorded in the parent conversation, was largely no — see that benchmark's findings), but **"can a bare LLM even reproduce what Aletheore's non-LLM, deterministic scanner computes, when given the same underlying data?"**

See `REPORT.md` for the results and full write-up. This file covers reproduction only.

## Why this comparison, and why it's fair

Flash Review is a thin LLM wrapper around a diff — that's the part of Aletheore that gets commoditized every time a new frontier model ships. Hotspots, ownership, and dead-code detection are not LLM calls at all; they're git-history and import-graph analysis. A model can't out-compute a `Counter()` by being smarter — either it has the exact data and computes correctly, or it doesn't. This benchmark makes sure the bare-LLM side always has the exact data: same git log, same import lines, no information withheld, no impossible task assigned as a strawman.

## Corpus

[pallets/flask](https://github.com/pallets/flask), a real, third-party, well-known repository (not authored by Aletheore). Pinned to commit `2a8a38b051fc248865730bf3511bf2e2ea325e81`. Local clone conventionally lives at `~/.aletheore-bench/multi-flask` (shared with other benchmarking in this repo — see `../pr-review-benchmark/`'s references to the same corpus).

## Prerequisites

- Python 3.10+
- `git`
- A local checkout of the corpus repo (see above)
- `aletheore` CLI installed (`pip install -e src` from repo root), for generating ground truth from Aletheore's own scanner
- `OPENAI_API_KEY` in `.env` at repo root, for the bare-LLM calls (real API cost, ~$0.01 per model per full run)

## Step 1: Build inputs from the corpus repo

```bash
cd ~/.aletheore-bench/multi-flask
python /path/to/benchmarks/deterministic-analysis-benchmark/scripts/build_inputs.py \
  /path/to/benchmarks/deterministic-analysis-benchmark/data 1500
```

This writes `hotspots_input.txt`, `ownership_input.txt`, and `deadcode_input.txt` to `data/` — see `scripts/build_inputs.py` for exactly what each contains. `1500` is the commit-count cap for the hotspots/ownership slice; see `REPORT.md`'s "A structural limit, not just an accuracy one" section for why the full history doesn't fit in a single LLM call at all.

Also generate Aletheore's own ground truth while in the corpus repo:

```bash
aletheore scan .
aletheore query hotspots > /path/to/data/ground_truth_hotspots_full_history.json
aletheore query ownership <any-file> > /path/to/data/ground_truth_ownership_REPO_WIDE_bug_note.json
aletheore query dead-code > /path/to/data/ground_truth_deadcode_full.json
```

Note the filename on the ownership ground truth — `aletheore query ownership <target>` currently ignores `target` and always returns the repo-wide aggregate (see `REPORT.md`'s "Known limitations"). Any file path produces identical output; that's not a mistake in this runbook, it's the bug.

## Step 2: Compute exact ground truth from the SAME sliced input the model sees

```bash
cd /path/to/benchmarks/deterministic-analysis-benchmark
python scripts/exact_ground_truth.py data
```

This is deliberately **not** `aletheore query hotspots`'s full-history output — it's a plain Python `Counter` over the identical 1,500-commit slice fed to the model, so the comparison is apples-to-apples: same data, one side counts it deterministically, one side is asked to count it.

## Step 3: Run the bare-LLM tests

```bash
python scripts/run_bare_llm.py data gpt-5.6-luna
python scripts/run_bare_llm.py data gpt-5.6-terra   # optional, not the production model
```

Each invocation makes exactly 3 real API calls (hotspots, ownership, dead-code), single-turn, no tools. Save the output — `data/model_outputs.md` in this repo is the exact transcript from the run `REPORT.md` describes.

## Step 4: Compare and write up

Manual step: read `data/model_outputs.md` against the exact ground truth from Step 2, number by number. `REPORT.md` is the result of doing this once; re-running Steps 1-3 and diffing against it is how to audit or refresh this report.

## Reproducibility

Every file needed to independently verify the numbers in `REPORT.md` is in `data/` — the exact inputs given to each model, the exact model outputs, and the exact ground-truth JSON from Aletheore's own scanner. Nothing in this benchmark depends on state that isn't checked into this directory.
