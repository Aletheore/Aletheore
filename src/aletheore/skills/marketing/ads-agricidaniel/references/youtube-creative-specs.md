# YouTube Ads creative generation contract

Use this reference for video briefs, storyboards, thumbnails, companion assets, and
Demand Gen assets intended for YouTube inventory. Exact duration, skippability,
ratio, resolution, file size, safe zone, thumbnail control, and copy limits depend
on campaign subtype and placement. Verify them from current official sources and
the selected Google Ads account.

## Specification gate

For every intended in-stream, in-feed, Shorts, bumper, connected-TV, Demand Gen, or
other surface, record objective, campaign subtype, inventory, geography, account
eligibility, official source ID, duration, ratio, dimensions, file/codec limits,
companion fields, safe areas, and skip or interaction behavior.

Return `needs_input` when a current load-bearing specification is absent. Do not
carry forward historical duration or skip rules, assume a custom thumbnail is
available, or state that a visual treatment universally improves CTR.

## YouTube-specific review

- Treat the opening, narrative, brand reveal, CTA, captions, audio, and landing page
  as one concept; a single storyboard frame is not a completed video ad.
- Generate materially different hooks and stories. Color saturation, faces, shock,
  and curiosity are hypotheses to test, not psychological laws.
- Preview each horizontal, square, or vertical deliverable in its intended surface
  using current overlays. Avoid hardcoded safe-zone pixels without a dated template.
- Distinguish YouTube inventory from non-YouTube Demand Gen inventory in the asset
  manifest and subsequent report.
- Never claim the static image workflow renders a playable video. If no approved
  video capability is installed, deliver a brief, storyboard, and asset plan only.

## Output contract

Record concept ID, surface, source IDs, duration, ratio, dimensions, format/codec,
copy, captions/audio plan, checksum, provenance, rights, safety review, and preview
results. Assets remain drafts until approved and upload-validated; no live YouTube
write capability is implied.
