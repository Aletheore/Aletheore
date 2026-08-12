# Why AIRview loses the comprehension benchmark

AIRview scores **1.21 / 3** on architecture questions; RepoWise scores **2.54**.
Our own raw code chunks score **1.67** — AIRview is worse than shipping no docs at
all and just retrieving source.

This is not a model-quality problem. DeepSeek generated **both** wikis in this run.
It is three design decisions in `github-app/scan_worker/live_wiki.py`.

## Cause 1 — the prompt caps depth by construction

`SUBSYSTEM_WRITING_SYSTEM_PROMPT` (live_wiki.py:63) asks for:

- `"2-4 sentence overview"` per subsystem
- `"1-2 sentence description"` per file role
- `"one sentence on what it does"` per symbol

AIRview is therefore structurally a **catalog of one-liners**. It cannot produce a
mechanism walkthrough, because nothing in the schema has room for one.

Measured on Flask — documentation prose per file:

| file | AIRview | RepoWise | ratio |
|---|---|---|---|
| `src/flask/app.py` | 1,107 | 14,461 | 13.1x |
| `src/flask/sansio/scaffold.py` | 695 | 11,964 | 17.2x |
| `src/flask/cli.py` | 1,230 | 12,801 | 10.4x |
| `src/flask/helpers.py` | 730 | 10,213 | 14.0x |
| **total, all files** | **32,807** | **409,915** | **12.5x** |

The judge's reasons say exactly this, unprompted, on nearly every loss:
*"mostly a list of files and functions with brief roles"*,
*"a catalog of files and functions with brief descriptions"*.

## Cause 2 — the grounding rule forbids the synthesis the question needs

The same prompt says: *"never cite a file that isn't in this subsystem's file list"*,
and `_sanitize_written_files` (live_wiki.py:117) drops anything outside the brief.

That is what makes AIRview verifiable. It is also what makes it unable to explain.
"How does a request flow from WSGI entry to response?" spans `app.py`, `ctx.py`,
`wrappers.py`, `sansio/app.py`. If those land in different clusters, **no single
page is allowed to describe the path**, and the answer cannot be written at all.

This is a real tension, not a bug: the evidence guarantee and the explanatory power
are in direct conflict as currently specified. Judge on a05: ours 0.5, theirs 3.0 —
*"lacks specific details on the extension lifecycle hooks and how third-party
packages register"* — a purely cross-cutting question.

## Cause 3 — clusters are dependency communities, not importance

The 12 subsystems the scanner produced for Flask:

| files | chars | subsystem |
|---|---|---|
| 29 | 301 | Framework Core **Tests** |
| 18 | 333 | **Framework Core** |
| 9 | 254 | Tutorial Application |
| 8 | 367 | Testing Utilities |
| 5 | 389 | JSON Integration |
| 5 | 315 | Views And Helpers |
| 4 | 335 | Celery Integration |
| 1 | 278 | Documentation Configuration |
| 1 | 327 | Blueprint Apps Package |
| 1 | 258 | CLI Apps Package |
| 1 | 285 | Inner Package |
| 1 | 275 | WSGI Entry |

Three things are wrong here:

1. **The largest subsystem is tests.** 8 of 12 subsystems are tests, examples, or
   config. RepoWise ranks by PageRank and offers `--skip-tests`.
2. **The 18 most important files in Flask share one 333-character description.**
   `app.py`, `ctx.py`, `sessions.py`, `templating.py` and all of `sansio/` are one
   undifferentiated blob — while a single test fixture ("Inner Package") gets its
   own subsystem.
3. **Community detection is the wrong axis for documentation.** It groups by import
   topology; a reader wants grouping by concept. `sansio/` exists for a *design*
   reason the graph cannot see — and a03 ("why is there a sansio package") is one of
   our worst losses, 1.0 vs 3.0.

## What would actually move the number

Ordered by expected effect per unit of work:

1. **Add a file-level page type.** The single biggest lever. RepoWise's win is
   almost entirely its 79 per-file pages. We already have per-file `role` +
   `key_symbols` in the brief — the structure exists, the prompt just refuses to
   elaborate on it. Expect most of the 1.2 → 2.5 gap to close here.
