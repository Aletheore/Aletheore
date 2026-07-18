# Aletheore Full Marketing Website Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the minimal placeholder homepage with real WHY/HOW narrative, a live showcase of three real repos actually scanned by Aletheore (Django, Express, Kubernetes), an interactive TOON before/after demo with real measured numbers, and Motion-driven animation — all still static HTML/CSS/JS with zero build step.

**Architecture:** A one-time offline data-generation step produces real `website/showcase-data.js` from actually running `aletheore scan` against three pinned-commit clones. Motion is vendored locally as a single self-contained UMD file (no CDN dependency, no bundler). `index.html` is rebuilt with a warm narrative zone followed by a dark "PROOF" zone (reusing the existing `--bg-dark` design token) containing the showcase and TOON demo, both driven by the real data file. Legal/pricing pages are reskinned only, content unchanged.

**Tech Stack:** Plain HTML/CSS/JS, Motion v12.42.2 (vendored UMD bundle, global `Motion` object — `Motion.animate`, `Motion.inView`, `Motion.scroll`), the `aletheore` CLI itself (already installed from source per this repo's own README) as the data-generation tool.

## Global Constraints

- **No build step, no framework, no bundler.** Explicitly decided to avoid the Vercel build-step failure class this project has hit twice already (Procta's Puppeteer prerender; this site's own Vercel Git-import defaulting to the wrong Root Directory).
- **No fabricated numbers anywhere.** Every statistic on the site must trace to an actual value in `website/showcase-data.js`, which itself must trace to real `aletheore scan` output against a pinned commit SHA.
- **Motion is vendored locally**, not loaded from a CDN at runtime — `website/vendor/motion.js`, the real self-contained UMD bundle from `https://cdn.jsdelivr.net/npm/motion@12.42.2/dist/motion.js` (confirmed: 139KB, zero external imports, attaches to a global `Motion` object).
- **Pinned commit SHAs** (confirmed real, current HEAD of each repo's default branch as of 2026-07-18):
  - Django: `3d34265d5d1b83fee5df3c1b6f55087b1a6a1ded`
  - Express: `ae6dd37680e3a00618d6c8a3e522f0ee4eeba1a4`
  - Kubernetes: `bd1a1b897340ef91595c36439fed49b9072f8b1d`
- **Kubernetes is cloned shallow (`--depth 1`)** — confirmed practical (385MB, ~30s, vs. 1.49GB for full history) since Kubernetes's role in the showcase is scale/architecture at breadth, not git-history depth (that's Django's role, cloned with full history).
- **Known real limitation to represent honestly, not paper over**: Aletheore's dependency-vulnerability/license checker only reads a root `requirements.txt` (pip) or `package.json` (npm) — it does not parse `go.mod`. Django has no root `requirements.txt` (only `tests/requirements/*.txt`, which the checker doesn't read) and Kubernetes is Go. This means Django and Kubernetes will almost certainly show **zero** dependency/license findings in their real scan output — not a demo failure, a real and honestly-stated boundary of the tool today. Express (real `package.json` with real semver-range dependencies) is the repo expected to show real dependency/license findings. Task 4's copy must state whichever is actually true for each repo based on Task 1's real output — do not assume specific counts before Task 1 runs.
- The TOON demo's numbers are already measured and fixed: **51,137 → 27,892 tokens (45.5% fewer)**, **217,413 → 96,694 bytes (55.5% smaller)**, measured 2026-07-18 with OpenAI's `o200k_base` tokenizer against Aletheore's own `prototype/.aletheore/evidence.json`/`evidence.toon` pair (Aletheore's own dogfooded self-scan, not one of the three showcase repos).

---

## Task 1: Generate real showcase data

**Files:**
- Create: `website/showcase-data.js`
- Create: `scripts/generate-showcase-data.sh` (documents the exact reproducible process)

**Interfaces:**
- Produces: `website/showcase-data.js` — a plain JS file assigning a global `const SHOWCASE = {...}` object (loaded via `<script src="showcase-data.js">` before `script.js`, no module system needed), with real values from Task 1's actual scans. Later tasks (4, 6, 8) read from this object's real fields.

