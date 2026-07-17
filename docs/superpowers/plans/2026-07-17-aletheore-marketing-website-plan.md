# Marketing Website Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a 5-page static marketing site (Home, Pricing, Terms, Privacy, Refund) to `website/`, deployable to `aletheore.com` via Vercel with no build step.

**Architecture:** Plain HTML files sharing one stylesheet, one inline SVG mark, one `vercel.json`. No framework, no JS build, no backend — matches the actual scope (5 pages that rarely change).

**Tech Stack:** HTML, CSS. No JS framework, no build tooling.

## Global Constraints

- Design tokens (from the approved spec): `--bg-cream: #f5f0e6; --bg-dark: #17140f; --text-primary: #1a1a1a; --text-muted: #6b6459; --accent: #e0863a; --accent-hover: #c96f26; --card-bg: #ffffff; --border-subtle: rgba(0,0,0,0.08);`
- No fabricated stats/social proof anywhere on the site.
- Pricing: Free tier (real feature set) vs Pro — ~~$15~~ **$12/mo**, up to 3 team members, **+$4/mo per additional member**.
- Contact address: `arihantkaul@outlook.com`.
- Nav links only to pages that exist in this plan (no Docs/Changelog/Blog/Discord links).

---

## Task 1: Shared stylesheet, logo mark, Home page

**Files:**
- Create: `website/styles.css`
- Create: `website/assets/logo-mark.png` (copy of the real "A" monogram asset, not generated)
- Create: `website/index.html`

- [ ] **Step 1: Write the shared stylesheet**

Create `website/styles.css`:

```css
:root {
  --bg-cream: #f5f0e6;
  --bg-dark: #17140f;
  --text-primary: #1a1a1a;
  --text-muted: #6b6459;
  --accent: #e0863a;
  --accent-hover: #c96f26;
  --card-bg: #ffffff;
  --border-subtle: rgba(0, 0, 0, 0.08);
  --font: -apple-system, "Segoe UI", "Inter", sans-serif;
}

* { box-sizing: border-box; margin: 0; padding: 0; }

body {
  font-family: var(--font);
  background: var(--bg-cream);
  color: var(--text-primary);
  line-height: 1.5;
}

a { color: inherit; text-decoration: none; }

.container { max-width: 1100px; margin: 0 auto; padding: 0 24px; }

nav {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 20px 0;
}

.nav-links { display: flex; gap: 28px; align-items: center; }
.nav-links a { color: var(--text-muted); font-size: 15px; }
.nav-links a:hover { color: var(--text-primary); }

.wordmark { display: flex; align-items: center; gap: 10px; font-weight: 700; font-size: 20px; }
.wordmark img { width: 28px; height: 28px; border-radius: 7px; display: block; }

.btn {
  display: inline-block;
  padding: 12px 22px;
  border-radius: 8px;
  font-weight: 600;
  font-size: 15px;
  cursor: pointer;
}
.btn-primary { background: var(--accent); color: #fff; border: none; }
.btn-primary:hover { background: var(--accent-hover); }
.btn-secondary { background: transparent; color: var(--text-primary); border: 1px solid var(--border-subtle); }
.btn-secondary:hover { border-color: var(--text-primary); }

.hero {
  display: grid;
  grid-template-columns: 1.4fr 1fr;
  gap: 48px;
  align-items: center;
  padding: 64px 0;
}
.hero h1 { font-size: 44px; line-height: 1.2; font-weight: 700; }
.hero h1 .accent { color: var(--accent); font-style: italic; }
.hero p { margin: 20px 0 28px; color: var(--text-muted); font-size: 17px; max-width: 520px; }
.hero-actions { display: flex; gap: 14px; }
.hero-mark {
  aspect-ratio: 1;
  display: flex;
}
.hero-mark img { width: 100%; height: 100%; object-fit: cover; border-radius: 16px; }

section { padding: 56px 0; }
section h2 { font-size: 28px; margin-bottom: 32px; }

.card-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 20px; }
.card {
  background: var(--card-bg);
  border: 1px solid var(--border-subtle);
  border-radius: 14px;
  padding: 28px;
}
.card.dark { background: var(--bg-dark); color: #fff; }
.card h3 { font-size: 17px; margin-bottom: 10px; }
.card p { color: var(--text-muted); font-size: 14px; }
.card.dark p { color: #a09a8f; }

.terminal {
  background: var(--bg-dark);
  border-radius: 12px;
  padding: 20px;
  color: #d8d2c5;
  font-family: "SF Mono", Menlo, monospace;
  font-size: 13px;
  line-height: 1.7;
}

.humanist { display: grid; grid-template-columns: 1fr 1fr; gap: 48px; align-items: center; }
.humanist ul { margin-top: 16px; color: var(--text-muted); }
.humanist li { margin-bottom: 8px; }

footer {
  background: var(--bg-dark);
  color: #d8d2c5;
  padding: 48px 0;
  margin-top: 40px;
}
.footer-grid { display: grid; grid-template-columns: 2fr 1fr 1fr 1fr; gap: 32px; }
footer h4 { font-size: 13px; text-transform: uppercase; letter-spacing: 0.05em; color: #8a8377; margin-bottom: 14px; }
footer a { display: block; color: #d8d2c5; font-size: 14px; margin-bottom: 8px; }
footer a:hover { color: #fff; }

.legal-body { max-width: 720px; padding: 48px 0 80px; }
.legal-body h1 { font-size: 32px; margin-bottom: 24px; }
.legal-body h2 { font-size: 20px; margin: 32px 0 12px; }
.legal-body p { color: var(--text-muted); margin-bottom: 14px; }

.pricing-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; max-width: 760px; margin: 0 auto; }
.price-card { background: var(--card-bg); border: 1px solid var(--border-subtle); border-radius: 16px; padding: 36px; }
.price-card.pro { border-color: var(--accent); }
.price-card h3 { font-size: 20px; margin-bottom: 8px; }
.price-was { color: var(--text-muted); text-decoration: line-through; font-size: 16px; margin-right: 8px; }
.price-now { font-size: 36px; font-weight: 700; }
.price-sub { color: var(--text-muted); font-size: 14px; margin: 4px 0 20px; }
.price-card ul { color: var(--text-muted); font-size: 14px; }
.price-card li { margin-bottom: 8px; }
```

