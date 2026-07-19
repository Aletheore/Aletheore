# Aletheore Marketing Website Content Update Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. This is a content/copy update, not TDD software work - there are no unit tests to write; each task's "verify" step is a manual read-through instead.

**Goal:** Bring `website/` (the static marketing site, served from `aletheore.com` via Vercel) up to date with everything real that has shipped, and fix stale post-rename references left over from the `evidence.json` → `air.json` rename. Nothing in this plan is aspirational copy - every claim below is grounded in code verified during this session (see the "Evidence" note on each task).

**Architecture:** Plain static HTML/CSS/JS (no build step, no framework). All edits are direct edits to `website/index.html`, `website/pricing.html`, and `website/showcase-data.js`. No new pages needed.

## Global Constraints

- Do not invent features. Every bullet/claim added must trace to something verified in this session (cited per-task below). If in doubt, leave it out rather than guess.
- Match the existing site's voice: plain, concrete, first-person-founder tone (see the existing hero copy) - not generic SaaS marketing language.
- The GitHub Marketplace listing is **submitted and under review, not yet approved/live** as of 2026-07-19. Any copy referencing it must say "submitted for review" / "coming soon to GitHub Marketplace" - never imply it's already installable from Marketplace, since that would be false until GitHub approves it.

---

### Task 1: Fix stale post-rename `evidence.json`/`evidence.toon` references

**Files:**
- Modify: `website/index.html`
- Modify: `website/showcase-data.js`

**Evidence:** This repo's runtime was renamed from `.aletheore/evidence.json`/`.aletheore/evidence.toon` to `.aletheore/air.json`/`.aletheore/air.toon` earlier this session (the "AIR" rename, verified end-to-end via a live audit run). The website was never updated and still shows the old filenames in six places - this is factually wrong today: if a user runs `aletheore scan` and looks for `.aletheore/evidence.json` as the homepage's own example output tells them to, the file won't exist.

- [ ] **Step 1: Fix `website/index.html` line 42**

Change:
```
writes what it found to <code>evidence.json</code>
```
to:
```
writes what it found to <code>air.json</code>
```

- [ ] **Step 2: Fix `website/index.html` line 47**

Change:
```
<code>aletheore scan</code> runs once and writes two files: <code>evidence.json</code> (the canonical record - languages, dependency graph, clusters, git activity, secrets, licenses, vulnerabilities, API endpoints) and <code>evidence.toon</code>, a <a href="https://toonformat.dev">TOON</a>-encoded copy of the same data
```
to:
```
<code>aletheore scan</code> runs once and writes two files: <code>air.json</code> (the canonical record - languages, dependency graph, clusters, git activity, secrets, licenses, vulnerabilities, API endpoints) and <code>air.toon</code>, a <a href="https://toonformat.dev">TOON</a>-encoded copy of the same data
```

- [ ] **Step 3: Fix `website/index.html` line 52**

Change:
```
Every capability below runs from the same <code>evidence.json</code>
```
to:
```
Every capability below runs from the same <code>air.json</code>
```

- [ ] **Step 4: Fix `website/index.html` line 116**

Change:
```
Measured against Aletheore's own <code>evidence.json</code>/<code>evidence.toon</code> pair from a real self-scan
```
to:
```
Measured against Aletheore's own <code>air.json</code>/<code>air.toon</code> pair from a real self-scan
```

- [ ] **Step 5: Fix `website/index.html` line 147** (the terminal demo output block)

Change:
```
Evidence written to .aletheore/evidence.json<br>
```
to:
```
Evidence written to .aletheore/air.json<br>
```

- [ ] **Step 6: Fix `website/showcase-data.js` line 49**

Change:
```js
"source": "Aletheore's own evidence.json/evidence.toon (self-scan, 2026-07-18)",
```
to:
```js
"source": "Aletheore's own air.json/air.toon (self-scan, 2026-07-18)",
```

- [ ] **Step 7: Verify no stale references remain**

Run: `grep -rn "evidence\.json\|evidence\.toon" website/*.html website/*.js`
Expected: no output (zero matches).

- [ ] **Step 8: Commit**

```bash
git add website/index.html website/showcase-data.js
git commit -m "fix: website still referenced pre-rename evidence.json/evidence.toon filenames"
```

---

### Task 2: Fix stale "8 languages" claim to the real count of 11

**Files:**
- Modify: `website/index.html`

**Evidence:** Verified this session by reading `prototype/pyproject.toml` and `prototype/aletheore/scanner/graph.py`'s `EXTENSION` map directly: the scanner has real tree-sitter parsing wired in for **11** languages - Python, JavaScript, TypeScript, Go, Rust, Java, Ruby, PHP, C, C++, C#. The site currently undersells this by three languages.

- [ ] **Step 1: Fix `website/index.html` line 57**