2. **Allow cross-subsystem citation, then verify it.** Relax "never cite outside
   this subsystem" to "cite anything, every citation is verified against evidence".
   `verify_citations` already does the checking; the restriction is redundant
   belt-and-braces that costs the explanatory content. This is the fix that makes
   flow/lifecycle questions answerable at all.
3. **Raise the length caps.** "2-4 sentences" → sectioned prose (Overview,
   Responsibilities, How it works, Gotchas). Cheap to try, immediate effect.
4. **Weight clusters by PageRank and demote tests/examples** so the core gets the
   budget instead of fixtures.
5. **Add a "why" pass.** Nothing in the current prompt asks for design rationale,
   which is precisely what the judge rewarded in RepoWise's pages.

## What not to conclude

- Not a model problem — same model wrote both wikis.
- Not a cost problem — AIRview used *less* LLM spend, and got less back.
- RepoWise's prose is well grounded (0.5% ungrounded identifiers, and most of those
  benign). Do not attack their accuracy; we lose that argument on the evidence.

---

## Is AIRview "efficient"? Tested, and mostly no

The tempting reading: 1.21 vs 2.54 while writing 12.5x less prose sounds efficient.
Two measurements, opposite answers.

### Per character delivered — no, we are worse

The judge received an **equal 12,000-character budget from each system**. The 12.5x
is total corpus size; retrieval only ever delivers a fixed budget. At identical
context size we scored half as well, so per character actually shown we are less
efficient, not more.

Sweeping the budget to test whether terse one-liners pack more signal:

| budget | AIRview | RepoWise | gap |
|---|---|---|---|
| 12,000 | 1.21 | 2.54 | 1.33 |
| 3,000 | 1.21 | 2.25 | 1.04 |
| 1,000 | 1.00 | 1.58 | 0.58 |

The gap narrows as the budget tightens — RepoWise's long pages suffer more from
truncation — but **there is no crossover. AIRview does not lead at any budget**,
and preference stays lopsided (17-6 even at 1,000 chars). AIRview also degrades at
1,000, so it is not genuinely budget-insensitive.

### Per dollar — yes, genuinely

| | tokens | calls | cost |
|---|---|---|---|
| RepoWise | 807,636 | 114 | $0.1751 |
| AIRview | 54,056 | 13 | ~$0.0117 (blended-rate estimate) |

14.9x fewer tokens, 8.8x fewer calls. Quality per dollar: **AIRview 103 vs
RepoWise 14.5 — 7.1x better**.

### Why this is not the win it looks like

1. **The efficiency and the quality gap are the same design decision.** AIRview is
   cheap *because* the prompt caps it at one sentence per symbol. That is the
   identical fact as Cause 1 above. You cannot bank the efficiency as a virtue and
   also plan to fix the quality — fixing it spends exactly that efficiency.
2. **$0.18 vs $0.012 per repo is a margin story, not a customer story.** On a
   $29.99/mo plan neither number is visible to the user. A developer asking "how
   does request handling work" gets a worse answer from us and does not care that
   it was cheaper to produce.
3. **Do not publish "7x more efficient" as a quality claim.** It is true and
   defensible as a unit-economics statement. Used as a proxy for docs quality it is
   the kind of overstatement this whole benchmark exists to avoid.

---

# Fixes implemented, and what they moved

All five recommendations shipped. Re-measured with the identical harness, judge,
rubric and 12,000-char budget.

| | AIRview v1 | **AIRview v2** | RepoWise |
|---|---|---|---|
| architecture score (0-3) | 1.21 | **1.96** | 2.42 |
| gap to RepoWise | 1.33 | **0.46** | — |
| total prose | 50,686 ch | 113,011 ch | 409,915 ch |
| tokens | 54,056 | 108,706 | 807,636 |
| LLM calls | 13 | 39 | 114 |
| cost | ~$0.012 | ~$0.024 | $0.175 |

**65% of the gap closed for 2x the tokens.** v2 now also beats our own raw code
chunks (1.67), which v1 did not — the docs finally earn their place over just
retrieving source. Still behind RepoWise, and judge preference is still 17-7
against us; this narrowed the gap, it did not close it.

## What changed

