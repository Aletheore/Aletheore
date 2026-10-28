# Aletheore marketing site (Next.js)

Rebuild of the static site in `../website`, which stays live until cutover.

    npm install
    npm run dev        # http://localhost:3100
    npm run build && npm run start
    npm run lint && npm run typecheck && npm test
    npm run e2e        # needs `npm run start` running and Google Chrome installed

Data (regenerate when the repo changes):

    npm run data:graph    # reads /tmp/aletheore-evidence/.aletheore/air.json
    npm run data:chain    # runs `aletheore query` in /tmp/aletheore-evidence

Evidence: clone the repo to /tmp/aletheore-evidence and run `aletheore scan .` there first.
Spec: docs/superpowers/specs/2026-09-26-marketing-site-rebuild-design.md

## Mascot

Use `src/components/site/aletheore-mascot.tsx` for the reusable A-mark character. Its poses are `neutral`, `typing`,
`working`, `review`, `explaining`, `thinking`, `jump-ready`, `jump-air`, `landing`, `walking`, `waving`, `success`,
`caution`, `reading`, `curious`, and `resting`; optional `motion` supports `idle`, `typing`, and the `jump` sequence.
Use `label` only when the mascot conveys information; otherwise it
is hidden from assistive technology. The shared header already uses the neutral idle pose. Artwork lives in
`public/mascot/`; preserve its base design, and keep motion still when `prefers-reduced-motion` is enabled.

Preview: the Vercel project `aletheore-site-next` (root directory `site`) builds every push to this branch. Its ignore step
skips a build when nothing under `site/` changed. Known cost until cutover: because the project is linked to the whole
repository and Next.js needs its root directory, branches that do not contain `site/` show a failed "Vercel -
aletheore-site-next" status. Cutover (site/ on master) removes that. The production site is the separate
`aletheore-website` project serving `website/`.