- [ ] **Step 2: Copy the real logo mark asset**

The mark is a real, already-designed asset (a geometric "A" monogram, white on black, 1024x1024
PNG) — not generated. Copy it into place:

```bash
cp ~/Desktop/screen.png website/assets/logo-mark.png
```

Confirm it landed correctly:

Run: `file website/assets/logo-mark.png`
Expected: `website/assets/logo-mark.png: PNG image data, 1024 x 1024, 8-bit/color RGB, non-interlaced`

Note: this is a solid black square (no transparency, confirmed via the `file` output above showing
RGB, not RGBA) — it's styled as a self-contained rounded-square badge everywhere it's used
(`border-radius` applied via CSS in Step 1), not treated as a transparent icon that needs a
background color of its own.

- [ ] **Step 3: Write the Home page**

Create `website/index.html`:

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Aletheore — Evidence-grounded code audits</title>
  <meta name="description" content="A deterministic scanner that never states a claim it can't point back to a specific fact in your repo.">
  <link rel="stylesheet" href="styles.css">
</head>
<body>
  <div class="container">
    <nav>
      <div class="wordmark">
        <img src="assets/logo-mark.png" alt="" width="28" height="28">
        Aletheore
      </div>
      <div class="nav-links">
        <a href="pricing.html">Pricing</a>
        <a href="https://github.com/Aletheore/Aletheore">GitHub</a>
        <a class="btn btn-primary" href="https://github.com/Aletheore/Aletheore">Get Started</a>
      </div>
    </nav>

    <section class="hero">
      <div>
        <h1>I got tired of code-review tools that just guess. So I built one that <span class="accent">has to show its work.</span></h1>
        <p>Aletheore is a deterministic repository scanner — no LLM, fully unit-tested. Every claim it makes traces back to a specific field in real evidence: your dependency graph, your git history, your actual secrets and vulnerabilities.</p>
        <div class="hero-actions">
          <a class="btn btn-primary" href="https://github.com/Aletheore/Aletheore">Start Your First Audit →</a>
          <a class="btn btn-secondary" href="https://github.com/Aletheore/Aletheore">Read the Protocol</a>
        </div>
      </div>
      <div class="hero-mark">
        <img src="assets/logo-mark.png" alt="Aletheore A monogram">
      </div>
    </section>

    <section>
      <h2>Evidence-first architecture.</h2>
      <div class="card-grid">
        <div class="card">
          <h3>Traceability Matrix</h3>
          <p>Every downstream claim — the report, the query tools, the dashboard — reads from the same evidence.json and never states a fact it can't point back to a specific field.</p>
        </div>
        <div class="card">
          <h3>Zero-Config CLI</h3>
          <p><code>aletheore scan</code> runs the full deterministic scanner in one command — no LLM call, safe to run in CI, no configuration required.</p>
        </div>
        <div class="card dark">
          <h3>Visual Evidence Trails</h3>
          <p>A live local dashboard: dependency graph, an Obsidian-style cluster graph, trend charts over your repo's own scan history.</p>
        </div>
      </div>
    </section>

    <section class="humanist">
      <div>
        <h2>Humanist tools for a technical world.</h2>
        <p>Nothing leaves your machine when you run a local scan. No accounts, no tracking. Bring your own API key for the agentic audit report, or skip it entirely — the deterministic scan works standalone.</p>
        <ul>
          <li>Deterministic scan runs with zero API calls</li>
          <li>Open source, MIT licensed</li>
          <li>Local dashboard, local history, local evidence</li>
        </ul>
      </div>
      <div class="terminal">
        $ aletheore scan .<br>
        Scanning /Users/you/your-repo...<br>
        Detecting languages, frameworks, and build tools<br>
        Building module dependency graph<br>
        Checking dependency vulnerabilities (OSV.dev)<br>
        Evidence written to .aletheore/evidence.json<br>
        Snapshot saved to .aletheore/history/
      </div>
    </section>
  </div>

  <footer>
    <div class="container footer-grid">
      <div>
        <div class="wordmark" style="color: #fff;">
          <img src="assets/logo-mark.png" alt="" width="24" height="24">
          Aletheore
        </div>
        <p style="color: #8a8377; font-size: 14px; margin-top: 12px; max-width: 280px;">Evidence-grounded repository audits. Open source, local-first.</p>
      </div>
      <div>
        <h4>Product</h4>
        <a href="pricing.html">Pricing</a>
        <a href="https://github.com/Aletheore/Aletheore">GitHub</a>
      </div>
      <div>
        <h4>Community</h4>
        <a href="https://github.com/sponsors/ArihantK15">Sponsor</a>
        <a href="https://github.com/Aletheore/Aletheore/issues">Issues</a>
      </div>
      <div>
        <h4>Legal</h4>
        <a href="terms.html">Terms</a>
        <a href="privacy.html">Privacy</a>
        <a href="refund.html">Refund Policy</a>
      </div>
    </div>
  </footer>
