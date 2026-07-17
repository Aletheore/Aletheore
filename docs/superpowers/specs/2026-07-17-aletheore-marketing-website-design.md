# Aletheore Marketing Website (Minimum Viable Site)

**Status:** Draft, pending review
**Date:** 2026-07-17

## Problem

`website/` in this repo is still a placeholder (its own README says so explicitly: "Status:
Placeholder," "Owner: TODO"). Two real, converging needs make this urgent now: Lemon Squeezy
(the billing platform just chosen over GitHub Marketplace, for global reach and its native
affiliate program) requires a live website during merchant verification, and this is also
Aletheore's first real public-facing surface — the same GTM need flagged back at the start of
this session's GitHub App work.

Scoped to the minimum that's actually needed now, not the full future marketing site: a real,
honest homepage plus the pages a billing platform's verification process requires (pricing,
terms, privacy, refund policy). Polish and expansion (docs, blog, more sections) come later.

## Goals

- Five static pages: **Home, Pricing, Terms of Service, Privacy Policy, Refund Policy**.
- Visual direction locked from an approved Stitch mockup: warm cream/beige background, black
  text, a single warm amber/terracotta accent, a geometric owl mark, humanist sans-serif
  typography, a conversational hero line ("I got tired of code-review tools that just guess. So
  I built one that has to show its work."), card-based feature sections, one dark-background
  contrast section (terminal screenshot).
- **Every claim on the site must be real**, matching this project's own evidence-grounded ethos.
  The mockup's "12.4k stars / 450+ [something]" stats card used placeholder numbers — checked
  live against the real repo (`gh repo view`): **0 stars, 0 forks**, a genuinely new project.
  That section is dropped from Home entirely, not faked and not left as a broken placeholder.
- Pricing page shows real, decided numbers: **Free tier** (current free feature set), **Pro:
  ~~$15~~ $12/mo, up to 3 team members**, **+$4/mo per additional member**.
- Deployed as a static site to `aletheore.com` via Vercel — no build step, no framework, no
  server-side rendering. Five HTML files is not a scale that justifies a static-site generator,
  and a build-step-free deploy sidesteps entirely the class of bug that broke Procta's own
  marketing site earlier this session (a Puppeteer-based prerender script that passed in GitHub
  Actions but failed in Vercel's build sandbox) — there's no build step here to fail.

## Non-Goals

- **No docs, blog, changelog, or examples pages.** The mockup's nav bar shows these as
  placeholders for a future, fuller site — not built now. The nav for this minimum version links
  only to the five real pages that exist.
- **No CMS, no dynamic content, no backend.** Pure static HTML/CSS, matching the actual current
  need (a handful of pages that rarely change) rather than infrastructure sized for a site that
  doesn't exist yet.
- **No pixel-perfect reproduction of the Stitch mockup.** The mockup is a screenshot, not a
  source file — colors/spacing here are close, described approximations meant to capture the
  same warm, humanist, evidence-first tone, not an exact match. Refining to match precisely is a
  fast-follow if it doesn't land close enough on the first pass.
- **No payment integration in this spec.** This site enables Lemon Squeezy's verification step by
  existing; actually wiring Lemon Squeezy checkout/webhooks into the GitHub App is separate,
  future work once the account itself is approved.

## Architecture

### File structure

```
website/
  index.html          # Home
  pricing.html
  terms.html
  privacy.html
  refund.html
  styles.css           # shared, one stylesheet, no per-page CSS files
  assets/
    owl-mark.svg        # or .png if a suitable geometric owl asset needs sourcing/generating
  vercel.json           # static deploy config
```

### Design tokens (approximate, from the mockup)

```css
:root {
  --bg-cream: #f5f0e6;
  --bg-dark: #17140f;
  --text-primary: #1a1a1a;
  --text-muted: #6b6459;
  --accent: #e0863a;
  --accent-hover: #c96f26;
  --card-bg: #ffffff;
  --border-subtle: rgba(0,0,0,0.08);
  --font-heading: -apple-system, "Segoe UI", "Inter", sans-serif;
  --font-body: -apple-system, "Segoe UI", "Inter", sans-serif;
}
```

### Home page sections (top to bottom)

1. **Nav**: "Aletheore" wordmark + owl mark, links to Pricing/Terms (minimal nav for this
   version — no Docs/Changelog/Security links since those pages don't exist yet), a "Sign In"
   or "GitHub" CTA button top-right.
2. **Hero**: headline "I got tired of code-review tools that just guess. So I built one that
   has to show its work." (the italicized/accent-colored clause on "has to show its work"),
   subtext describing Aletheore in one sentence (deterministic evidence-grounded scanner, no
   LLM guessing, every claim traced to a fact), owl mark illustration right-aligned, two CTAs:
   "Start Your First Audit" (primary, links to the GitHub repo's README/quickstart) and "Read
   the Protocol" (secondary, links to the repo).
3. **"Evidence-first architecture"** section, card grid: Traceability Matrix, Zero-Config CLI,
   Visual Evidence Trails — each a short 1-2 sentence real description of an actual shipped
   feature (dependency graph, dead-code/hotspot detection, symbol-source lookup, layer-violation
   detection — pull real feature names from `README.md`'s own "What's actually shipped" list
   rather than inventing new feature names for the website that don't match the product).
4. **"Humanist tools for a technical world"** section: a real terminal screenshot or realistic
   mock of `aletheore scan` / `aletheore audit` output, with 2-3 real bullet points about the
   local-first, no-tracking, BYOK ethos already established in `README.md`.
5. **Footer**: Aletheore wordmark + one-line description, link columns (GitHub repo, Sponsors,
   Terms/Privacy/Refund) — no Discord/Blog links since those don't exist.

### Pricing page

Two-column comparison: **Free** (the real free feature set — deterministic scan, PR comments via
the GitHub Action or App, free dashboard) vs **Pro** (~~$15~~ **$12/mo**, everything in Free plus
managed audits, Slack/Teams alerts, branch-protection Check Runs, endpoint health monitoring, up
to 3 team members, **+$4/mo per additional member**). Same visual language as Home (cream
background, amber accent, card-based).

### Legal pages (Terms, Privacy, Refund)

Real, standard content appropriate for a solo-founder SaaS selling a $12/mo subscription via a
Merchant of Record (Lemon Squeezy) — not boilerplate copied verbatim from an unrelated template,
but standard, honest terms covering: what's being sold, refund window/policy, data handling
(tying back to the project's own "nothing leaves your machine except transiently for hosted
features" posture already established for the GitHub App), contact information
(`arihantkaul@outlook.com`, the same contact address already used elsewhere in this project, e.g.
Procta's pause page).

## Testing

- Every internal link (nav, footer, CTAs) resolves to a real page or a real external URL (the
  GitHub repo) — no dead links, no `#` placeholders.
- The stats/social-proof section is confirmed absent from the shipped Home page (a direct check
  against the actual HTML, not just "we meant to remove it").
- Pricing page's numbers match exactly: Pro $12/mo (shown with $15 struck through), 3 members
  included, $4/mo per additional member — checked against the real rendered page text, not just
  the source.
- `vercel.json` deploys as a pure static site — confirmed by checking the deployed site actually
  serves `pricing.html` at `/pricing` (or equivalent clean URL) without a build step running.

## Success Criteria

1. `aletheore.com` serves a real, live site with all five pages reachable.
2. Lemon Squeezy's merchant application accepts the site URL during verification (the actual
   proof this spec exists to satisfy).
3. No fabricated claim anywhere on the site — every stat, every feature description, every price
   traces to something real and currently true.
