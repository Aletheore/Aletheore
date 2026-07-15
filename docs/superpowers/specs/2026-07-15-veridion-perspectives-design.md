# Veridion "Perspectives" Design — Six Audience-Specific Lenses

**Status:** Draft, pending review
**Date:** 2026-07-15

## Problem

The full original list of audience/persona checks (~75 items: SOC 2, SOC 3, ISO 27001, ISO
42001, GDPR, CCPA/CPRA, HIPAA, FERPA, DPDP compliance-certification personas; dozens of
redundant job-title variations of "is this secure," "is this investable," "is this well-run";
and several items that were actually Veridion's own VDP-constitution self-review vocabulary,
not lenses for auditing a target repo at all) was reviewed and collapsed to six. The
named-certification personas were ruled out as a hard constraint, not a scoping question:
Veridion must never output anything shaped like a pass/fail or compliant/non-compliant claim
about a named regulation or certification (GDPR, HIPAA, SOC 2, ISO, etc.), because it lacks
the operational evidence — access logs, data processing agreements, employee training
records, incident history — those require, and outputting that shape of claim creates real
legal and trust risk, not just an accuracy risk.

This spec covers the six lenses that survived: **Security**, **Investor / Technical Due
Diligence**, **Onboarding / New Contributor**, **Engineering Manager / Process**,
**Documented Policy & Governance Gaps**, and **Documentation Quality**. All six are pure
re-synthesis of evidence Parts II-VI already compute — the same pattern Part IX (Roadmap)
already established — except two of them need one new, small deterministic evidence category
first (policy-document presence detection).

## Goals

- Add `evidence.repository.policy_docs`: marker-based detection of policy/governance-relevant
  files (LICENSE, README, SECURITY.md, CODE_OF_CONDUCT.md, etc.), same mechanism as
  `detect_build_tools`.
- Add a new `## Perspectives` section to the audit report, positioned after Roadmap and before
  Evidence Gaps — the final multi-audience reframing of everything the report already
  established, including Roadmap's own tier placements.
- Ship one new manual part covering all six lenses, each following a fixed three-part template
  (what this audience cares about / what evidence supports / what evidence doesn't cover) so
  the section stays disciplined rather than becoming six free-form essays.
- Structurally prevent any lens — especially Documented Policy & Governance Gaps — from ever
  producing a compliance-verdict-shaped claim, via the mandatory "what evidence doesn't cover"
  requirement in every lens.

## Non-Goals

- No named-certification or regulation-specific compliance verdicts (GDPR/HIPAA/SOC
  2/ISO-anything pass/fail) — this is the hard constraint carried through every lens, not
  scoped down from, ruled out entirely.
- No new per-function or per-module documentation-quality detection (docstring presence,
  comment density) — Documentation Quality reuses existing Part II findings (high-fan-in/
  god-modules) and the new `policy_docs` category only; a real docstring-coverage scanner is a
  separate, future increment if ever wanted.
- No separate output files per lens, no CLI flag to select a subset — all six lenses always
  run and appear in one `## Perspectives` section of the single report.
- No new claims about revenue, market, competitors, or anything requiring data outside the
  repository — this was already ruled out earlier this session and isn't revisited here.

## Evidence Schema Addition

```json
"repository": {
  "...": "...",
  "policy_docs": [
    {"name": "license", "evidence": "LICENSE"},
    {"name": "readme", "evidence": "README.md"},
    {"name": "security_policy", "evidence": "SECURITY.md"}
  ]
}
```

Marker dict (file or directory presence, `.exists()` check — directories and files use the
same check):

```python
POLICY_DOC_MARKERS = {
    "LICENSE": "license", "LICENSE.md": "license",
    "README.md": "readme",
    "SECURITY.md": "security_policy",
    "PRIVACY.md": "privacy_policy", "PRIVACY_POLICY.md": "privacy_policy",
    "CODE_OF_CONDUCT.md": "code_of_conduct",
    "CONTRIBUTING.md": "contributing_guide",
    "TERMS.md": "terms_of_service", "TERMS_OF_SERVICE.md": "terms_of_service",
    "GOVERNANCE.md": "governance_policy",
    "docs/security": "security_policy", "docs/privacy": "privacy_policy",
    "docs/compliance": "compliance_docs", "docs/governance": "governance_policy",
}
```

## Output Contract Change

Part I's numbered section list gains a new entry between Roadmap and Evidence Gaps:
`7. **Perspectives** — six audience-specific readings of the findings above, per the new
manual part below.` Evidence Gaps shifts from 7 to 8.

## The Six Lenses and Their Fixed Template

Each lens in the new manual part follows exactly this structure — a fixed sentence (written
into the manual, not generated), then two agent-filled subsections:

1. **What this audience cares about** (fixed, one sentence, in the manual itself).
2. **What evidence supports** — cite specific prior findings by exact reference (a Security
   finding, an Architecture coupling result, a Git ownership figure, a specific Roadmap tier
   item) reframed around this lens's concern. No new claims — same rule as Roadmap.
3. **What evidence doesn't cover** — mandatory, explicit. Not optional, not skippable even
   when the lens has plenty to say from (2). This is the structural mechanism that prevents
   any lens from drifting into an implied verdict.

- **Security**: "cares about attack surface and incident-response readiness." Draws from
  Security's secrets/vulnerability findings and Git's ownership concentration (who could even
  respond to an incident).
- **Investor / Technical Due Diligence**: "cares about cost to inherit and financial risk if
  key people leave." Draws from Git's bus-factor/cadence findings, Architecture's coupling
  findings (cost to change), AI-usage findings (vendor lock-in).
- **Onboarding / New Contributor**: "cares about where to start and what's dangerous to touch
  on day one." Draws from Architecture's clusters (a map of the codebase), Repository
  Intelligence's high-fan-in/god-module findings (danger zones).
- **Engineering Manager / Process**: "cares about team practice health, not individual
  financial risk." Draws from Git's cadence trend, unmerged/stale branch findings (process
  bottlenecks), ownership distribution framed as a team-practice question rather than a
  financial one.
