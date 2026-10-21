---
name: code-review
description: Review code for actionable defects. Correctness is the core; performance and security are optional sub-cases of the same engine. Anchors on agreements between participants across a boundary, forces a violating execution, and refutes every candidate before reporting. Use when asked to review changes, find bugs, audit a codebase, or check whether a fix is safe.
skill_id: code-review/code-review-phuryn
title: Code Review
source_url: https://github.com/phuryn/pm-skills/tree/07b34f64ee828df08953f505b7f747aef85ae7c6/pm-ai-shipping/skills/code-review
license: MIT
shelf: quality
aletheore: {"id": "code-review/code-review-phuryn", "shelf": "quality", "category": "code-review", "origin": "https://github.com/phuryn/pm-skills/tree/07b34f64ee828df08953f505b7f747aef85ae7c6/pm-ai-shipping/skills/code-review", "license": "MIT", "license_source": "LICENSE", "upstream_commit": "07b34f64ee828df08953f505b7f747aef85ae7c6", "quality": 3, "curation_note": "defect-focused review; adapt: slash refs", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": ["skill-ref", "slash"], "companion_files": 3, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "mentions_agents": ["Claude Code"], "mentions": ["claude", "agents"], "adapted": {"slash-skill": 1}, "status": "ready"}
---

# Code Review

## Purpose

Most review output is noise: a list of things that *look* wrong, unranked, unrefuted, and impossible
to act on. This skill produces the opposite — a small number of findings, each with a required
behaviour, a feasible trigger, a concrete contradiction, an observable consequence, and the strongest
counterargument already checked.

Its central bet: **the defects reviewers miss are rarely visible inside one file.** They are
disagreements between two participants that each look reasonable alone — a caller and a callee, a
producer and a consumer, a writer and a later reader, two branches that should establish the same
state. A checklist applied file-by-file cannot see those, because the two halves are never in view at
the same time. So the unit of review here is the **agreement**, not the file.

## Structure: one engine, three anchors

Code review is the skill. **Correctness is its core** — the dimension generic tooling covers worst,
and the one described in full below. **Performance and security are sub-cases**: the same engine, the
same refutation discipline, the same report contract, with a different anchor and one or two extra
rules each.

| Sub-case | Anchor | Where its rules live |
|---|---|---|
| **Correctness** *(core, default)* | Agreements between participants across a boundary | This file + `references/correctness-taxonomy.md` |
| **Performance** | Workload → resource demand → growth or contention → consequence | `references/performance-review.md` |
| **Security** | Source → trust boundary → sink, with an attacker controlling the source | `references/security-review.md` |

Read a sub-case's file only when that sub-case is selected. Each is short on purpose: it states what
*differs*, and the rest of this file still applies.

Sub-cases are independently *activated*, not mutually exclusive. One root cause can carry correctness
and security impact — report it once, with both impacts.

## Invocation

```
/pm-ai-shipping:code-review
/pm-ai-shipping:code-review dimensions=correctness scope=changes
/pm-ai-shipping:code-review dimensions=performance,security
/pm-ai-shipping:code-review dimensions=all
```

Claude Code ships its own bundled `code-review`. Use the plugin-qualified form above when you mean
this one.

These are instruction arguments, not shell flags.

- **Default: `correctness`.** Bare "review this" or "find bugs" means correctness only.
- An explicit list selects exactly those sub-cases; `all` selects three. Never silently reinterpret
  an unknown or empty selection — ask.
- **Scope:** use what was asked. Otherwise review working changes if present, else the repository.
- **State the selected dimensions, the scope and the comparison baseline before investigating.**
- Reviewing changes means following dependencies *beyond* the changed lines, and distinguishing
  defects the change **introduced** from defects it merely **revealed**.
- Review and report. Apply fixes only when asked.

## Shared engine

Every sub-case uses one skeleton. Only the anchor and the refutation rules differ.

**Map a flow → identify an obligation → inspect every participant → construct a violating execution
→ trace the consequence → attempt refutation → report.**

Build one minimal map first: inputs, major execution flows, who owns which state, external
dependencies, observable effects. Each selected sub-case enriches it — do not build three maps, and
do not make a security-only run wait on correctness mapping.

## Correctness: the agreement engine

A *boundary* is semantic, not a file split. It separates a caller and a callee, two callbacks, two
executions of the same function, a producer and a consumer, or a value written now and read later.

For each consequential agreement, hold these in working notes — not in the report:

```
Participants:
Value, entity or effect exchanged:
Authority (who decides the real answer):
Identity and lifetime/version:
Required relationship:
Evidence for that relationship:
Relevant transitions or orderings:
Observable consumer or consequence:
```

**Establish the obligation without inventing intent.** Evidence comes from specifications,
documented contracts, language or protocol semantics, tests that encode an expectation, or a
necessary producer/consumer relationship. A consumer's implementation alone does not prove the
consumer is right. Where participants disagree, say why the disagreement produces a *wrong outcome* —
sometimes the contradiction is certain while which side should change is genuinely open. Missing
documentation is a limitation, not automatically a finding.

**Start where agreements are most likely to break:** values transformed or negotiated, identities
reassigned, work becoming asynchronous, state persisted and reloaded, several effects that must
agree. Then do a local pass over ordinary decisions, arithmetic, boundaries and error branches — the
anchor must not become a filter that discards plain bugs.

### Force a violating execution

A suspicion is not a finding until you construct the execution that breaks it. Where the
implementation permits:

- make a **requested** value differ from the **accepted or effective** one;
- keep two operations live at once and vary their completion order;
- change the relevant identity or generation between observation and use;
- compare distinct transitions that should end in equivalent state;
- inject failure between effects, and interruption before completion;
- exercise empty, exact-boundary and adjacent-boundary inputs.

Establish that each case is actually reachable. Do not assume it.

### Two lenses that need a forced probe, not a mention

Across a large evaluation of planted runtime defects in real codebases, two classes were almost never
*even reported* by strong agents — not missed at the fix, missed at the look. Naming them in a
checklist will not help; each needs an explicit probe:

1. **Authority reconciliation.** Follow a proposed value through validation, normalisation,
   negotiation or commit, and find downstream state still derived from the **proposal** where the
   authority can return something different. *A requested value is not an applied value.* Probe:
   force them apart and ask what still reads the request.
2. **Identity and correlation.** Trace how an operation's result finds its originating entity, then
   establish that the key is unique, stable and live for long enough — under overlap, reordering,
   removal and reuse. A label, a position or arrival order is suspicious exactly when those
   properties can fail. Probe: run two operations concurrently and complete them out of order.

The full set of thirteen diagnostic lenses, each with a detection tell, is in
`references/correctness-taxonomy.md`. They are overlapping lenses, not a quota to fill.

## Refutation: the discipline that makes this worth running

A candidate becomes a finding only with all five:

1. **A supported obligation** — what must hold, and on what evidence.
2. **A feasible execution** — inputs, state and ordering the real system permits.
3. **A concrete contradiction** — where the obligation fails.
4. **An observable consequence** — wrong output, state, effect, completion or progress.
5. **An examined counterargument** — the strongest mechanism that would prevent or repair it.

Actively hunt for the refutation: an enclosing guarantee that makes the execution impossible;
synchronisation excluding the interleaving; reconciliation before any consequential read; an
intentional contract; a precondition excluding the input; a different owner responsible for it.

| Outcome | Rule |
|---|---|
| **Keep** | Evidence establishes the defect; the counterargument checked does not prevent it. |
| **Drop** | Cited evidence defeats the execution, the obligation or the consequence. |
| **Unresolved** | An essential contract or runtime fact is unknown. List it *separately from findings*. |

Do not import the security sub-case's attacker/victim test into correctness. **A correctness defect
can harm only the person who triggered it and still be serious.** Equally, "keep unless disproved" is
too permissive here — an ungrounded suspicion with no constructed execution is not a finding. When
both sub-cases are active, apply each test only to its own dimension.

**Absorption is not prevention.** The most expensive refutation mistake is finding something
downstream that happens to hide the defect - a cache that usually holds the value, a retry that
usually succeeds, a default that is usually right - and dropping the finding. That is not a
guarantee, it is a coincidence with good odds, and it fails the day the absorber is cold, evicted or
reconfigured. Drop only on a mechanism that makes the execution *impossible*, and say which mechanism
it was. For the same reason, **"it works nearly always" describes a race, not a refutation** - a
timing window that usually resolves correctly is a finding, and the fact that you had to reason about
which side usually wins is the evidence.

Passing tests, unfamiliar code, a suspicious name, a missing test and a sibling difference are
evidence to investigate — none of them is proof, and none is refutation. Deduplicate by violated
agreement and root cause, never by file. There is no findings quota; zero supported findings is a
valid result.

## Parallelism

Fan out over **complete flows or connected groups of agreements** — never over files, and never one
agent per taxonomy class. Partitioning by file is precisely the split that hides cross-boundary
defects, which are the ones worth finding.

1. The coordinator builds the initial map and identifies shared state.
2. Each worker gets a bounded flow, its participants, the selected sub-cases and open questions.
3. Workers inspect **both sides** of their agreements and may follow dependencies outside their list.
4. Workers return candidates, cited evidence, completed refutations and unresolved relationships.
5. The coordinator reconciles assumptions and any relationship that crosses assignments.
6. Strong candidates get a separate verification pass before they are reported.

**Allow overlapping reads.** Two workers reading the same authority is far cheaper than either one
holding half its contract. Keep integration capacity in reserve: an unresolved relationship spanning
two assignments stays unexamined until someone closes it. One level of fan-out is the target; if
delegation is unavailable or the scope is small, run the same procedure sequentially.

**Run workers on the strongest model available, and match the current session's effort level.** This
is recall-first work: a missed cross-boundary flow is the costly failure, and a worker that silently
drops to a cheaper model or a lower effort is the cheapest way to lose one. If any worker is rerouted
or downgraded, say which in the report — a reader who assumes one model saw everything will
misjudge the coverage.

**One model. Name it on every worker.** Fan-out here buys coverage, not a second opinion. Pass the
coordinator's own model explicitly on each spawn — "inherit" is not a routing decision, and a worker
that quietly lands on a cheaper model is the easiest way to lose a finding. **Do not bring in a
different model**, to review or to cross-check, unless you are explicitly asked: mixing models makes
the result unattributable, and when this skill is being measured or compared across models, one
foreign worker invalidates the number. The independent second-model pass is a separate,
explicitly-invoked step (`/ship-check` Step 6), never something this skill reaches for on its own.

**Tell workers they are reading, not editing.** A review worker needs to read, search and navigate;
it must not modify the tree. State that in the worker's instructions — a worker that starts editing
drifts from reviewing into "helpfully" fixing and stops reporting what it silently repaired, and the
findings can no longer be checked against the code they describe. Say it in the prompt rather than
assuming the host will enforce it, and confirm the tree is unchanged when the run ends.

## Report

Lead with supported findings, ordered by impact. Keep severity separate from evidential strength.

```
Review scope:
Comparison baseline:
Selected dimensions:

[Severity] [Dimension] Concrete consequence
  Expectation:  required behaviour, and the evidence for it
  Trigger:      feasible preconditions and execution
  Defect:       the violated relationship
  Evidence:     source locations for EVERY participant
  Impact:       observable consequence and affected scope
  Refutation:   strongest counterargument checked, and why it fails
  Remedy:       minimal correction to the violated relationship
  Verification: what was executed, versus established from source

Coverage:
Unexamined areas and essential unknowns:
```

Cite **both** participants for a cross-boundary defect, and do not group findings only by file — that
hides the relationship the review exists to find.

**Coverage means work performed, not boxes ticked.** For each selected sub-case report: examined with
supported findings · examined, none supported · not applicable, with reason · not examined, with
reason. Zero findings in a category does **not** mean "not covered", and a table of ticks is not
evidence of completeness. Say "no supported findings in the examined scope" — never that the code is
bug-free.

## Notes

- Say explicitly what is well built. A review that only accuses is easy to dismiss.
- The two sub-cases have mature commands behind them: `/security-audit-static` (trust boundaries,
  sinks, OWASP backstop) and `/performance-audit-static` (over-fetching, indexes, caching). Run the
  command when the sub-case is the whole job; use the reference file when it is one dimension of a
  broader review. This skill does not restate either.
- For the doc-vs-code axis use the `intended-vs-implemented` skill.
- A static review produces code-review findings, not confirmed exploits or measured regressions.