</body>
</html>
```

- [ ] **Step 4: Verify no placeholder/fabricated content**

Run: `grep -iF -e "lorem ipsum" -e "12.4k" -e "TODO" -e "coming soon" website/index.html`
Expected: no output (no matches) — `-F` treats each `-e` pattern as a literal string, not a regex, so the `.` in "12.4k" matches only a literal period

Run: `grep -c "href=" website/index.html`
Expected: a number greater than 0 (confirms links exist); manually confirm each `href` in the file points to `pricing.html`, `terms.html`, `privacy.html`, `refund.html`, or a real `github.com/Aletheore/Aletheore` / `github.com/sponsors/ArihantK15` URL — no bare `#` links.

- [ ] **Step 5: Commit**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion
git add website/styles.css website/assets/logo-mark.png website/index.html
git commit -m "feat(website): shared styles, logo mark, Home page"
```

---

## Task 2: Pricing page

**Files:**
- Create: `website/pricing.html`

- [ ] **Step 1: Write the page**

Create `website/pricing.html`:

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Pricing — Aletheore</title>
  <link rel="stylesheet" href="styles.css">
</head>
<body>
  <div class="container">
    <nav>
      <a class="wordmark" href="index.html">
        <img src="assets/logo-mark.png" alt="" width="28" height="28">
        Aletheore
      </a>
      <div class="nav-links">
        <a href="pricing.html">Pricing</a>
        <a href="https://github.com/Aletheore/Aletheore">GitHub</a>
        <a class="btn btn-primary" href="https://github.com/Aletheore/Aletheore">Get Started</a>
      </div>
    </nav>

    <section style="text-align: center; padding-top: 40px;">
      <h2 style="font-size: 34px;">Simple, honest pricing.</h2>
      <p style="color: var(--text-muted); max-width: 480px; margin: 12px auto 40px;">The deterministic scanner is free forever. Pro adds managed audits, alerts, and monitoring for small teams.</p>

      <div class="pricing-grid">
        <div class="price-card">
          <h3>Free</h3>
          <div class="price-now">$0</div>
          <div class="price-sub">forever</div>
          <ul>
            <li>Deterministic scan (CLI, MCP, GitHub Action)</li>
            <li>Automatic PR comments on new secrets, vulnerabilities, layer violations</li>
            <li>Free hosted dashboard</li>
            <li>Local dashboard, history, and query tools</li>
          </ul>
        </div>
        <div class="price-card pro">
          <h3>Pro</h3>
          <div><span class="price-was">$15</span><span class="price-now">$12</span></div>
          <div class="price-sub">/month, up to 3 team members</div>
          <ul>
            <li>Everything in Free</li>
            <li>Managed audit runs (PR comment or CLI/MCP)</li>
            <li>Slack / Teams alerts on new findings</li>
            <li>Branch-protection Check Runs (secrets)</li>
            <li>Endpoint health monitoring + public status API</li>
            <li>+$4/mo per additional team member</li>
          </ul>
        </div>
      </div>
    </section>
  </div>

  <footer>
    <div class="container footer-grid">
      <div>
        <div class="wordmark" style="color: #fff;">
          <img src="assets/logo-mark.png" alt="" width="24" height="24">
          Aletheore
        </div>
      </div>
      <div>
        <h4>Product</h4>
        <a href="pricing.html">Pricing</a>
        <a href="https://github.com/Aletheore/Aletheore">GitHub</a>
      </div>
      <div>
        <h4>Community</h4>
        <a href="https://github.com/sponsors/ArihantK15">Sponsor</a>
      </div>
      <div>
        <h4>Legal</h4>
        <a href="terms.html">Terms</a>
        <a href="privacy.html">Privacy</a>
        <a href="refund.html">Refund Policy</a>
      </div>
    </div>
  </footer>
</body>
</html>
```

