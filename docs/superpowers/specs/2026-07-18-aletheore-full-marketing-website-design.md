# Aletheore Full Marketing Website (Proper Version)

**Status:** Draft, pending review
**Date:** 2026-07-18

## Problem

The current `website/` (shipped 2026-07-17, see
`docs/superpowers/specs/2026-07-17-aletheore-marketing-website-design.md`) was deliberately
scoped as a minimum viable site: five plain HTML/CSS pages, generic feature-card copy, no
animation, no proof beyond a static terminal screenshot. It existed to unblock Lemon Squeezy
merchant verification, not to be the real front door for the product.

Now that the minimum site has served its purpose (site is live, Lemon Squeezy application is in
review), it's time to build the real version: genuine design polish, actual WHY/HOW explanations
grounded in the real product (not a generic feature list), and concrete proof — real scan output
from real, well-known public repositories, not claims.

## Goals

- Replace the current generic "Evidence-first architecture" card-grid section with real
  narrative content explaining **why** Aletheore is deterministic-first (no LLM in the scan
  path, every feature reads the same `evidence.json`) and **how** it actually works
  (`scan` → `evidence.json`/`evidence.toon` → every other command/tool reads from there).
- A **live showcase section**: real `aletheore scan` output from three real, well-known public
  repositories — **Django** (Python, real pip dependencies, deep git history), **Express**
  (JavaScript, smaller/faster contrast repo, real npm dependencies), and **Kubernetes** (Go,
  deliberately huge — a genuine stress test of the scanner at scale, not just a feature demo).
  All three scanned once, offline, with real output committed as static data — the site never
  scans anything live.
- An **interactive TOON demo**: a real side-by-side of `evidence.json` vs `evidence.toon` for
  the same underlying data, with real, freshly-measured numbers (not the older "63.6%" figure
  from prior marketing notes, which was scoped to a narrower endpoint-mapping subset) —
  **55.5% smaller in bytes, 45.5% fewer tokens** (measured with OpenAI's `o200k_base` tokenizer
  against Aletheore's own full `evidence.json`/`evidence.toon` pair from a real self-scan on
  2026-07-18), animated in as the section scrolls into view.
- Static HTML/CSS + vanilla JS, animated with **Motion** (motion.dev's vanilla-JS API, not
  React — Motion supports plain JS directly, no framework needed). No build step, same deploy
  model as the current site.
- Pricing/Terms/Privacy/Refund pages carry over largely as-is (content is already accurate),
  reskinned to match the refreshed visual system.

## Non-Goals

- **Not a live scanner.** No backend, no on-demand scanning triggered by a site visitor. All
  showcase data is pre-computed offline and committed as static data
  (`website/showcase-data.js`).
- **Not dumping raw `evidence.json` wholesale.** Django's real evidence file is 217KB — the
  showcase shows a curated real summary (module count, dependency-graph edge count, real
  license/vulnerability finding counts, git activity) plus one representative real snippet pair
  for the TOON demo, not the entire file.
- **Not the hosted dashboard.** `aletheore dashboard` (raised separately as a possible future
  React rewrite) is a different product surface entirely — untouched here.
- **Not rewriting legal-page substance.** `terms.html`/`privacy.html`/`refund.html` keep their
  existing accurate content; only visual reskinning applies.
- **Not React, not any framework.** Chosen explicitly to avoid reintroducing the build-step
  failure class this project has now hit twice this session (Procta's Puppeteer prerender
  breaking in Vercel's build sandbox; this very site's Vercel Git-import defaulting to the wrong
  Root Directory). A static site has nothing to misconfigure at build time because there is no
  build.

## Architecture

### Visual direction: hybrid split

Top of the page stays warm and narrative (today's cream/amber palette, unchanged design tokens
from `styles.css`) for the hero and WHY/HOW sections. Partway down, the page shifts into a
dark "PROOF" zone (`--bg-dark: #17140f`, already an existing design token) containing the live
showcase and the TOON demo together — visually distinct so it reads as "now here's the
receipts," mirroring Aletheore's own claim-then-citation ethos structurally, not just in copy.
The page returns to the warm palette for the CTA/footer.

### Homepage section order

1. Nav + hero — unchanged headline/monogram treatment.
2. **Why deterministic-first** — real narrative: no LLM in the scan path, tree-sitter + git log
   only, every downstream feature (`query`, `diff`, `dashboard`, `mcp`, even `audit`'s report)
   reads the same `evidence.json` and cites specific fields rather than free-form claims.
3. **How it actually works** — `aletheore scan` writes evidence once; every other command reads
   it; `evidence.toon` exists specifically for the coding-agent adapter and MCP tool results,
   since that's where token cost is actually paid (per `prototype/README.md`'s own description
   of `scan`, `mcp`, and `audit`).
4. **Live showcase** (dark zone) — three cards: Django, Express, Kubernetes. Each shows real
   numbers from an actual scan: module count, dependency-graph edge count, real
   license/vulnerability findings, git activity depth. Kubernetes additionally shows real
   wall-clock scan time as its own proof point (scanning ~1.5GB of Go at scale). Clicking a card
   can expand to a small real dependency-graph snippet, reusing the same graph-rendering
   approach already used by the local dashboard (`aletheore dashboard`).
5. **TOON demo** (dark zone, same visual zone as showcase) — real side-by-side of an actual
   `evidence.json` snippet vs. the corresponding `evidence.toon` snippet (from the Django scan,
   a good median size among the three), with the token-count reduction (51,137 → 27,892 tokens,
   45.5% fewer) animated in as the section scrolls into view via Motion's vanilla API.
6. CTA + footer, warm palette, unchanged from today.

### Generating the real showcase data

A one-time, documented, reproducible offline process, completed before the new site ships:

1. Clone Django, Express, and Kubernetes at pinned commit SHAs — the SHAs are recorded
   alongside the generated data so the numbers are independently reproducible and auditable, not
   silently stale claims.
2. Run `aletheore scan` against each clone. For Kubernetes specifically, record real wall-clock
   scan time as part of the showcase copy.
3. Extract a curated subset into `website/showcase-data.js`: module count, dependency-graph edge
   count, real license/vulnerability finding counts, git activity summary, and (for the TOON
   demo) the real `evidence.json`/`evidence.toon` byte and token counts for the Django scan.
4. Commit the pinned commit SHAs alongside the extracted data.

### Motion usage

Scroll-triggered fade/slide-in per *section* (not per individual element, to avoid an
over-animated feel), a counting-up animation for the 45.5% TOON figure, subtle hover-lift on the
three showcase repo cards. Nothing loops or runs on a timer — everything is reveal-on-scroll or
reveal-on-interaction, and degrades gracefully with JS disabled (content stays fully readable,
just static — no information is ever hidden exclusively behind an animation).

## Testing / Success Criteria

- Every number rendered on the site (module counts, token percentages, finding counts) traces
  directly to a value present in `website/showcase-data.js`, checked by comparing the rendered
  page's text against that file's actual values — not eyeballed.
- `showcase-data.js`'s own numbers trace to the real scan output recorded against the pinned
  commit SHAs — independently reproducible by re-running `aletheore scan` against the same SHAs.
- No fabricated statistic anywhere on the site, continuing the standard already established for
  the minimum site.
- All internal/external links resolve; legal pages remain reachable with unchanged accurate
  content.
- Site deploys with zero build step, verified live on `aletheore.com` identically to how the
  current site deploys today (`git push` → Vercel rebuilds automatically, no framework, no
  build-step misconfiguration surface).
- Motion animations verified to not hide any content when JavaScript is disabled.
