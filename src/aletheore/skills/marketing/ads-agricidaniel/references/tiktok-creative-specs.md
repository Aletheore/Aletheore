# TikTok Ads creative generation contract

TikTok is not universally vertical-only, and upload acceptance, aspect ratios,
audio, duration, copy fields, safe zones, and automation vary by ad format,
placement, market, device, and account. Resolve the exact requirements from current
official evidence and the selected account before generation.

## Specification gate

For each intended In-Feed, Spark, TopView, image, carousel, search, shopping, app,
or other eligible surface, record the official source ID and date, objective,
placement, identity/post ownership, ratio, dimensions, file type, codec, duration,
audio requirements, copy fields, safe area, and account eligibility.

Return `needs_input` when the surface or specification cannot be verified. Do not
reject horizontal, square, static, or silent assets by universal rule; determine
their applicability for the selected format. Do not infer that an accepted asset
will perform well.

## TikTok-specific review

- Preview UI overlays on the current placement and device. Use official templates
  or preview tools rather than fixed pixel coordinates copied from an old guide.
- Verify Spark authorization and post state before treating organic content as an
  ad asset.
- Treat sound as a creative and accessibility decision subject to format rules,
  licensing, captions, brand context, and the user's likely sound-off behavior.
- Keep concepts native to the audience and offer without fabricating UGC,
  testimonials, identities, or performance proof.
- Treat Smart+, Symphony, and other automated creative surfaces as discoverable
  account capabilities; verify access, settings, asset use, and owner intent.

## Output contract

Record concept ID, placement, source IDs, ratio, dimensions, format, codec/duration/
audio where relevant, identity authorization, copy, checksum, provenance, rights,
safety review, and preview results. Generation remains draft-only and does not
imply upload, post authorization, or live TikTok mutation support.
