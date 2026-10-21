# Contextual Benchmarks

**Verified:** 2026-07-11
**Refresh:** quarterly, and whenever a cited dataset changes
**Evidence status:** no numeric benchmark is bundled as a universal account threshold

Benchmarks are comparison evidence, not pass/fail controls. Use them to form a
question, then decide from the advertiser's economics, cohort, and same-window
account data. Do not score an account down merely because it differs from a
cross-industry median.

## Evidence contract

Before quoting any external benchmark, record:

| Field | Required value |
| --- | --- |
| Source | Publisher, direct URL, and source-ledger ID |
| Provenance | Platform, independent researcher, agency/vendor, or account data |
| Publication | Publication date and retrieval date |
| Population | Platform, objective, format, geography, industry, and sample size |
| Statistic | Mean, median, percentile, modeled result, or case study |
| Measurement | Numerator, denominator, attribution window, currency, and taxes/fees |
| Time | Observation window and seasonality |
| Fit | Why the cohort is comparable to this account |
| Limits | Sponsorship, exclusions, survivorship bias, or missing methodology |

If any field that could change the conclusion is unknown, label the benchmark
`provisional`. Platform-published uplift figures are `vendor-supplied`; a case
study is not a general baseline.

## Comparison order

Use the narrowest defensible comparison:

1. Same account, same objective, same attribution definition, prior comparable period.
2. Same account experiment or holdout with an agreed success metric.
3. First-party CRM or revenue cohort joined to spend.
4. Comparable peer cohort with disclosed methodology.
5. Broad industry or platform benchmark, labeled directional only.

Never blend sources with different attribution windows, currencies, conversion
definitions, or funnel stages without normalizing and disclosing the conversion.

## Account-specific baseline

Build a baseline from complete periods only:

```text
baseline_window = periods before the evaluated change, excluding known outages
comparison_window = same duration and conversion-lag maturity
delta = (comparison - baseline) / baseline
```

Segment by platform, objective, campaign type, geography, new/returning customer,
and device only when the segment has enough observations to be decision-useful.
Report low-volume segments as uncertain rather than replacing them with an
industry average.

For cost and value metrics, reconcile:

```text
CPA  = spend / accepted conversions
ROAS = accepted conversion value / spend
MER  = business revenue / total advertising spend
```

State whether revenue is gross or net and whether spend includes fees, credits,
and taxes. Platform ROAS and business MER answer different questions and should
not be treated as interchangeable.

## Evidence questions by metric

| Metric | Ask before interpreting |
| --- | --- |
| CTR | Which impression and click definitions, placement mix, and objective? |
| CPC/CPM | Which auction, geography, season, currency, and billing basis? |
| CVR | Which conversion, denominator, lag, and consent population? |
| CPA/CPL | Was lead quality or downstream acceptance included? |
| ROAS | Which value source, returns/cancellations, attribution, and customer cohort? |
| Frequency | Which reach window and audience overlap; is measured performance deteriorating? |
| Creative fatigue | Is there a sustained change versus a comparable baseline after mix and spend shifts? |

## Output contract

Every benchmark comparison must include the observed account value, benchmark
value, cohort fit, source ID, confidence, and the decision it informs. A gap is
an observation, not a diagnosis. Recommendation language must connect the gap to
account evidence such as marginal efficiency, query quality, creative-level
decay, conversion quality, or incrementality.

## Prohibited uses

- Fixed minimum monthly budgets presented as platform requirements.
- Fixed conversion-count, frequency, creative-life, or budget-to-CPA thresholds
  presented as universal truths.
- Vendor uplift percentages presented as expected account outcomes.
- Cross-platform cost comparisons without normalizing objective and outcome quality.
- “Top performer” or percentile claims without a population and statistic definition.

For platform-specific observed facts, use the relevant audit reference and its
dated source. If no current source pack fits the account, omit the number and
turn it into a measurement question.
