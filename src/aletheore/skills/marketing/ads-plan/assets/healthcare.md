# Healthcare paid-media planning questions

> Status: qualitative, unscored planning aid; not medical, privacy, or legal advice.

## Required gates

- Context gate: identify the provider, service or product, audience, jurisdiction,
  care setting, objective, conversion, accountable privacy owner, and clinical reviewer.
- Evidence gate: verify current official source IDs from platforms and regulators, including
  `hhs-tracking-guidance-official` when its scope is applicable.
- Privacy gate: determine whether pages, events, URLs, forms, audiences, or vendor flows
  could disclose protected or sensitive health information; block uncertain tracking uses.
- Clinical-claim gate: require approved indication, evidence, limitations, risk language,
  and qualified review before using outcome or comparative claims.
- Regulatory gate: identify category, region, certification, targeting, disclosure,
  accessibility, and emergency-content requirements from current official evidence.
- Capability gate: keep sensitive data out of unsupported providers, prompts, reports,
  logs, and account operations.

## Planning questions

- What service or product is offered, to whom, by which authorized entity?
- Which claims are approved, understandable, balanced, and supported?
- Which conversion events are useful without exposing health status or treatment interest?
- Which targeting or creative concepts risk sensitive-trait inference or exploitation?
- How will consent, vendor access, retention, deletion, incident response, and review work?
- What should happen when a user may need urgent or professional care rather than an ad?

## Candidate considerations

- Consider education, access, service explanation, and trust hypotheses only after the
  privacy, clinical, regulatory, and platform gates pass.
- Separate marketing measurement from medical outcomes and never infer diagnosis.

## Output guardrails

- Return questions, source IDs, approvals, unresolved risks, owners, and stop conditions.
- Do not prescribe eligibility, targeting, tracking, claims, bids, allocations, or outcomes.
