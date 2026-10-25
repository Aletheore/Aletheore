# Budget Allocation and Scaling

**Verified:** 2026-07-11
**Refresh:** foundational review by 2027-01-07; platform eligibility at run time
**Evidence status:** deterministic finance math; allocation heuristics are contextual

Allocate budget from business constraints and marginal evidence. Do not begin
with a fixed platform mix, a minimum monthly spend, or a universal scaling step.

## Required inputs

- Objective and accepted business outcome.
- Gross margin or contribution margin and variable fulfilment costs.
- Cash, credit, monthly, daily, platform, and campaign ceilings.
- Target CPA, contribution per conversion, or target profit; state who approved it.
- Conversion lag, sales-cycle lag, return/cancellation window, and data maturity.
- Current spend, accepted conversions, value, and confidence by platform.
- Contractual, geographic, policy, inventory, and creative-capacity constraints.

Missing owner-approved ceilings blocks an account write. Missing economics makes
the plan provisional.

## Core math

```text
CPA = spend / accepted conversions
ROAS = accepted conversion value / spend
contribution_after_ads = accepted contribution - spend
break_even_CPA = contribution per accepted conversion
break_even_ROAS = 1 / contribution_margin_rate
marginal_CPA = incremental spend / incremental accepted conversions
marginal_ROAS = incremental accepted value / incremental spend
```

Use net accepted value where returns, fraud, cancellations, duplicate leads, or
sales rejection materially affect the result. Do not infer profit from platform
ROAS alone.

## Allocation sequence

1. Reserve non-negotiable commitments and measurement costs.
2. Protect campaigns with causal or strong same-account evidence of positive
   marginal contribution, subject to saturation and cash constraints.
3. Fund bounded experiments with a declared hypothesis, minimum detectable
   effect, decision date, and stop conditions.
4. Hold a contingency only when the business has a defined use for it.
5. Reallocate from the weakest marginal opportunity, not necessarily the worst
   average CPA or ROAS.

Any “proven / growth / experiment” split is an operator policy, not a default.
Record the chosen split and rationale in the run manifest.

## Scaling decision

Draft a scale change only when all are true:

- The conversion and revenue data have matured through the relevant lag.
- Tracking, consent, policy, inventory, and landing experience are healthy enough
  to interpret the result.
- Marginal performance is within the owner-approved economic boundary.
- The campaign is eligible for the proposed budget and bidding change.
- The advertiser can absorb learning, delivery, and cash-flow volatility.
- A before/after measure, verification window, and rollback trigger exist.

The size and timing of a change come from platform simulations, account history,
conversion cycles, and blast-radius limits. No universal percentage is safe.

## Pause or reduce decision

Do not pause solely because spend crosses a fixed multiple of target CPA. First
check conversion lag, tracking outages, low sample size, downstream lead quality,
seasonality, and whether the object is needed for an active experiment.

Immediate containment is appropriate for an owner-defined runaway-spend ceiling,
policy or legal exposure, broken destination, confirmed tracking corruption, or
unauthorized delivery. Prefer the smallest reversible action and preserve evidence.

## Portfolio view

Compare platforms on accepted business outcomes over the same mature window.
Include:

- Spend share, accepted value share, and contribution after advertising.
- Marginal rather than only average efficiency.
- Confidence and evidence coverage.
- Incrementality or holdout evidence where available.
- Constraints that prevent movement, such as inventory, audience, policy, or
  creative throughput.

When spend or accepted value is unavailable, do not fabricate proportional
weights. Present scenarios and identify the missing decision input.

## Change packet

Every proposed reallocation states current and proposed amounts, affected object
IDs, economic rationale, expected effect, uncertainty, learning impact, owner,
approval, verification date, rollback threshold, and idempotency key. The
recommendation remains a draft until the mutation gate passes.
