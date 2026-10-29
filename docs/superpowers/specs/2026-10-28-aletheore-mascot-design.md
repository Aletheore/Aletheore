# Aletheore mascot and reusable motion states

Status: owner-approved and implemented, 2026-10-28.

## Owner direction

The mascot must belong to Aletheore's identity rather than read as a generic creature. The owner approved the A-mark character concept and asked for reusable stances, including jumping, typing, and working, for use across the site and product. Preserve the normal character design while varying poses.

## Character lock

- The approved A-mark character is the canonical master artwork.
- Keep its angular A silhouette, face location, proportions, charcoal outline, warm paper body, rust cheeks, and restrained green accent consistent in every state.
- Pose art may move limbs, change a mild expression, or include a small relevant prop such as the existing evidence card or a simple laptop. Props must not obscure the A silhouette or become part of the permanent character design.
- Do not add outfits, new body parts, colors, or decorative effects to the master. The mascot is friendly and calm.

## State sheet and web component

Use the approved 4-by-4 pose sheet as the source for the reusable website component. Its named states are neutral, typing, working, review, explaining, thinking, jump-ready, jump-air, landing, walking, waving, success, caution, reading, curious, and resting. The component accepts a named pose, optional motion, size, and accessible label; it is decorative and hidden from assistive technology by default.

The shared site header shows a small neutral mascot with a gentle idle bob beside the existing A mark and wordmark. The homepage also shows the typing/work loop by its evidence headline. Other pages can use the same component in another state. Available motion is intentionally limited to a gentle idle bob, discrete typing/work frame swaps, and a short jump-ready/air/land sequence. Animate the intact image using `transform` and discrete sprite frames; never morph, scale-distort, or redraw the character. Honor `prefers-reduced-motion` by presenting a stable state.

The CLI remains a text interface. Remove its box-face ASCII art; retain the meter figures and explanations as plain text. The richer visual character lives in graphical surfaces.

## Out of scope

No autonomous assistant behavior, speech, telemetry, new network traffic, system tray/desktop pet, or scan-result recommendations. Future guide capabilities remain separate work.

## Acceptance

- The original master and pose sheet are bundled as transparent PNGs without alteration.
- All 16 pose names map to valid sprite-sheet cells and the component uses the same source artwork at each size.
- Typing and jump motion use only discrete frames plus transform; reduced-motion preference disables looping and resolves motion to a stable pose.
- The shared site header shows a neutral idle mascot beside the unchanged wordmark and official A mark; the homepage shows the typing/work loop.
- CLI output contains no ASCII box character and all existing meter figures/caveats remain intact.
- Site lint, typecheck, unit tests, build, asset checks, and focused CLI tests pass where the local environment supports them.