- [ ] **Step 1: Write the data-generation script**

Create `scripts/generate-showcase-data.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

WORKDIR=$(mktemp -d)
trap 'rm -rf "$WORKDIR"' EXIT

echo "=== Django (full clone, pinned commit) ==="
git clone https://github.com/django/django.git "$WORKDIR/django"
git -C "$WORKDIR/django" checkout 3d34265d5d1b83fee5df3c1b6f55087b1a6a1ded
time aletheore scan "$WORKDIR/django"
cp "$WORKDIR/django/.aletheore/evidence.json" "$WORKDIR/django-evidence.json"

echo "=== Express (full clone, pinned commit) ==="
git clone https://github.com/expressjs/express.git "$WORKDIR/express"
git -C "$WORKDIR/express" checkout ae6dd37680e3a00618d6c8a3e522f0ee4eeba1a4
time aletheore scan "$WORKDIR/express"
cp "$WORKDIR/express/.aletheore/evidence.json" "$WORKDIR/express-evidence.json"
cp "$WORKDIR/express/.aletheore/evidence.toon" "$WORKDIR/express-evidence.toon"

echo "=== Kubernetes (shallow clone, pinned commit) ==="
git clone --depth 1 https://github.com/kubernetes/kubernetes.git "$WORKDIR/kubernetes"
KUBERNETES_SCAN_START=$(date +%s)
aletheore scan "$WORKDIR/kubernetes"
KUBERNETES_SCAN_END=$(date +%s)
echo "kubernetes scan seconds: $((KUBERNETES_SCAN_END - KUBERNETES_SCAN_START))" | tee "$WORKDIR/kubernetes-scan-seconds.txt"
cp "$WORKDIR/kubernetes/.aletheore/evidence.json" "$WORKDIR/kubernetes-evidence.json"

python3 "$(dirname "$0")/extract-showcase-data.py" \
  --django "$WORKDIR/django-evidence.json" \
  --express "$WORKDIR/express-evidence.json" \
  --express-toon "$WORKDIR/express-evidence.toon" \
  --kubernetes "$WORKDIR/kubernetes-evidence.json" \
  --kubernetes-scan-seconds "$WORKDIR/kubernetes-scan-seconds.txt" \
  --out "$(dirname "$0")/../website/showcase-data.js"

echo "Wrote website/showcase-data.js"
```

- [ ] **Step 2: Write the extraction script**

Create `scripts/extract-showcase-data.py`:

```python
#!/usr/bin/env python3
"""Extracts a curated, real subset of aletheore scan output into website/showcase-data.js.

Every field here traces to an actual field in a real evidence.json produced by
`aletheore scan` against a pinned commit of the named repo - see
scripts/generate-showcase-data.sh for the exact reproducible process.
"""
import argparse
import json


def summarize(evidence_path: str) -> dict:
    with open(evidence_path) as f:
        data = json.load(f)
    return {
        "moduleCount": len(data["repository"]["modules"]),
        "dependencyEdgeCount": len(data["repository"]["dependency_graph"]["edges"]),
        "licenseFindingsCount": len(data["security"]["dependency_licenses"]["findings"]),
        "licenseChecked": data["security"]["dependency_licenses"]["checked"],
        "licenseReason": data["security"]["dependency_licenses"].get("reason"),
        "vulnerabilityFindingsCount": len(data["security"]["dependency_vulnerabilities"]["findings"]),
        "vulnerabilityChecked": data["security"]["dependency_vulnerabilities"]["checked"],
        "vulnerabilityReason": data["security"]["dependency_vulnerabilities"].get("reason"),
        "totalCommits": data["git"].get("total_commits"),
        "clusterCount": len(data["architecture"]["clusters"]),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--django", required=True)
    parser.add_argument("--express", required=True)
    parser.add_argument("--express-toon", required=True)
    parser.add_argument("--kubernetes", required=True)
    parser.add_argument("--kubernetes-scan-seconds", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    django = summarize(args.django)
    express = summarize(args.express)
    kubernetes = summarize(args.kubernetes)

    with open(args.express_toon) as f:
        express_toon_bytes = len(f.read())
    with open(args.express) as f:
        express_json_bytes = len(f.read())

    with open(args.kubernetes_scan_seconds) as f:
        kubernetes_scan_seconds = int(f.read().strip().split(": ")[1])

    showcase = {
        "django": {**django, "commitSha": "3d34265d5d1b83fee5df3c1b6f55087b1a6a1ded"},
        "express": {
            **express,
            "commitSha": "ae6dd37680e3a00618d6c8a3e522f0ee4eeba1a4",
            "evidenceJsonBytes": express_json_bytes,
            "evidenceToonBytes": express_toon_bytes,
        },
        "kubernetes": {
            **kubernetes,
            "commitSha": "bd1a1b897340ef91595c36439fed49b9072f8b1d",
            "scanSeconds": kubernetes_scan_seconds,
            "clonedShallow": True,
        },
        "toonDemo": {
            "source": "Aletheore's own evidence.json/evidence.toon (self-scan, 2026-07-18)",
            "jsonTokens": 51137,
            "toonTokens": 27892,
            "tokenReductionPercent": 45.5,
            "jsonBytes": 217413,
            "toonBytes": 96694,
            "byteReductionPercent": 55.5,
        },
    }

    with open(args.out, "w") as f:
        f.write("// Generated by scripts/generate-showcase-data.sh - do not hand-edit.\n")
        f.write("// Every value here traces to a real `aletheore scan` run against the\n")
        f.write("// pinned commit SHA recorded alongside it.\n")
        f.write(f"const SHOWCASE = {json.dumps(showcase, indent=2)};\n")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run the generation script for real**

Run: `chmod +x scripts/generate-showcase-data.sh && ./scripts/generate-showcase-data.sh`

Expected: three real clones happen (Django and Express with full history, Kubernetes shallow), `aletheore scan` runs against each printing its normal phase-by-phase progress, and `website/showcase-data.js` is written. This will take real time - Django and Express are moderate, Kubernetes is the deliberate stress test and may take several minutes depending on network and the license/vulnerability check's per-dependency requests. Let it run to completion rather than interrupting.

- [ ] **Step 4: Verify the real output**

Run: `cat website/showcase-data.js`
Expected: a `const SHOWCASE = {...}` object with real, non-placeholder numbers for `django`, `express`, `kubernetes`, and `toonDemo`. Confirm `django.licenseFindingsCount` and `kubernetes.licenseFindingsCount` are `0` (or `licenseChecked: false` with a real `licenseReason`) per the Global Constraints note above - if either shows a large nonzero count, stop and re-read `security.dependency_licenses` in that repo's raw evidence.json before proceeding, since that would mean this plan's assumption about the checker's real behavior was wrong and Task 4's copy needs to match whatever is actually true instead.

- [ ] **Step 5: Commit**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion
git add scripts/generate-showcase-data.sh scripts/extract-showcase-data.py website/showcase-data.js
git commit -m "feat(website): generate real showcase data from Django/Express/Kubernetes scans"
```

---

## Task 2: Vendor Motion locally

**Files:**
- Create: `website/vendor/motion.js`

**Interfaces:**
- Produces: a global `Motion` object (`Motion.animate`, `Motion.inView`, `Motion.scroll`) available to any script loaded after this one via a plain `<script src="vendor/motion.js"></script>` tag - no module system, no bundler.

- [ ] **Step 1: Download the real, pinned, self-contained bundle**

Run:
```bash
mkdir -p website/vendor
curl -sL "https://cdn.jsdelivr.net/npm/motion@12.42.2/dist/motion.js" -o website/vendor/motion.js
wc -c website/vendor/motion.js
```
Expected: a file of roughly 139KB.

- [ ] **Step 2: Verify it's genuinely self-contained**

Run: `grep -oE 'from\s*["'"'"'][a-z@/.-]+["'"'"']' website/vendor/motion.js`
Expected: no output (or only matches inside string literals unrelated to ES module imports) - confirming no runtime dependency on jsdelivr or any other external host once vendored.