- [ ] **Step 2: Verify the exact pricing figures are present**

Run: `grep -oE '\$15|\$12|\$4|3 team members' website/pricing.html`
Expected:
```
$15
$12
$4
3 team members
```
(order may vary; all four must appear — `-E` for portable alternation across both GNU and BSD/macOS grep)

- [ ] **Step 3: Commit**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion
git add website/pricing.html
git commit -m "feat(website): Pricing page"
```

---

## Task 3: Legal pages (Terms, Privacy, Refund)

**Files:**
- Create: `website/terms.html`
- Create: `website/privacy.html`
- Create: `website/refund.html`

- [ ] **Step 1: Write Terms of Service**

Create `website/terms.html`:

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Terms of Service — Aletheore</title>
  <link rel="stylesheet" href="styles.css">
</head>
<body>
  <div class="container">
    <nav>
      <a class="wordmark" href="index.html">
        <img src="assets/logo-mark.png" alt="" width="28" height="28">
        Aletheore
      </a>
      <div class="nav-links">
        <a href="pricing.html">Pricing</a>
        <a href="https://github.com/Aletheore/Aletheore">GitHub</a>
      </div>
    </nav>
    <div class="legal-body">
      <h1>Terms of Service</h1>
      <p>Last updated: July 2026</p>

      <h2>What Aletheore is</h2>
      <p>Aletheore is a repository audit tool: a free, open-source, local-first CLI and a paid GitHub App tier (managed audits, Slack/Teams alerts, branch-protection Check Runs, endpoint health monitoring). The free tier is available under the terms of its open-source license in the project repository.</p>

      <h2>The paid subscription</h2>
      <p>The Pro plan is $12/month for up to 3 team members, with additional members billed at $4/month each. Subscriptions are billed and processed by our payment provider (Lemon Squeezy, acting as merchant of record). By subscribing, you agree to Lemon Squeezy's own terms of service in addition to these.</p>

      <h2>Acceptable use</h2>
      <p>You may not use Aletheore to scan repositories you do not have the right to scan, or to circumvent security measures of systems you do not own or have explicit permission to test.</p>

      <h2>No warranty</h2>
      <p>Aletheore is provided as-is. The deterministic scanner is unit-tested and evidence-grounded, but no software is guaranteed to catch every issue in every repository.</p>

      <h2>Contact</h2>
      <p>Questions about these terms: <a href="mailto:arihantkaul@outlook.com" style="color: var(--accent);">arihantkaul@outlook.com</a></p>
    </div>
  </div>
</body>
</html>
```

- [ ] **Step 2: Write Privacy Policy**

Create `website/privacy.html`:

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Privacy Policy — Aletheore</title>
  <link rel="stylesheet" href="styles.css">
