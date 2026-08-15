# Deterministic Analysis vs. Bare LLM — Report

**Question this answers:** when Aletheore's Flash Review wraps an LLM around a PR diff, that wrapper doesn't reliably out-catch a bare model call on the same diff (see `../pr-review-benchmark/`'s companion finding from the same investigation — file-level PR review is where the LLM commodity problem bites hardest). So what does the rest of the product — hotspots, ownership, dead-code detection, all computed from real git history and a real import graph, not an LLM's read of a diff — actually add? This benchmark tests that directly: give a bare LLM the same raw data Aletheore's deterministic scanner consumes, ask it the same question, and check whether it can produce the same answer.

**Short answer: no.** Not "worse." Not "close but rounds wrong." On every one of the three tests below, a bare LLM given complete, sufficient data either fabricated wrong numbers with full confidence, or (to its credit, once) refused to answer at all and asked for real code instead. Aletheore's scanner produces the exact answer, every time, without being asked twice.

## Corpus

[pallets/flask](https://github.com/pallets/flask) at commit `2a8a38b051fc248865730bf3511bf2e2ea325e81` (2026-08-11), 5,555 commits, 83 Python files. Chosen because it's real, well-known, not written by Aletheore, and already used elsewhere in this repo's benchmarking (see `../pr-review-benchmark/`'s citation-grounding corpus notes referencing the same clone's import-density stats).

`aletheore` CLI version `0.8.12`.

## Methodology — what makes this a fair test

The bare-LLM side is never handed an impossible task and never denied information the deterministic side has. For each test:

1. Extract the **minimum raw data** a human would need to compute the answer by hand (a git log slice, or every file's own import lines) — not a summary, not a hint, the actual underlying facts.
2. Give that **identical data** to the LLM in a single `simple_completion()` call (no tools, no code execution, no multi-turn correction) and ask it to compute the same thing Aletheore's scanner computes.
3. Compute the **exact ground truth from that same slice** with a five-line Python `Counter` (see `scripts/exact_ground_truth.py`) — not from Aletheore's full-history output, so the comparison isn't "LLM given less data than the tool." Model and tool see the same facts.
4. Score the model's answer against that exact ground truth, number by number.

Two models tested: `gpt-5.6-luna` (Aletheore's current production model) and `gpt-5.6-terra` (tested earlier in this investigation, not in production — kept here only because it was already mid-run when the decision was made to standardize on Luna; see the parent investigation for why). Both at provider-default reasoning (`extra_body=None`).

Full raw inputs, raw model outputs, and reproduction scripts are in `data/` and `scripts/` in this directory.

## Test 1: Hotspots (which files change the most)

**Input:** raw `git log --name-only` for the most recent 1,500 commits (the full 5,555-commit history is 205K tokens — [it doesn't fit in a single completion at all](#a-structural-limit-not-just-an-accuracy-one), so this is already a concession to the bare-LLM side, not a stress test).

**Task:** count how many commits touched each file path; report the top 10.

| Rank | Exact ground truth | Terra | Luna |
|---:|---|---|---|
| 1 | `CHANGES.rst` — 244 | `CHANGES.rst` — 218 ❌ | *(declined — see below)* |
| 2 | `src/flask/app.py` — 112 | `src/flask/app.py` — 154 ❌ (+38%) | |
| 3 | `requirements/dev.txt` — 94 | `src/flask/helpers.py` — 123 ❌ (wrong rank *and* wrong count — real #6) | |
| 4 | `.pre-commit-config.yaml` — 93 | `requirements/dev.txt` — 117 ❌ | |
| 5 | `.github/workflows/tests.yaml` — 72 | `.pre-commit-config.yaml` — 105 ❌ | |
| 6 | `src/flask/helpers.py` — 69 | `src/flask/blueprints.py` — 96 ❌ (**not in real top 10 at all**) | |
| 7 | `.github/workflows/publish.yaml` — 59 | `src/flask/cli.py` — 94 ❌ (**fabricated**) | |
| 8 | `requirements/tests.txt` — 59 | `src/flask/scaffold.py` — 92 ❌ (**fabricated**) | |
| 9 | `requirements/docs.txt` — 58 | `tests/test_basic.py` — 89 ❌ (**fabricated**) | |
| 10 | `pyproject.toml` — 57 | `pyproject.toml` — 83 ❌ | |

**Terra: 0 of 10 counts correct.** Every single number is wrong, four of the ten entries don't belong in the real top 10 at all, and it presented all of this with no hedge or caveat.

**Luna declined to guess:**

> *"I'm unable to reliably produce exact counts from this extremely large log without programmatically parsing it."*

— and handed back a correct 12-line Python script to compute it exactly (reproduced in `data/model_outputs.md`). This is the right answer to a task an LLM can't reliably do in a forward pass, and it's worth naming as the more trustworthy failure mode of the two: a customer who trusts Terra's table gets confidently wrong numbers; a customer who gets Luna's answer gets nothing wrong, just nothing useful either.

**Aletheore's scanner:** exact, deterministic, every run.

## Test 2: Ownership (who actually owns this code)

**Input:** same 1,500-commit slice, just `author name|email` per commit (52.7KB — far smaller than the hotspots input, so if there's any test bare LLMs should win, it's this one).

**Task:** count commits per unique author, report the top 8.

| Rank | Exact ground truth | Terra | Luna |
|---:|---|---|---|
| 1 | David Lord — 1,063 (70.87%) | 1,065 (+2) | **1,116 (+53, 5% high)** |
| 2 | Grey Li — 65 (4.33%) | 65 ✓ | 77 (+12) |
| 3 | dependabot[bot] — 61 (4.07%) | 61 ✓ | 48 (−13) |
| 4 | pgjones — 47 (3.13%) | 47 ✓ | 44 (−3) |
| 5 | pre-commit-ci[bot] — 38 (2.53%) | 39 (+1) | 38 ✓ |
| 6 | dependabot-preview[bot] — 31 (2.07%) | 31 ✓ | 25 (−6) |
| 7 | Frank Yu — 6 (0.40%) | 6 ✓ | **omitted entirely** |
| 8 (tie) | Adrian Moennich — 6 (0.40%) | 5 ❌, tied with **Maxim G. Ivanov (fabricated — not in real top 8)** | 5 ❌, tied with **Maxim G. Ivanov (fabricated)** |

Both models also rendered several percentages as nonsensical fractions (`"61/15%"`, `"77/15%"`) instead of computing `61/1500`.

**Terra: 4 of 8 exact, mean error 0.4 on the rest, but still fabricates a person into the ranking.** **Luna: 1 of 8 exact, mean error ~14.5, drops a real contributor, fabricates the same phantom person Terra did.** Whatever coincidence produced "Maxim G. Ivanov" in both outputs is itself worth noting — see [Known limitations](#known-limitations).

**Aletheore's scanner:** exact — see `data/ground_truth_ownership_REPO_WIDE_bug_note.json`, and read the caveat on that filename before citing this number; **the per-file `ownership` query is currently broken** (see below), so this comparison uses only the repo-wide aggregate, which is unaffected by that bug.

## Test 3: Dead code (unreachable modules)

**Input:** every one of the 83 `.py` files' own `import`/`from` lines (20.8KB total — the entire file tree's import graph, nothing withheld).

**Task:** which files does nothing else in the repo import?

**Ground truth (Aletheore's scanner):** exactly 2 — `docs/conf.py`, `examples/celery/make_celery.py`. The scanner recognizes 19 legitimate reachability exceptions (`__init__.py`, `__main__.py`, `cli.py`, `wsgi.py`, pytest-discovered test files, etc. — see `entry_points_detected` in `data/ground_truth_deadcode_full.json`) and correctly excludes all of them.

**Terra:** flagged 49 files. Found both real ones — buried inside 47 false positives, nearly every test file in the repo among them. Recall 100%, **precision 4.1%**. To its credit, appended: *"This is only based on the shown static import statements; it does not account for pytest discovery, CLI module-name strings, `python -m`, dynamic imports, or framework-driven loading."*

**Luna:** flagged 48 files (nearly the same list, 2 fewer). Recall 100%, **precision 4.2%**. No caveat.

If a customer got either list as-is, they'd see their own test suite reported as dead code and stop trusting the tool on the first read. That's not a marginal accuracy gap — it's the difference between a usable feature and a discarded one.

## A structural limit, not just an accuracy one

The full 5,555-commit history for hotspots is **205,425 tokens** as raw `git log --name-only` text — [measured directly](scripts/build_inputs.py), not estimated. That's past what fits in one completion for most models before you even get to whether the model can count correctly once it's in there. The 1,500-commit slice used above is already a concession *to* the bare-LLM side. A real production hotspots query over this repo's *actual* full history isn't just something an LLM gets wrong — for the whole history at once, it's something an LLM literally cannot be shown at all in one call. Aletheore's scanner has no such ceiling; it processed all 5,555 commits without anyone needing to decide how much history to leave out.

## Known limitations

- **`aletheore query ownership <target>` ignores `target` entirely.** `src/aletheore/query.py:87-88`'s `find_ownership()` returns `evidence["git"]["ownership"]` unconditionally — confirmed by diffing the query's output for two different files and getting byte-identical results. The underlying data model (`GraphSnapshot.ownership` in `src/aletheore/git_intel/graph_store.py`) has no per-file breakdown at all; only a repo-wide aggregate exists. The CLI's own signature (`ownership <file>`) implies per-file resolution the tool can't currently deliver. This is a real gap in the exact feature this report is arguing for — flagged here rather than worked around, and left unfixed pending a scoped decision on whether to add real per-file ownership (a schema change, not a one-line fix).
- **Sample size is one repository.** Flask's history and import structure are not universal; a monorepo, a repo with heavy dynamic imports, or a much larger codebase could shift these numbers. The qualitative pattern (bare LLM confidently wrong on exhaustive counting; deterministic tool exact) is the load-bearing claim, not the specific percentages.
- **Both models independently fabricated the same nonexistent contributor** ("Maxim G. Ivanov") in the ownership test. Worth investigating whether this is a real person from flask's broader (un-sliced) history that both models pattern-matched into the wrong slice, rather than a pure hallucination — not resolved here.
- **Terra is not the production model.** These results describe Terra alongside Luna because both were run before the decision to standardize on Luna; they're included for completeness of the record, not as an argument for switching models. The main product claim in this report — deterministic analysis vs. any bare LLM — holds for both.

## Reproducing this

```bash
cd benchmarks/deterministic-analysis-benchmark
# From inside a target repo checkout:
python scripts/build_inputs.py /path/to/this/data 1500
# Ground truth (no API calls):
python scripts/exact_ground_truth.py data
# Bare-LLM outputs (real API calls, ~$0.01):
python scripts/run_bare_llm.py data gpt-5.6-luna
```
