# Retrieval benchmark — Aletheore vs RepoWise

Measured on Flask, two task types, run 2026-08-12.

**Headline: we win at locating code. We lose at explaining it.**
Both halves are reported with equal prominence, as is the one speed metric we lose.

| Task | Aletheore | RepoWise |
|---|---|---|
| Locate the implementing file (top-1 / 3 / 5) | **75.0% / 90.6% / 96.9%** | 28.1% / 56.2% / 56.2% |
| Explain architecture (blind judge, 0–3) | 1.21 – 1.67 | **2.08 – 2.67** |
| Query latency, in-process | 125 ms | **68 ms** |
| Index build | **74 s, $0.00** | ~7 min, $0.18 |

## Reproduce

See **REPRODUCIBILITY.md** — it lists every tool version, exactly which numbers
reproduce bit-for-bit (all retrieval and scoring) and which do not (anything
LLM-judged), and how to run it.

Short version:

```bash
git clone https://github.com/pallets/flask /tmp/bench-flask
git -C /tmp/bench-flask checkout 2a8a38b051fc248865730bf3511bf2e2ea325e81

python3 scripts/verify_ground_truth.py            # must print 32/32
cd /tmp/bench-flask && aletheore scan . && aletheore index .
cd - && python3 scripts/run_aletheore.py
python3 scripts/score.py results/results_aletheore.json=ALETHEORE
```

Every published number can be recomputed from `results/*.json` with no API key
and no network. The RepoWise side needs an LLM key and `REPOWISE_EMBEDDER=ollama`
— without it, `repowise search --mode semantic` silently degrades to full-text.

## Layout

| path | what |
|---|---|
| `questions/location.json` | 32 "which file implements X" questions + verified ground truth |
| `questions/architecture.json` | 12 "explain how X works" questions |
| `scripts/verify_ground_truth.py` | asserts every ground-truth anchor exists — run before anything |
| `scripts/run_aletheore.py`, `run_repowise.py` | retrieval runners, emit ranked file lists + latency |
| `scripts/score.py`, `score2.py` | top-k scoring; `score2` also drops non-file pages |
| `scripts/run_arch.py`, `judge_arch.py` | architecture arm: context capture + blind order-swapped judge |
| `scripts/build_airview.py` | builds AIRview for an arbitrary repo from its `air.json` |
| `results/*.json` | raw per-query outputs — recompute any number yourself |

## Scope limits

- One repository (Flask), one language (Python), 44 questions total.
- **Questions authored by us.** Mitigated by sourcing from Flask's public API/docs
  and mechanically verifying every anchor, but it remains the weakest link.
- The architecture arm depends on an LLM judge. See METHODOLOGY.md for the
  confounds and how they were controlled.

See `METHODOLOGY.md` for full method, every adjustment made in RepoWise's favour,
and the errors we caught in our own first run.