</head>
<body>
  <div class="container">
    <nav>
      <a class="wordmark" href="index.html">
        <img src="assets/logo-mark.png" alt="" width="28" height="28">
        Aletheore
      </a>
      <div class="nav-links">
        <a href="pricing.html">Pricing</a>
        <a href="https://github.com/Aletheore/Aletheore">GitHub</a>
      </div>
    </nav>
    <div class="legal-body">
      <h1>Privacy Policy</h1>
      <p>Last updated: July 2026</p>

      <h2>The free CLI</h2>
      <p>Running <code>aletheore scan</code> or <code>aletheore audit</code> locally sends nothing to Aletheore's servers. Evidence is written to your own machine. If you use the agentic <code>audit</code> command, evidence (not source code) is sent to whichever LLM provider you configure, using your own API key.</p>

      <h2>The GitHub App (Pro tier)</h2>
      <p>Installing the GitHub App is a different trust boundary. Source code is transiently cloned to run a scan, then immediately discarded — only derived evidence (secrets findings, dependency graphs, etc., never raw source) is stored. Managed audit runs send evidence (not source code) to our LLM provider using our own shared key.</p>

      <h2>What we store</h2>
      <p>For paid installations: your GitHub account/organization identity, evidence snapshots from scans, and (if configured) a Slack/Teams webhook URL and health-check monitoring settings. We do not sell or share this data with third parties beyond the service providers necessary to run Aletheore (our LLM provider for managed audits, our payment provider for billing).</p>

      <h2>Contact</h2>
      <p>Questions about this policy or a data deletion request: <a href="mailto:arihantkaul@outlook.com" style="color: var(--accent);">arihantkaul@outlook.com</a></p>
    </div>
  </div>
</body>
</html>
```

- [ ] **Step 3: Write Refund Policy**

Create `website/refund.html`:

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Refund Policy — Aletheore</title>
  <link rel="stylesheet" href="styles.css">
</head>
<body>
  <div class="container">
    <nav>
      <a class="wordmark" href="index.html">
        <img src="assets/logo-mark.png" alt="" width="28" height="28">
        Aletheore
      </a>
      <div class="nav-links">
        <a href="pricing.html">Pricing</a>
        <a href="https://github.com/Aletheore/Aletheore">GitHub</a>
      </div>
    </nav>
    <div class="legal-body">
      <h1>Refund Policy</h1>
      <p>Last updated: July 2026</p>

      <h2>14-day refund window</h2>
      <p>If you're not satisfied with the Pro plan within the first 14 days of a new subscription, email us for a full refund, no questions asked.</p>

      <h2>After 14 days</h2>
      <p>Subscriptions can be cancelled at any time and will remain active until the end of the current billing period. We don't offer prorated refunds for partial months after the initial 14-day window, since the plan gives you continued access through the period you already paid for.</p>

      <h2>How to request one</h2>
      <p>Email <a href="mailto:arihantkaul@outlook.com" style="color: var(--accent);">arihantkaul@outlook.com</a> with your GitHub organization/account name. Refunds are processed through Lemon Squeezy back to your original payment method.</p>
    </div>
  </div>
</body>
</html>
```

- [ ] **Step 4: Verify all three pages exist and link correctly**

Run: `for f in terms privacy refund; do test -f website/$f.html && echo "$f.html exists"; done`
Expected:
```
terms.html exists
privacy.html exists
refund.html exists
```

Run: `grep -l "arihantkaul@outlook.com" website/terms.html website/privacy.html website/refund.html`
Expected: all three filenames printed (confirms real contact info is present in each, not omitted)

- [ ] **Step 5: Commit**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion
git add website/terms.html website/privacy.html website/refund.html
git commit -m "feat(website): Terms, Privacy, and Refund Policy pages"
```

---

## Task 4: Vercel deploy config + full link check

**Files:**
- Create: `website/vercel.json`

- [ ] **Step 1: Write the deploy config**

Create `website/vercel.json`:

```json
{
  "cleanUrls": true,
  "trailingSlash": false
}
```

`cleanUrls: true` serves `pricing.html` at `/pricing` (no `.html` in the URL) with zero build step — Vercel's static file server handles this natively, nothing to configure beyond this file.

- [ ] **Step 2: Full link and content check across all 5 pages**

Run:
```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion/website
grep -LiF -e "lorem ipsum" -e "12.4k" -e "450+" -e "TODO" -e "coming soon" *.html
```
Expected: all 5 filenames printed (`-L` prints only files with zero matches; `-F` treats each `-e` pattern as a literal string so "12.4k" and "450+" aren't misread as regexes)

Run:
```bash
grep -c 'href="[a-z]' *.html
```
Expected: every file shows a count greater than 0 (no file has zero real links)

Run:
```bash
grep -rn 'href="#"' *.html
```
Expected: no output (no dead placeholder links anywhere)

- [ ] **Step 3: Commit**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion
git add website/vercel.json
git commit -m "feat(website): Vercel static deploy config"
```

Deployment itself (pointing `aletheore.com` at this via Vercel, same as the earlier GTM discussion) happens as part of the combined live-deployment pass already planned for the GitHub App work, alongside submitting the Lemon Squeezy merchant application against the now-live site.