1. **File-level pages** (`generate_file_pages`, `build_file_page_record`).
   Sectioned markdown — Overview / Why it exists / How it works / Key symbols /
   Gotchas — for the top files by importance. 22 planned on Flask, **18 kept: 4 were
   rejected by citation verification**, which had rejected nothing in v1. Pages hang
   off the existing `files` entry as `detail`, so there is no migration and no new
   table.
2. **Cross-subsystem citation allowed, still verified.** The prose may now cite any
   file in the repo; `verify_citations` already checked repo-wide, so the old
   "never cite outside this subsystem" rule was blocking explanation while adding no
   safety. The `files`/`key_symbols` arrays stay brief-restricted — those are
   structural.
3. **Length caps raised and a rationale pass added.** Subsystem descriptions 2-4 →
   4-8 sentences with a required "why it exists as a separate unit" sentence; file
   roles 1-2 → 2-3 sentences; symbol explanations 1 → 1-2.
4. **Importance ranking** (`rank_files_by_importance`): in-degree + churn + symbol
   count, with tests/examples/docs demoted 0.15x.

## Two bugs found while implementing

- **Ranking on in-degree alone buried the most important files.** Entry points sit
  at the *top* of the import tree, so little imports them: Flask's `app.py` ranked
  15th, below `typing.py`. Fixed by adding a symbol-count term.
- **Hotspot churn was always read as zero** — the scanner emits `churn_count`, the
  ranking read `commits`. (On this corpus churn is degenerate anyway: 30 entries all
  at `churn_count: 1`, from limited history.)
- **The AIRview cache had no prompt version**, so its key depended only on the scan.
  Editing any prompt would have silently served pages written by the old prompt
  forever. `build_evidence_packet` now carries `prompt_version`, and
  `AIRVIEW_PROMPT_VERSION` is bumped to "2".

## Where the remaining 0.46 is

The judge still prefers RepoWise on breadth: they write a page for all 79 files, we
write 18. The cheapest next lever is raising `DEFAULT_MAX_FILE_PAGES` / lowering
`FILE_PAGE_SCORE_FLOOR` — this is now a dial, not a rewrite. Whether that is worth
the tokens is a product call, not a technical one.

## Is "more pages" the next lever? Measured: no

`max_files` was **a dial connected to nothing**. `FILE_PAGE_SCORE_FLOOR` was
anchored to the top score, and Flask's `src/flask/__init__.py` scores 79 - 2.7x the
runner-up, because everything imports the re-export hub. That set the floor at 3.16
and selected the same 22 files whether `max_files` was 22, 40, or 83. Fixed by
anchoring the floor to the median non-demoted score; the budget is now the control.

But raising it buys almost nothing here. Below the cut, exactly one substantive file
sits above the tests: `src/flask/blueprints.py` (2.23). Everything from rank 24 down
is `tests/*` and `examples/*`.

**The 79-vs-18 framing is misleading.** RepoWise's 79 file pages include ~40 test,
example and config files. We document 22 of Flask's ~24 substantive source files.
Coverage is already near-complete; the remaining gap is depth and two blind spots.

## The actual next levers, in value order

1. **Declaration-only files are invisible to us.** The scanner extracts functions and
   classes; `src/flask/signals.py` has neither. It is 17 lines of module-level
   `x = _signals.signal(...)` assignments — and it exports **10 public names, Flask's
   entire signals API**. Consequences, both measured here: it gets no wiki page (the
   no-symbols guard skips it), and it was **our only miss in the location benchmark**
   (q32 — nothing for an embedder to grip). Same root cause, both halves. Extending
   symbol extraction to module-level assignments fixes both, and generalises to
   settings modules, registries, enums and route tables.
2. **Citation rejection is all-or-nothing.** `debughelpers.py` (7 functions, 4
   classes) produced a page twice, cited something unverifiable both times, and was
   discarded entirely. Subsystems already degrade gracefully — `SUBSYSTEM_DESCRIPTION_UNAVAILABLE`
   keeps the verified file list and drops only the prose. File pages should do the
   same: strip the offending sentence, keep the verified sections.
3. **Depth per page**: 3,454 chars vs RepoWise's ~5,188. The 250-400 word target in
   `FILE_PAGE_WRITING_SYSTEM_PROMPT` is a one-line change.
4. **The frontend does not render `detail`.** The pages exist in stored data and are
   invisible to users. No benchmark impact, maximum product impact.

