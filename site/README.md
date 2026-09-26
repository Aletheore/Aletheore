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

Preview: the Vercel project `aletheore-site-next` builds this app from the repository root (install and build run inside
`site/`), because a root directory of `site` fails on every branch that does not have the folder yet. Its ignore step
(`git diff --quiet HEAD^ HEAD -- site/`) skips a build when nothing under `site/` changed, so pull requests to other parts
of the repo are not built. The production site is the separate `aletheore-website` project serving `website/`, and stays
as it is until cutover.