Change:
```
Secrets, dependency vulnerabilities and licenses across 8 languages, API endpoint mapping, architecture clustering, and full git history
```
to:
```
Secrets, dependency vulnerabilities and licenses across 11 languages (Python, JavaScript, TypeScript, Go, Rust, Java, Ruby, PHP, C, C++, C#), API endpoint mapping, architecture clustering, and full git history
```

- [ ] **Step 2: Commit**

```bash
git add website/index.html
git commit -m "fix: update stale '8 languages' claim to the real count of 11"
```

---

### Task 3: Add missing deterministic-feature cards to the homepage feature grid

**Files:**
- Modify: `website/index.html`

**Evidence:** These are real, shipped, tested scanner capabilities that exist in `prototype/aletheore/` but have zero representation anywhere on the site today - confirmed by grep against the current homepage content (no mention of "dead code", "hotspot", "database", or the newly-shipped infrastructure/environment-variable detection).
- Dead code detection: `prototype/aletheore/dead_code.py`.
- Git hotspots / history intelligence: part of `git_intel` evidence, surfaced via `aletheore query hotspots` and the dashboard.
- Database model detection: `prototype/aletheore/scanner/detect.py`'s `detect_database()` (ORM frameworks, migration directories, schema files) - shipped this session, independently verified live against this repo (found 4 real migration files).
- Infrastructure/environment detection: `detect_infrastructure()`/`detect_environment_variables()` in the same file - shipped this session, independently verified live (found the real `github-app/docker-compose.yml` with 6 services, 11 real env var names with zero values captured).

**Context:** The feature grid is a repeating `<article class="feature-card">` pattern - see `website/index.html` lines 55-80 for the existing five cards. Add these three new cards in the same `<div class="feature-grid">` block, using the exact same markup shape (a `$ aletheore ...` command line, an `<h3>` title, one descriptive `<p>`).

- [ ] **Step 1: Add three new feature cards**