Run: `grep -c "globalThis.Motion" website/vendor/motion.js`
Expected: `1` or more, confirming it attaches to a global `Motion` object as expected.

- [ ] **Step 3: Commit**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion
git add website/vendor/motion.js
git commit -m "feat(website): vendor Motion 12.42.2 locally (self-contained UMD bundle, no CDN dependency)"
```

---

## Task 3: Rebuild the warm zone (hero, Why, How)

**Files:**
- Modify: `website/index.html`

**Interfaces:**
- Consumes: nothing from earlier tasks (pure content, no data dependency).
- Produces: the page's warm-zone markup, which Task 4 appends the dark PROOF zone after.

- [ ] **Step 1: Replace the current card-grid section with real Why/How narrative**

In `website/index.html`, replace the entire `<section>...Evidence-first architecture...</section>` block (the card-grid one) with:

```html
    <section id="why">
      <h2>Why deterministic-first.</h2>
      <p class="section-intro">No LLM ever runs in the scan path. Aletheore reads your repository with tree-sitter and git log, writes what it found to <code>evidence.json</code>, and every other command - <code>query</code>, <code>diff</code>, the dashboard, the MCP server, even <code>audit</code>'s written report - reads from that same file. Nothing downstream states a claim it can't point back to a specific field in it.</p>
    </section>

    <section id="how">
      <h2>How it actually works.</h2>
      <p class="section-intro"><code>aletheore scan</code> runs once and writes two files: <code>evidence.json</code> (the canonical record - languages, dependency graph, clusters, git activity, secrets, licenses, vulnerabilities, API endpoints) and <code>evidence.toon</code>, a <a href="https://toonformat.dev">TOON</a>-encoded copy of the same data specifically for the MCP server's tool results and the coding-agent adapter <code>audit</code> uses - because that's where token cost is actually paid, not in the file Aletheore itself keeps on disk.</p>
    </section>
```

- [ ] **Step 2: Run to verify no regressions**

Run: `grep -c "id=\"why\"\|id=\"how\"" website/index.html`
Expected: `2`

- [ ] **Step 3: Commit**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion
git add website/index.html
git commit -m "feat(website): replace generic feature cards with real Why/How narrative"
```

---

## Task 4: Build the dark PROOF zone (live showcase + TOON demo)

**Files:**
- Modify: `website/index.html`

**Interfaces:**
- Consumes: `website/showcase-data.js`'s real `SHOWCASE.django`, `SHOWCASE.express`, `SHOWCASE.kubernetes`, `SHOWCASE.toonDemo` fields (Task 1).
- Produces: DOM elements with specific IDs/classes (`#proof-zone`, `.showcase-card[data-repo=...]`, `#toon-token-count`) that Task 6's Motion script targets.

- [ ] **Step 1: Add the showcase-data script tag**

In `website/index.html`, add before the closing `</body>` (this must load before `script.js` in Task 6):

```html
  <script src="showcase-data.js"></script>
```

- [ ] **Step 2: Add the PROOF zone markup**

Insert this new `<section>` immediately after the `#how` section from Task 3, before the closing `</div>` of `.container`:

