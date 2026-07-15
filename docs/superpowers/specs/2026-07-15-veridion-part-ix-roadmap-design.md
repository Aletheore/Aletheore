# Veridion Part IX (Roadmap Generation) Design

**Status:** Draft, pending review
**Date:** 2026-07-15

## Problem

Parts I-V plus the scan/query subcommands are done and validated across four rounds of real
dogfooding. The remaining original 10-part vision (VI-X) is largely the highest-risk territory
this project has been disciplined about avoiding — most of it isn't repo-derivable (business
metrics, startup DD) or requires real semantic understanding no deterministic scanner can
ground (AI/prompt-injection review). Part X (40 numeric scorecards) was explicitly ruled out:
it requires subjective weighting a scanner can't ground, and risks exactly the
confident-but-unfounded output this whole project exists to prevent — the same failure mode
Veridion's own constitution warns against for governance ("legitimacy must not be reduced to
one numeric score").

Part IX (roadmap generation) is different: it needs **zero new evidence**. It's a synthesis
layer — take what Repository Intelligence, Git Intelligence, Architecture Review, and Security
already reported in the *same audit run*, and prioritize it into a roadmap. No new scanner
code, no new `evidence.json` schema. This is the lowest-risk, cheapest addition available from
the original vision, provided it stays disciplined about not inventing new claims.

## Goals

- Add a **Roadmap** section to the audit report, appearing after Security and before Evidence
  Gaps in the output contract.
- Every roadmap item must trace to a specific finding already reported elsewhere in the *same*
  report — this is prioritization and synthesis of existing findings, not a new source of
  claims about the repository.
- Three priority tiers (Immediate / Near-term / Longer-term), not numeric scores or day-count
  estimates — matching the discipline that ruled out Part X's scorecards.
- Systematic coverage: every prior section must be considered for roadmap-worthy findings, not
  just the 2-3 most dramatic ones. If a section has nothing to contribute, that's stated
  explicitly.

## Non-Goals

- No ROI, difficulty, dependency-graph, or risk *scores* per item — these aren't
  evidence-derivable and would reintroduce the false-precision problem Part X was cut for.
- No specific day-count timeframes (30/60/90/180/365) — implies an estimation precision
  evidence can't support. Three qualitative tiers instead.
- No new `evidence.json` fields, no new Python scanning code — this part is 100%
  reasoning-phase manual content.
- No claims about future business impact, cost, or ROI of any roadmap item — that's Part
  VII/VIII territory, already ruled out as not repo-derivable.

## Design

**Output contract change**: Part I's numbered section list gets a sixth entry inserted before
Evidence Gaps: `5. Roadmap — prioritized findings from prior sections, per Part IX below` (the
existing Evidence Gaps entry shifts from 6 to 7).

**Tiering heuristic** (interpretation guidance in the new Part IX manual, not a new scanner
rule — this is prose guidance for the reasoning agent, evidence-grounded but not
evidence-computed):

- **Immediate**: High-confidence findings from prior sections representing either an active
  risk (a real, non-placeholder secret finding; a confirmed circular import or layer
  violation) or a trivial, no-judgment-needed fix (e.g. unpushed local commits sitting only on
  one machine; a dependency already flagged with a real OSV advisory).
- **Near-term**: High-confidence findings needing real but bounded effort — adding test
  coverage for a specific high-fan-in untested module, reviewing or merging specific named
  stale branches with real `ahead_of_main` counts, addressing a specific god-module.
- **Longer-term**: Medium or Low-confidence findings needing investigation before action (e.g.
  cross-cluster coupling flagged "worth investigating" in Architecture Review), or findings
  that are structurally larger in scope (expanding language coverage for currently-unparseable
  files).

**Mandatory rules** for the new manual section:
1. Every roadmap item must cite the exact prior-section finding it comes from (e.g. "Immediate:
   fix the two circular import chains named in Architecture Review" — not a generic "reduce
   circular dependencies" restated without the specific chains).
2. No new claims — if something wasn't reported in Repository Intelligence, Git Intelligence,
   Architecture Review, or Security, it cannot appear on the roadmap. This section only
   reorders and prioritizes, it does not investigate further.
3. Every prior section must be explicitly considered. If a section (e.g. Security) had nothing
   roadmap-worthy, the Roadmap section says so plainly rather than silently omitting it.
4. No numeric scores, no day-count estimates, no ROI/difficulty/risk ratings — tier plus a
   one-line evidence-grounded rationale only.

## Testing Strategy

No unit tests apply — this part has no Python code. Verification is a live dogfood run (no
new evidence-generation testing needed, since Part IX consumes evidence, it doesn't produce
it): confirm the Roadmap section appears in the right position, every item cites a real prior
finding by checking it back against the report's own earlier sections, no numeric scores
appear anywhere in the section, and at least one section is explicitly noted as contributing
nothing (proving the "state it plainly" rule is followed, not just the common case).

## Success Criteria

1. A full reasoning-phase run against Procta produces a Roadmap section in the correct
   position (after Security, before Evidence Gaps).
2. Every roadmap item can be traced back to a specific finding already stated earlier in the
   same report — zero new claims introduced in this section.
3. No numeric scores, ratings, or day-count estimates appear anywhere in the Roadmap section.
4. If any prior section contributed nothing to the roadmap, that is stated explicitly rather
   than silently skipped.