Insert these three `<article class="feature-card">` blocks into the `<div class="feature-grid">` in `website/index.html` (after the existing "Deterministic scan" card is a natural place, but exact position doesn't matter - keep them together):

```html
        <article class="feature-card">
          <p class="feature-cmd">$ aletheore query dead-code</p>
          <h3>Dead code detection</h3>
          <p>Flags unreachable modules and functions from real call-graph analysis - not a guess, a traced absence of any incoming edge from a known entry point.</p>
        </article>
        <article class="feature-card">
          <p class="feature-cmd">$ aletheore query hotspots</p>
          <h3>Git hotspot intelligence</h3>
          <p>Surfaces the files that change most and who actually owns them, straight from git log - the parts of your codebase most likely to need a careful review.</p>
        </article>
        <article class="feature-card">
          <p class="feature-cmd">$ aletheore query database</p>
          <h3>Database &amp; infrastructure detection</h3>
          <p>Finds ORM frameworks, migration directories, schema files, Docker Compose services, Kubernetes manifests, and declared environment variable names (never values) - all from static analysis, nothing executed.</p>
        </article>
```

- [ ] **Step 2: Verify**

Open `website/index.html` in a browser (or `file://` path) and confirm the new cards render in the feature grid with the same visual styling as the existing five (no unclosed tags, no missing class).

- [ ] **Step 3: Commit**

```bash
git add website/index.html
git commit -m "feat: add dead code, git hotspot, and database/infra detection cards to homepage"
```

---

### Task 4: Add a "Hosted GitHub App" section introducing the paid product

**Files:**
- Modify: `website/index.html`

**Evidence:** The homepage currently only mentions a "GitHub Action" card (the older composite Action for PR diffs) and has zero mention of the separate, newer GitHub App product (webhooks, managed audits, Flash review, Slack/Teams alerts, check runs, health monitoring) - confirmed via grep, zero hits for "GitHub App" or "Marketplace" anywhere on the homepage. This is the single biggest content gap: a visitor reading the homepage today has no way to discover the hosted product exists at all.

**Context on wording:** As of 2026-07-19 the GitHub Marketplace listing ("Aletheore for GitHub") has been submitted and is under GitHub's review - it is not yet publicly installable from Marketplace. Do not link a `github.com/marketplace/...` URL yet if it 404s; use a "Coming soon to GitHub Marketplace" framing and link to the pricing page instead, where the Free/Pro plans are already described.

- [ ] **Step 1: Add a new section**

Insert a new `<section>` in `website/index.html`, right after the closing `</section>` of the `id="features"` block and before `<section id="proof-zone"` (i.e., between the deterministic-scanner feature grid and the proof-zone showcase section):

```html
    <section id="github-app">
      <h2>Also available as a hosted GitHub App.</h2>
      <p class="section-intro">Everything above runs locally with the CLI. If you'd rather it just run on every pull request without you doing anything, the Aletheore GitHub App posts the same evidence-grounded findings as PR comments and branch-protection check runs automatically - free on every plan.</p>
      <div class="feature-grid">
        <article class="feature-card">
          <p class="feature-cmd">on: pull_request</p>
          <h3>Automatic PR comments</h3>
          <p>Every pull request gets a comment listing new secrets, vulnerabilities, and license issues - free, no setup beyond installing the App.</p>
        </article>
        <article class="feature-card">
          <p class="feature-cmd">on: pull_request</p>
          <h3>Branch-protection check runs</h3>
          <p>A check run fails the pull request when a new secret is detected, so it can gate merges the same way any other required check does.</p>
        </article>
        <article class="feature-card">
          <p class="feature-cmd">/aletheore audit</p>
          <h3>AI-powered managed audits (Pro)</h3>
          <p>Comment <code>/aletheore audit</code> on any pull request for a full narrative audit report - security, architecture, and investor-style perspectives, grounded in the same deterministic evidence, no API key required.</p>
        </article>
        <article class="feature-card">
          <p class="feature-cmd">on: pull_request</p>
          <h3>Automatic Flash reviews (Pro)</h3>
          <p>Every push to an open pull request gets a fast, citation-constrained review of just the new diff - every finding names an exact file and line, or it isn't reported at all.</p>
        </article>
        <article class="feature-card">
          <p class="feature-cmd">Slack / Teams</p>
          <h3>Alerts on new findings (Pro)</h3>
          <p>Point Aletheore at a Slack or Microsoft Teams webhook and get notified the moment a new secret or vulnerability lands on a pull request.</p>
        </article>
        <article class="feature-card">
          <p class="feature-cmd">GET /v1/health/...</p>
          <h3>Endpoint health monitoring (Pro)</h3>
          <p>Aletheore maps your API endpoints from source, then checks them live on a schedule and alerts on reachability or latency regressions.</p>
        </article>
      </div>
      <p class="section-intro">
        <a href="https://github.com/marketplace" rel="noopener">Coming soon to GitHub Marketplace</a> - the listing is submitted and under review. In the meantime, see the <a href="pricing.html">Free and Pro plans</a>.
      </p>
    </section>
```

- [ ] **Step 2: Verify**

Read through the new section for accuracy against this task's Evidence note - confirm no claim here goes beyond what's listed (e.g. don't add "SOC 2 compliant" or similar unverified claims).

- [ ] **Step 3: Commit**

```bash
git add website/index.html
git commit -m "feat: add homepage section introducing the hosted GitHub App"
```

---

### Task 5: Add Flash review to the pricing page's Pro plan feature list

**Files:**
- Modify: `website/pricing.html`

**Evidence:** `website/pricing.html`'s Pro plan already lists "Managed audit runs from PR comments, CLI, or MCP", "Slack / Teams alerts on new findings", "Branch-protection Check Runs for new secrets", and "Endpoint health monitoring and a public status API" - but has zero mention of Flash review, a real, shipped, tested Pro-tier feature (`github-app/scan_worker/flash_review.py`, wired into the `pull_request` webhook alongside the free diff comment).

- [ ] **Step 1: Find the Pro plan's feature list**

Open `website/pricing.html` and locate the `<h3>Pro</h3>` plan's feature list (a `<ul>` near the existing "Managed audit runs from PR comments..." line).

- [ ] **Step 2: Add the missing bullet**

Add a new `<li>` to that list, in the same style as its neighbors:
```html
<li>Automatic Flash reviews on every push to an open pull request</li>
```

- [ ] **Step 3: Commit**

```bash
git add website/pricing.html
git commit -m "feat: add Flash review to the pricing page's Pro feature list"
```

---

### Task 6: Add the GitHub Marketplace status link to the pricing page

**Files:**
- Modify: `website/pricing.html`

**Context:** Same "submitted, not yet approved" constraint as Task 4 - phrase this as pending, not live.

- [ ] **Step 1: Add a status line near the Pro plan's call-to-action**

Add, near wherever the Pro plan's existing sign-up/CTA button or link is:
```html
<p class="plan-note">Also available as a GitHub App - <a href="https://github.com/marketplace" rel="noopener">listing submitted, currently under GitHub's review</a>.</p>
```

- [ ] **Step 2: Commit**

```bash
git add website/pricing.html
git commit -m "feat: note GitHub Marketplace listing status on pricing page"
```

---

### Task 7: Final read-through

- [ ] **Step 1: Open every changed page in a browser and read top to bottom**

Check: no broken HTML (unclosed tags), no remaining stale `evidence.json`/`evidence.toon`/`8 languages` references anywhere, the new GitHub App section reads naturally in context, no claim beyond what's cited in this plan's Evidence notes.

- [ ] **Step 2: Grep for the two known stale terms once more, repo-wide**

Run: `grep -rn "evidence\.json\|evidence\.toon\|8 languages" website/`
Expected: zero matches.

- [ ] **Step 3: Deploy**

This site deploys via Vercel on push to the branch it's connected to (confirmed this session via the Vercel bot comments appearing on PRs against this repo) - a normal `git push` to the relevant branch is sufficient, no separate deploy step needed. Confirm the live site at `aletheore.com` reflects the changes after Vercel's build completes.