```html
    <section id="proof-zone" class="proof-zone">
      <div class="proof-label">PROOF</div>
      <h2>Scanned for real. Not a mockup.</h2>
      <p class="section-intro">Three real, well-known public repositories, actually scanned by <code>aletheore scan</code> at a pinned commit. The numbers below are read directly from that real output.</p>

      <div class="showcase-grid">
        <article class="showcase-card" data-repo="django">
          <h3>Django</h3>
          <p class="showcase-sub">Python · full git history</p>
          <ul class="showcase-stats" data-fields="moduleCount,dependencyEdgeCount,clusterCount,totalCommits"></ul>
        </article>
        <article class="showcase-card" data-repo="express">
          <h3>Express</h3>
          <p class="showcase-sub">JavaScript · real npm dependencies</p>
          <ul class="showcase-stats" data-fields="moduleCount,dependencyEdgeCount,licenseFindingsCount,vulnerabilityFindingsCount"></ul>
        </article>
        <article class="showcase-card" data-repo="kubernetes">
          <h3>Kubernetes</h3>
          <p class="showcase-sub">Go · the stress test</p>
          <ul class="showcase-stats" data-fields="moduleCount,dependencyEdgeCount,scanSeconds"></ul>
        </article>
      </div>

      <div class="toon-demo">
        <h3>The same data, TOON-encoded.</h3>
        <p class="section-intro">Measured against Aletheore's own <code>evidence.json</code>/<code>evidence.toon</code> pair from a real self-scan - the exact data the MCP server and <code>audit</code>'s coding-agent adapter read.</p>
        <div class="toon-counters">
          <div class="toon-counter">
            <div class="toon-counter-value" id="toon-token-count" data-target="45.5">0%</div>
            <div class="toon-counter-label">fewer tokens</div>
          </div>
          <div class="toon-counter">
            <div class="toon-counter-value" id="toon-byte-count" data-target="55.5">0%</div>
            <div class="toon-counter-label">smaller on disk</div>
          </div>
        </div>
      </div>
    </section>
```

- [ ] **Step 3: Write the rendering script that fills in real values**

Create `website/script.js` with the data-rendering portion (Task 6 appends the Motion-driven animation portion to this same file):

```javascript
const STAT_LABELS = {
  moduleCount: "modules",
  dependencyEdgeCount: "dependency edges",
  clusterCount: "clusters",
  totalCommits: "commits scanned",
  licenseFindingsCount: "license findings",
  vulnerabilityFindingsCount: "vulnerability findings",
  scanSeconds: "seconds to scan",
};

function renderShowcaseCards() {
  document.querySelectorAll(".showcase-card").forEach((card) => {
    const repo = card.dataset.repo;
    const data = SHOWCASE[repo];
    const list = card.querySelector(".showcase-stats");
    const fields = list.dataset.fields.split(",");
    list.innerHTML = fields
      .map((field) => `<li><strong>${data[field]}</strong> ${STAT_LABELS[field]}</li>`)
      .join("");
  });
}

renderShowcaseCards();
```

- [ ] **Step 4: Run to verify real values render**

Since this is static JS with no test runner, verify by serving the directory locally and reading the rendered DOM:

Run: `cd website && python3 -m http.server 8123 &`
Run: `sleep 1 && curl -s http://localhost:8123/index.html | grep -A2 "showcase-card"`

Then open `http://localhost:8123` in a real browser (or use `curl` against a headless-rendered snapshot if available) and confirm the `.showcase-stats` lists are populated with real numbers matching `website/showcase-data.js` exactly - not empty, not placeholder text. Stop the server after: `kill %1`.

- [ ] **Step 5: Commit**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion
git add website/index.html website/script.js
git commit -m "feat(website): add live showcase and TOON demo markup, rendered from real scan data"
```

---

## Task 5: Style the new sections

**Files:**
- Modify: `website/styles.css`

**Interfaces:**
- Consumes: existing design tokens (`--bg-dark`, `--accent`, `--text-muted`, etc., already defined in `:root`).
- Produces: `.proof-zone`, `.showcase-grid`, `.showcase-card`, `.toon-demo`, `.toon-counters`, `.toon-counter` classes that Task 6's Motion script attaches hover/animation behavior to.

- [ ] **Step 1: Add the PROOF zone styles**

Append to `website/styles.css`:

```css
.proof-zone {
  margin: 0 -24px;
  padding: 64px 24px;
  background: var(--bg-dark);
  color: #d8d2c5;
}

.proof-label {
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.08em;
  color: var(--accent);
  margin-bottom: 12px;
}

.proof-zone h2 {
  color: #fff;
}

.proof-zone .section-intro {
  color: #b9b1a4;
}

.showcase-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 20px;
  margin-top: 32px;
}

.showcase-card {
  background: #000;
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 8px;
  padding: 24px;
  transition: transform 0.2s ease;
}

.showcase-card:hover {
  transform: translateY(-4px);
}

.showcase-card h3 {
  color: #fff;
  margin-bottom: 4px;
}

