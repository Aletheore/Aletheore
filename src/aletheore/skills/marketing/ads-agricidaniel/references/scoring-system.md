# Claude Ads scoring contract

The production scoring engine is the sole authority for health scores. Prompts,
agents, reports, and tests must call it rather than reimplementing this document.

## Outputs

Keep four outputs separate:

1. `health_score`: observed performance and implementation health, 0-100.
2. `evidence_coverage`: proportion of applicable control weight with known results.
3. `regulatory_exposure`: independent P0/P1 risk summary, not score padding.
4. `opportunities`: unscored optional, beta, premium, or ineligible capabilities.

Do not publish a letter grade. Pair every score with coverage status and data window.

## Control states

- `pass`: the evidence satisfies the control.
- `fail`: the evidence does not satisfy the control.
- `unknown`: applicable, but evidence is missing or inconclusive.
- `not_applicable`: the control does not apply to this account or campaign.

`not_applicable` is removed from health and coverage denominators. `unknown` is
removed from health calculation but remains in the coverage denominator.

## Severity weights

| Severity | Weight | Meaning |
| --- | ---: | --- |
| critical | 5 | Immediate material revenue, data, account, privacy, or policy risk |
| high | 3 | Material performance or operational risk |
| medium | 1 | Meaningful improvement with lower urgency |
| informational | 0 | Context or unscored opportunity |

Severity represents impact if the control fails. It must not be inflated because
a feature is new or strategically interesting.

## Category calculation

For each category, using known applicable controls only:

```text
category_health = 100 * sum(pass_weight) / sum(known_control_weight)
```

where `control_weight` is the severity weight. A failed control earns zero. The
category must not multiply its declared category weight into every control.

Category coverage is:

```text
category_coverage = 100 * sum(known_control_weight) / sum(applicable_control_weight)
```

Platform health applies normalized category weights after category calculation:

```text
platform_health = sum(category_health * category_weight)
```

The caller must supply a versioned scoring profile whose category weights total
exactly 100; the production engine validates this invariant. The capability
manifest describes executable platform surfaces and is not, by itself, a scoring
profile. A category with no applicable controls is removed and the remaining
category weights are renormalized for that run.

## Coverage status

| Coverage | Status | Reporting rule |
| ---: | --- | --- |
| 80-100% | graded | Publish health with coverage |
| 60-79.99% | provisional | Publish health labeled provisional |
| below 60% | insufficient_evidence | Do not present health as an account grade |

Unknown critical controls must also appear in the priority output even when total
coverage remains above 80%.

## Platform categories

The following are initial product reference profiles. They are not inferred from
platform behavior and do not become executable merely by appearing in this file:

- Google: measurement 25, waste 20, structure 15, keywords 15, creative 15, settings 10.
- Meta: measurement 25, creative 25, structure 20, audiences 15, delivery 10, policy 5.
- YouTube: measurement 25, creative 25, structure 20, audiences 15, delivery 10, policy 5.
- LinkedIn: measurement 25, audiences 20, creative 20, structure 15, delivery 15, policy 5.
- TikTok: measurement 25, creative 25, delivery 15, structure 15, audiences 15, policy 5.
- Microsoft: measurement 25, structure 20, delivery 15, creative 15, audiences 15, policy 10.
- Apple: measurement 25, structure 20, keywords 20, creative 15, delivery 15, policy 5.
- Amazon: measurement 20, retail 20, targeting 20, structure 15, creative 10, delivery 10, policy 5.
- Reddit: measurement 25, structure 20, creative 20, audiences 15, delivery 10, policy 10.
- Pinterest: measurement 25, retail 20, creative 20, structure 15, audiences 10, policy 10.
- Snapchat: measurement 25, creative 20, structure 20, audiences 15, delivery 10, policy 10.
- X: measurement 25, structure 20, creative 20, audiences 15, delivery 10, policy 10.

Before operational use, bind the selected profile to the run manifest and verify
that its category names match the applicable controls. Change profiles only through
a versioned product decision and regression analysis.

The executable state lives in
`control-plane/manifests/control-registry.json` and
`control-plane/manifests/scoring-profiles.json`. The current v1 profiles are
explicitly disabled: catalog rows remain typed informational watchlists or
source-refresh discovery items. A disabled profile yields no account health and
zero approved evidence coverage. Do not copy the reference weights above into a
run or assign severity in a prompt.

## Portfolio health

Use platform spend from the same time window:

```text
portfolio_health = sum(platform_health * platform_spend_share)
```

If spend is unavailable, equal-weight platforms and mark the result provisional.
Do not include an insufficient-evidence platform in the numerical aggregate;
surface its missing weight and the resulting portfolio coverage explicitly.

## Deduplication and applicability

- Model one canonical risk as one scored control with multiple observations.
- Do not score both a root tracking failure and every symptom as separate penalties.
- Record feature availability, account eligibility, geography, campaign type, and
  maturity before declaring a control applicable.
- Keep watch-list and upcoming-change items unscored until they affect the account.
- Do not score adoption of a newly announced feature as health.

## Recommendations

Scores prioritize investigation; they do not authorize changes. Each recommendation
must cite the relevant finding, declare confidence, consider sample size and
conversion lag, and pass the mutation gate before any account write.
