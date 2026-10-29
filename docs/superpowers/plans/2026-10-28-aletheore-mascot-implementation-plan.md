# Aletheore mascot implementation plan

Owner-approved and implemented: 2026-10-28.

1. Preserve the approved original character and pose sheet as source assets in `site/public/mascot/`; verify dimensions and alpha transparency.
2. Add a reusable site component with 16 named poses and optional `idle`, `typing`, and `jump` motion. Keep artwork fixed; use only discrete sprite positions and transforms. Respect reduced-motion preferences.
3. Place the neutral mascot beside the existing A mark and wordmark in the shared site header so it appears throughout the website.
4. Remove the rejected terminal ASCII face while preserving all meter values, uncertainty labels, and setup guidance in CLI output.
5. Update the meter spec and site documentation to describe the cross-surface mascot and its design lock.
6. Add tests for the complete pose map, asset format/dimensions/transparency, site integration, motion rules, reduced-motion behavior, and CLI text output.
7. Run focused Python tests and all practical site checks. If dependencies are unavailable locally, record the specific unavailable checks instead of installing packages or claiming they passed.

Do not commit or stage unrelated user files.
