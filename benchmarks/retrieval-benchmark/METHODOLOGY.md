# Retrieval benchmark — Aletheore indexed search vs RepoWise semantic search

**Corpus:** Flask @ `2a8a38b051fc248865730bf3511bf2e2ea325e81` (83 Python files)
**Questions:** 32, natural-language, authored from Flask's public API/docs.
All 32 ground-truth anchors programmatically verified present in the claimed file.
**Scoring unit:** source file. A hit = the correct implementing file appears in top-k.
**Embedder held constant:** Ollama `nomic-embed-text` (768-dim) for both tools.

## Accuracy

| | top-1 | top-3 | top-5 |
|---|---|---|---|
| **Aletheore** (hybrid RRF over code chunks) | **75.0%** (24/32) | **90.6%** (29/32) | **96.9%** (31/32) |
| RepoWise `--mode semantic` | 28.1% (9/32) | 56.2% (18/32) | 56.2% (18/32) |
| RepoWise `--mode fulltext` | 21.9% (7/32) | 56.2% (18/32) | 65.6% (21/32) |
| RepoWise *oracle* (best of both modes per question) | 40.6% | 71.9% | 78.1% |

The oracle row is not achievable in practice — it retroactively picks whichever
mode happened to win each question. Included as a generous upper bound.

## Speed

Measured two ways, because the first way was misleading.

| | in-process (library) | via CLI (user-experienced) |
|---|---|---|
| Aletheore | 125 ms mean / 115 median / 184 p95 | 2853 ms mean |
| RepoWise | **68 ms** mean / 67 median / 80 p95 | 3025 ms mean |

