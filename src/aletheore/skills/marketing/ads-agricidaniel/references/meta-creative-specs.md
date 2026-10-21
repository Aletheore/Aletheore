# Meta Ads creative generation contract

Use this reference for Facebook and Instagram creative planning. Placement ratios,
safe zones, durations, copy truncation, file limits, enhancement behavior, and
format availability are volatile. Resolve them from current official evidence and
the selected Ads Manager account before rendering.

## Specification gate

Build a placement matrix for the exact campaign objective and inventory. For every
Feed, Stories, Reels, carousel, video, messaging, or other selected placement,
record the official source ID and date, ratio, dimensions, file type and size,
duration, copy fields, safe area, crop/overlay behavior, and eligibility.

Return `needs_input` when the current specification or account surface cannot be
verified. Do not apply one placement's visible-copy or safe-zone rule to another,
and do not claim that a particular ratio universally performs best.

## Meta-specific review

- Confirm whether automatic placements, creative enhancements, catalog assets,
  music, text variation, and image expansion are enabled and eligible.
- Preview every intended Facebook and Instagram placement. UI overlays and crops
  vary by surface, device, and product version.
- Keep the core promise, product, brand identifiers, and required disclosure clear
  in all approved previews without relying on unsupported hardcoded pixel zones.
- For carousels, preserve narrative and visual continuity while recording each card
  as a distinct asset with its own destination and copy.
- Keep creative concepts materially distinct; resizing the same image is coverage,
  not a creative-diversity test.

## Output contract

The asset manifest records concept ID, placement, specification source IDs, ratio,
dimensions, format, duration where relevant, copy fields, checksum, provenance,
rights, safety review, preview result, and automation settings. Generated assets are
drafts. This skill does not imply a live Meta upload or mutation capability.