- **Documented Policy & Governance Gaps**: "cares about what this repo's own paper trail
  documents and where it's silent." Draws from `policy_docs` directly — reads the actual
  content of any detected file and quotes it; states plainly which common policy areas
  (security, privacy, contribution process, license) have no corresponding file at all. **Must
  never characterize this as compliance status for any named regulation or certification** —
  only "documented" vs. "not found in this repository."
- **Documentation Quality**: "cares about whether core code is explained anywhere." Draws from
  `policy_docs`'s `readme`/`contributing_guide` entries and Repository Intelligence's
  high-fan-in/god-module findings, reframed as "these are what most needs a README section or
  comment, per their fan-in count."

## Testing Strategy

`detect_policy_docs` (formerly this session's detector pattern) unit-tested against synthetic
fixtures covering: multiple markers present, zero markers present (empty list, not omitted
key), and a directory-based marker (`docs/security/`) alongside file-based ones. No test
coverage needed for the manual content itself (matches Part IX's precedent — no code, no
tests) beyond the live dogfood verification below.

## Success Criteria

1. `evidence.repository.policy_docs` correctly detects markers against a real repo — verify
   against Veridion's own repo, which already has `SECURITY.md`, `CODE_OF_CONDUCT.md`,
   `GOVERNANCE.md`, `CONTRIBUTING.md`, `LICENSE` at its root (from the constitution bootstrap
   skeleton) — a real, non-empty test case, not just a synthetic fixture.
2. A full reasoning-phase report contains a `## Perspectives` section positioned after Roadmap
   and before Evidence Gaps, with all six lenses present.
3. Every lens's "what evidence supports" claims trace to a finding already stated earlier in
   the same report — zero new claims introduced in this section, same rule Roadmap already
   proved workable.
4. Every lens includes an explicit, non-empty "what evidence doesn't cover" statement.
5. Nowhere in the Perspectives section does any text assert or imply compliance/non-compliance
   with a named regulation or certification (GDPR, HIPAA, SOC 2, ISO 27001/42001, CCPA, FERPA,
   DPDP, or any other named standard) — this is checked explicitly, not assumed absent.