.showcase-sub {
  color: var(--accent);
  font-size: 12px;
  margin-bottom: 16px;
}

.showcase-stats {
  list-style: none;
  padding: 0;
  font-size: 13px;
  color: #b9b1a4;
}

.showcase-stats li {
  margin-bottom: 6px;
}

.showcase-stats strong {
  color: #fff;
  font-size: 15px;
}

.toon-demo {
  margin-top: 56px;
  text-align: center;
}

.toon-demo h3 {
  color: #fff;
}

.toon-counters {
  display: flex;
  justify-content: center;
  gap: 48px;
  margin-top: 24px;
}

.toon-counter-value {
  font-size: 48px;
  font-weight: 760;
  color: var(--accent);
}

.toon-counter-label {
  font-size: 13px;
  color: #b9b1a4;
  margin-top: 4px;
}

@media (max-width: 760px) {
  .showcase-grid {
    grid-template-columns: 1fr;
  }

  .toon-counters {
    flex-direction: column;
    gap: 24px;
  }
}
```

- [ ] **Step 2: Verify visually**

Run: `cd website && python3 -m http.server 8123 &` then open `http://localhost:8123` in a real browser. Confirm the PROOF zone renders full-bleed dark, the three showcase cards are laid out in a row (stacking on narrow widths), and the TOON counters are visually prominent. Stop the server after: `kill %1`.

- [ ] **Step 3: Commit**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion
git add website/styles.css
git commit -m "feat(website): style the PROOF zone, showcase cards, and TOON counters"
```

---

## Task 6: Wire up Motion animations

**Files:**
- Modify: `website/script.js`
- Modify: `website/index.html`

**Interfaces:**
- Consumes: the global `Motion` object (Task 2), the `.showcase-card`/`.toon-counter-value` DOM elements (Tasks 4-5).

- [ ] **Step 1: Load the vendored Motion script before script.js**

In `website/index.html`, before the `<script src="showcase-data.js"></script>` tag added in Task 4, add:

```html
  <script src="vendor/motion.js"></script>
```

Final script-tag order at the bottom of `<body>` must be: `vendor/motion.js`, then `showcase-data.js`, then `script.js`.

- [ ] **Step 2: Append the animation logic to script.js**

Append to `website/script.js` (after the `renderShowcaseCards()` call already there from Task 4):

```javascript
function animateProofZoneOnScroll() {
  const proofZone = document.getElementById("proof-zone");
  if (!proofZone) return;

  Motion.inView(
    proofZone,
    () => {
      Motion.animate(
        proofZone,
        { opacity: [0, 1], transform: ["translateY(24px)", "translateY(0)"] },
        { duration: 0.6, easing: "ease-out" }
      );
      animateTokenCounters();
    },
    { amount: 0.3 }
  );
}

function animateTokenCounters() {
  document.querySelectorAll(".toon-counter-value").forEach((el) => {
    const target = parseFloat(el.dataset.target);
    Motion.animate(0, target, {
      duration: 1.2,
      easing: "ease-out",
      onUpdate: (latest) => {
        el.textContent = `${latest.toFixed(1)}%`;
      },
    });
  });
}

animateProofZoneOnScroll();
```

- [ ] **Step 3: Verify graceful degradation with JS disabled**

Run: `cd website && python3 -m http.server 8123 &`

In a real browser, open dev tools, disable JavaScript, then load `http://localhost:8123`. Confirm the PROOF zone, showcase cards (with the `data-fields` attribute visible but empty stat text, since `renderShowcaseCards()` never ran), and TOON counters (showing the static `0%` placeholder text from the HTML) are all still present in the DOM and none of the actual page content is hidden by `opacity:0` left unset by a Motion animation that never fired - if the proof zone appears invisible with JS off, fix the CSS so `.proof-zone` has a non-zero default opacity in `styles.css` (Motion's `opacity: [0, 1]` animation should only apply via JS, not as a CSS default). Re-enable JavaScript and confirm the section animates in normally on reload. Stop the server after: `kill %1`.