---

# Declaration-only fix: shipped, half-worked

Module-level bindings are now extracted (`_extract_python` returns a fifth
`constants` list; `symbols.constants` is on every module, empty for languages
whose extractor does not yet populate it). Consumers updated: search index,
wiki_mapping briefs, live_wiki file pages, evidence packet, evidence resolution,
`find_symbol_source`. On Flask: **134 module-level constants recorded**, and
`signals.py` went from 0 symbols to 11.

## Wiki side: fixed

`src/flask/signals.py` now earns a file page with all **10 public signals** as key
symbols. It was previously skipped entirely by the no-symbols guard, leaving Flask's
whole signals API undocumented. This was the goal and it works.

## Retrieval side: NOT fixed, and it cost a point

| | top-1 | top-3 | top-5 | q32 |
|---|---|---|---|---|
| before | **75.0%** | 90.6% | 96.9% | miss |
| after (chunk per constant) | 71.9% | 90.6% | 96.9% | miss |
| after (one grouped chunk) | 71.9% | 90.6% | 96.9% | miss |

Two attempts, neither fixed the target question.

**Attempt 1 - a chunk per constant.** `signals.py` went from 1 chunk to 12, but each
was a ~60-character line (`template_rendered = _signals.signal("template-rendered")`)
carrying almost no meaning, and eleven thin chunks diluted the file's representation.
q32 still missed; q15 regressed (`testing.py` lost top-1 to `app.py`).

**Attempt 2 - one grouped declarations chunk per file.** Recovered q15, and lifted
`signals.py` from unranked to **rank 12** on q32 — real movement, still not top-5.
But the grouped chunk now *over*-matches: it contains `appcontext_pushed`,
`appcontext_popped`, `request_started`, which beat `ctx.py` on q06, *"the object that
holds per-request state pushed onto and popped off a stack"*. One regression traded
for another.

## Stopping here deliberately

Two rounds of retrieval tuning against 32 questions we wrote ourselves is already at
the edge of fitting noise rather than improving retrieval. A third round that moves
top-1 back to 75% would not be evidence of anything - the honest move is a larger,
independently-authored question set before tuning further.

The scanner change stays regardless: a file exporting ten public names is not an
empty module, and every consumer of the evidence was being told that it was. That is
a correctness fix whose value does not depend on this benchmark. But it did not buy
the retrieval win it was supposed to, and the top-1 number went **down**.

## Wiki-to-wiki, after the declaration fix

The constants change was aimed at the wiki, and measured there it delivers.

| | score | gap | tokens | calls | cost |
|---|---|---|---|---|---|
| AIRview v1 (baseline) | 1.21 | 1.21 | 54,056 | 13 | ~$0.012 |
| AIRview v2 (file pages, caps, cross-citation) | 1.96 | 0.46 | 108,706 | 39 | ~$0.024 |
| **AIRview v3 (+ module-level declarations)** | **2.17** | **0.25** | 113,775 | 38 | ~$0.025 |
| RepoWise | 2.42 | — | 807,636 | 114 | $0.175 |

**81% of the original gap closed, at 7.1x fewer tokens and 7.1x lower cost.**
Judge preference moved from 4-20 against us to 8-15.

The declaration fix contributed **+0.21** on its own, for +5,000 tokens. Verified
pages rose from 18/22 to **21/22**, and `src/flask/signals.py` now carries a
3,363-character page opening "These signals allow extensions and applications to
hook into framework events" - which is close to verbatim what the retrieval question
q32 asked for, and which did not exist in any form before this change.

So the same fix that was net-negative for retrieval (-3.1 points of top-1) is
clearly positive for the wiki (+0.21). Reported separately rather than netted,
because they are different products and the trade runs in opposite directions.

## Scope of this PR

The scanner now records module-level bindings and every wiki consumer uses them.
The **search-index chunking** change is deliberately **not** included: it measured
net-negative (top-1 75.0% → 71.9%, and it did not fix the question it targeted), and
the wiki result above does not depend on it. `build_chunks` simply ignores the new
`symbols.constants` key, so retrieval behaviour is byte-identical to master.

That change is worth revisiting only against a larger, independently-authored
question set - tuning it further against these 32 self-authored questions would be
fitting noise.
