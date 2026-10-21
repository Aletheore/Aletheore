# Automation Delegation Classifier

**Verified:** 2026-07-11
**Refresh:** foundational review by 2027-01-07; platform modules at run time
**Scope:** oversight and mutation risk, not an account-health score

Classify what authority has been delegated, not whether a campaign carries an
“automated” product label. One campaign can delegate bidding while keeping
budget, targeting, creative, and approvals under human control.

## Delegation dimensions

Observe each dimension separately:

- Goal and conversion selection.
- Bidding and bid targets.
- Budget allocation and pacing.
- Audience or query expansion and exclusions.
- Placement selection.
- Creative generation, variation, and destination selection.
- Campaign creation and structural changes.
- Monitoring, pausing, and incident containment.
- External agent/MCP read and write authority.

Use the current account UI/API and capability manifest. Do not infer a module's
state from the campaign name.

## Tiers

| Tier | Definition | Required oversight |
| --- | --- | --- |
| T0 Observed manual | No decision module is delegated; tools may report only | Standard QA and change control |
| T1 Assisted | Automation recommends or drafts, but a human selects and applies | Review evidence, applicability, and final diff |
| T2 Bounded delegation | One or more modules act within explicit account-defined limits | Per-module goals, ceilings, monitoring, and rollback |
| T3 Broad platform delegation | The platform controls most delivery modules while humans own goals, assets, and guardrails | Signal quality, objective, creative, exclusion, and marginal-outcome review |
| T4 External agent write | An external agent can create or change account objects | Full mutation gate, least privilege, idempotency, independent verification, and incident response |

The account tier is the highest active authority, but retain the dimension map;
the summary tier alone is not decision-complete.

## Classification method

1. Inventory active campaigns and connected integrations.
2. Capture each dimension's observed state, owner, limits, and evidence timestamp.
3. Separate recommendation, draft, apply, and autonomous-apply authority.
4. Identify conflicting controllers, such as platform pacing plus an external
   budget agent.
5. Assign confidence and list unknown or unavailable states.
6. Reclassify after material configuration or connector changes.

## Review by tier

- T0: verify manual operations are intentional and monitored; manual is not
  automatically safer or healthier.
- T1: check recommendation quality and reviewer accountability.
- T2: test ceilings, edge cases, alerting, and recovery for each delegated module.
- T3: inspect business-goal alignment, conversion quality, marginal performance,
  creative/destination controls, exclusions, and platform eligibility.
- T4: apply `mcp-integration.md`; no write without exact capability verification,
  approval, audit, remote verification, and rollback.

## Findings

Automation adoption and novelty are unscored context. Score only a stable control
such as missing approval, absent ceiling, conflicting automation, unauthorized
scope, broken monitoring, or no rollback when applicable.

Return platform, campaign/object IDs, dimension, observed authority, tier,
evidence, confidence, risk, owner, and remediation. Do not prescribe “more
automation” or “more manual control” without a diagnosed account problem and a
testable expected result.