- [ ] **Step 4: Commit**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion
git add website/index.html website/script.js
git commit -m "feat(website): wire up Motion scroll-reveal and animated TOON counters"
```

---

## Task 7: Reskin legal and pricing pages

**Files:**
- Modify: `website/pricing.html`
- Modify: `website/terms.html`
- Modify: `website/privacy.html`
- Modify: `website/refund.html`

**Interfaces:**
- Consumes: `website/vendor/motion.js` (Task 2), `website/styles.css` additions (Task 5) - these pages get the same `<script src="vendor/motion.js">` include for visual consistency (e.g. subtle nav/hover polish) but do not need the showcase-data or PROOF-zone-specific script logic.

- [ ] **Step 1: Add the vendored Motion script tag to each page**

In each of `pricing.html`, `terms.html`, `privacy.html`, `refund.html`, add before the closing `</body>`:

```html
  <script src="vendor/motion.js"></script>
```

No further content changes - these pages' actual text stays exactly as-is (already accurate, per this plan's Non-Goals).

- [ ] **Step 2: Run to verify no regressions**

Run: `grep -l "vendor/motion.js" website/pricing.html website/terms.html website/privacy.html website/refund.html`
Expected: all four filenames printed.

Run: `for f in website/pricing.html website/terms.html website/privacy.html website/refund.html; do grep -c "\$11.99\|\$3.99" "$f"; done`
Expected: confirms the real pricing figures from the earlier price-correction commits are still present and untouched.

- [ ] **Step 3: Commit**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion
git add website/pricing.html website/terms.html website/privacy.html website/refund.html
git commit -m "feat(website): include vendored Motion on legal/pricing pages for visual consistency"
```

---

## Task 8: Final verification pass

**Files:**
- No new files - this task only runs checks across everything built in Tasks 1-7.

- [ ] **Step 1: Confirm every rendered number traces to real data**

Run:
```bash
cd website && python3 -m http.server 8123 &
sleep 1
curl -s http://localhost:8123/ | grep -oE '[0-9]+(\.[0-9]+)?%?' | sort -u > /tmp/rendered-numbers.txt
python3 -c "
import json, re
text = open('showcase-data.js').read()
obj_text = text[text.index('{'):text.rindex('}') + 1]
print(json.dumps(json.loads(obj_text), indent=2))
" > /tmp/real-showcase-data.txt
kill %1
cat /tmp/rendered-numbers.txt
cat /tmp/real-showcase-data.txt
```
Manually confirm every number appearing on the rendered page (module counts, edge counts, the 45.5%/55.5% TOON figures, Kubernetes scan seconds) is present in `/tmp/real-showcase-data.txt` - not a rounded, invented, or stale value.

- [ ] **Step 2: Confirm no dead links**

Run: `grep -oE 'href="[a-z]+\.html"' website/*.html | sed 's/.*href="//;s/"//' | sort -u | while read t; do [ -f "website/$t" ] || echo "MISSING: $t"; done`
Expected: no output.

- [ ] **Step 3: Confirm the pinned commit SHAs are recorded and match reality**

Run: `grep -oE '"commitSha": "[a-f0-9]+"' website/showcase-data.js`
Expected: exactly the three SHAs listed in this plan's Global Constraints - `3d34265d5d1b83fee5df3c1b6f55087b1a6a1ded`, `ae6dd37680e3a00618d6c8a3e522f0ee4eeba1a4`, `bd1a1b897340ef91595c36439fed49b9072f8b1d`.

- [ ] **Step 4: Confirm zero build step still holds**

Run: `cat website/vercel.json`
Expected: unchanged from before this plan - `{"cleanUrls": true, "trailingSlash": false}`, no `buildCommand` key added anywhere.

- [ ] **Step 5: Deploy and verify live**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion
git push origin master
```

Wait for Vercel's automatic Git-triggered deploy to finish (check `https://vercel.com/arihantk15s-projects/aletheore-website/deployments`), then:

Run: `curl -s -L https://aletheore.com/ | grep -c "PROOF"`
Expected: `1` or more, confirming the new PROOF zone is live in production, not just locally.