**RepoWise's retrieval is ~1.8x faster than ours in-process.** Via CLI the two are
within ~6% of each other; most of that number is Python interpreter + import
startup (~709 ms floor for RepoWise's CLI), not retrieval.

## Setup cost (one-time)

| | time | $ |
|---|---|---|
| Aletheore (`scan` + `index`) | 74 s (22 s + 52 s) | **$0.00** (local embeddings, no LLM) |
| RepoWise (`init --coverage 1.0` + `reindex`) | ~6 min generation + 43 s embed | **$0.1751** (114 calls, 364K in / 443K out, `deepseek-v4-flash`) |

Index artifacts: Aletheore 511 code chunks / 2.0 MB. RepoWise 110 wiki pages +
88 decision records = 198 vectors.

## Fairness adjustments made (all favour RepoWise)

1. **Coverage raised to 100%.** RepoWise defaults to `--coverage 0.20` — 16 file
   pages for 83 Python files, a hard ~20% retrieval ceiling. Overridden to
   `--coverage 1.0` (79 file pages) so it is not judged on a default it did not choose.
2. **Non-file pages discounted.** Structural pages (`layer:*`, `scc-*`,
   `onboarding/*`) cannot match a source file by construction and consume rank
   slots. Re-scored with them dropped: top-1 28.1% (unchanged), top-5 56.2% → 59.4%.
   Not the cause of the gap.
3. **Both modes reported**, plus the unachievable best-of-both oracle.
4. **Real LLM, not a local model.** First attempt used local `qwen2.5-coder:14b`;
   discarded because it does not represent the shipped product.

## Bug found in RepoWise (affects its own users, not just this benchmark)

`repowise search --mode semantic` **silently degrades to full-text search.**

In `repowise/cli/commands/search_cmd.py:148-176`, `_search_semantic` wraps the
LanceDB path in `except Exception: pass` and falls through to FTS — while still
printing the header `Semantic search: '<query>'`. The user has no signal.

Trigger: `search` has no `--embedder` flag (unlike `reindex`), so it calls
`_resolve_embedder(None)`. With no cloud embedding key set this resolves to
`mock` → 8-dim `MockEmbedder` → LanceDB raises
`Invalid input, No vector column found to match with the query vector dimension: 8`
→ swallowed → FTS.

This invalidated our first "semantic" run: it was byte-identical to the fulltext
run on all 32/32 questions. Fixed by setting `REPOWISE_EMBEDDER=ollama`.
After the fix, 0/32 results matched the FTS run.

## Honest reading

- Aletheore is decisively better at **"which file implements X"** — the gap holds
  even against RepoWise's unachievable oracle, and survives every adjustment made
  in RepoWise's favour.
- Aletheore is **cheaper and faster to set up** ($0 / 74 s vs $0.18 / ~7 min).
- **RepoWise's retrieval is genuinely faster per query** (68 ms vs 125 ms in-process).
  We should not claim a speed win.
- The tools optimise for different targets: we index code chunks and return
  `file:line`; RepoWise indexes generated prose and returns wiki pages. These
  questions ask "where is this implemented", which is our target. A question set
  asking "explain the architecture" would likely invert the result — that is not
  measured here and should not be inferred either way.

## Aletheore's one miss (q32)

"Where are the notification hooks that extensions can subscribe to declared?"
Expected `src/flask/signals.py`; we return `examples/celery/make_celery.py` first.
`signals.py` is 17 lines of bare `Signal()` assignments with almost no prose —
little for an embedder to grip. RepoWise's semantic mode also missed it; its
fulltext mode hit it. A genuine weakness of chunk-based retrieval on
declaration-only files.

---

# Part 2 — Architecture / comprehension questions (12 questions)

Different task: "explain how X works", not "which file implements X". File-level
scoring is the wrong instrument, so this half uses a **blind LLM judge** on the
retrieved material, 0-3 scale, each question graded twice with the two systems'
positions swapped to cancel position bias. Tool names scrubbed from all material.
Equal ~12,000-character context budget per system.

| Arm (our side) | our score | RepoWise | judge preference |
|---|---|---|---|
| Raw code chunks (CLI product) | 1.67 | **2.08** | 8 vs 14 (2 tie) |
| AIRview, full payload, retrieved | 1.21 | **2.54** | 4 vs 20 (0 tie) |
| AIRview + code chunks | 1.50 | **2.46** | 7 vs 17 (0 tie) |
| ~~AIRview prose only~~ (measurement error, see below) | ~~0.46~~ | ~~2.67~~ | — |

**Correction.** The first AIRview arm scored 0.46 because the harness fed the judge
only subsystem descriptions plus bare file paths — 4,443 of AIRview's 50,686
characters, 8.8% of it. AIRview also carries a `role` per file and an
`explanation` per key symbol with line numbers (83 files, 479 symbols documented).
Re-run with the full payload, retrieved per question into the same budget, the
score is **1.21**. The conclusion is unchanged but the original number was wrong.

**RepoWise wins this half, in every arm.** This is the mirror image of Part 1 and
should be reported with equal prominence.

Why: AIRview is a *breadth map* — 12 subsystem summaries (~309 chars each) plus a
726-char overview, 4,443 characters of prose total. RepoWise generates 110 detailed
per-file pages. For "explain the architecture of this module", their depth wins.
AIRview alone scores worse than our raw code chunks because it is too thin to
answer a detailed question.

Judge confound: the Part-1-arm judge was DeepSeek, which also generated RepoWise's
wiki (ours is raw source, no LLM) — a possible self-preference bias pointing at the
result that won. In the AIRview arms both sides are DeepSeek-generated prose, which
largely cancels it; RepoWise still wins. An independent local judge run is pending.

## Citation grounding (the "evidence-backed" claim, measured)

| | result |
|---|---|
| RepoWise `file:line` citations across all 110 pages | **0** |
| RepoWise backticked identifiers claimed | 2,136 |
| ...not found anywhere in the repo | **10 (0.5%)** |
| AIRview subsystems rejected by citation verification | 0 of 12 |

**Do not claim RepoWise hallucinates.** At 0.5% ungrounded — and most of those are
benign (`Makefile`, `conftest`, `Expires`, `TIMESTAMP` are real things, just not
Python symbols) — their prose is well grounded. Genuine errors found: they document
`before_first_request` and `has_blinker`, both removed from modern Flask (staleness,
not fabrication).

The real, defensible difference is **verifiability, not accuracy**: RepoWise emits
no line-level citations, so its claims cannot be mechanically checked or clicked
through to source. AIRview runs `verify_citations` and *discards an entire page*
whose `file:line` references don't resolve against scanner evidence
(`live_wiki.py:157-166`). That is a process guarantee, not a measured quality win —
and on this corpus it rejected nothing, so it cost nothing and proved nothing.

## Independent judge cross-check (confound resolved)

The architecture judge was DeepSeek, which also generated RepoWise's wiki. Ours is
raw source with no LLM, so any self-preference bias pointed at the side that won.
Re-judged the raw-code-chunks arm with `llama3.1:8b` locally (`num_ctx=16384` — the
4096 default would have silently truncated ~7,000-token prompts and graded partial
material).

| judge | Aletheore | RepoWise | gap | ties |
|---|---|---|---|---|
| deepseek-chat | 1.67 | 2.08 | 0.41 | 2/24 |
| llama3.1:8b (independent) | 2.38 | 2.58 | 0.20 | 16/24 |

**Direction holds: both judges favour RepoWise.** The self-preference confound does
not explain the result.

**Magnitude does not hold.** The independent judge scores everything higher and ties
16 of 24 pairings — behaviour typical of a small model compressing toward the middle
rather than genuinely finding the systems equivalent. Treat it as a directional
check, not a magnitude estimate, and do not quote either gap as precise.

Caveat: this cross-check covers only the raw-code-chunks arm. The AIRview arms were
judged by DeepSeek alone — though in those arms both sides are DeepSeek-generated
prose, which largely neutralises the same confound.
