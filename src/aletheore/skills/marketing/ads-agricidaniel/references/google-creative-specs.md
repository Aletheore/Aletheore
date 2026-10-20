# Google Ads creative generation contract

Use this reference for generation planning across Google Ads surfaces, including
asset-based, search, feed, video, and Demand Gen campaigns. It intentionally does
not freeze dimensions, character limits, asset counts, feature names, or format
availability: those are current platform claims and must come from dated official
evidence and the target account.

## Specification gate

Before generating, create a placement matrix with:

- campaign type, objective, network, placement, geography, and account eligibility;
- official source ID, retrieval date, and refresh date for every required asset;
- accepted file type, ratio, pixel range, byte limit, copy limit, and asset count;
- crop behavior, logo/text restrictions, policy constraints, and approval state;
- whether the item is required, recommended, optional, automatically generated, or
  unavailable for the selected campaign.

If a load-bearing specification is absent or stale, return `needs_input`. A familiar
canvas size is not proof that an upload will be accepted. Do not describe announced,
beta, premium, or account-ineligible automation as enabled.

## Google-specific review

- Separate Search copy, asset-group images and logos, feed assets, Demand Gen
  assets, and YouTube assets; requirements and cropping are not interchangeable.
- Validate the landing page, product feed, business name, logo ownership, and policy
  claims before generating assets around them.
- Produce materially different concepts, not only crops or color changes, when the
  brief calls for creative testing.
- Keep critical subjects within the official placement safe areas and preview every
  supported crop. Do not assume text or logos are universally forbidden or allowed.
- Treat automatic asset creation, final-URL behavior, and generated copy as governed
  account settings. Record their current state and owner intent.

## Output contract

Each asset-manifest entry records concept ID, source IDs, placement, ratio,
dimensions, format, size, checksum, copy fields, rights/provenance, safety review,
preview result, and account eligibility. A generated file is a draft until its
manifest validates and the operator approves it. Generation does not imply upload
or live Google Ads write support.
