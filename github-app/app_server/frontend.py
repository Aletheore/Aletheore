"""Server-rendered HTML shell for the managed dashboard.

No build step, matching this codebase's existing convention (see
aletheore.dashboard.DASHBOARD_HTML for the local dashboard's identical
approach) - each page is a static string with an embedded <script> that
fetches JSON from the real app_server/admin.py APIs and renders it
client-side. org/repo are read from the URL path in JS rather than
interpolated server-side, so these strings never need to survive a
str.format() pass against CSS full of literal braces.

Each dashboard section (overview, security, dead code, health, wiki,
settings) is its own real route rather than an anchor on one long page -
each fetches only the data it needs and can show full detail without
competing for space with five other sections.
"""

from functools import lru_cache
from html import escape
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app_server.admin import _administered_installation_ids_for_session_or_401
from app_server.auth import SESSION_COOKIE_NAME, get_current_session, sign_checkout_installation_id
from app_server.config import get_settings
from app_server.db import list_installations_for_ids
from app_server.github_install import github_app_install_url
from app_server.llm_cost import EXTRA_SEAT_PRICE_USD
from app_server.paddle_pricing import resolve_price_id_for_plan

frontend_router = APIRouter()

PRICING_URL = "https://www.aletheore.com/pricing"

# Both pinned to an exact version with a Subresource Integrity hash - a
# floating "@10"/"@latest" tag would let jsdelivr (or anyone who
# compromised it) serve different, unverified code into the authenticated
# dashboard origin at any time. SRI would be no-op against a floating tag
# anyway: the hash would go stale the moment the CDN's "latest" pointer
# moved. Regenerate the hash (openssl dgst -sha384 -binary <file> | openssl
# base64 -A) any time the pinned version bumps.
ICONS_LINK = (
    '<link rel="stylesheet" '
    'href="https://cdn.jsdelivr.net/npm/@tabler/icons-webfont@3.45.0/dist/tabler-icons.min.css" '
    'integrity="sha384-Ty9WrQxUB1vb9rF2T/wNBTcyJbiR5tK7e3gTrDGAbepOnWoasjD9lNXP4z0QkZML" '
    'crossorigin="anonymous">'
)
MERMAID_SCRIPT = (
    '<script src="https://cdn.jsdelivr.net/npm/mermaid@10.9.8/dist/mermaid.min.js" '
    'integrity="sha384-N3QqR/7q+xm3BGX+CBbNI8AUmRRqcsDzToy+0z1NLDI0QmTKW8zvwLvqulJgk3dP" '
    'crossorigin="anonymous"></script>'
)

STYLE = """
<style>
:root {
  --ink-900: #17150F;
  --ink-700: #6B6659;
  --slate-50: #FBFAF7;
  --slate-100: #F1EEE6;
  --slate-200: #E2DED2;
  --slate-400: #9A9384;
  --slate-500: #8A8377;
  --slate-600: #6B6659;
  --paper: #FFFFFF;
  --accent: #C1571F;
  --accent-strong: #A64717;
  --accent-soft: color-mix(in srgb, var(--accent) 8%, var(--paper));
  --accent-soft-strong: color-mix(in srgb, var(--accent) 16%, var(--paper));
  --success: #3D6B41;
  --success-soft: color-mix(in srgb, var(--success) 12%, var(--paper));
  --warning: #8A6A1A;
  --warning-soft: color-mix(in srgb, var(--warning) 12%, var(--paper));
  --critical: #A33327;
  --critical-soft: color-mix(in srgb, var(--critical) 12%, var(--paper));
  --border: #E2DED2;
  --border-strong: #C9C3B2;
  --shadow-card: none;
  --shadow-card-hover: none;
  --shadow-lift: none;
  --font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  --font-mono: ui-monospace, "SF Mono", "Cascadia Code", "Roboto Mono", Menlo, monospace;
  --page-bg: var(--slate-50);
}
* { box-sizing: border-box; }
body { margin: 0; font-family: var(--font-sans); color: var(--ink-900); background-color: var(--page-bg); }
a { color: var(--accent); }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0,0,0,0); white-space: nowrap; border: 0; }

/* ---- Sign-in ---- */
.signin { min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 4rem 1.5rem; }
.signin-card { width: 100%; max-width: 430px; background: var(--paper);
  border: 1px solid var(--border); border-radius: 4px; padding: 2.5rem 2.2rem; text-align: center; }
.wordmark { font-family: var(--font-sans); font-weight: 760; font-size: 25px; color: var(--ink-900); margin: 0 0 7px; }
.tagline { font-size: 13.5px; color: var(--slate-600); margin: 0 0 2rem; line-height: 1.6; }
.gh-btn { width: 100%; display: flex; align-items: center; justify-content: center; gap: 10px; background: var(--ink-900); color: var(--paper);
  border: none; border-radius: 9px; font-family: var(--font-sans); font-size: 14px; font-weight: 650; padding: 12px 16px; cursor: pointer; text-decoration: none; }
.gh-btn:hover { background: var(--ink-700); }
.gh-btn:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.scope-note { margin-top: 1.5rem; padding-top: 1.25rem; border-top: 1px solid var(--border); font-size: 12px; color: var(--slate-600); line-height: 1.6; text-align: left; }
.scope-note code { font-family: var(--font-mono); font-size: 11px; color: var(--slate-500); }

/* ---- Repo picker ---- */
.picker-wrap { max-width: 980px; margin: 0 auto; padding: 3.4rem 1.75rem; }
.picker-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 2rem; }
.picker-head h1 { font-size: 28px; font-weight: 720; margin: 0; }
.picker-org-group { margin-bottom: 2rem; }
.picker-org-label { font-size: 11px; color: var(--slate-400); font-weight: 500; margin-bottom: 10px; }
.picker-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 14px; }
.picker-card { display: flex; align-items: center; gap: 12px; text-decoration: none; color: var(--ink-900); background: var(--paper); border: 1px solid var(--border);
  border-radius: 4px; padding: 17px 18px; transition: background-color 0.12s ease, border-color 0.12s ease; }
.picker-card:hover { border-color: var(--border-strong); background: var(--slate-100); }
.picker-card-icon { width: 40px; height: 40px; border-radius: 4px; background: var(--accent-soft); color: var(--accent-strong);
  display: flex; align-items: center; justify-content: center; font-size: 18px; flex-shrink: 0; }
.picker-card-body { min-width: 0; flex: 1; }
.picker-repo { font-size: 15px; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.picker-plan { display: inline-block; margin-top: 7px; font-size: 11px; font-weight: 500; padding: 2px 9px; border-radius: 4px; background: var(--slate-100); color: var(--slate-600); }
.picker-plan.paid { background: var(--accent-soft); color: var(--accent-strong); }
.picker-card-arrow { color: var(--slate-400); font-size: 16px; flex-shrink: 0; }
.picker-card-pending { cursor: default; }
.picker-card-pending:hover { border-color: var(--border); background: var(--paper); }
.picker-pending-note { margin-top: 4px; font-size: 11.5px; color: var(--slate-500); }

/* ---- Shared UI atoms ---- */
.btn { font-family: var(--font-sans); font-size: 12.5px; font-weight: 650; border-radius: 4px; padding: 8px 12px;
  border: 1px solid var(--border-strong); background: var(--paper); color: var(--ink-900); cursor: pointer; display: inline-flex; align-items: center; gap: 6px; transition: background-color 0.12s ease; }
.btn:hover { background: var(--slate-100); }
.btn:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.btn-accent { background: var(--accent); color: #FFFFFF; border-color: var(--accent); }
.btn-accent:hover { background: var(--accent-strong); }
/* docs.html's own .btn is solid dark (a different default than the
   shared outline .btn most pages use for less-primary actions,
   confirmed by comparing docs.html/index.html/flash.html - all solid -
   against endpoints.html - outline, matching the shared default).
   Scoped to this one button rather than touching the shared class,
   which many other, non-primary buttons across every page also use. */
#docs-download-link { background: var(--ink-900); color: #FBFAF7; border-color: var(--ink-900); font-size: 13px; font-weight: 600; padding: 8px 14px; }
#docs-download-link:hover { background: var(--ink-700); }
.chip { display: inline-flex; align-items: center; gap: 5px; font-size: 11.5px; font-weight: 500; padding: 2px 9px; border-radius: 4px; }
.stepper { display: inline-flex; align-items: center; border: 1px solid var(--border-strong); border-radius: 4px; overflow: hidden; vertical-align: middle; }
.stepper button { font-family: var(--font-mono); font-size: 15px; font-weight: 600; width: 30px; height: 30px; border: none; background: var(--paper);
  color: var(--ink-900); cursor: pointer; display: flex; align-items: center; justify-content: center; transition: background-color 0.12s ease; }
.stepper button:hover { background: var(--slate-100); }
.stepper button:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
.stepper input[type="number"] { width: 56px; height: 30px; border: none; border-left: 1px solid var(--border-strong); border-right: 1px solid var(--border-strong);
  text-align: center; font-family: var(--font-mono); font-size: 13px; background: var(--paper); color: var(--ink-900); -moz-appearance: textfield; }
.stepper input[type="number"]::-webkit-outer-spin-button, .stepper input[type="number"]::-webkit-inner-spin-button { -webkit-appearance: none; margin: 0; }
.chip.critical { background: var(--critical-soft); color: var(--critical); }
.chip.warning { background: var(--warning-soft); color: var(--warning); }
.chip.success { background: var(--success-soft); color: var(--success); }
.chip.neutral { background: var(--slate-100); color: var(--slate-600); }
.field { width: 100%; font-family: var(--font-mono); font-size: 12px; padding: 8px 10px; border: 1px solid var(--border-strong); border-radius: 4px; background: var(--slate-100); color: var(--ink-900); }
.field:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.empty-state { padding: 1.5rem; text-align: center; color: var(--slate-600); font-size: 13px; }
.error-banner { background: var(--critical-soft); color: var(--critical); border-radius: 10px; padding: 12px 15px; font-size: 13px; margin: 1rem 0; }
.locked-feature { position: relative; border-radius: 10px; overflow: hidden; min-height: 150px; }
.locked-preview { filter: blur(5px); opacity: 0.65; pointer-events: none; user-select: none; padding: 2px; }
.locked-overlay { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center;
  justify-content: center; text-align: center; gap: 6px; padding: 1.5rem; background: rgba(0, 0, 0, 0.04); }
@media (prefers-color-scheme: dark) { .locked-overlay { background: rgba(0, 0, 0, 0.35); } }
:root[data-theme="dark"] .locked-overlay { background: rgba(0, 0, 0, 0.35); }
:root[data-theme="light"] .locked-overlay { background: rgba(0, 0, 0, 0.04); }
.locked-icon { width: 34px; height: 34px; border-radius: 50%; background: var(--accent-soft); color: var(--accent-strong);
  display: flex; align-items: center; justify-content: center; font-size: 17px; margin-bottom: 2px; }
.locked-title { font-size: 13.5px; font-weight: 500; }
.locked-desc { font-size: 12px; color: var(--slate-600); max-width: 38ch; line-height: 1.5; }
.locked-feature .btn-accent { margin-top: 4px; }
.form-row { display: flex; gap: 8px; margin-top: 8px; flex-wrap: wrap; }
.form-row .field { flex: 1; min-width: 120px; }
.token-reveal { font-family: var(--font-mono); font-size: 12px; background: var(--warning-soft); color: var(--ink-900);
  border-radius: 7px; padding: 10px 12px; margin: 8px 0; word-break: break-all; }
.copy-box { display: flex; align-items: center; gap: 8px; }
.copy-box .field { font-size: 11.5px; }

/* ---- Dashboard shell ---- */
.shell { display: grid; grid-template-columns: 220px minmax(0, 1fr); min-height: 100vh; }
.sidebar { border-right: 1px solid var(--border); padding: 20px 14px; display: flex; flex-direction: column; gap: 1.45rem; position: sticky; top: 0; height: 100vh; }
.brand { display: flex; align-items: center; gap: 8px; padding: 0 6px; }
.brand-mark { width: 20px; height: 20px; border: 1.5px solid var(--ink-900); border-radius: 3px; display: flex; align-items: center; justify-content: center; font-family: var(--font-mono); font-size: 11px; font-weight: 700; flex-shrink: 0; }
.brand-name { font-weight: 650; font-size: 14.5px; letter-spacing: -0.01em; }
.nav-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--slate-400); flex-shrink: 0; }
.nav-dot.paid { background: var(--accent); }
.nav-group-label { font-size: 11px; color: var(--slate-400); padding: 0 8px 6px; }
.nav-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 1px; }
.nav-item { display: flex; align-items: center; gap: 9px; padding: 6px 8px; border-radius: 4px; font-size: 13px; color: var(--ink-700); text-decoration: none; transition: background-color 0.12s ease, color 0.12s ease; }
.nav-item i { font-size: 16px; color: var(--ink-700); opacity: 0.95; }
.nav-item:hover { background: var(--paper); }
.nav-item.active { background: var(--accent-soft); color: var(--accent-strong); font-weight: 600; }
.nav-item.active i { color: var(--accent-strong); }
.nav-item:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.nav-item.disabled { cursor: default; }
.nav-item.disabled:hover { background: none; }
.plan-badge-wrap { margin-top: auto; }
.plan-card { background: var(--paper); border: 1px solid var(--border); border-radius: 4px; padding: 12px; }
.plan-name { font-size: 12px; font-weight: 500; display: flex; align-items: center; gap: 6px; text-transform: capitalize; }
.plan-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--accent); }
.plan-sub { font-size: 11px; color: var(--slate-600); margin-top: 3px; line-height: 1.5; }

.main { padding: 32px 48px 60px; min-width: 0; max-width: 1180px; margin: 0 auto; width: 100%; }
.topbar { display: flex; align-items: baseline; justify-content: space-between; gap: 1rem; margin-bottom: 1.4rem; flex-wrap: wrap; }
.breadcrumb { font-size: 12px; color: var(--slate-600); }
.breadcrumb b { color: var(--ink-900); font-weight: 500; }
.breadcrumb a { color: var(--slate-600); text-decoration: none; }
.breadcrumb a:hover { color: var(--ink-900); }
.h1 { font-size: 20px; font-weight: 650; letter-spacing: -0.01em; margin: 3px 0 0; }
.repo-path { font-family: var(--font-mono); font-size: 13px; color: var(--slate-600); }
.page-sub { font-size: 13px; color: var(--slate-600); margin-top: 4px; }
.topbar-right { font-size: 12px; color: var(--slate-400); font-family: var(--font-mono); }

.dashboard-summary { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 18px; align-items: center;
  margin-bottom: 1.15rem; border: 1px solid var(--border); border-radius: 4px;
  background: var(--paper);
  padding: 18px; }
.dashboard-summary-kicker { font-size: 11px; font-weight: 720; color: var(--accent-strong); }
.dashboard-summary h2 { margin: 5px 0 6px; font-size: 22px; line-height: 1.2; }
.dashboard-summary p { margin: 0; color: var(--slate-600); font-size: 13px; line-height: 1.55; max-width: 72ch; }
.summary-chip-row { display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-end; }
.summary-chip { display: inline-flex; align-items: center; gap: 7px; border: 1px solid var(--border); border-radius: 4px;
  background: var(--slate-100); padding: 7px 10px; color: var(--ink-700); font-size: 12px; white-space: nowrap; }
.summary-chip i { color: var(--accent-strong); font-size: 14px; }

/* Adjacent vertical margins collapse to the LARGER value, they do not
   add - .topbar's shared margin-bottom (22.4px) and this margin-top
   collapse through #top-error's empty div between them. 28px here (not
   22.4px + a delta) is what actually produces a 28px gap, matching
   index.html's page-head-to-strip spacing. Scoped to #stat-strip rather
   than raising .topbar itself, since other pages' own mockups want
   different values there (endpoints.html's page-head is 24px, for one). */
.stat-strip { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); border: 1px solid var(--border); border-radius: 4px; overflow: hidden; margin-top: 28px; margin-bottom: 1.7rem; }
/* Endpoint health's 3-cell aggregate row - shares .stat-card/.stat-label/
   .stat-value (identical cell treatment to Overview's own stat-strip,
   confirmed by measuring both mockups) via its own grid column count and
   page-specific vertical rhythm (24px above from endpoints.html's own
   .page-head margin-bottom, 28px below to the first .section, both
   margin-top on this element to collapse-to-larger against .topbar's
   shared margin-bottom rather than stack on top of it). */
.summary-row { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); border: 1px solid var(--border); border-radius: 4px; overflow: hidden; margin-top: 24px; margin-bottom: 28px; }
/* endpoints.html's own .summary-label/.summary-value gap is 8px, via the
   label's margin-bottom, not the value's margin-top (which is 0, with
   the value's line-height set to 1 explicitly) - scoped to #summary-row
   rather than changing the shared .stat-label/.stat-value (also used by
   Overview's own stat-strip, already pixel-measured and approved at a
   different gap value; touching the shared rule would regress it). */
#summary-row .stat-label { margin-bottom: 8px; }
#summary-row .stat-value { margin-top: 0; line-height: 1; }
#summary-row .stat-value .of { font-size: 15px; color: var(--slate-400); font-weight: 500; margin-left: 4px; }
.stat-card { background: var(--paper); border-right: 1px solid var(--border); padding: 16px 18px; text-decoration: none; color: inherit; display: block; transition: background-color 0.12s ease; }
.stat-card:last-child { border-right: none; }
a.stat-card:hover { background: var(--slate-50); }
a.stat-card:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.stat-label { font-size: 12px; color: var(--slate-600); }
.stat-value { font-family: var(--font-mono); font-variant-numeric: tabular-nums; font-size: 26px; font-weight: 650; margin-top: 5px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.stat-value.critical { color: var(--critical); }
.stat-value.warning { color: var(--warning); }
.stat-value.success { color: var(--success); }
.stat-delta { font-size: 11px; color: var(--slate-400); margin-top: 5px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

.section { background: var(--paper); border: 1px solid var(--border); border-radius: 4px; margin-bottom: 1.15rem; scroll-margin-top: 1rem; overflow: hidden; }
.section-head { display: flex; align-items: center; justify-content: space-between; padding: 15px 18px; border-bottom: 1px solid var(--border); gap: 1rem; flex-wrap: wrap; }
.section-title { font-size: 14.5px; font-weight: 500; display: flex; align-items: center; gap: 8px; }
.section-title i { font-size: 16px; color: var(--slate-400); }
.section-sub { font-size: 12px; color: var(--slate-600); }
.section-body { padding: 8px 18px 16px; }
.plain-section-head { display: flex; align-items: center; justify-content: space-between; margin: 0 0 12px; }
.plain-section-head h2 { font-size: 14px; font-weight: 650; margin: 0; }
.plain-section-head .count { font-family: var(--font-mono); font-size: 12px; color: var(--slate-600); }
/* Overriding via margin-top on the head itself (not the previous element's
   margin-bottom) so the two collapse to whichever is larger, matching
   index.html's own per-gap measurements - #findings-head sits below
   #stat-strip (27.2px margin-bottom) needing a 36px gap, #usage-head sits
   below .finding-list (27.2px margin-bottom) needing a 40px gap, and
   collapsing is exactly what makes max(27.2, 36)/max(27.2, 40) work
   instead of stacking on top of the existing margin. */
#findings-head { margin-top: 36px; }
#usage-head { margin-top: 40px; }

table.findings { width: 100%; border-collapse: collapse; font-size: 13px; }
table.findings th { text-align: left; font-size: 11px; color: var(--slate-400); font-weight: 500; padding: 8px 8px; border-bottom: 1px solid var(--border); }
table.findings td { padding: 10px 8px; border-bottom: 1px solid var(--border); vertical-align: top; }
table.findings tr:last-child td { border-bottom: none; }
.finding-title { font-weight: 500; }
.finding-cite { font-family: var(--font-mono); font-size: 11.5px; color: var(--slate-600); overflow-wrap: anywhere; }
.sev-stripe { display: inline-block; width: 3px; height: 13px; border-radius: 2px; margin-right: 8px; vertical-align: -2px; }
.sev-stripe.critical { background: var(--critical); }
.sev-stripe.warning { background: var(--warning); }
.sev-stripe.neutral { background: var(--slate-400); }

.finding-list { border: 1px solid var(--border); border-radius: 4px; overflow: hidden; margin-bottom: 1.7rem; }
.finding-row { display: grid; grid-template-columns: 14px minmax(0, 1fr) auto; gap: 12px; align-items: start; padding: 13px 16px; border-bottom: 1px solid var(--border); transition: background-color 0.12s ease; }
.finding-row:last-child { border-bottom: none; }
.finding-row:hover { background: var(--slate-50); }
.sev-dot { width: 7px; height: 7px; border-radius: 50%; margin-top: 5px; flex-shrink: 0; }
.sev-dot.critical { background: var(--critical); }
.sev-dot.warning { background: var(--warning); }
.sev-dot.minor { background: var(--slate-400); }
.finding-row .msg { font-size: 13.5px; line-height: 1.5; }
.finding-row .cite { font-family: var(--font-mono); font-size: 12px; color: var(--slate-600); margin-top: 4px; overflow-wrap: anywhere; }
.finding-row .tool { font-family: var(--font-mono); font-size: 11px; color: var(--slate-400); border: 1px solid var(--border); border-radius: 3px; padding: 2px 6px; white-space: nowrap; align-self: start; }

.deadcode-list, .dep-list { display: flex; flex-direction: column; }
.deadcode-row { display: flex; align-items: baseline; gap: 12px; padding: 10px 0; border-bottom: 1px solid var(--border); font-size: 13px; flex-wrap: wrap; }
.deadcode-row:last-child { border-bottom: none; }
.deadcode-path { font-family: var(--font-mono); font-size: 12.5px; flex: 1 1 320px; min-width: 0; overflow-wrap: anywhere; }
.deadcode-meta { font-size: 11.5px; color: var(--slate-600); }

/* endpoints.html's flat, bordered row-list treatment - .health-grid used
   to be a 2-column tile grid of rounded, slate-100-filled cards; each
   target group now gets its own bordered .endpoint-list-style box, and
   rows use endpoints.html's own 4-column grid (method tag, path, latency,
   status pill) instead of a tile. Real per-target grouping (a mockup
   with no target concept doesn't have to solve for) is preserved - only
   the row/list chrome changed, not the grouping structure. */
/* endpoints.html's own row treatment, reused by class name directly
   (.method/.path/.latency/.status-pill) rather than reinvented - real
   per-target grouping (a mockup with no target concept doesn't have to
   solve for) is preserved via .health-grid/.health-target-group*, only
   the row/list chrome inside each group changed to match the mockup. */
.health-grid { border: 1px solid var(--border); border-radius: 4px; overflow: hidden; }
.health-row { display: grid; grid-template-columns: 54px minmax(0, 1fr) auto auto; gap: 14px; align-items: center; padding: 12px 16px; border-bottom: 1px solid var(--border); }
.health-row:last-child { border-bottom: none; }
.method { font-family: var(--font-mono); font-size: 11px; font-weight: 700; text-align: center; padding: 2px 0; border-radius: 3px; border: 1px solid var(--border-strong); color: var(--slate-600); }
.method.get { color: #2E6B8A; border-color: #2E6B8A; }
.method.post { color: var(--success); border-color: var(--success); }
.method.delete { color: var(--critical); border-color: var(--critical); }
.method.put, .method.patch { color: var(--warning); border-color: var(--warning); }
.path { font-family: var(--font-mono); font-size: 13px; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.path .file { color: var(--slate-400); font-size: 11.5px; margin-left: 8px; }
.latency { font-family: var(--font-mono); font-variant-numeric: tabular-nums; font-size: 12px; color: var(--slate-600); white-space: nowrap; }
.status-pill { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; white-space: nowrap; }
.status-pill .dot { width: 7px; height: 7px; border-radius: 50%; }
.status-pill.up .dot { background: var(--success); }
.status-pill.down .dot { background: var(--critical); }
.status-pill.up { color: var(--success); }
.status-pill.down { color: var(--critical); }
.health-target-group { margin-bottom: 1.2rem; }
.health-target-group:last-child { margin-bottom: 0; }
.health-target-group-label { font-size: 12px; font-weight: 500; margin-bottom: 8px; display: flex; align-items: center; gap: 8px; }
.health-history { grid-column: 1 / -1; background: var(--slate-50); border-radius: 8px; padding: 8px 10px; margin: -4px 0 4px; }
.health-history-list { display: flex; flex-direction: column; gap: 5px; }
.health-history-row { display: flex; align-items: center; gap: 10px; font-size: 11.5px; }
.health-checked { font-size: 11.5px; color: var(--slate-400); white-space: nowrap; }

.wiki-banner { display: flex; align-items: center; justify-content: space-between; gap: 1rem; background: var(--slate-100); border: 1px solid var(--border); border-radius: 4px; padding: 13px 15px; margin: 10px 0 14px; flex-wrap: wrap; }
.wiki-banner-text { font-size: 12.5px; color: var(--ink-700); line-height: 1.5; max-width: 46ch; }
.wiki-banner-text b { font-weight: 600; color: var(--ink-900); }
/* docs.html's own plain stat-row (mono number + label pills) - replaces
   the earlier .docs-overview kicker/heading/description card, which the
   mockup has no equivalent of. */
.stat-row { display: flex; gap: 24px; margin: 20px 0 28px; flex-wrap: wrap; }
.stat-pill { font-family: var(--font-mono); }
.stat-pill .n { font-size: 20px; font-weight: 650; }
.stat-pill .l { font-size: 11.5px; color: var(--slate-600); font-family: var(--font-sans); margin-left: 6px; }
/* docs.html's two-column layout: main content plus a sticky right rail
   (Recently updated / Hotspots / Jump to). */
.main-grid { display: grid; grid-template-columns: minmax(0, 1fr) 260px; gap: 44px; align-items: start; }
.main-col { min-width: 0; }
.rail { display: flex; flex-direction: column; gap: 20px; position: sticky; top: 32px; min-width: 0; }
.rail-card { border: 1px solid var(--border); border-radius: 4px; padding: 14px 16px; background: var(--paper); }
.rail-card h3 { font-size: 12px; font-weight: 650; margin: 0 0 10px; color: var(--slate-600); }
.rail-row { display: flex; justify-content: space-between; align-items: baseline; gap: 10px; padding: 7px 0; border-bottom: 1px solid var(--border); }
.rail-row:last-child { border-bottom: none; padding-bottom: 0; }
.rail-row .path { font-family: var(--font-mono); font-size: 11.5px; color: var(--ink-900); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.rail-row .meta { color: var(--slate-400); font-size: 11px; white-space: nowrap; flex-shrink: 0; }
.rail-card a.rail-link { display: block; font-size: 12.5px; color: var(--slate-600); text-decoration: none; padding: 5px 0; }
.rail-card a.rail-link:hover { color: var(--accent); }
@media (max-width: 880px) { .main-grid { grid-template-columns: minmax(0, 1fr); } .rail { position: static; } }
.docs-stat-pill { min-width: 104px; border: 1px solid var(--border); border-radius: 4px; background: var(--slate-100);
  padding: 10px 12px; }
.docs-stat-value { font-family: var(--font-mono); font-size: 18px; font-weight: 720; color: var(--ink-900); }
.docs-stat-label { margin-top: 2px; font-size: 11px; color: var(--slate-600); }
.docs-status-banner { border: 1px solid var(--border); border-radius: 4px; padding: 12px 14px; margin: 0 0 14px; font-size: 12.5px; line-height: 1.55; }
.docs-status-banner.failed { border-color: var(--critical); background: var(--critical-soft); color: var(--critical); }
.docs-status-banner.partial { border-color: var(--warning); background: var(--warning-soft); color: var(--warning); }
/* docs.html's own flat-row module list, replacing the earlier 2-column
   card grid - the mockup's own callout confirms the intent was a flat
   bordered row with a rotating chevron, not a card-with-shadow rebuild. */
.docs-grid { display: flex; flex-direction: column; }
.docs-module-card { border: 1px solid var(--border); border-radius: 4px; margin-bottom: 8px; background: var(--paper); }
.docs-module-summary { list-style: none; cursor: pointer; padding: 14.5px 16px; display: flex; align-items: center; gap: 12px; }
.docs-module-summary::-webkit-details-marker { display: none; }
.docs-module-chevron { font-family: var(--font-mono); font-size: 11px; color: var(--slate-400); transition: transform 0.15s ease; flex-shrink: 0; }
.docs-module-card[open] .docs-module-chevron { transform: rotate(90deg); }
.docs-module-path { font-family: var(--font-mono); font-size: 13px; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.docs-chip { font-family: var(--font-mono); font-size: 10.5px; color: var(--slate-600); border: 1px solid var(--border-strong); border-radius: 3px; padding: 1px 6px; flex-shrink: 0; }
.docs-module-content { padding: 0 16px 16px 42px; border-top: 1px solid var(--border); }
.docs-module-content-inner { font-size: 12px; color: var(--slate-600); padding: 14px 0 0; }
.docs-symbol-row { border-bottom: 1px solid var(--border); padding: 10px 0; }
.docs-symbol-row:last-child { border-bottom: none; }
.docs-symbol-row .sig { font-family: var(--font-mono); font-size: 12.5px; padding: 3px 0; }
.docs-symbol-row .sig .name { color: var(--ink-900); }
.docs-symbol-row .sig .kind { color: var(--slate-400); margin-left: 8px; font-family: var(--font-sans); }
.docs-symbol-row .desc { font-size: 12.5px; line-height: 1.6; color: var(--slate-600); margin: 6px 0; max-width: 72ch; }
.docs-symbol-row .cite { font-family: var(--font-mono); font-size: 11px; color: var(--slate-400); }
.docs-symbol-row .flag { font-size: 11px; margin-left: 8px; }
.docs-symbol-row .flag.undocumented { color: var(--slate-400); font-style: italic; }
.docs-symbol-row .flag.ai { color: var(--accent-strong); }
.docs-commit-card { display: flex; align-items: center; justify-content: space-between; gap: 16px; border: 1px solid var(--border); border-radius: 4px; padding: 14px;
  background: var(--paper); }
.docs-commit-copy { min-width: 0; }
.docs-commit-title { font-size: 13.5px; font-weight: 650; margin-bottom: 4px; }
.docs-commit-desc { font-size: 12.5px; color: var(--slate-600); line-height: 1.55; }
.docs-commit-desc a { font-weight: 650; }
.diagram-wrap { overflow-x: auto; border: 1px solid var(--border); border-radius: 4px; background: var(--slate-50); padding: 14px; }
.graph-card { border: 1px solid var(--border); border-radius: 4px; background: var(--paper); margin-bottom: 20px; }
.graph-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 12px 16px; border-bottom: 1px solid var(--border); flex-wrap: wrap; }
.graph-toolbar select { font-family: var(--font-sans); font-size: 12.5px; border: 1px solid var(--border-strong); border-radius: 4px; padding: 6px 8px; background: var(--paper); color: var(--ink-900); }
.graph-toolbar .hint { font-size: 12px; color: var(--slate-400); font-family: var(--font-mono); }
#graph-reset-btn { font-weight: 600; padding: 7px 12px; }
.graph-wrap { position: relative; }
svg#depgraph { width: 100%; height: 460px; display: block; background: var(--paper); cursor: grab; }
svg#depgraph:active { cursor: grabbing; }
.g-node circle { fill: var(--paper); stroke: var(--ink-900); stroke-width: 1.4; cursor: grab; }
.g-node.hub circle { stroke: var(--accent); stroke-width: 1.8; }
.g-node text { font-family: var(--font-mono); font-size: 10px; fill: var(--ink-900); pointer-events: none; }
.g-node.dim circle { stroke: var(--border-strong); }
.g-node.dim text { fill: var(--slate-400); }
.g-edge { stroke: var(--border-strong); stroke-width: 1; }
.g-edge.g-edge-ambiguous { stroke-dasharray: 3 3; }
.g-edge.dim { stroke: var(--border); }
.g-edge.lit { stroke: var(--accent); stroke-width: 1.4; }
.graph-hover-info { padding: 10px 16px; border-top: 1px solid var(--border); font-family: var(--font-mono); font-size: 12px; color: var(--slate-600); min-height: 16px; }
.cluster-item { border: 1px solid var(--border); border-radius: 4px; padding: 12px 14px; background: var(--paper); margin-bottom: 20px; }
.cluster-item .name { font-size: 13px; font-weight: 600; margin-bottom: 4px; }
.cluster-item .count { font-family: var(--font-mono); font-size: 11.5px; color: var(--slate-600); }
.callout { border: 1px solid var(--border); border-left: 2px solid var(--slate-600); padding: 12px 14px; font-size: 12.5px; color: var(--slate-600); line-height: 1.55; margin-bottom: 20px; border-radius: 0 4px 4px 0; }
.diagram-wrap .mermaid { display: flex; justify-content: center; min-width: max-content; }
.diagram-wrap.diagram-zoomable { cursor: zoom-in; }
.diagram-wrap.diagram-zoomable::after { content: "Click to open full diagram"; display: block; margin-top: 8px; color: var(--slate-400); font-size: 11px; text-align: center; }
.diagram-wrap.diagram-drilldown.diagram-zoomable::after { content: "Click a box to explore it - click elsewhere to open full diagram"; }
.diagram-wrap .node.diagram-node-clickable { cursor: pointer; }
.diagram-wrap .node.diagram-node-clickable:hover > * { filter: brightness(1.35); }
.diagram-zoom-overlay { position: fixed; inset: 0; z-index: 1000; background: rgba(10, 8, 4, 0.94);
  overflow: auto; padding: 84px 28px 36px; cursor: zoom-out; }
.diagram-zoom-content { cursor: grab; display: block; }
.diagram-zoom-content svg { max-width: none; display: block; border-radius: 12px; background: rgba(255, 255, 255, 0.04); box-shadow: 0 30px 90px rgba(0, 0, 0, 0.45); }
.diagram-zoom-toolbar { position: fixed; top: 18px; left: 50%; transform: translateX(-50%); z-index: 1001;
  display: flex; align-items: center; gap: 8px; max-width: calc(100vw - 28px); border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 99px; background: rgba(12, 10, 7, 0.84); padding: 7px; color: #F5F0E6; box-shadow: 0 14px 40px rgba(0, 0, 0, 0.32); }
.diagram-zoom-toolbar button { border: 1px solid rgba(255, 255, 255, 0.12); border-radius: 99px; background: rgba(255, 255, 255, 0.08);
  color: #F5F0E6; cursor: pointer; font-family: var(--font-sans); font-size: 12px; font-weight: 650; padding: 6px 10px; }
.diagram-zoom-toolbar button:hover { background: rgba(255, 255, 255, 0.14); }
.diagram-zoom-hint { padding: 0 8px; color: #D8D2C5; font-size: 12px; white-space: nowrap; }
.subsystem-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; margin-top: 20px; }
.subsystem-card { display: flex; flex-direction: column; align-items: flex-start; justify-content: flex-start;
  border: 1px solid var(--border); border-radius: 4px; padding: 12px; text-align: left; background: var(--paper);
  cursor: pointer; font-family: var(--font-sans); transition: background-color 0.12s ease; }
.subsystem-card:hover { border-color: var(--border-strong); background: var(--slate-100); }
.subsystem-name { font-size: 13px; font-weight: 600; margin-bottom: 3px; color: var(--ink-900); }
.subsystem-desc { font-size: 12px; color: var(--slate-600); line-height: 1.55; }
.subsystem-files { font-family: var(--font-mono); font-size: 10.5px; color: var(--slate-400); margin-top: 8px; }
.subsystem-detail { border-top: 1px solid var(--border); margin-top: 14px; padding-top: 14px; }
.subsystem-detail-file { margin-bottom: 10px; border: 1px solid var(--border); border-radius: 4px; background: var(--slate-50); padding: 11px; }
.subsystem-detail-path { font-family: var(--font-mono); font-size: 12.5px; font-weight: 500; overflow-wrap: anywhere; }
.subsystem-detail-role { font-size: 12.5px; color: var(--slate-600); margin: 3px 0 6px; }
.subsystem-detail-symbol { font-family: var(--font-mono); font-size: 11.5px; color: var(--ink-700); padding: 2px 0 2px 14px; }
.subsystem-detail-symbol .line { color: var(--slate-400); }
.wiki-md { margin-top: 9px; border-top: 1px solid var(--border); padding-top: 8px; }
.wiki-md > summary { font-size: 11.5px; color: var(--accent-strong); cursor: pointer; font-family: var(--font-sans); }
.wiki-md > summary:hover { text-decoration: underline; }
.wiki-md-h { font-size: 11.5px; font-weight: 600; color: var(--ink-700); margin: 9px 0 3px; }
.wiki-md-p { font-size: 12.5px; color: var(--slate-600); line-height: 1.6; margin: 0 0 6px; }
.wiki-md-list { margin: 0 0 6px; padding-left: 17px; }
.wiki-md-list li { font-size: 12.5px; color: var(--slate-600); line-height: 1.6; margin-bottom: 3px; }
.wiki-md code { font-family: var(--font-mono); font-size: 11.5px; background: var(--slate-100); padding: 1px 4px; border-radius: 4px; overflow-wrap: anywhere; }

.settings-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 24px; align-items: start; }
.settings-section { margin-top: 24px; }
.settings-block { background: var(--paper); border: 1px solid var(--border); border-radius: 4px;
  padding: 18px 20px; margin-bottom: 16px; }
.settings-block-label { font-size: 13px; font-weight: 600; margin-bottom: 14px; }
.settings-block-hint { font-size: 11px; color: var(--slate-600); margin-top: 6px; line-height: 1.5; }
/* Overview's seats block renders an empty #seat-billing-status hint div
   between the button row and the status line (populated only after a
   buySeat/removeSeat click) - real gap found while pixel-matching the
   seats block height against index.html's own seats block, which has no
   such element: an empty block-level div still takes up a full
   line-height + margin-top even with no text, adding height the mockup
   never accounted for. */
.settings-block-hint:empty { display: none; }
.credit-figure { font-family: var(--font-mono); font-size: 34px; font-weight: 650; letter-spacing: -0.01em; line-height: 1; }
.credit-figure .of { font-size: 14px; color: var(--slate-600); font-weight: 500; margin-left: 6px; }
.credit-meter { height: 4px; border-radius: 2px; background: var(--border); margin: 14px 0 4px; overflow: hidden; }
.credit-meter-fill { height: 100%; background: var(--accent); }
.credit-breakdown { font-size: 11px; color: var(--slate-400); display: flex; justify-content: space-between; }
/* #usage-body scopes this to Overview only (its id is unique to that page)
   rather than raising the shared .settings-block-hint font-size, which
   Settings' own many hint lines also use and hasn't been measured against
   any mockup - index.html's dedicated .block-hint is 12px, 1px larger
   than the shared 11px default. */
#usage-body .settings-block-hint { font-size: 12px; }
.divider-label { font-size: 11px; color: var(--slate-400); margin: 20px 0 12px; display: flex; align-items: center; gap: 10px; }
.divider-label::after { content: ""; flex: 1; height: 1px; background: var(--border); }
.qty-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.qty-prefix { font-family: var(--font-mono); font-size: 13px; color: var(--slate-600); }
.status-line { display: flex; align-items: center; gap: 7px; font-size: 12px; color: var(--slate-600); margin-top: 4px; }
.status-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--success); flex-shrink: 0; }
/* ---- Flash dashboard (the credits page) - flash.html's own vocabulary
   not otherwise shared. Everything else on that page (settings-grid,
   credit-figure/meter, stepper, status-line) reuses Overview's already-
   ported classes above. */
.plan-pill { font-family: var(--font-mono); font-size: 11px; color: var(--slate-600); border: 1px solid var(--border-strong); border-radius: 3px; padding: 2px 7px; margin-left: 8px; vertical-align: middle; }
.credit-hero { border: 1px solid var(--border); border-radius: 4px; padding: 22px 24px; background: var(--paper); margin-bottom: 32px; display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 24px; align-items: center; }
.credit-hero .credit-figure { font-size: 38px; }
.credit-hero .credit-meter { max-width: 420px; margin: 16px 0 6px; }
.credit-hero .credit-sub { font-size: 12px; color: var(--slate-600); }
.credit-actions { display: flex; flex-direction: column; align-items: flex-end; gap: 10px; }
.credit-actions .qty-row { gap: 10px; }
.btn-small { font-size: 12px; padding: 6px 10px; }
.install-tag { margin-left: auto; font-size: 10px; color: var(--slate-400); }
.review-list { border: 1px solid var(--border); border-radius: 4px; overflow: hidden; margin-bottom: 32px; }
.review-row { display: grid; grid-template-columns: 16px minmax(0, 1fr) auto auto; gap: 14px; align-items: center; padding: 12px 16px; border-bottom: 1px solid var(--border); text-decoration: none; color: inherit; }
.review-row:last-child { border-bottom: none; }
.review-row:hover { background: var(--slate-100); }
.review-status { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.review-status.commented { background: var(--warning); }
.review-status.clean { background: var(--success); }
.review-status.skipped, .review-status.failed { background: var(--slate-400); }
.review-title { font-size: 13.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.review-title .repo { color: var(--slate-600); font-family: var(--font-mono); font-size: 12px; margin-left: 6px; }
.review-meta { font-size: 12px; color: var(--slate-600); font-family: var(--font-mono); white-space: nowrap; }
.review-cost { font-size: 12px; color: var(--slate-400); font-family: var(--font-mono); white-space: nowrap; }
.upgrade-card { border: 1px solid var(--border); border-radius: 4px; padding: 20px 22px; display: flex; justify-content: space-between; align-items: center; gap: 20px; flex-wrap: wrap; }
.upgrade-card h3 { font-size: 14px; margin: 0 0 6px; font-weight: 650; }
.upgrade-card p { font-size: 12.5px; color: var(--slate-600); margin: 0; max-width: 56ch; line-height: 1.55; }
.settings-help-links { display: flex; gap: 14px; margin-top: 8px; }
.settings-help-links a { font-size: 11px; color: var(--accent-strong); text-decoration: none; font-weight: 500; }
.settings-help-links a:hover { text-decoration: underline; }
.alert-channel + .alert-channel { margin-top: 16px; padding-top: 16px; border-top: 1px solid var(--border); }
.alert-channel-label { font-size: 11px;
  color: var(--slate-400); font-weight: 600; margin-bottom: 9px; }
.danger-zone { margin-top: 16px; border: 1px solid var(--critical); border-radius: 4px; padding: 16px 18px; }
.danger-zone .settings-block-label { color: var(--critical); }
.danger-zone .btn-danger { background: var(--critical); border-color: var(--critical); color: #fff; }
.danger-zone .btn-danger[disabled] { opacity: 0.5; cursor: not-allowed; }
.danger-repo-list { font-size: 11px; color: var(--slate-600); margin-top: 6px; word-break: break-all; }
.token-row { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 7px 0; border-bottom: 1px solid var(--border); font-size: 12.5px; }
.token-row:last-child { border-bottom: none; }
.token-label { font-weight: 500; }
.token-meta { font-size: 11px; color: var(--slate-600); }
.claim-page { min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 3rem 1.5rem; }
.claim-card { width: 100%; max-width: 560px; background: var(--paper);
  border: 1px solid var(--border); border-radius: 4px; padding: 2.15rem; }
.claim-card h1 { margin: 0 0 0.7rem; font-size: 26px; font-weight: 720; }
.claim-card p { color: var(--slate-600); line-height: 1.6; font-size: 14px; margin: 0 0 1.2rem; }
.claim-options { display: flex; flex-direction: column; gap: 8px; margin: 1rem 0; }
.claim-option { display: flex; align-items: center; gap: 9px; padding: 10px 12px; border: 1px solid var(--border); border-radius: 4px; }
.claim-option input { accent-color: var(--accent); }

@media (max-width: 860px) {
  .shell { grid-template-columns: 1fr; }
  .sidebar { position: static; height: auto; flex-direction: column; overflow: visible; border-right: none; border-bottom: 1px solid var(--border); }
  .nav-list { flex-direction: row; flex-wrap: wrap; }
  .nav-item { white-space: nowrap; }
  .main { padding: 1.2rem 1rem 2.5rem; }
  .dashboard-summary { grid-template-columns: 1fr; }
  .summary-chip-row { justify-content: flex-start; }
  .stat-strip { grid-template-columns: repeat(2, minmax(0,1fr)); }
  .health-grid, .subsystem-grid, .settings-grid, .docs-grid { grid-template-columns: 1fr; }
  .credit-hero { grid-template-columns: minmax(0, 1fr); }
  .credit-actions { align-items: flex-start; }
  .docs-overview, .docs-module-summary, .docs-commit-card { grid-template-columns: 1fr; }
  .docs-overview-stats, .docs-module-meta { justify-content: flex-start; }
  .picker-head { align-items: flex-start; gap: 1rem; flex-direction: column; }
  .diagram-zoom-toolbar { left: 14px; right: 14px; transform: none; justify-content: center; flex-wrap: wrap; border-radius: 14px; }
  .diagram-zoom-hint { order: 2; width: 100%; text-align: center; }
}

/* Real bug found at a true 375px viewport (device-emulated, not a window
   resize): 4 narrow .stat-strip cells (Overview) or 3 narrow
   #summary-row cells (Endpoint health) squeeze .stat-value's 26px mono
   figure past its own cell width, and .stat-value's overflow/ellipsis
   rule (there to truncate a long text value like "Not configured")
   silently clips a NUMBER instead ("98.7%" rendering as "98.…") - a
   truncated stat is actively misleading, never acceptable, unlike a
   truncated label or path. Shrinking the figure and cell padding a
   further step below 860px's existing 2-column reflow keeps every
   digit visible instead. */
@media (max-width: 600px) {
  .stat-card { padding: 12px; }
  .stat-value { font-size: 20px; }
  /* 3 narrow columns is tighter than Overview's own 4-strip (which only
     drops to 2 columns, never lower) - stacking to one column is the
     safer of the peer's two suggested fixes for this specific row count
     at this width, guaranteed not to clip regardless of exact content
     width rather than relying on the same 20px figure just barely fitting. */
  .summary-row { grid-template-columns: 1fr; }
  .summary-row .stat-card { border-right: none; border-bottom: 1px solid var(--border); }
  .summary-row .stat-card:last-child { border-bottom: none; }
}

</style>
"""

FETCH_HELPERS = """
async function apiGet(url) {
  const res = await fetch(url);
  if (res.status === 401) {
    // A 401 here means the session cookie still looks valid (get_current_session
    // only checks the cookie's own signature/TTL) but the GitHub token it wraps
    // no longer works - redirecting to '/' bounces right back into the same
    // "valid" session and re-triggers this exact call, looping forever.
    // /auth/logout actually deletes the server-side session, so the next load
    // of '/' correctly shows the real sign-in page instead.
    window.location.href = '/auth/logout';
    return null;
  }
  if (!res.ok) {
    console.error('apiGet failed: ' + url + ' -> ' + res.status);
    return null;
  }
  return res;
}
async function apiPost(url, body) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (res.status === 401) {
    window.location.href = '/auth/logout';
    return null;
  }
  if (!res.ok) {
    console.error('apiPost failed: ' + url + ' -> ' + res.status);
    return null;
  }
  return res;
}
function findingIdentityKey(findingType, f) {
  // Mirrors app_server/dismissed_findings.py's finding_identity_key() -
  // identity is always recomputed server-side on dismiss/undismiss (never
  // trusted from the client); this is only used client-side to check
  // membership against the dismissed_finding_keys set the read endpoint
  // already returns.
  if (findingType === 'secret') return f.path + '\x1f' + f.pattern + '\x1f' + f.match_preview;
  if (findingType === 'static_analysis') return f.path + '\x1f' + f.line + '\x1f' + f.tool + '\x1f' + f.rule_id;
  return f.ecosystem + '\x1f' + f.package + '\x1f' + f.advisory_id;
}
function staticAnalysisSevChip(severity) {
  // Shared (not defined per-page) - both the overview page's "Recent
  // security findings" preview and the full Security page's table render
  // static-analysis findings with this same severity mapping.
  if (severity === 'blocker' || severity === 'critical') return { stripe: 'critical', chip: 'critical', label: 'Critical' };
  if (severity === 'major') return { stripe: 'warning', chip: 'warning', label: 'Warning' };
  return { stripe: 'neutral', chip: 'neutral', label: 'Info' };
}
function relativeTime(iso) {
  const diffMs = Date.now() - new Date(iso).getTime();
  const mins = Math.round(diffMs / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return mins + ' minute' + (mins === 1 ? '' : 's') + ' ago';
  const hours = Math.round(mins / 60);
  if (hours < 24) return hours + ' hour' + (hours === 1 ? '' : 's') + ' ago';
  const days = Math.round(hours / 24);
  return days + ' day' + (days === 1 ? '' : 's') + ' ago';
}
// Compact unit (5m/2h/3d) for the Overview topbar's fine-print "last scan"
// line, matching index.html's own compact style there - a separate
// function rather than changing relativeTime()'s own output, since that
// shared function's full-word format ("5 minutes ago") is also used in
// several other, more prose-like contexts (endpoint health, token/member
// lists) that aren't part of this mockup and shouldn't change with it.
function compactRelativeTime(iso) {
  const diffMs = Date.now() - new Date(iso).getTime();
  const mins = Math.round(diffMs / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return mins + 'm ago';
  const hours = Math.round(mins / 60);
  if (hours < 24) return hours + 'h ago';
  const days = Math.round(hours / 24);
  return days + 'd ago';
}
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
  });
}
function planDisplayName(plan) {
  var names = { free: 'Aletheore Community', flash: 'Aletheore Flash', air: 'Aletheore AIR' };
  return names[plan] || names.air;
}
function planShortName(plan) {
  var names = { free: 'Community', flash: 'Flash', air: 'AIR' };
  return names[plan] || names.air;
}
// Minimal markdown for AIRview file pages. The text is model-written from
// repository content, so it is escaped FIRST and only then are a handful of
// markdown tokens promoted to tags. Every angle bracket is already an entity
// by that point, so nothing smuggled through a repo into the model's output
// can become live HTML - promotion only ever adds tags this function wrote.
function renderWikiMarkdown(src) {
  function inline(text) {
    return text
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\\*\\*([^*]+)\\*\\*/g, '<strong>$1</strong>');
  }
  const out = [];
  let inList = false;
  function closeList() {
    if (inList) { out.push('</ul>'); inList = false; }
  }
  escapeHtml(String(src || '')).split('\\n').forEach(function (raw) {
    const line = raw.trim();
    if (!line) { closeList(); return; }
    if (line.slice(0, 3) === '## ') {
      closeList();
      out.push('<h5 class="wiki-md-h">' + inline(line.slice(3)) + '</h5>');
      return;
    }
    if (line.slice(0, 2) === '- ' || line.slice(0, 2) === '* ') {
      if (!inList) { out.push('<ul class="wiki-md-list">'); inList = true; }
      out.push('<li>' + inline(line.slice(2)) + '</li>');
      return;
    }
    closeList();
    out.push('<p class="wiki-md-p">' + inline(line) + '</p>');
  });
  closeList();
  return out.join('');
}
"""

SIGNIN_HTML = f"""<!DOCTYPE html>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Aletheore</title>
{ICONS_LINK}
{STYLE}
<div class="signin">
  <div class="signin-card">
    <h1 class="sr-only">Sign in to Aletheore</h1>
    <p class="wordmark">Aletheore</p>
    <p class="tagline">Evidence-grounded audits for your repositories.<br>Sign in to see findings for the orgs you administer.</p>
    <a class="gh-btn" href="/auth/login">
      <svg width="17" height="17" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8Z"></path></svg>
      Continue with GitHub
    </a>
    <div class="scope-note">
      Requests read access to repository contents and metadata, and permission to post check runs and comments. We never request write access to code.
    </div>
  </div>
</div>
"""

PICKER_HTML = f"""<!DOCTYPE html>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Your repositories — Aletheore</title>
{ICONS_LINK}
{STYLE}
<div class="picker-wrap">
  <div class="picker-head">
    <h1>Your repositories</h1>
    <a class="btn" href="/auth/logout">Sign out</a>
  </div>
  <div id="picker-body"><div class="empty-state">Loading&hellip;</div></div>
</div>
<script>
{FETCH_HELPERS}
(async function () {{
  const body = document.getElementById('picker-body');
  const res = await apiGet('/app/repos');
  if (!res) return;
  const data = await res.json();
  // Flash installations have no dashboard, but their owner still needs to see
  // the AI credit balance and buy more - listed here next to any AIR repos,
  // because one login can administer both (AIR on a personal account, Flash
  // on an org).
  const billingAccounts = data.billing_accounts || [];
  if (data.repos.length === 0 && billingAccounts.length === 0) {{
    body.innerHTML = '<div class="empty-state">No managed repositories yet. Aletheore Community (free) runs self-service - the CLI, the free GitHub Action, and free GitHub App usage all work without a hosted dashboard, so a free installation won\\'t appear here. Install the Aletheore GitHub App on an organization and subscribe to AIR to get a managed dashboard.</div>';
    return;
  }}
  const byOrg = {{}};
  data.repos.forEach(function (r) {{
    (byOrg[r.org] = byOrg[r.org] || []).push(r);
  }});
  body.innerHTML = '';
  Object.keys(byOrg).sort().forEach(function (org) {{
    const group = document.createElement('div');
    group.className = 'picker-org-group';
    const grid = byOrg[org].map(function (r) {{
      const planBadge = '<span class="picker-plan' + (r.plan !== 'free' ? ' paid' : '') + '">' + escapeHtml(planDisplayName(r.plan)) + '</span>';
      if (r.initialized === false) {{
        const note = r.scan_limit_reached
          ? '10 repos per month limit reached &mdash; please wait for next month'
          : 'Initialization required &mdash; waiting for the first scan to complete';
        return '<div class="picker-card picker-card-pending">' +
          '<div class="picker-card-icon"><i class="ti ti-git-branch" aria-hidden="true"></i></div>' +
          '<div class="picker-card-body"><div class="picker-repo">' + escapeHtml(r.repo) + '</div>' +
          planBadge +
          '<div class="picker-pending-note">' + note + '</div></div>' +
          '</div>';
      }}
      return '<a class="picker-card" href="/dashboard/' + encodeURIComponent(r.org) + '/' + encodeURIComponent(r.repo) + '">' +
        '<div class="picker-card-icon"><i class="ti ti-git-branch" aria-hidden="true"></i></div>' +
        '<div class="picker-card-body"><div class="picker-repo">' + escapeHtml(r.repo) + '</div>' +
        planBadge + '</div>' +
        '<i class="ti ti-chevron-right picker-card-arrow" aria-hidden="true"></i></a>';
    }}).join('');
    group.innerHTML = '<div class="picker-org-label">' + escapeHtml(org) + '</div><div class="picker-grid">' + grid + '</div>';
    body.appendChild(group);
  }});
  if (billingAccounts.length > 0) {{
    const flashGroup = document.createElement('div');
    flashGroup.className = 'picker-org-group';
    const flashGrid = billingAccounts.map(function (a) {{
      return '<a class="picker-card" href="/credits/' + encodeURIComponent(a.installation_id) + '">' +
        '<div class="picker-card-icon"><i class="ti ti-coin" aria-hidden="true"></i></div>' +
        '<div class="picker-card-body"><div class="picker-repo">' + escapeHtml(a.account_login) + '</div>' +
        '<span class="picker-plan paid">' + escapeHtml(planDisplayName(a.plan)) + '</span>' +
        '<div class="picker-pending-note">$' + (Number(a.credit_remaining_usd) || 0).toFixed(2) + ' AI credit &middot; buy more</div></div>' +
        '<i class="ti ti-chevron-right picker-card-arrow" aria-hidden="true"></i></a>';
    }}).join('');
    flashGroup.innerHTML = '<div class="picker-org-label">Flash organizations (AI credit)</div><div class="picker-grid">' + flashGrid + '</div>';
    body.appendChild(flashGroup);
  }}
}})();
</script>
"""

_NAV_ITEMS = [
    ("overview", "", "ti-layout-dashboard", "Overview"),
    ("security", "/security", "ti-shield-check", "Findings"),
    ("deadcode", "/dead-code", "ti-trash", "Dead code"),
    ("health", "/health", "ti-activity", "Endpoint health"),
    ("wiki", "/wiki", "ti-book-2", "AIRview"),
    ("docs", "/docs", "ti-file-text", "Docs"),
]


def _sidebar(active: str) -> str:
    repo_items = "".join(
        f'<li><a class="nav-item{" active" if key == active else ""}" data-href="{suffix}">'
        f'<i class="ti {icon}" aria-hidden="true"></i>{label}</a></li>'
        for key, suffix, icon, label in _NAV_ITEMS
    )
    settings_active = " active" if active == "settings" else ""
    return f"""
  <nav class="sidebar" aria-label="Dashboard navigation">
    <div class="brand"><span class="brand-mark">A</span><span class="brand-name">Aletheore</span></div>
    <div>
      <div class="nav-group-label">Repository</div>
      <ul class="nav-list" id="repo-switch-list"><li><a class="nav-item" aria-hidden="true">&hellip;</a></li></ul>
    </div>
    <div>
      <div class="nav-group-label">This repository</div>
      <ul class="nav-list">{repo_items}</ul>
    </div>
    <div>
      <div class="nav-group-label">Account</div>
      <ul class="nav-list">
        <li><a class="nav-item{settings_active}" data-href="/settings"><i class="ti ti-settings" aria-hidden="true"></i>Settings</a></li>
        <li><a class="nav-item" href="/auth/logout"><i class="ti ti-logout" aria-hidden="true"></i>Sign out</a></li>
      </ul>
    </div>
    <div class="plan-badge-wrap">
      <div class="plan-card">
        <div class="plan-name"><span class="plan-dot"></span><span id="plan-name">&hellip;</span></div>
        <div class="plan-sub" id="plan-sub"></div>
      </div>
    </div>
  </nav>
"""


# Included at the top of every dashboard page's <script>: parses org/repo
# from the URL, wires the sidebar's nav hrefs (the sidebar HTML itself is
# static per-page, only the org/repo prefix is computed client-side), and
# loads the plan badge from the same admin endpoint every page needs
# anyway for its own paid-gate check.
PAGE_HEAD_JS = """
window.addEventListener('pageshow', function (event) {
  if (event.persisted) { window.location.reload(); }
});
const parts = window.location.pathname.split('/').filter(Boolean);
const org = decodeURIComponent(parts[1]);
const repo = decodeURIComponent(parts[2]);
const base = '/app/' + encodeURIComponent(org) + '/' + encodeURIComponent(repo);
const adminBase = '/admin/' + encodeURIComponent(org) + '/' + encodeURIComponent(repo);
const pageBase = '/dashboard/' + encodeURIComponent(org) + '/' + encodeURIComponent(repo);

document.querySelectorAll('.nav-item[data-href]').forEach(function (el) {
  el.href = pageBase + el.dataset.href;
});
const cOrg = document.getElementById('crumb-org');
const cRepo = document.getElementById('crumb-repo');
if (cOrg) cOrg.textContent = org;
if (cRepo) { cRepo.textContent = repo; cRepo.href = pageBase; }
document.title = document.title.replace('{repo}', repo).replace('{org}', org);

async function loadRepoSwitcher() {
  const list = document.getElementById('repo-switch-list');
  const res = await apiGet('/app/repos');
  const repos = (res && res.ok ? (await res.json()).repos : []).filter(function (r) { return r.initialized; });
  if (repos.length === 0) { list.innerHTML = ''; return; }
  list.innerHTML = repos.map(function (r) {
    const isActive = r.org === org && r.repo === repo;
    return '<li><a class="nav-item' + (isActive ? ' active' : '') + '" href="/dashboard/' + encodeURIComponent(r.org) + '/' + encodeURIComponent(r.repo) + '">' +
      '<span class="nav-dot paid"></span>' + escapeHtml(r.repo_full_name) + '</a></li>';
  }).join('');
}
loadRepoSwitcher();

async function loadPlanBadge() {
  const res = await apiGet(adminBase);
  const nameEl = document.getElementById('plan-name');
  const subEl = document.getElementById('plan-sub');
  const planLineEl = document.getElementById('repo-plan-line');
  if (!res) return null;
  if (res.status === 402) {
    nameEl.textContent = planDisplayName('free');
    subEl.textContent = 'Upgrade for AIRview and settings.';
    if (planLineEl) planLineEl.textContent = org + '/' + repo + ' · ' + planShortName('free') + ' plan';
    return 'free';
  }
  if (!res.ok) { nameEl.textContent = ''; subEl.textContent = ''; return null; }
  const data = await res.json();
  nameEl.textContent = planDisplayName(data.installation.plan);
  subEl.textContent = data.installation.plan === 'free' ? 'Upgrade for AIRview and settings.' : 'AIRview and priority scans included.';
  if (planLineEl) planLineEl.textContent = org + '/' + repo + ' · ' + planShortName(data.installation.plan) + ' plan';
  return data;
}
"""

CONFIRM_UPGRADE_JS = f"""
function confirmUpgrade() {{
  if (window.confirm('This is a paid feature. Go to pricing?')) {{
    window.open('{PRICING_URL}', '_blank', 'noopener');
  }}
}}

function lockedFeature(title, description, previewHtml) {{
  return '<div class="locked-feature">' +
    '<div class="locked-preview">' + previewHtml + '</div>' +
    '<div class="locked-overlay">' +
      '<div class="locked-icon"><i class="ti ti-lock" aria-hidden="true"></i></div>' +
      '<div class="locked-title">' + escapeHtml(title) + '</div>' +
      '<div class="locked-desc">' + escapeHtml(description) + '</div>' +
      '<button class="btn btn-accent" onclick="confirmUpgrade()">Upgrade</button>' +
    '</div>' +
  '</div>';
}}
"""

# Shared by every page with real-money actions (Settings, Overview) - was
# duplicated per-page (a second, separately-maintained copy already existed
# for the standalone /credits page's own installation-scoped API shape,
# _CREDITS_JS below). adminBase-based, not installation-id-based, since
# every caller of this constant already has org/repo in scope. Each caller
# sets window._reloadUsage to its own refresh function before invoking
# these (loadSettings on Settings, loadUsage on Overview) instead of this
# file hardcoding one page's refresh call - buySeat/removeSeat need to
# re-render whichever page's seat UI actually called them.
BILLING_ACTIONS_JS = """
async function buySeat(btn) {
  // Disabled for the whole round trip, not just re-enabled on failure like
  // most other buttons on this page: real gap found via audit - buySeat/
  // removeSeat are the only real-money actions on this page with no
  // double-click guard at all. A second click landing before the first
  // response comes back fires a second, genuinely separate POST /seats/buy
  // - the backend's per-installation lock (admin.py's
  // _seat_adjustment_lock) only serializes the two against each other, it
  // does not collapse them into one purchase, so both succeed and the
  // customer is billed for two extra seats from what looked like one
  // click. window._reloadUsage() below re-renders this whole section
  // (including this button) once the real seat count is known, so there
  // is no separate re-enable path to also get right for the SUCCESS path -
  // but that reasoning only covers success. Real gap found by Flash Review
  // on this same change: on a genuine network failure (fetch() itself
  // rejects, before res/data ever exist) the function exits via an
  // unhandled exception, the refresh never runs, and the button - a
  // real-money action - stays disabled forever with no page-reload-free
  // recovery. try/finally re-enables on every exit; harmless on the
  // success path too, since the refresh has already replaced this
  // button's DOM node by the time finally runs.
  btn.disabled = true;
  const status = document.getElementById('seat-billing-status');
  status.textContent = 'Updating billing...';
  status.style.color = 'var(--slate-600)';
  try {
    const res = await fetch(adminBase + '/seats/buy', { method: 'POST' });
    const data = await res.json().catch(function () { return {}; });
    if (res.ok) {
      status.textContent = 'Seat added - billing updated. Refreshing...';
      status.style.color = 'var(--success)';
      if (window._reloadUsage) window._reloadUsage();
    } else {
      status.textContent = data.detail || 'Could not buy a seat.';
      status.style.color = 'var(--critical)';
    }
  } finally {
    btn.disabled = false;
  }
}

async function removeSeat(btn) {
  // See buySeat's comment - same double-click gap and same network-failure
  // stuck-button gap, same fix for both.
  btn.disabled = true;
  const status = document.getElementById('seat-billing-status');
  status.textContent = 'Updating billing...';
  status.style.color = 'var(--slate-600)';
  try {
    const res = await fetch(adminBase + '/seats/remove', { method: 'POST' });
    const data = await res.json().catch(function () { return {}; });
    if (res.ok) {
      status.textContent = 'Seat removed - billing updated. Refreshing...';
      status.style.color = 'var(--success)';
      if (window._reloadUsage) window._reloadUsage();
    } else {
      status.textContent = data.detail || 'Could not remove a seat.';
      status.style.color = 'var(--critical)';
    }
  } finally {
    btn.disabled = false;
  }
}

async function openBillingPortal() {
  const status = document.getElementById('seat-billing-status');
  if (status) { status.textContent = 'Opening billing portal...'; status.style.color = 'var(--slate-600)'; }
  const res = await fetch(adminBase + '/billing-portal');
  const data = await res.json().catch(function () { return {}; });
  if (res.ok && data.url) {
    window.location.href = data.url;
    return;
  }
  if (status) {
    status.textContent = data.detail || 'Could not open the billing portal.';
    status.style.color = 'var(--critical)';
  }
}

async function buyCredit(btn) {
  const statusEl = document.getElementById('topup-status');
  if (typeof Paddle === "undefined") {
    statusEl.textContent = 'Checkout is unavailable right now - try disabling any ad/script blocker and reload.';
    return;
  }
  // parseInt would accept "7.9" (silently truncated to 7) or "1e5" (parsed
  // as 1) - Number() + an explicit integer check rejects both instead of
  // quietly charging a different amount than what's on screen.
  const rawAmount = Number(document.getElementById('topup-amount').value);
  const amount = Number.isInteger(rawAmount) ? rawAmount : NaN;
  if (!amount || amount < 5 || amount > 1000) {
    statusEl.textContent = 'Enter an amount between $5 and $1000.';
    return;
  }
  // Real gap found via audit: buySeat/removeSeat both guard against a
  // rapid double-click firing two independent purchases (see buySeat's
  // comment); this button had no guard at all - two clicks before the
  // first apiGet() round trip returns could open two stacked
  // Paddle.Checkout.open() overlays with two different signed
  // checkout_installation_tokens. Re-enabled in finally - unlike
  // buySeat/removeSeat, this button's DOM node is never replaced by a
  // re-render, so it must actually come back (e.g. the customer closes
  // the overlay without completing checkout and wants to try again).
  btn.disabled = true;
  statusEl.textContent = 'Opening checkout...';
  statusEl.style.color = '';
  try {
    window._creditCheckoutCompleted = false;
    // The installation token is minted with a 30-minute TTL (auth.py's
    // sign_checkout_installation_id) - re-fetch it fresh here instead of
    // reusing page-load time's copy, so a tab left open past 30 minutes
    // doesn't send Paddle a token the webhook can no longer resolve (money
    // taken, no credit granted). window._creditTopupPriceId is a static
    // price id set once at page load and doesn't need refreshing.
    const res = await apiGet(adminBase);
    if (!res || !res.ok) {
      statusEl.textContent = 'Could not start checkout - try again.';
      return;
    }
    const data = await res.json();
    // Associates the checkout with the installation's existing Paddle
    // customer record (already returned in data.installation, same source
    // /subscribe's checkout page reads for its own pwCustomer wiring) -
    // without it, an existing subscriber topping up credit would re-enter
    // their email and Paddle would silently open a second customer record,
    // splitting billing history and producing a transaction whose
    // customer_id the subscription webhook path can't attribute back to
    // this installation.
    const paddleCustomerId = data.installation && data.installation.paddle_customer_id;
    Paddle.Checkout.open({
      items: [{ priceId: window._creditTopupPriceId, quantity: amount }],
      customData: { installation_token: data.checkout_installation_token },
      ...(paddleCustomerId ? { customer: { id: paddleCustomerId } } : {}),
      settings: {
        displayMode: 'overlay',
        variant: 'one-page',
        successUrl: 'https://app.aletheore.com/dashboard',
      },
    });
  } finally {
    btn.disabled = false;
  }
}
"""


def _page_head(title: str) -> str:
    return f"""<!DOCTYPE html>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
{ICONS_LINK}
{MERMAID_SCRIPT}
{STYLE}"""


def _topbar(h1: str, right_id: str = "", sub_id: str = "", show_breadcrumb: bool = True, sub_class: str = "repo-path", right_html: str = "", margin_bottom: str = "") -> str:
    # right_html carries real markup (e.g. Docs' "Export as Markdown" link,
    # which needs its own href/download attributes) - right_id alone only
    # ever produces an empty div a script populates with text later, which
    # can't express that.
    right = right_html or (f'<div class="topbar-right" id="{right_id}"></div>' if right_id else "")
    # sub_class defaults to Overview's mono "org/repo - plan" treatment;
    # other pages needing a plain descriptive sentence under the H1 (e.g.
    # Endpoint health's "Live checks against every mapped API endpoint...")
    # pass "page-sub" instead - same slot, different mockup treatment.
    sub = f'<div class="{sub_class}" id="{sub_id}"></div>' if sub_id else ""
    # Overview's own mockup has no breadcrumb - the H1 line is the top of
    # the page - but every other page's topbar keeps it, so this defaults
    # to on and PAGE_HEAD_JS's existing crumb-org/crumb-repo population
    # already null-checks both elements rather than assuming they exist.
    breadcrumb = (
        '<div class="breadcrumb"><a id="crumb-org" href="/dashboard"></a> '
        '<span style="color:var(--slate-400);">/</span> <b><a id="crumb-repo"></a></b></div>'
        if show_breadcrumb else ""
    )
    # .h1's margin-top exists to space it away from the breadcrumb above it -
    # with no breadcrumb, that margin just pushes the H1 down from where
    # .main's own top padding already puts it, which is what the mockup's
    # H1 sits flush at.
    h1_style = "" if show_breadcrumb else ' style="margin-top:0"'
    # .topbar's shared margin-bottom (22.4px) is a fine default, but a page
    # whose own mockup wants a smaller exact gap below its page-head
    # (docs.html: 20px, via margin-collapse with .stat-row's own 20px
    # margin-top) can't get there by adding more margin below - collapse
    # only ever takes the larger side. margin_bottom overrides .topbar's
    # own value directly for that one page instead.
    topbar_style = f' style="margin-bottom:{margin_bottom}"' if margin_bottom else ""
    return f"""
    <div class="topbar"{topbar_style}>
      <div>
        {breadcrumb}
        <h1 class="h1"{h1_style}>{h1}</h1>
        {sub}
      </div>
      {right}
    </div>
"""


def _shell(active: str, body: str) -> str:
    return f"""
<div class="shell">
  {_sidebar(active)}
  <main class="main">
    {body}
  </main>
</div>
"""


# ---------------------------------------------------------------------------
# Overview page - stats only, each stat links into its own detail page.
# ---------------------------------------------------------------------------
# A function, not a plain module-level constant like the other _HTML pages -
# same reason as _settings_html(): its Usage section needs a real inline
# Paddle checkout (get_settings().paddle_client_token/paddle_environment),
# and calling get_settings() at plain module-import time would make
# importing this file require a fully configured settings environment.
@lru_cache(maxsize=1)
def _overview_html() -> str:
    return _page_head("Overview — {repo} — Aletheore") + _shell(
    "overview",
    _topbar("Overview", "last-scanned", "repo-plan-line", show_breadcrumb=False)
    + """
    <div id="top-error"></div>
    <div class="stat-strip" id="stat-strip">
      <a class="stat-card" data-href="/security"><div class="stat-label">Open findings</div><div class="stat-value" id="stat-findings">&ndash;</div><div class="stat-delta" id="stat-findings-sub"></div></a>
      <a class="stat-card" data-href="/dead-code"><div class="stat-label">Dead code</div><div class="stat-value" id="stat-deadcode">&ndash;</div><div class="stat-delta" id="stat-deadcode-sub"></div></a>
      <a class="stat-card" data-href="/health"><div class="stat-label">Endpoint uptime</div><div class="stat-value" id="stat-uptime">&ndash;</div><div class="stat-delta" id="stat-uptime-sub"></div></a>
      <div class="stat-card"><div class="stat-label">Modules scanned</div><div class="stat-value" id="stat-modules">&ndash;</div><div class="stat-delta" id="stat-modules-sub"></div></div>
    </div>
    <div class="plain-section-head" id="findings-head">
      <h2>Recent findings</h2>
      <div class="count" id="findings-count"></div>
    </div>
    <div id="recent-security-body"><div class="empty-state">Loading&hellip;</div></div>
    <div class="plain-section-head" id="usage-head" style="display:none">
      <h2>Usage</h2>
    </div>
    <div id="usage-body"></div>
"""
) + f"""
<script src="https://cdn.paddle.com/paddle/v2/paddle.js"></script>
<script>
{FETCH_HELPERS}
{PAGE_HEAD_JS}
{CONFIRM_UPGRADE_JS}
{BILLING_ACTIONS_JS}
document.querySelectorAll('[data-href]').forEach(function (el) {{
  if (el.tagName === 'A' && el.dataset.href) el.href = pageBase + el.dataset.href;
}});
window._reloadUsage = loadUsage;

// Same guarded pattern as Settings' own Paddle.Initialize() call - a
// blocked cdn.paddle.com load must only disable the credit top-up button,
// never take down the rest of this script (including loadOverview() at
// the bottom, which renders the whole page).
if (typeof Paddle !== "undefined") {{
  Paddle.Environment.set("{get_settings().paddle_environment}");
  Paddle.Initialize({{
    token: "{get_settings().paddle_client_token}",
    eventCallback: function (event) {{
      const status = document.getElementById('topup-status');
      if (!status || !event || !event.name) return;
      if (event.name === 'checkout.loaded') {{
        status.textContent = '';
      }} else if (event.name === 'checkout.completed') {{
        window._creditCheckoutCompleted = true;
        status.textContent = 'Purchase complete - your balance updates once the payment is confirmed.';
        status.style.color = 'var(--success)';
      }} else if (event.name === 'checkout.closed' && !window._creditCheckoutCompleted) {{
        status.textContent = '';
      }} else if (event.name === 'checkout.error') {{
        status.textContent = 'Checkout error - try again.';
        status.style.color = 'var(--critical)';
      }}
    }},
  }});
}}

async function loadOverview() {{
  const res = await apiGet(base);
  if (!res) return;
  if (!res.ok) {{
    const data = await res.json().catch(function () {{ return {{}}; }});
    const fallback = res.status === 403 ? "You don't administer this repository." : 'Repository not found.';
    document.getElementById('top-error').innerHTML = '<div class="error-banner">' + escapeHtml(data.detail || fallback) + '</div>';
    document.getElementById('recent-security-body').innerHTML = '<div class="empty-state">Unavailable.</div>';
    return;
  }}
  const data = await res.json();
  const history = data.history || [];
  if (history.length === 0) {{
    document.getElementById('last-scanned').textContent = 'No scans yet';
    document.getElementById('findings-count').textContent = '';
    document.getElementById('recent-security-body').innerHTML = '<div class="empty-state">No scans yet - findings will appear after the first pull request is scanned.</div>';
    return;
  }}
  const latest = history[0];
  const evidence = latest.evidence || {{}};
  const headSha = evidence._scan_head_sha;
  document.getElementById('last-scanned').textContent = 'last scan ' + compactRelativeTime(latest.scanned_at) + (headSha ? ' · head ' + headSha.slice(0, 8) : '');

  const dismissedKeys = data.dismissed_finding_keys || {{ secret: [], vulnerability: [], static_analysis: [] }};
  const security = evidence.security || {{}};
  const secretFindings = ((security.secrets || {{}}).findings || []).filter(function (f) {{
    return !f.likely_placeholder && !f.accepted && dismissedKeys.secret.indexOf(findingIdentityKey('secret', f)) === -1;
  }});
  const vulnFindings = ((security.dependency_vulnerabilities || {{}}).findings || []).filter(function (f) {{
    return dismissedKeys.vulnerability.indexOf(findingIdentityKey('vulnerability', f)) === -1;
  }});
  const staticAnalysisFindings = ((security.static_analysis || {{}}).findings || []).filter(function (f) {{
    return (dismissedKeys.static_analysis || []).indexOf(findingIdentityKey('static_analysis', f)) === -1;
  }});
  const totalFindings = secretFindings.length + vulnFindings.length + staticAnalysisFindings.length;
  document.getElementById('findings-count').textContent = totalFindings + ' open';

  document.getElementById('stat-findings').textContent = totalFindings;
  document.getElementById('stat-findings').className = 'stat-value' + (totalFindings > 0 ? ' critical' : ' success');
  // Real bug found at both 375px and 1280px: the full "N secret, N
  // dependency, N static analysis" text overflows the 204px cell at 11px
  // and silently ellipsis-truncates the last category off - dropping real
  // information the user needs to read. Never ellipsis a number/count;
  // shorten labels and drop zero-count categories instead, so it always
  // fits without losing anything real.
  const findingSubParts = [];
  if (secretFindings.length > 0) findingSubParts.push(secretFindings.length + ' secret' + (secretFindings.length === 1 ? '' : 's'));
  if (vulnFindings.length > 0) findingSubParts.push(vulnFindings.length + ' dep' + (vulnFindings.length === 1 ? '' : 's'));
  if (staticAnalysisFindings.length > 0) findingSubParts.push(staticAnalysisFindings.length + ' static');
  document.getElementById('stat-findings-sub').textContent = findingSubParts.length ? findingSubParts.join(', ') : 'No findings';

  const deadCode = (evidence.repository || {{}}).dead_code || {{}};
  const unreachable = deadCode.unreachable_modules || [];
  const unusedDeps = deadCode.unused_dependencies || [];
  document.getElementById('stat-deadcode').textContent = unreachable.length;
  document.getElementById('stat-deadcode').className = 'stat-value' + (unreachable.length > 0 ? ' warning' : ' success');
  document.getElementById('stat-deadcode-sub').textContent = unusedDeps.length + ' unused dependencies';

  const moduleCount = ((evidence.repository || {{}}).modules || []).length;
  document.getElementById('stat-modules').textContent = moduleCount;
  document.getElementById('stat-modules-sub').textContent = history.length + ' scan' + (history.length === 1 ? '' : 's') + ' recorded';

  const recentBody = document.getElementById('recent-security-body');
  const securePreview = secretFindings.slice(0, 5);
  const vulnPreview = vulnFindings.slice(0, 5 - securePreview.length);
  const staticAnalysisPreview = staticAnalysisFindings.slice(0, Math.max(0, 5 - securePreview.length - vulnPreview.length));
  if (securePreview.length === 0 && vulnPreview.length === 0 && staticAnalysisPreview.length === 0) {{
    recentBody.innerHTML = '<div class="empty-state">No open findings.</div>';
  }} else {{
    let rows = '';
    securePreview.forEach(function (f) {{
      rows += '<div class="finding-row"><div class="sev-dot critical"></div><div><div class="msg">Possible ' + escapeHtml(f.pattern) + ' secret</div>' +
        '<div class="cite">' + escapeHtml(f.path) + ':' + f.line + '</div></div>' +
        '<div class="tool">trivy</div></div>';
    }});
    vulnPreview.forEach(function (f) {{
      rows += '<div class="finding-row"><div class="sev-dot warning"></div><div><div class="msg">' + escapeHtml(f.advisory_id) + ': ' + escapeHtml(f.summary || 'known vulnerability') + '</div>' +
        '<div class="cite">' + escapeHtml(f.package) + '@' + escapeHtml(f.installed_version) + '</div></div>' +
        '<div class="tool">osv</div></div>';
    }});
    staticAnalysisPreview.forEach(function (f) {{
      const sev = staticAnalysisSevChip(f.severity);
      const dotClass = sev.stripe === 'neutral' ? 'minor' : sev.stripe;
      rows += '<div class="finding-row"><div class="sev-dot ' + dotClass + '"></div><div><div class="msg">' + escapeHtml(f.message) + '</div>' +
        '<div class="cite">' + escapeHtml(f.path) + ':' + f.line + '</div></div>' +
        '<div class="tool">' + escapeHtml(f.tool || 'static analysis') + '</div></div>';
    }});
    recentBody.innerHTML = '<div class="finding-list">' + rows + '</div>';
  }}
}}

async function loadUptimeStat() {{
  const res = await apiGet(base + '/health');
  if (!res || !res.ok) return;
  const data = await res.json();
  const endpoints = data.endpoints || [];
  if (endpoints.length === 0) {{
    document.getElementById('stat-uptime').textContent = '–';
    document.getElementById('stat-uptime-sub').textContent = 'Add a target in Endpoint health';
    return;
  }}
  const up = endpoints.filter(function (e) {{ return e.reachable; }}).length;
  const pct = Math.round((up / endpoints.length) * 100);
  document.getElementById('stat-uptime').textContent = pct + '%';
  document.getElementById('stat-uptime').className = 'stat-value' + (pct === 100 ? ' success' : pct < 90 ? ' critical' : ' warning');
  document.getElementById('stat-uptime-sub').textContent = up + ' of ' + endpoints.length + ' endpoints up';
}}

async function loadUsage() {{
  const section = document.getElementById('usage-head');
  const body = document.getElementById('usage-body');
  const res = await apiGet(adminBase);
  if (!res || !res.ok) return;  // free/locked plan - no managed billing to show
  const data = await res.json();
  section.style.display = '';
  // Headline is base remaining only, measured against the real monthly
  // allotment - matching index.html's own semantics ($12.40 of $18.00,
  // purchased credit shown on its own breakdown line below). Mixing
  // purchased credit into the headline would read as "$40 of $18" after
  // a large top-up, which is not what "of $18" is supposed to mean.
  const baseCredit = data.base_credit_remaining_usd || 0;
  const topupCredit = data.topup_credit_balance_usd || 0;
  const allotment = data.base_credit_allotment_usd || 0;
  const pct = allotment > 0 ? Math.max(0, Math.min(100, Math.round((baseCredit / allotment) * 100))) : 0;
  const hasSubscription = !!data.installation.paddle_subscription_id;
  const renewsAt = data.subscription_renews_at
    ? new Date(data.subscription_renews_at).toLocaleDateString(undefined, {{ month: 'short', day: 'numeric' }})
    : null;
  window._creditTopupPriceId = data.credit_topup_price_id;
  body.innerHTML =
    '<div class="settings-grid">' +
      '<div class="settings-block">' +
        '<div class="settings-block-label">Credit balance</div>' +
        '<div class="credit-figure">$' + baseCredit.toFixed(2) + (allotment > 0 ? ' <span class="of">of $' + allotment.toFixed(2) + '</span>' : '') + '</div>' +
        (allotment > 0 ? '<div class="credit-meter"><div class="credit-meter-fill" style="width:' + pct + '%"></div></div>' : '') +
        '<div class="credit-breakdown"><span>$' + allotment.toFixed(2) + ' included this month</span>' +
          '<span>' + (topupCredit > 0 ? '+ $' + topupCredit.toFixed(2) + ' purchased, never expires' : '') + '</span>' +
        '</div>' +
        (data.credit_topup_price_id
          ? '<div class="divider-label">buy more credit</div>' +
            '<div class="qty-row">' +
              '<span class="qty-prefix">$</span>' +
              '<div class="stepper">' +
                '<button type="button" onclick="document.getElementById(&#39;topup-amount&#39;).stepDown()" aria-label="Decrease amount">&minus;</button>' +
                '<input type="number" id="topup-amount" min="5" max="1000" step="5" value="10">' +
                '<button type="button" onclick="document.getElementById(&#39;topup-amount&#39;).stepUp()" aria-label="Increase amount">+</button>' +
              '</div>' +
              '<button class="btn btn-accent" onclick="buyCredit(this)">Buy credit</button>' +
            '</div>' +
            '<div id="topup-status" class="settings-block-hint"></div>' +
            '<div class="settings-block-hint">$5 minimum &middot; charged once, added immediately</div>'
          : '<div class="settings-block-hint" style="margin-top:10px;">Buying additional credit is coming soon.</div>') +
      '</div>' +
      '<div class="settings-block">' +
        '<div class="settings-block-label">Team seats</div>' +
        '<div class="settings-block-hint">' + data.seat_limit + ' included &middot; ' + (data.members || []).length + ' in use</div>' +
        '<div class="qty-row" style="margin-top:14px;">' +
          (hasSubscription
            ? '<button class="btn" onclick="buySeat(this)">Buy extra seat</button>'
            : '') +
          '<button class="btn" onclick="openBillingPortal()">Manage billing</button>' +
        '</div>' +
        '<div id="seat-billing-status" class="settings-block-hint"></div>' +
        (hasSubscription
          ? '<div class="status-line"><span class="status-dot"></span>Subscription active' + (renewsAt ? ', renews ' + renewsAt : '') + '</div>'
          : '<div class="status-line"><span class="status-dot" style="background:var(--slate-400);"></span>No active subscription</div>') +
      '</div>' +
    '</div>';
}}

loadOverview();
loadUptimeStat();
loadUsage();
loadPlanBadge();
</script>
"""


# ---------------------------------------------------------------------------
# Security findings page.
# ---------------------------------------------------------------------------
SECURITY_HTML = _page_head("Security findings — {repo} — Aletheore") + _shell(
    "security",
    _topbar("Security findings")
    + """
    <section class="section">
      <div class="section-head">
        <div class="section-title"><i class="ti ti-shield-check" aria-hidden="true"></i>Findings</div>
        <span class="section-sub">Every claim below cites the exact file and line it was found at.</span>
      </div>
      <div class="section-body" id="security-body"><div class="empty-state">Loading&hellip;</div></div>
    </section>
"""
) + f"""
<script>
{FETCH_HELPERS}
{PAGE_HEAD_JS}

function findingActionButtonHtml(findingType, f, label, handler) {{
  if (findingType === 'secret') {{
    return '<button class="btn" data-type="secret" data-path="' + escapeHtml(f.path) +
      '" data-pattern="' + escapeHtml(f.pattern) + '" data-match-preview="' + escapeHtml(f.match_preview) +
      '" onclick="' + handler + '(this)">' + label + '</button>';
  }}
  if (findingType === 'static_analysis') {{
    return '<button class="btn" data-type="static_analysis" data-path="' + escapeHtml(f.path) +
      '" data-line="' + f.line + '" data-tool="' + escapeHtml(f.tool) + '" data-rule-id="' + escapeHtml(f.rule_id) +
      '" onclick="' + handler + '(this)">' + label + '</button>';
  }}
  return '<button class="btn" data-type="vulnerability" data-ecosystem="' + escapeHtml(f.ecosystem) +
    '" data-package="' + escapeHtml(f.package) + '" data-advisory-id="' + escapeHtml(f.advisory_id) +
    '" onclick="' + handler + '(this)">' + label + '</button>';
}}

function findingPayloadFromButton(btn) {{
  if (btn.dataset.type === 'secret') {{
    return {{
      finding_type: 'secret',
      finding: {{ path: btn.dataset.path, pattern: btn.dataset.pattern, match_preview: btn.dataset.matchPreview }},
    }};
  }}
  if (btn.dataset.type === 'static_analysis') {{
    return {{
      finding_type: 'static_analysis',
      finding: {{ path: btn.dataset.path, line: Number(btn.dataset.line), tool: btn.dataset.tool, rule_id: btn.dataset.ruleId }},
    }};
  }}
  return {{
    finding_type: 'vulnerability',
    finding: {{ ecosystem: btn.dataset.ecosystem, package: btn.dataset.package, advisory_id: btn.dataset.advisoryId }},
  }};
}}

async function dismissFinding(btn) {{
  btn.disabled = true;
  await apiPost(base + '/findings/dismiss', findingPayloadFromButton(btn));
  loadSecurity();
}}

async function undismissFinding(btn) {{
  btn.disabled = true;
  await apiPost(base + '/findings/undismiss', findingPayloadFromButton(btn));
  loadSecurity();
}}

function toggleDismissedFindings(event, dismissedSecretFindings, dismissedVulnFindings, dismissedStaticAnalysisFindings) {{
  event.preventDefault();
  const el = document.getElementById('dismissed-findings-body');
  if (el.style.display !== 'none') {{ el.style.display = 'none'; return; }}
  let rows = '';
  dismissedSecretFindings.forEach(function (f) {{
    rows += '<tr><td><span class="finding-title">Possible ' + escapeHtml(f.pattern) + ' secret</span></td>' +
      '<td class="finding-cite">' + escapeHtml(f.path) + ':' + f.line + '</td>' +
      '<td>' + findingActionButtonHtml('secret', f, 'Undismiss', 'undismissFinding') + '</td></tr>';
  }});
  dismissedVulnFindings.forEach(function (f) {{
    rows += '<tr><td><span class="finding-title">' + escapeHtml(f.advisory_id) + ': ' + escapeHtml(f.summary || 'known vulnerability') + '</span></td>' +
      '<td class="finding-cite">' + escapeHtml(f.package) + '@' + escapeHtml(f.installed_version) + '</td>' +
      '<td>' + findingActionButtonHtml('vulnerability', f, 'Undismiss', 'undismissFinding') + '</td></tr>';
  }});
  (dismissedStaticAnalysisFindings || []).forEach(function (f) {{
    rows += '<tr><td><span class="finding-title">' + escapeHtml(f.message) + '</span></td>' +
      '<td class="finding-cite">' + escapeHtml(f.path) + ':' + f.line + '</td>' +
      '<td>' + findingActionButtonHtml('static_analysis', f, 'Undismiss', 'undismissFinding') + '</td></tr>';
  }});
  el.innerHTML = '<table class="findings" style="opacity:0.7"><thead><tr><th>Finding</th><th>Evidence</th><th></th></tr></thead><tbody>' + rows + '</tbody></table>';
  el.style.display = 'block';
}}

async function loadSecurity() {{
  const res = await apiGet(base);
  const body = document.getElementById('security-body');
  if (!res) return;
  if (!res.ok) {{ body.innerHTML = '<div class="empty-state">Unavailable.</div>'; return; }}
  const data = await res.json();
  const history = data.history || [];
  if (history.length === 0) {{ body.innerHTML = '<div class="empty-state">No scans yet.</div>'; return; }}
  const dismissedKeys = data.dismissed_finding_keys || {{ secret: [], vulnerability: [], static_analysis: [] }};
  const evidence = history[0].evidence || {{}};
  const security = evidence.security || {{}};
  const allSecretFindings = ((security.secrets || {{}}).findings || []).filter(function (f) {{ return !f.likely_placeholder && !f.accepted; }});
  const allVulnFindings = (security.dependency_vulnerabilities || {{}}).findings || [];
  const allStaticAnalysisFindings = (security.static_analysis || {{}}).findings || [];

  const secretFindings = allSecretFindings.filter(function (f) {{ return dismissedKeys.secret.indexOf(findingIdentityKey('secret', f)) === -1; }});
  const vulnFindings = allVulnFindings.filter(function (f) {{ return dismissedKeys.vulnerability.indexOf(findingIdentityKey('vulnerability', f)) === -1; }});
  const staticAnalysisFindings = allStaticAnalysisFindings.filter(function (f) {{ return (dismissedKeys.static_analysis || []).indexOf(findingIdentityKey('static_analysis', f)) === -1; }});
  const dismissedSecretFindings = allSecretFindings.filter(function (f) {{ return dismissedKeys.secret.indexOf(findingIdentityKey('secret', f)) !== -1; }});
  const dismissedVulnFindings = allVulnFindings.filter(function (f) {{ return dismissedKeys.vulnerability.indexOf(findingIdentityKey('vulnerability', f)) !== -1; }});
  const dismissedStaticAnalysisFindings = allStaticAnalysisFindings.filter(function (f) {{ return (dismissedKeys.static_analysis || []).indexOf(findingIdentityKey('static_analysis', f)) !== -1; }});
  const dismissedCount = dismissedSecretFindings.length + dismissedVulnFindings.length + dismissedStaticAnalysisFindings.length;

  window._dismissedSecretFindings = dismissedSecretFindings;
  window._dismissedVulnFindings = dismissedVulnFindings;
  window._dismissedStaticAnalysisFindings = dismissedStaticAnalysisFindings;

  if (secretFindings.length === 0 && vulnFindings.length === 0 && staticAnalysisFindings.length === 0) {{
    body.innerHTML = '<div class="empty-state">No open findings.</div>';
    if (dismissedCount > 0) {{
      body.innerHTML += '<p class="section-sub" style="margin-top:16px"><a href="#" onclick="toggleDismissedFindings(event, window._dismissedSecretFindings, window._dismissedVulnFindings, window._dismissedStaticAnalysisFindings)">Show dismissed (' + dismissedCount + ')</a></p>' +
        '<div id="dismissed-findings-body" style="display:none"></div>';
    }}
    return;
  }}
  let rows = '';
  secretFindings.forEach(function (f) {{
    rows += '<tr><td><span class="sev-stripe critical"></span><span class="finding-title">Possible ' + escapeHtml(f.pattern) + ' secret</span></td>' +
      '<td class="finding-cite">' + escapeHtml(f.path) + ':' + f.line + '</td>' +
      '<td><span class="chip critical">Critical</span></td>' +
      '<td>' + findingActionButtonHtml('secret', f, 'Dismiss', 'dismissFinding') + '</td></tr>';
  }});
  vulnFindings.forEach(function (f) {{
    rows += '<tr><td><span class="sev-stripe warning"></span><span class="finding-title">' + escapeHtml(f.advisory_id) + ': ' + escapeHtml(f.summary || 'known vulnerability') + '</span></td>' +
      '<td class="finding-cite">' + escapeHtml(f.package) + '@' + escapeHtml(f.installed_version) + '</td>' +
      '<td><span class="chip warning">Warning</span></td>' +
      '<td>' + findingActionButtonHtml('vulnerability', f, 'Dismiss', 'dismissFinding') + '</td></tr>';
  }});
  // Presented as Aletheore's own findings, same as PR review comments -
  // f.tool/f.rule_id (SonarQube/Semgrep/Bearer/gosec/Bandit/Joern/Trivy/PMD) stay
  // out of the visible row; only f.message and the citation are shown.
  staticAnalysisFindings.forEach(function (f) {{
    const sev = staticAnalysisSevChip(f.severity);
    rows += '<tr><td><span class="sev-stripe ' + sev.stripe + '"></span><span class="finding-title">' + escapeHtml(f.message) + '</span></td>' +
      '<td class="finding-cite">' + escapeHtml(f.path) + ':' + f.line + '</td>' +
      '<td><span class="chip ' + sev.chip + '">' + sev.label + '</span></td>' +
      '<td>' + findingActionButtonHtml('static_analysis', f, 'Dismiss', 'dismissFinding') + '</td></tr>';
  }});
  body.innerHTML = '<table class="findings"><thead><tr><th>Finding</th><th>Evidence</th><th>Severity</th><th></th></tr></thead><tbody>' + rows + '</tbody></table>';
  if (dismissedCount > 0) {{
    body.innerHTML += '<p class="section-sub" style="margin-top:16px"><a href="#" onclick="toggleDismissedFindings(event, window._dismissedSecretFindings, window._dismissedVulnFindings, window._dismissedStaticAnalysisFindings)">Show dismissed (' + dismissedCount + ')</a></p>' +
      '<div id="dismissed-findings-body" style="display:none"></div>';
  }}
}}

loadSecurity();
loadPlanBadge();
</script>
"""


# ---------------------------------------------------------------------------
# Dead code page - full paths, never truncated.
# ---------------------------------------------------------------------------
DEADCODE_HTML = _page_head("Dead code — {repo} — Aletheore") + _shell(
    "deadcode",
    _topbar("Dead code")
    + """
    <section class="section">
      <div class="section-head">
        <div class="section-title"><i class="ti ti-trash" aria-hidden="true"></i>Unreferenced modules and dependencies</div>
        <span class="section-sub">Modules nothing else in the repo imports</span>
      </div>
      <div class="section-body" id="deadcode-body"><div class="empty-state">Loading&hellip;</div></div>
    </section>
"""
) + f"""
<script>
{FETCH_HELPERS}
{PAGE_HEAD_JS}

async function loadDeadCode() {{
  const res = await apiGet(base);
  const body = document.getElementById('deadcode-body');
  if (!res) return;
  if (!res.ok) {{ body.innerHTML = '<div class="empty-state">Unavailable.</div>'; return; }}
  const data = await res.json();
  const history = data.history || [];
  if (history.length === 0) {{ body.innerHTML = '<div class="empty-state">No scans yet.</div>'; return; }}
  const evidence = history[0].evidence || {{}};
  const deadCode = (evidence.repository || {{}}).dead_code || {{}};
  const unreachable = deadCode.unreachable_modules || [];
  const unusedDeps = deadCode.unused_dependencies || [];

  if (unreachable.length === 0 && unusedDeps.length === 0) {{
    body.innerHTML = '<div class="empty-state">No dead code detected.</div>';
    return;
  }}
  let html = '<div class="deadcode-list">';
  unreachable.forEach(function (m) {{
    html += '<div class="deadcode-row"><span class="deadcode-path">' + escapeHtml(m.path) + '</span>' +
      '<span class="chip warning">Unreferenced</span><span class="deadcode-meta">' + escapeHtml(m.reason) + '</span></div>';
  }});
  unusedDeps.forEach(function (d) {{
    html += '<div class="deadcode-row"><span class="deadcode-path">' + escapeHtml(d.package) + '</span>' +
      '<span class="chip neutral">Unused dependency</span><span class="deadcode-meta">' + escapeHtml(d.ecosystem) + '</span></div>';
  }});
  html += '</div>';
  body.innerHTML = html;
}}

loadDeadCode();
loadPlanBadge();
</script>
"""


# ---------------------------------------------------------------------------
# Endpoint health page - multi-target configuration + live results + the
# public status API URL.
# ---------------------------------------------------------------------------
HEALTH_HTML = _page_head("Endpoint health — {repo} — Aletheore") + _shell(
    "health",
    _topbar("Endpoint health", sub_id="health-sub", sub_class="page-sub", show_breadcrumb=False)
    + """
    <div class="summary-row" id="summary-row" style="display:none">
      <div class="stat-card"><div class="stat-label">Uptime, last 24h</div><div class="stat-value" id="summary-uptime">&ndash;</div></div>
      <div class="stat-card"><div class="stat-label">Reachable now</div><div class="stat-value" id="summary-reachable">&ndash;</div></div>
      <div class="stat-card"><div class="stat-label">Median latency</div><div class="stat-value" id="summary-latency">&ndash;</div></div>
    </div>
    <div class="plain-section-head" id="endpoints-list-head">
      <h2>Endpoints</h2>
      <div class="count" id="endpoints-list-count"></div>
    </div>
    <div id="health-body"><div class="empty-state">Loading&hellip;</div></div>
    <section class="section">
      <div class="section-head">
        <div class="section-title"><i class="ti ti-target-arrow" aria-hidden="true"></i>Monitored targets</div>
        <span class="section-sub" id="target-usage"></span>
      </div>
      <div class="section-body" id="targets-body"><div class="empty-state">Loading&hellip;</div></div>
    </section>
    <section class="section">
      <div class="section-head">
        <div class="section-title"><i class="ti ti-world" aria-hidden="true"></i>Public status API</div>
        <span class="section-sub">For your own status page</span>
      </div>
      <div class="section-body" id="status-api-body"><div class="empty-state">Loading&hellip;</div></div>
    </section>
    <section class="section">
      <div class="section-head">
        <div class="section-title"><i class="ti ti-list-check" aria-hidden="true"></i>Monitored endpoints</div>
        <span class="section-sub" id="endpoints-usage"></span>
      </div>
      <div class="section-body" id="endpoints-body"><div class="empty-state">Loading&hellip;</div></div>
    </section>
    <section class="section" id="stale-endpoints-section" style="display:none;">
      <div class="section-head">
        <div class="section-title"><i class="ti ti-alert-triangle" aria-hidden="true"></i>Never reachable</div>
        <span class="section-sub">Found in code, checked repeatedly, never once returned successfully</span>
      </div>
      <div class="section-body" id="stale-endpoints-body"></div>
    </section>
"""
) + f"""
<script>
{FETCH_HELPERS}
{PAGE_HEAD_JS}
{CONFIRM_UPGRADE_JS}

const TARGETS_LOCKED_PREVIEW =
  '<div class="token-row"><div><div class="token-label">Production</div><div class="token-meta">https://api.example.com &middot; threshold 3000ms</div></div>' +
  '<button class="btn">Remove</button></div>';

function renderTargetRows(targets) {{
  let rows = '';
  (targets || []).forEach(function (t) {{
    rows += '<div class="token-row"><div><div class="token-label">' + escapeHtml(t.label) + '</div>' +
      '<div class="token-meta">' + escapeHtml(t.base_url) + (t.latency_threshold_ms ? ' &middot; threshold ' + t.latency_threshold_ms + 'ms' : '') + '</div></div>' +
      '<button class="btn" data-target-id="' + t.id + '" onclick="removeTarget(this)">Remove</button></div>';
  }});
  return rows || '<div class="token-meta" style="padding:7px 0;">No targets configured yet.</div>';
}}

async function removeTarget(btn) {{
  btn.disabled = true;
  // Same stuck-button gap found elsewhere on this page (see buySeat's
  // comment): a fetch() rejection used to skip the else branch entirely
  // and leave this disabled forever. finally re-enables on every exit;
  // harmless on success too, since loadTargets() re-renders this row.
  try {{
    const res = await fetch(adminBase + '/health-targets/' + btn.dataset.targetId, {{ method: 'DELETE' }});
    if (res.ok) {{ loadTargets(); loadResults(); }}
  }} finally {{
    btn.disabled = false;
  }}
}}

async function addTarget() {{
  const labelInput = document.getElementById('new-target-label');
  const urlInput = document.getElementById('new-target-url');
  const thresholdInput = document.getElementById('new-target-threshold');
  const status = document.getElementById('target-status');
  const label = labelInput.value.trim();
  const baseUrl = urlInput.value.trim();
  if (!label || !baseUrl) return;
  const res = await fetch(adminBase + '/health-targets', {{
    method: 'POST', headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify({{
      label: label, base_url: baseUrl,
      latency_threshold_ms: thresholdInput.value ? parseInt(thresholdInput.value, 10) : null,
    }}),
  }});
  const data = await res.json().catch(function () {{ return {{}}; }});
  if (!res.ok) {{ status.textContent = data.detail || 'Could not add target.'; status.style.color = 'var(--critical)'; return; }}
  labelInput.value = ''; urlInput.value = ''; thresholdInput.value = '';
  status.textContent = '';
  loadTargets();
  loadResults();
}}

async function loadTargets() {{
  const res = await apiGet(adminBase);
  const body = document.getElementById('targets-body');
  const statusApiBody = document.getElementById('status-api-body');
  if (!res) return;
  if (res.status === 402) {{
    body.innerHTML = lockedFeature('Multiple health check targets are a paid feature', 'Monitor staging, production, or any URL per repository.', TARGETS_LOCKED_PREVIEW);
    statusApiBody.innerHTML = '<div class="empty-state">Available on paid plans.</div>';
    document.getElementById('target-usage').textContent = '';
    return;
  }}
  if (!res.ok) {{ body.innerHTML = '<div class="empty-state">Unavailable.</div>'; return; }}
  const data = await res.json();
  document.getElementById('target-usage').textContent = (data.health_targets || []).length + ' of ' + data.health_target_limit + ' used';
  body.innerHTML = '<div id="target-list">' + renderTargetRows(data.health_targets) + '</div>' +
    '<div class="form-row"><input class="field" id="new-target-label" placeholder="Label, e.g. Production" style="flex:1 1 140px;">' +
    '<input class="field" id="new-target-url" placeholder="https://api.example.com" style="flex:2 1 220px;">' +
    '<input class="field" id="new-target-threshold" type="number" placeholder="Threshold ms" style="flex:1 1 100px;">' +
    '<button class="btn" onclick="addTarget()">Add</button></div>' +
    '<div id="target-status" class="settings-block-hint"></div>';

  const origin = window.location.origin;
  const statusUrl = origin + data.public_status_url;
  const publicStatusEnabled = data.public_status_enabled === true;
  statusApiBody.innerHTML =
    '<label style="display:flex;align-items:center;gap:7px;font-size:12.5px;">' +
    '<input type="checkbox" id="public-status-toggle"' +
    (publicStatusEnabled ? ' checked' : '') +
    '> Make this repo\\'s endpoint status publicly readable</label>' +
    '<div id="public-status-status" class="settings-block-hint"></div>' +
    (publicStatusEnabled
      ? '<div class="copy-box"><input class="field" id="status-url-field" value="' + escapeHtml(statusUrl) + '" readonly>' +
        '<button class="btn" id="copy-status-url">Copy</button></div>' +
        '<div class="settings-block-hint">Unauthenticated and CORS-enabled - safe to call from a public status page.</div>'
      : '<div class="settings-block-hint">Off by default. Endpoint paths, reachability, and latency for this repo are only visible in this dashboard until you turn this on.</div>');

  document.getElementById('public-status-toggle').addEventListener('change', async function (e) {{
    const statusEl = document.getElementById('public-status-status');
    statusEl.textContent = 'Saving...';
    const res = await fetch(adminBase + '/public-status', {{
      method: 'PUT', headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ enabled: e.target.checked }}),
    }});
    if (!res.ok) {{ statusEl.textContent = 'Could not save.'; e.target.checked = !e.target.checked; return; }}
    statusEl.textContent = 'Saved.';
    loadTargets();
  }});

  const copyBtn = document.getElementById('copy-status-url');
  if (copyBtn) {{
    copyBtn.addEventListener('click', function () {{
      navigator.clipboard.writeText(statusUrl).then(function () {{
        copyBtn.textContent = 'Copied';
        setTimeout(function () {{ copyBtn.textContent = 'Copy'; }}, 1500);
      }});
    }});
  }}
}}

async function loadResults() {{
  const res = await apiGet(base + '/health');
  if (!res) return;
  const body = document.getElementById('health-body');
  if (!res.ok) {{ body.innerHTML = '<div class="empty-state">Health data unavailable.</div>'; return; }}
  const data = await res.json();
  const endpoints = data.endpoints || [];
  document.getElementById('endpoints-list-count').textContent = endpoints.length + ' mapped';

  const summaryRow = document.getElementById('summary-row');
  if (endpoints.length === 0) {{
    summaryRow.style.display = 'none';
  }} else {{
    summaryRow.style.display = '';
    const uptimeEl = document.getElementById('summary-uptime');
    if (data.uptime_pct_24h === null || data.uptime_pct_24h === undefined) {{
      uptimeEl.textContent = '–';
      uptimeEl.className = 'stat-value';
    }} else {{
      const pct = data.uptime_pct_24h * 100;
      uptimeEl.textContent = pct.toFixed(1) + '%';
      uptimeEl.className = 'stat-value' + (pct === 100 ? ' success' : pct < 90 ? ' critical' : ' warning');
    }}
    const up = endpoints.filter(function (e) {{ return e.reachable; }}).length;
    const reachableEl = document.getElementById('summary-reachable');
    reachableEl.innerHTML = up + '<span class="of">of ' + endpoints.length + '</span>';
    // The mockup's own .summary-value only defines success/critical (no
    // warning variant) and its own example is styled success at 11 of 12 -
    // any endpoint at all being reachable reads as "up", not an alarm;
    // only zero reachable is critical.
    reachableEl.className = 'stat-value' + (up === 0 ? ' critical' : ' success');
    const latencies = endpoints
      .map(function (e) {{ return e.reachable ? e.latency_ms : null; }})
      .filter(function (l) {{ return l !== null && l !== undefined; }})
      .sort(function (a, b) {{ return a - b; }});
    const latencyEl = document.getElementById('summary-latency');
    if (latencies.length === 0) {{
      latencyEl.textContent = '–';
    }} else {{
      const mid = Math.floor(latencies.length / 2);
      const median = latencies.length % 2 === 0 ? (latencies[mid - 1] + latencies[mid]) / 2 : latencies[mid];
      latencyEl.textContent = Math.round(median) + 'ms';
    }}
  }}

  if (endpoints.length === 0) {{
    body.innerHTML = '<div class="empty-state">No results yet - add a target above.</div>';
    return;
  }}
  const groups = {{}};
  endpoints.forEach(function (e) {{
    const key = e.target_label || 'Unlabeled';
    (groups[key] = groups[key] || []).push(e);
  }});
  let html = '';
  if (data.monitored_endpoint_count < data.total_endpoint_count) {{
    html += '<div class="settings-block-hint" style="margin-bottom:10px;">Monitoring ' +
      data.monitored_endpoint_count + ' of ' + data.total_endpoint_count +
      ' API endpoints found in this repo - see "Monitored endpoints" above to choose which.</div>';
  }}
  let rowIndex = 0;
  const rowMeta = {{}};
  Object.keys(groups).sort().forEach(function (label) {{
    const rows = groups[label];
    const up = rows.filter(function (e) {{ return e.reachable; }}).length;
    html += '<div class="health-target-group"><div class="health-target-group-label">' + escapeHtml(label) +
      '<span class="chip ' + (up === rows.length ? 'success' : 'critical') + '">' + up + ' of ' + rows.length + ' up</span></div>' +
      '<div class="health-grid">';
    rows.forEach(function (e) {{
      const rowId = 'health-row-' + rowIndex;
      rowMeta[rowId] = {{ target_id: e.target_id, method: e.method, path: e.path }};
      const methodClass = (e.method || '').toLowerCase();
      const location = e.evidence_resolution && e.evidence_resolution.file
        ? '<span class="file">' + escapeHtml(e.evidence_resolution.file) + (e.evidence_resolution.line ? ':' + e.evidence_resolution.line : '') + '</span>'
        : '';
      const checkedTitle = 'title="checked ' + compactRelativeTime(e.checked_at) + '"';
      html += '<div class="health-row" id="' + rowId + '" style="cursor:pointer;" onclick="toggleEndpointHistory(\\'' + rowId + '\\')">' +
        '<div class="method ' + escapeHtml(methodClass) + '">' + escapeHtml(e.method) + '</div>' +
        '<div class="path">' + escapeHtml(e.path) + location + '</div>' +
        '<div class="latency">' + (e.reachable ? Math.round(e.latency_ms) + 'ms' : '&mdash;') + '</div>' +
        '<div class="status-pill ' + (e.reachable ? 'up' : 'down') + '" ' + checkedTitle + '><span class="dot"></span>' +
          (e.reachable ? 'up' : (e.status_code ? escapeHtml(String(e.status_code)) + ' · ' + compactRelativeTime(e.checked_at) : 'down')) + '</div></div>' +
        '<div class="health-history" id="' + rowId + '-history" style="display:none;"></div>';
      rowIndex += 1;
    }});
    html += '</div></div>';
  }});
  body.innerHTML = html;
  window._healthRowMeta = rowMeta;

  const staleEndpoints = data.stale_endpoints || [];
  const staleSection = document.getElementById('stale-endpoints-section');
  const staleBody = document.getElementById('stale-endpoints-body');
  if (staleEndpoints.length === 0) {{
    staleSection.style.display = 'none';
  }} else {{
    staleSection.style.display = '';
    let staleHtml = '<div class="health-grid">';
    staleEndpoints.forEach(function (e) {{
      const location = e.file
        ? '<span class="file">' + escapeHtml(e.file) + (e.line ? ':' + e.line : '') + '</span>'
        : '';
      staleHtml += '<div class="health-row">' +
        '<div class="method">' + escapeHtml(e.method) + '</div>' +
        '<div class="path">' + escapeHtml(e.path) + location + '</div>' +
        '<div class="latency">&mdash;</div>' +
        '<div class="status-pill down"><span class="dot"></span>' + e.check_count + ' checks</div></div>';
    }});
    staleHtml += '</div>';
    staleBody.innerHTML = staleHtml;
  }}
}}

async function toggleEndpointHistory(rowId) {{
  const panel = document.getElementById(rowId + '-history');
  if (!panel) return;
  if (panel.style.display !== 'none') {{ panel.style.display = 'none'; return; }}

  const meta = (window._healthRowMeta || {{}})[rowId];
  if (!meta) return;
  panel.style.display = '';
  panel.innerHTML = '<div class="empty-state">Loading&hellip;</div>';

  const params = new URLSearchParams({{ method: meta.method, path: meta.path }});
  if (meta.target_id !== null && meta.target_id !== undefined) {{ params.set('target_id', meta.target_id); }}
  const res = await apiGet(base + '/health/history?' + params.toString());
  if (!res || !res.ok) {{ panel.innerHTML = '<div class="empty-state">History unavailable.</div>'; return; }}
  const data = await res.json();
  const checks = data.checks || [];
  if (checks.length === 0) {{ panel.innerHTML = '<div class="empty-state">No history yet.</div>'; return; }}

  let html = '<div class="health-history-list">';
  checks.forEach(function (c) {{
    html += '<div class="health-history-row">' +
      '<div class="status-pill ' + (c.reachable ? 'up' : 'down') + '"><span class="dot"></span>' +
        (c.reachable ? 'up' : (c.status_code ? escapeHtml(String(c.status_code)) : 'down')) + '</div>' +
      '<div class="latency">' + (c.reachable ? Math.round(c.latency_ms) + 'ms' : '&mdash;') + '</div>' +
      '<div class="health-checked">' + compactRelativeTime(c.checked_at) + '</div></div>';
  }});
  html += '</div>';
  panel.innerHTML = html;
}}

function renderEndpointRows(endpoints) {{
  return endpoints.map(function (e, i) {{
    return '<label class="health-endpoint-row" style="display:flex;align-items:center;gap:8px;padding:5px 0;font-size:12.5px;">' +
      '<input type="checkbox" class="endpoint-select-checkbox" data-index="' + i + '"' +
      (e.monitored ? ' checked' : '') + '>' +
      '<span class="chip" style="min-width:44px;text-align:center;">' + escapeHtml(e.method || '') + '</span>' +
      '<span>' + escapeHtml(e.path || '') + '</span></label>';
  }}).join('');
}}

async function loadEndpoints() {{
  const res = await apiGet(adminBase + '/health-endpoints');
  const body = document.getElementById('endpoints-body');
  const usage = document.getElementById('endpoints-usage');
  if (!res) return;
  if (res.status === 402) {{ body.innerHTML = '<div class="empty-state">Available on paid plans.</div>'; usage.textContent = ''; return; }}
  if (!res.ok) {{ body.innerHTML = '<div class="empty-state">Unavailable.</div>'; return; }}
  const data = await res.json();
  window._healthEndpoints = data.endpoints || [];
  usage.textContent = data.monitored_endpoint_count + ' of ' + data.total_endpoint_count + ' monitored';
  if (data.endpoints.length === 0) {{
    body.innerHTML = '<div class="empty-state">No API endpoints found in this repo yet.</div>';
    return;
  }}
  // candidate_count is the real "eligible to be monitored" set BEFORE the
  // cap - every endpoint in auto mode, or exactly the still-real selected
  // ones in manual mode. Using total_endpoint_count (the whole repo) here
  // instead would be wrong in manual mode: a repo with 200 endpoints where
  // a customer selected only 5 would wrongly claim their 5-endpoint
  // selection was "still capped", even though nothing of theirs was cut off.
  const atCap = data.candidate_count > data.cap;
  let html = '';
  if (data.mode === 'auto' && atCap) {{
    html += '<div class="settings-block-hint" style="margin-bottom:8px;">This repo has more endpoints (' +
      data.total_endpoint_count + ') than Aletheore checks at once (' + data.cap + ') - the first ' +
      data.cap + ' below (in scan order) are monitored by default. Uncheck/check below and Save to choose exactly which ones instead.</div>';
  }} else if (data.mode === 'manual') {{
    html += '<div class="settings-block-hint" style="margin-bottom:8px;">You have chosen exactly which endpoints are monitored below' +
      (atCap ? ' (your selection has more than ' + data.cap + ' - only the first ' + data.cap + ', sorted by path, are checked)' : '') + '.</div>';
  }}
  html += '<details><summary style="cursor:pointer;font-size:12.5px;color:var(--muted);">Choose endpoints (' +
    data.endpoints.length + ')</summary><div style="margin-top:8px;max-height:320px;overflow-y:auto;">' +
    renderEndpointRows(data.endpoints) + '</div>' +
    '<div class="form-row" style="margin-top:8px;">' +
    '<button class="btn" onclick="saveEndpointSelection()">Save selection</button>' +
    (data.mode === 'manual' ? '<button class="btn" onclick="resetEndpointSelection()">Reset to automatic</button>' : '') +
    '</div><div id="endpoints-status" class="settings-block-hint"></div></details>';
  body.innerHTML = html;
}}

async function saveEndpointSelection() {{
  const status = document.getElementById('endpoints-status');
  const checked = Array.from(document.querySelectorAll('.endpoint-select-checkbox:checked'));
  const selections = checked.map(function (el) {{
    const e = window._healthEndpoints[parseInt(el.dataset.index, 10)];
    return {{ method: e.method, path: e.path }};
  }});
  status.textContent = 'Saving...';
  const res = await fetch(adminBase + '/health-endpoints', {{
    method: 'PUT', headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify({{ selections: selections }}),
  }});
  if (!res.ok) {{ status.textContent = 'Could not save.'; return; }}
  loadEndpoints();
  loadResults();
}}

async function resetEndpointSelection() {{
  const status = document.getElementById('endpoints-status');
  status.textContent = 'Resetting...';
  const res = await fetch(adminBase + '/health-endpoints', {{
    method: 'PUT', headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify({{ selections: [] }}),
  }});
  if (!res.ok) {{ status.textContent = 'Could not reset.'; return; }}
  loadEndpoints();
  loadResults();
}}

document.getElementById('health-sub').textContent = 'Live checks against every mapped API endpoint, every 3 minutes';
loadTargets();
loadResults();
loadEndpoints();
loadPlanBadge();
</script>
"""


# ---------------------------------------------------------------------------
# AIRview page.
# ---------------------------------------------------------------------------
WIKI_LOCKED_PREVIEW = (
    '<div class="diagram-wrap"><svg width="400" height="70" viewBox="0 0 400 70"><g font-size="12">'
    '<rect x="10" y="16" width="110" height="38" rx="7" fill="var(--accent-soft)" stroke="var(--accent)"></rect>'
    '<text x="65" y="39" text-anchor="middle" fill="var(--accent-strong)">Checkout API</text>'
    '<rect x="200" y="16" width="110" height="38" rx="7" fill="var(--accent-soft)" stroke="var(--accent)"></rect>'
    '<text x="255" y="39" text-anchor="middle" fill="var(--accent-strong)">Payments</text>'
    '<path d="M120,35 L200,35" stroke="var(--slate-400)" stroke-width="1.3"></path>'
    "</g></svg></div>"
    '<div class="subsystem-grid">'
    '<div class="subsystem-card"><div class="subsystem-name">Checkout API</div><div class="subsystem-desc">Validates carts and creates a payment session before handing off downstream.</div></div>'
    '<div class="subsystem-card"><div class="subsystem-name">Payments</div><div class="subsystem-desc">Wraps the payment SDK and reconciles session state with webhook ingest.</div></div>'
    "</div>"
)

WIKI_HTML = _page_head("AIRview — {repo} — Aletheore") + _shell(
    "wiki",
    _topbar("AIRview")
    + """
    <p class="section-sub" style="margin: -0.6rem 0 1.2rem;">Generated from the real module dependency graph - the same evidence the architecture wiki below reads too, just explorable instead of static.</p>
    <div id="graph-body"><div class="empty-state">Loading&hellip;</div></div>
    <section class="section">
      <div class="section-head">
        <div class="section-title"><i class="ti ti-book-2" aria-hidden="true"></i>Architecture wiki</div>
        <span class="section-sub">Regenerated automatically on every push</span>
      </div>
      <div class="section-body" id="wiki-body"><div class="empty-state">Loading&hellip;</div></div>
    </section>
"""
) + f"""
<script>
{FETCH_HELPERS}
{PAGE_HEAD_JS}
{CONFIRM_UPGRADE_JS}

if (window.mermaid) {{
  mermaid.initialize({{
    startOnLoad: false,
    theme: window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'neutral',
    securityLevel: 'strict',
  }});
}}
let mermaidSeq = 0;
// nodeClickMap (optional): {{ "exact node label": subsystemId }}. When given,
// each matching rendered node becomes its own click target that drills into
// that subsystem instead of the whole diagram's zoom-open - the overview
// diagram's boxes are themselves subsystems, so clicking one should show
// that one rather than only ever re-opening the same fixed diagram.
async function renderDiagram(container, text, nodeClickMap) {{
  if (!text || !window.mermaid) {{ container.remove(); return; }}
  try {{
    const id = 'mmd-' + (mermaidSeq++);
    const {{ svg }} = await mermaid.render(id, text);
    container.innerHTML = svg;
    const wrap = container.closest('.diagram-wrap');
    if (wrap) {{
      wrap.classList.add('diagram-zoomable');
      wrap.classList.toggle('diagram-drilldown', !!nodeClickMap);
      wrap.onclick = function () {{ openDiagramZoom(svg); }};
    }}
    if (nodeClickMap) {{
      const svgEl = container.querySelector('svg');
      (svgEl ? svgEl.querySelectorAll('.node') : []).forEach(function (node) {{
        const subsystemId = nodeClickMap[node.textContent.trim()];
        if (!subsystemId) return;
        node.classList.add('diagram-node-clickable');
        node.addEventListener('click', function (event) {{
          event.stopPropagation();
          showSubsystem(subsystemId);
        }});
      }});
    }}
  }} catch (e) {{
    container.remove();
  }}
}}

function openDiagramZoom(svgHtml) {{
  closeDiagramZoom();
  const overlay = document.createElement('div');
  overlay.className = 'diagram-zoom-overlay';
  const content = document.createElement('div');
  content.className = 'diagram-zoom-content';
  content.innerHTML = svgHtml;
  const svg = content.querySelector('svg');
  let naturalWidth = 800;
  let naturalHeight = 600;
  if (svg) {{
    const vb = svg.viewBox && svg.viewBox.baseVal;
    naturalWidth = (vb && vb.width) || parseFloat(svg.getAttribute('width')) || naturalWidth;
    naturalHeight = (vb && vb.height) || parseFloat(svg.getAttribute('height')) || naturalHeight;
    svg.style.maxWidth = 'none';
    svg.style.display = 'block';
  }}

  let scale = Math.min(
    1,
    Math.max(0.18, (window.innerWidth - 96) / naturalWidth),
    Math.max(0.18, (window.innerHeight - 150) / naturalHeight)
  );
  let initialScale = scale;

  const toolbar = document.createElement('div');
  toolbar.className = 'diagram-zoom-toolbar';
  toolbar.innerHTML =
    '<button type="button" data-zoom="out">-</button>' +
    '<button type="button" data-zoom="fit">Fit</button>' +
    '<button type="button" data-zoom="in">+</button>' +
    '<span class="diagram-zoom-hint">Scroll to zoom - drag/scroll to pan - Esc closes</span>' +
    '<button type="button" data-zoom="close">Close</button>';

  function updateContentInset() {{
    const scaledWidth = naturalWidth * scale;
    const scaledHeight = naturalHeight * scale;
    content.style.marginLeft = Math.max(0, (overlay.clientWidth - scaledWidth) / 2) + 'px';
    content.style.marginTop = Math.max(0, (overlay.clientHeight - scaledHeight - 120) / 2) + 'px';
  }}

  // Resize the SVG's actual layout dimensions instead of using CSS
  // transform. That keeps the browser scroll area honest, so zoomed-in
  // diagrams remain reachable instead of painting beyond the scroll range.
  function applyScale(anchor) {{
    if (!svg) return;
    svg.style.width = (naturalWidth * scale) + 'px';
    svg.style.height = (naturalHeight * scale) + 'px';
    updateContentInset();
    if (anchor) {{
      overlay.scrollLeft = anchor.x * scale - overlay.clientWidth / 2;
      overlay.scrollTop = anchor.y * scale - overlay.clientHeight / 2;
    }} else {{
      overlay.scrollLeft = Math.max(0, (overlay.scrollWidth - overlay.clientWidth) / 2);
      overlay.scrollTop = Math.max(0, (overlay.scrollHeight - overlay.clientHeight) / 2);
    }}
  }}

  overlay.appendChild(content);
  overlay.appendChild(toolbar);
  overlay.addEventListener('click', function (event) {{
    if (event.target === overlay) closeDiagramZoom();
  }});
  content.addEventListener('click', function (event) {{ event.stopPropagation(); }});
  toolbar.addEventListener('click', function (event) {{
    event.stopPropagation();
    const action = event.target && event.target.dataset ? event.target.dataset.zoom : null;
    if (!action) return;
    if (action === 'close') {{ closeDiagramZoom(); return; }}
    if (action === 'fit') {{
      scale = initialScale;
      applyScale();
      return;
    }}
    const center = {{
      x: (overlay.scrollLeft + overlay.clientWidth / 2) / scale,
      y: (overlay.scrollTop + overlay.clientHeight / 2) / scale,
    }};
    scale = Math.min(4, Math.max(initialScale, scale + (action === 'in' ? 0.18 : -0.18)));
    applyScale(center);
  }});
  overlay.addEventListener('wheel', function (e) {{
    if (e.ctrlKey || e.metaKey || Math.abs(e.deltaY) > Math.abs(e.deltaX)) {{
      const center = {{
        x: (overlay.scrollLeft + overlay.clientWidth / 2) / scale,
        y: (overlay.scrollTop + overlay.clientHeight / 2) / scale,
      }};
      e.preventDefault();
      scale = Math.min(4, Math.max(initialScale, scale + (e.deltaY < 0 ? 0.14 : -0.14)));
      applyScale(center);
    }}
  }}, {{ passive: false }});

  let dragStart = null;
  content.addEventListener('pointerdown', function (event) {{
    dragStart = {{ x: event.clientX, y: event.clientY, left: overlay.scrollLeft, top: overlay.scrollTop }};
    content.setPointerCapture(event.pointerId);
    content.style.cursor = 'grabbing';
  }});
  content.addEventListener('pointermove', function (event) {{
    if (!dragStart) return;
    event.preventDefault();
    overlay.scrollLeft = dragStart.left - (event.clientX - dragStart.x);
    overlay.scrollTop = dragStart.top - (event.clientY - dragStart.y);
  }});
  content.addEventListener('pointerup', function (event) {{
    dragStart = null;
    content.releasePointerCapture(event.pointerId);
    content.style.cursor = 'grab';
  }});
  content.addEventListener('pointercancel', function () {{
    dragStart = null;
    content.style.cursor = 'grab';
  }});

  document.body.appendChild(overlay);
  document.body.style.overflow = 'hidden';
  document.addEventListener('keydown', onDiagramZoomKeydown);
  applyScale();
}}

function onDiagramZoomKeydown(e) {{
  if (e.key === 'Escape') closeDiagramZoom();
}}

function closeDiagramZoom() {{
  const overlay = document.querySelector('.diagram-zoom-overlay');
  if (overlay) overlay.remove();
  document.body.style.overflow = '';
  document.removeEventListener('keydown', onDiagramZoomKeydown);
}}

async function showSubsystem(subsystemId) {{
  const res = await apiGet(base + '/wiki/' + encodeURIComponent(subsystemId));
  if (!res || !res.ok) return;
  const data = await res.json();
  const s = data.subsystem;
  let detail = document.getElementById('subsystem-detail');
  if (!detail) {{
    detail = document.createElement('div');
    detail.id = 'subsystem-detail';
    detail.className = 'subsystem-detail';
    document.getElementById('wiki-body').appendChild(detail);
  }}
  let filesHtml = '';
  (s.files || []).forEach(function (f) {{
    let symbolsHtml = '';
    (f.key_symbols || []).forEach(function (sym) {{
      symbolsHtml += '<div class="subsystem-detail-symbol"><span class="line">' + sym.line + '</span> ' + escapeHtml(sym.name) + ' &mdash; ' + escapeHtml(sym.explanation || '') + '</div>';
    }});
    // The reference page is 500-800 words per file, so it starts collapsed -
    // expanded by default it would bury the file list this view exists to show.
    // (Raised from 250-400 in AIRVIEW_PROMPT_VERSION 5 - see live_wiki.py. The
    // collapse-by-default call itself wasn't revisited; longer pages make that
    // worth reconsidering separately.)
    let detailHtml = '';
    if (f.detail) {{
      detailHtml = '<details class="wiki-md"><summary>Reference</summary>' +
        renderWikiMarkdown(f.detail) + '</details>';
    }}
    filesHtml += '<div class="subsystem-detail-file"><div class="subsystem-detail-path">' + escapeHtml(f.path) + '</div>' +
      '<div class="subsystem-detail-role">' + escapeHtml(f.role) + '</div>' + symbolsHtml + detailHtml + '</div>';
  }});
  detail.innerHTML = '<h3 style="font-size:14px;font-weight:500;margin:0 0 6px;">' + escapeHtml(s.name) + '</h3>' +
    '<p style="font-size:12.5px;color:var(--slate-600);margin:0 0 10px;">' + escapeHtml(s.description) + '</p>' +
    '<div class="diagram-wrap"><div class="mermaid" id="subsystem-diagram"></div></div>' + filesHtml;
  renderDiagram(document.getElementById('subsystem-diagram'), s.diagram_mermaid);
  detail.scrollIntoView({{ behavior: 'smooth', block: 'nearest' }});
}}

async function loadWiki() {{
  const body = document.getElementById('wiki-body');
  const planRes = await apiGet(adminBase);
  if (!planRes) return;
  if (planRes.status === 402) {{
    body.innerHTML = lockedFeature(
      'AIRview is a paid feature',
      'An LLM-written map of this repo, with real dependency diagrams grounded in the scanner\\'s own evidence.',
      {WIKI_LOCKED_PREVIEW!r}
    );
    return;
  }}
  const res = await apiGet(base + '/wiki');
  if (!res) return;
  if (res.status === 402) {{
    body.innerHTML = lockedFeature(
      'AIRview is a paid feature',
      'An LLM-written map of this repo, with real dependency diagrams grounded in the scanner\\'s own evidence.',
      {WIKI_LOCKED_PREVIEW!r}
    );
    return;
  }}
  if (!res.ok) {{ body.innerHTML = '<div class="empty-state">AIRview unavailable.</div>'; return; }}
  const data = await res.json();
  if (!data.overview) {{
    if (data.build_status === 'failed') {{
      body.innerHTML = '<div class="empty-state">AIRview build failed' +
        (data.build_error ? ': ' + escapeHtml(data.build_error) : '.') +
        ' Contact support if this persists.</div>';
    }} else {{
      body.innerHTML = '<div class="empty-state">AIRview hasn\\'t been built yet - it generates automatically shortly after upgrading.</div>';
    }}
    return;
  }}
  // A failed status here means a later incremental update broke, not the
  // first build (which is what the branch above handles) - without this,
  // the customer just sees increasingly stale content with zero signal
  // that anything is wrong.
  let staleBanner = '';
  if (data.build_status === 'failed') {{
    staleBanner = '<div class="empty-state" style="color:var(--critical);margin-bottom:12px;">' +
      'The latest AIRview update failed' + (data.build_error ? ': ' + escapeHtml(data.build_error) : '.') +
      ' Showing the last successful build below - it may be stale.</div>';
  }}
  let html = staleBanner +
    '<div class="wiki-banner"><div class="wiki-banner-text"><b>Built and kept current by a frontier model.</b> Every diagram edge below is a real import in this repo, never inferred.</div></div>' +
    '<div class="diagram-wrap"><div class="mermaid" id="overview-diagram"></div></div>' +
    '<div class="subsystem-grid" id="subsystem-grid"></div>';
  body.innerHTML = html;
  const overviewNodeClickMap = {{}};
  (data.subsystems || []).forEach(function (s) {{ overviewNodeClickMap[s.name] = s.subsystem_id; }});
  renderDiagram(document.getElementById('overview-diagram'), data.overview.diagram_mermaid, overviewNodeClickMap);
  const grid = document.getElementById('subsystem-grid');
  (data.subsystems || []).forEach(function (s) {{
    const card = document.createElement('button');
    card.className = 'subsystem-card';
    card.innerHTML = '<div class="subsystem-name">' + escapeHtml(s.name) + '</div>' +
      '<div class="subsystem-desc">' + escapeHtml(s.description) + '</div>';
    card.addEventListener('click', function () {{ showSubsystem(s.subsystem_id); }});
    grid.appendChild(card);
  }});
  if ((data.subsystems || []).length === 0) {{
    grid.outerHTML = '<div class="empty-state">No subsystems generated yet.</div>';
  }}
}}

let graphNodes = [];
let graphEdges = [];
let graphAllClusters = [];

async function loadGraph() {{
  const container = document.getElementById('graph-body');
  container.innerHTML = '<div class="empty-state">Loading&hellip;</div>';
  const res = await apiGet(base + '/graph');
  if (!res) {{ container.innerHTML = '<div class="empty-state">Graph unavailable.</div>'; return; }}
  if (res.status === 402) {{
    container.innerHTML = lockedFeature(
      'AIRview is a paid feature',
      'A live, explorable map of every module and import in this repo.',
      {WIKI_LOCKED_PREVIEW!r}
    );
    return;
  }}
  if (res.status === 404) {{ container.innerHTML = '<div class="empty-state">No dependency graph available yet.</div>'; return; }}
  if (!res.ok) {{ container.innerHTML = '<div class="empty-state">Graph unavailable.</div>'; return; }}
  const data = await res.json();
  graphAllClusters = data.clusters || [];
  graphNodes = data.nodes || [];
  graphEdges = data.edges || [];

  const options = ['<option value="all">All modules (' + graphNodes.length + ')</option>'].concat(
    graphAllClusters.map(function (c) {{
      return '<option value="' + c.id + '">' + escapeHtml(c.name) + ' (' + c.modules.length + ')</option>';
    }})
  );
  container.innerHTML =
    '<div class="graph-card">' +
      '<div class="graph-toolbar">' +
        '<select id="graph-cluster-select" onchange="renderGraphForCluster(this.value)">' + options.join('') + '</select>' +
        '<span class="hint">drag nodes &middot; scroll to zoom &middot; hover to trace imports</span>' +
        '<button class="btn" id="graph-reset-btn" onclick="document.getElementById(&#39;depgraph&#39;)._resetView()">Reset view</button>' +
      '</div>' +
      '<div class="graph-wrap"><svg id="depgraph" viewBox="0 0 900 460"></svg></div>' +
      '<div class="graph-hover-info" id="graph-hover-info">Hover a module to see what it imports.</div>' +
    '</div>' +
    '<div class="cluster-item" id="cluster-summary-item">' +
      '<div class="name">Clusters</div>' +
      '<div class="count" id="cluster-summary">computing&hellip;</div>' +
    '</div>' +
    '<div class="callout">' +
      'This graph is real, not a static image - genuine force-directed physics (repulsion + spring edges), running against this repo\\'s real module names and import edges. The architecture wiki below is generated from the same evidence, just as a static diagram with AI-written subsystem descriptions.' +
    '</div>';

  const namedClusters = graphAllClusters.filter(function (c) {{ return c.modules.length > 1; }});
  const singletonCount = graphAllClusters.length - namedClusters.length;
  let clusterSummaryText = namedClusters.length
    ? namedClusters.map(function (c) {{ return c.name + ' (' + c.modules.length + ' modules)'; }}).join(', ')
    : 'No clusters detected yet.';
  if (singletonCount > 0) {{
    clusterSummaryText += (namedClusters.length ? ', and ' : '') + singletonCount + ' single-module cluster' + (singletonCount === 1 ? '' : 's') + '.';
  }}
  document.getElementById('cluster-summary').textContent = clusterSummaryText;

  // A repo-wide graph is unreadable past a couple hundred nodes and the
  // naive O(n^2) repulsion below would visibly lag - default to the
  // largest cluster instead of "all" once the graph is big enough that
  // either problem would actually show up.
  const select = document.getElementById('graph-cluster-select');
  if (graphNodes.length > 150 && graphAllClusters.length > 0) {{
    const largest = graphAllClusters.reduce(function (a, b) {{ return b.modules.length > a.modules.length ? b : a; }});
    select.value = String(largest.id);
  }}
  renderGraphForCluster(select.value);
}}

function renderGraphForCluster(clusterValue) {{
  const nodeSet = clusterValue === 'all' ? null : new Set(
    (graphAllClusters.find(function (c) {{ return String(c.id) === String(clusterValue); }}) || {{ modules: [] }}).modules
  );
  const nodes = nodeSet ? graphNodes.filter(function (n) {{ return nodeSet.has(n.id); }}) : graphNodes;
  const nodeIds = new Set(nodes.map(function (n) {{ return n.id; }}));
  const edges = graphEdges.filter(function (e) {{ return nodeIds.has(e.source) && nodeIds.has(e.target); }});
  runForceGraph(nodes, edges);
}}

function runForceGraph(rawNodes, rawEdges) {{
  const existingSvg = document.getElementById('depgraph');
  // Every cluster-filter change calls this again - without stopping the
  // previous run's loop first, each switch left its old
  // requestAnimationFrame(tick) chain running forever alongside the new
  // one (a real bug: found in review, before this the graph never
  // settled and burned CPU indefinitely on repeated filter changes).
  if (existingSvg._stopGraphTick) existingSvg._stopGraphTick();

  const W = 900, H = 460;
  const degree = {{}};
  rawEdges.forEach(function (e) {{ degree[e.source] = (degree[e.source] || 0) + 1; degree[e.target] = (degree[e.target] || 0) + 1; }});
  const nodes = rawNodes.map(function (n, i) {{
    const angle = (i / Math.max(rawNodes.length, 1)) * Math.PI * 2;
    return {{
      id: n.id, hub: (degree[n.id] || 0) >= 6,
      x: W / 2 + Math.cos(angle) * 220, y: H / 2 + Math.sin(angle) * 160,
      vx: 0, vy: 0, fx: null, fy: null,
    }};
  }});
  const byId = {{}};
  nodes.forEach(function (n) {{ byId[n.id] = n; }});
  const edges = rawEdges
    .map(function (e) {{ return {{ source: byId[e.source], target: byId[e.target], ambiguous: !!e.ambiguous }}; }})
    .filter(function (e) {{ return e.source && e.target; }});
  const neighborsOf = {{}};
  nodes.forEach(function (n) {{ neighborsOf[n.id] = new Set(); }});
  edges.forEach(function (e) {{ neighborsOf[e.source.id].add(e.target.id); neighborsOf[e.target.id].add(e.source.id); }});

  const svg = document.getElementById('depgraph');
  svg.innerHTML = '';
  const svgNS = 'http://www.w3.org/2000/svg';
  const world = document.createElementNS(svgNS, 'g');
  svg.appendChild(world);

  const edgeEls = edges.map(function (e) {{
    const line = document.createElementNS(svgNS, 'line');
    line.setAttribute('class', 'g-edge' + (e.ambiguous ? ' g-edge-ambiguous' : ''));
    world.appendChild(line);
    return line;
  }});
  const nodeEls = nodes.map(function (n) {{
    const g = document.createElementNS(svgNS, 'g');
    g.setAttribute('class', 'g-node' + (n.hub ? ' hub' : ''));
    const circle = document.createElementNS(svgNS, 'circle');
    circle.setAttribute('r', n.hub ? 8 : 5);
    g.appendChild(circle);
    const text = document.createElementNS(svgNS, 'text');
    text.textContent = n.id.length > 40 ? '…' + n.id.slice(-37) : n.id;
    text.setAttribute('x', n.hub ? 12 : 9);
    text.setAttribute('y', 4);
    g.appendChild(text);
    world.appendChild(g);
    n._el = g;
    return g;
  }});

  function render() {{
    edges.forEach(function (e, i) {{
      edgeEls[i].setAttribute('x1', e.source.x); edgeEls[i].setAttribute('y1', e.source.y);
      edgeEls[i].setAttribute('x2', e.target.x); edgeEls[i].setAttribute('y2', e.target.y);
    }});
    nodes.forEach(function (n) {{ n._el.setAttribute('transform', 'translate(' + n.x + ',' + n.y + ')'); }});
  }}

  let dragging = null;
  let ticking = true;
  function tick() {{
    if (!ticking) return;
    for (let i = 0; i < nodes.length; i++) {{
      for (let j = i + 1; j < nodes.length; j++) {{
        const a = nodes[i], b = nodes[j];
        const dx = a.x - b.x, dy = a.y - b.y;
        const dist2 = dx * dx + dy * dy || 0.01;
        const dist = Math.sqrt(dist2);
        const force = 900 / dist2;
        const fx = (dx / dist) * force, fy = (dy / dist) * force;
        a.vx += fx; a.vy += fy; b.vx -= fx; b.vy -= fy;
      }}
    }}
    edges.forEach(function (e) {{
      const a = e.source, b = e.target;
      const dx = b.x - a.x, dy = b.y - a.y;
      const dist = Math.sqrt(dx * dx + dy * dy) || 0.01;
      const force = (dist - 100) * 0.02;
      const fx = (dx / dist) * force, fy = (dy / dist) * force;
      a.vx += fx; a.vy += fy; b.vx -= fx; b.vy -= fy;
    }});
    let totalSpeed = 0;
    nodes.forEach(function (n) {{
      n.vx += (W / 2 - n.x) * 0.001; n.vy += (H / 2 - n.y) * 0.001;
      if (n.fx != null) {{ n.x = n.fx; n.y = n.fy; n.vx = 0; n.vy = 0; return; }}
      n.vx *= 0.82; n.vy *= 0.82;
      n.x = Math.max(16, Math.min(W - 16, n.x + n.vx));
      n.y = Math.max(16, Math.min(H - 16, n.y + n.vy));
      totalSpeed += Math.abs(n.vx) + Math.abs(n.vy);
    }});
    render();
    // Stop scheduling once the layout has settled (or there's nothing to
    // move) instead of running an O(n^2) loop forever - a drag restarts it
    // below, since a dragged node's own movement still needs to push its
    // neighbors even after the rest had gone quiet.
    if (dragging || totalSpeed > 0.05) {{
      requestAnimationFrame(tick);
    }} else {{
      ticking = false;
    }}
  }}
  tick();
  svg._stopGraphTick = function () {{ ticking = false; }};

  svg.onpointerdown = function (ev) {{
    const target = ev.target.closest('.g-node');
    if (!target) return;
    dragging = nodes[nodeEls.indexOf(target)];
    svg.setPointerCapture(ev.pointerId);
    if (!ticking) {{ ticking = true; tick(); }}
  }};
  svg.onpointermove = function (ev) {{
    if (!dragging) return;
    const rect = svg.getBoundingClientRect();
    dragging.fx = (ev.clientX - rect.left) * (W / rect.width);
    dragging.fy = (ev.clientY - rect.top) * (H / rect.height);
  }};
  svg.onpointerup = function () {{ if (dragging) {{ dragging.fx = null; dragging.fy = null; dragging = null; }} }};

  let zoom = 1;
  svg.onwheel = function (ev) {{
    ev.preventDefault();
    zoom = Math.max(0.5, Math.min(2.5, zoom - ev.deltaY * 0.001));
    world.setAttribute('transform', 'scale(' + zoom + ')');
  }};
  svg._resetView = function () {{
    zoom = 1;
    world.setAttribute('transform', 'scale(1)');
    nodes.forEach(function (n) {{ n.fx = null; n.fy = null; }});
    if (!ticking) {{ ticking = true; tick(); }}
  }};

  const hoverInfo = document.getElementById('graph-hover-info');
  nodeEls.forEach(function (g, i) {{
    g.onpointerenter = function () {{
      const n = nodes[i];
      const neighbors = neighborsOf[n.id];
      nodeEls.forEach(function (g2, j) {{ g2.classList.toggle('dim', j !== i && !neighbors.has(nodes[j].id)); }});
      edgeEls.forEach(function (el, k) {{
        const lit = edges[k].source.id === n.id || edges[k].target.id === n.id;
        el.classList.toggle('lit', lit); el.classList.toggle('dim', !lit);
      }});
      hoverInfo.textContent = n.id + '  ->  ' + (Array.from(neighbors).join(', ') || '(no resolved imports)');
    }};
    g.onpointerleave = function () {{
      nodeEls.forEach(function (g2) {{ g2.classList.remove('dim'); }});
      edgeEls.forEach(function (el) {{ el.classList.remove('lit'); el.classList.remove('dim'); }});
      hoverInfo.textContent = 'Hover a module to see what it imports.';
    }};
  }});
}}

loadGraph();
loadWiki();
loadPlanBadge();
</script>
"""


# ---------------------------------------------------------------------------
# Docs page - grounded API reference, AI-filled/polished where the source
# had no docstring, always marked distinct from a verbatim source comment.
# ---------------------------------------------------------------------------
DOCS_LOCKED_PREVIEW = (
    '<div class="docs-grid">'
    '<details class="docs-module-card" open><summary class="docs-module-summary">'
    '<div><div class="docs-module-title"><i class="ti ti-file-code"></i><span class="docs-module-path">checkout/session.py</span></div>'
    '<div class="docs-module-sub">1 documented symbol</div></div>'
    '<div class="docs-module-meta"><span class="docs-chip">1 symbol</span><i class="ti ti-chevron-down docs-module-chevron"></i></div>'
    '</summary><div class="docs-module-content"><pre class="docs-module-body">'
    '# checkout/session.py\n\n### `create_session(cart_id)`\n\nValidates a cart and opens a new payment session.\n\n`checkout/session.py:42`'
    '</pre></div></details>'
    "</div>"
)

DOCS_HTML = _page_head("Docs — {repo} — Aletheore") + _shell(
    "docs",
    _topbar(
        "Docs",
        show_breadcrumb=False,
        margin_bottom="0",
        right_html='<a class="btn" id="docs-download-link" href="#" download style="display:none">Export as Markdown</a>',
    )
    + """
    <div id="docs-stat-row"></div>
    <div class="main-grid">
      <div class="main-col">
        <div id="docs-body"><div class="empty-state">Loading&hellip;</div></div>

        <section class="section" id="docs-repo-commit-section" style="display:none">
          <div class="section-head">
            <div class="section-title"><i class="ti ti-git-pull-request" aria-hidden="true"></i>Commit to repo</div>
            <span class="section-sub">Also push this reference into your repo as .aletheore/docs/API.md</span>
          </div>
          <div class="section-body" id="docs-repo-commit-body"><div class="empty-state">Loading&hellip;</div></div>
        </section>
      </div>
      <aside class="rail" id="docs-rail" style="display:none">
        <div class="rail-card">
          <h3>Recently updated</h3>
          <div id="rail-recently-updated"></div>
        </div>
        <div class="rail-card">
          <h3>Hotspots</h3>
          <div id="rail-hotspots"></div>
        </div>
        <div class="rail-card">
          <h3>Jump to</h3>
          <a class="rail-link" href="#" id="rail-jump-all">All modules</a>
          <a class="rail-link" href="#" id="rail-jump-ai">AI-assisted only</a>
          <a class="rail-link" href="#" id="rail-jump-undoc">Undocumented symbols</a>
        </div>
      </aside>
    </div>
"""
) + f"""
<script>
{FETCH_HELPERS}
{PAGE_HEAD_JS}
{CONFIRM_UPGRADE_JS}

function docsSymbolCount(markdown) {{
  const matches = markdown.match(/^###\\s+/gm);
  return matches ? matches.length : 0;
}}

function docsHasAiText(markdown) {{
  return markdown.indexOf('AI-generated') !== -1 || markdown.indexOf('AI-polished') !== -1;
}}

function docsHasUndocumented(markdown) {{
  // docs_reference.py's own UNDOCUMENTED marker for a symbol with no
  // extracted docstring - real grounding text, not a guess at this
  // module's shape from the outside.
  return markdown.indexOf('Undocumented - no docstring found.') !== -1;
}}

// docs_reference.py's build_module_reference() produces an exact,
// deterministic markdown shape - "# path", then "## Classes"/"## Functions"
// sections, each symbol as "### `signature`", a body (docstring, an AI
// marker, or the UNDOCUMENTED marker), then a "`path:line`" citation line.
// Parsed here rather than rendered as a raw <pre> block, so the module
// card's expanded body can show a real symbol list (name, kind, citation,
// AI/undocumented flags) matching docs.html's own row-per-symbol layout,
// instead of visible markdown syntax (headers, backticks, asterisks).
function parseDocsMarkdown(markdown) {{
  const symbols = [];
  let kind = 'function';
  let current = null;
  function flush() {{
    if (!current) return;
    const bodyText = current.bodyLines.join('\\n').trim();
    const citeMatch = bodyText.match(/`([^`]+:\\d+)`\\s*$/);
    const citation = citeMatch ? citeMatch[1] : '';
    const description = (citeMatch ? bodyText.slice(0, citeMatch.index) : bodyText)
      .replace(/\\*\\(AI-generated - no docstring found in source\\)\\*/, '')
      .replace(/\\*\\(AI-polished from the original docstring\\)\\*/, '')
      .replace(/\\*Undocumented - no docstring found\\.\\*/, '')
      .trim();
    symbols.push({{
      name: current.name, kind: current.kind, signature: current.signature, citation: citation,
      isUndocumented: bodyText.indexOf('Undocumented - no docstring found.') !== -1,
      isAi: bodyText.indexOf('AI-generated') !== -1,
      isPolished: bodyText.indexOf('AI-polished') !== -1,
      description: description,
    }});
    current = null;
  }}
  // A line-by-line scan, not a nested split-by-header-level regex (a first
  // attempt at that swallowed every ### symbol header inside its enclosing
  // ## Classes/## Functions block instead of finding it, since ##? matches
  // "#" or "##" but never "###" - the exact real bug a Node-based test
  // against this repo's own real 89-symbol db.py caught before this ever
  // reached a browser).
  markdown.split('\\n').forEach(function (line) {{
    const sectionMatch = line.match(/^##\\s+(Classes|Functions)\\s*$/);
    if (sectionMatch) {{
      flush();
      kind = sectionMatch[1] === 'Classes' ? 'class' : 'function';
      return;
    }}
    const sigMatch = line.match(/^###\\s+`(.+)`\\s*$/);
    if (sigMatch) {{
      flush();
      const signature = sigMatch[1];
      current = {{ kind: kind, signature: signature, name: (signature.match(/^[^(]+/) || [signature])[0].trim(), bodyLines: [] }};
      return;
    }}
    if (current) current.bodyLines.push(line);
  }});
  flush();
  return symbols;
}}

function renderDocsOverview(modulePaths, modules) {{
  const symbolCount = modulePaths.reduce(function (total, path) {{
    return total + docsSymbolCount(modules[path] || '');
  }}, 0);
  const aiCount = modulePaths.filter(function (path) {{ return docsHasAiText(modules[path] || ''); }}).length;
  return '<div class="stat-row">' +
    '<div class="stat-pill"><span class="n">' + modulePaths.length + '</span><span class="l">modules</span></div>' +
    '<div class="stat-pill"><span class="n">' + symbolCount + '</span><span class="l">symbols</span></div>' +
    '<div class="stat-pill"><span class="n">' + aiCount + '</span><span class="l">AI-assisted files</span></div>' +
  '</div>';
}}

function renderDocsModule(modulePath, markdown) {{
  const details = document.createElement('details');
  details.className = 'docs-module-card';
  details.id = 'docs-module-' + modulePath.replace(/[^a-zA-Z0-9]/g, '-');
  const hasAi = docsHasAiText(markdown);
  details.dataset.ai = hasAi ? '1' : '0';
  details.dataset.undocumented = docsHasUndocumented(markdown) ? '1' : '0';
  const summary = document.createElement('summary');
  summary.className = 'docs-module-summary';
  summary.innerHTML =
    '<span class="docs-module-chevron">&#9654;</span>' +
    '<span class="docs-module-path" title="' + escapeHtml(modulePath) + '">' + escapeHtml(modulePath) + '</span>' +
    (hasAi ? '<span class="docs-chip">AI-assisted</span>' : '');
  const content = document.createElement('div');
  content.className = 'docs-module-content';
  const inner = document.createElement('div');
  inner.className = 'docs-module-content-inner';
  const symbols = parseDocsMarkdown(markdown);
  inner.innerHTML = symbols.length
    ? symbols.map(function (s) {{
        const flag = s.isUndocumented
          ? '<span class="flag undocumented">undocumented</span>'
          : (s.isAi || s.isPolished) ? '<span class="flag ai">' + (s.isPolished ? 'AI-polished' : 'AI-generated') + '</span>' : '';
        return '<div class="docs-symbol-row">' +
          '<div class="sig"><span class="name">' + escapeHtml(s.signature) + '</span><span class="kind">' + escapeHtml(s.kind) + '</span>' + flag + '</div>' +
          (s.description ? '<div class="desc">' + escapeHtml(s.description) + '</div>' : '') +
          '<div class="cite">' + escapeHtml(s.citation) + '</div>' +
        '</div>';
      }}).join('')
    : '<div class="docs-symbol-row">No public symbols found.</div>';
  content.appendChild(inner);
  details.appendChild(summary);
  details.appendChild(content);
  return details;
}}

async function loadDocs() {{
  const body = document.getElementById('docs-body');
  const planRes = await apiGet(adminBase);
  if (!planRes) return;
  if (planRes.status === 402) {{
    body.innerHTML = lockedFeature(
      'Docs is a paid feature',
      'A grounded API reference for every public function and class - signatures, docstrings, and file:line citations, with an AI-drafted description (clearly marked) filling gaps the source left undocumented.',
      {DOCS_LOCKED_PREVIEW!r}
    );
    return;
  }}
  const res = await apiGet(base + '/docs');
  if (!res) return;
  if (res.status === 402) {{
    body.innerHTML = lockedFeature(
      'Docs is a paid feature',
      'A grounded API reference for every public function and class - signatures, docstrings, and file:line citations, with an AI-drafted description (clearly marked) filling gaps the source left undocumented.',
      {DOCS_LOCKED_PREVIEW!r}
    );
    return;
  }}
  if (!res.ok) {{ body.innerHTML = '<div class="empty-state">Docs unavailable.</div>'; return; }}
  const data = await res.json();
  const modulePaths = Object.keys(data.modules || {{}});
  const downloadLink = document.getElementById('docs-download-link');
  if (modulePaths.length > 0) {{
    downloadLink.href = base + '/docs/export';
    downloadLink.style.display = '';
  }}
  if (modulePaths.length === 0) {{
    if (data.build_status === 'failed') {{
      body.innerHTML = '<div class="empty-state">Docs build failed' +
        (data.build_error ? ': ' + escapeHtml(data.build_error) : '.') +
        ' Contact support if this persists.</div>';
    }} else {{
      body.innerHTML = '<div class="empty-state">No public functions or classes found yet - Docs generates automatically shortly after a scan.</div>';
    }}
    return;
  }}
  let staleBanner = '';
  if (data.build_status === 'failed') {{
    staleBanner = '<div class="docs-status-banner failed">' +
      'The latest Docs update failed' + (data.build_error ? ': ' + escapeHtml(data.build_error) : '.') +
      ' Showing the last successful build below - it may be stale.</div>';
  }} else if (data.build_error) {{
    // A "ready" status with a build_error means a partial run: some files
    // got documented, others didn't (a transient API error, mid-run). The
    // ones below are real and current - this just says the rest is coming.
    staleBanner = '<div class="docs-status-banner partial">' +
      'The latest Docs update didn\\'t finish everything: ' + escapeHtml(data.build_error) +
      ' It will pick up automatically on the next run.</div>';
  }}
  document.getElementById('docs-stat-row').innerHTML = renderDocsOverview(modulePaths, data.modules || {{}});
  body.innerHTML = staleBanner;
  const list = document.createElement('div');
  list.className = 'docs-grid';
  list.id = 'docs-grid';
  modulePaths.sort().forEach(function (path) {{
    list.appendChild(renderDocsModule(path, data.modules[path]));
  }});
  body.appendChild(list);

  renderDocsRail(data.recently_updated || [], data.hotspots || []);
}}

function renderDocsRail(recentlyUpdated, hotspots) {{
  const rail = document.getElementById('docs-rail');
  rail.style.display = '';
  const recentEl = document.getElementById('rail-recently-updated');
  recentEl.innerHTML = recentlyUpdated.length
    ? recentlyUpdated.slice(0, 6).map(function (f) {{
        return '<div class="rail-row"><span class="path" title="' + escapeHtml(f.path) + '">' + escapeHtml(f.path) + '</span>' +
          '<span class="meta">' + compactRelativeTime(f.last_commit_at) + '</span></div>';
      }}).join('')
    : '<div class="rail-row"><span class="meta">No git history yet.</span></div>';
  const hotspotsEl = document.getElementById('rail-hotspots');
  // "N commits" (churn_count), not a percentile: hotspots is a churn-ranked
  // top-30 slice (HOTSPOT_LIMIT in git_intel/analyzer.py), not per-file
  // churn for the whole repo - ranking within only the visible top 30
  // would misrepresent a file's real standing against every file, most of
  // which have near-zero churn and never appear in this list at all.
  hotspotsEl.innerHTML = hotspots.length
    ? hotspots.slice(0, 6).map(function (h) {{
        return '<div class="rail-row"><span class="path" title="' + escapeHtml(h.path) + '">' + escapeHtml(h.path) + '</span>' +
          '<span class="meta">' + h.churn_count + ' commit' + (h.churn_count === 1 ? '' : 's') + '</span></div>';
      }}).join('')
    : '<div class="rail-row"><span class="meta">No hotspots yet.</span></div>';

  function filterModules(predicate) {{
    document.querySelectorAll('.docs-module-card').forEach(function (card) {{
      card.style.display = predicate(card) ? '' : 'none';
    }});
  }}
  document.getElementById('rail-jump-all').onclick = function (e) {{
    e.preventDefault();
    filterModules(function () {{ return true; }});
    document.getElementById('docs-grid').scrollIntoView({{ behavior: 'smooth', block: 'start' }});
  }};
  document.getElementById('rail-jump-ai').onclick = function (e) {{
    e.preventDefault();
    filterModules(function (card) {{ return card.dataset.ai === '1'; }});
    document.getElementById('docs-grid').scrollIntoView({{ behavior: 'smooth', block: 'start' }});
  }};
  document.getElementById('rail-jump-undoc').onclick = function (e) {{
    e.preventDefault();
    filterModules(function (card) {{ return card.dataset.undocumented === '1'; }});
    document.getElementById('docs-grid').scrollIntoView({{ behavior: 'smooth', block: 'start' }});
  }};
}}

async function loadDocsRepoCommitSettings() {{
  const section = document.getElementById('docs-repo-commit-section');
  const body = document.getElementById('docs-repo-commit-body');
  const res = await apiGet(adminBase + '/docs-repo-commit');
  if (!res || !res.ok) return;  // paid-gate 402, or not yet an admin - main Docs section above already explains why
  section.style.display = '';
  const data = await res.json();
  renderDocsRepoCommit(body, data.enabled, data.pr_number);
}}

function renderDocsRepoCommit(body, enabled, prNumber) {{
  let statusHtml = enabled
    ? '<div class="docs-commit-title">Repo commit is enabled</div><div class="docs-commit-desc">.aletheore/docs/API.md is kept current on a single rolling pull request.'
      + (prNumber ? ' <a href="https://github.com/' + org + '/' + repo + '/pull/' + prNumber + '" target="_blank" rel="noopener">View PR #' + prNumber + '</a>' : ' The first pull request opens the next time Docs regenerates.')
      + '</div>'
    : '<div class="docs-commit-title">Dashboard-only reference</div><div class="docs-commit-desc">Docs is available here, but Aletheore is not opening a docs update pull request in your repository.</div>';
  body.innerHTML = '<div class="docs-commit-card"><div class="docs-commit-copy">' + statusHtml + '</div>' +
    '<button class="btn" id="docs-repo-commit-toggle"><i class="ti ti-git-pull-request" aria-hidden="true"></i>' + (enabled ? 'Disable' : 'Enable') + '</button></div>';
  document.getElementById('docs-repo-commit-toggle').onclick = async function () {{
    const res = await fetch(adminBase + '/docs-repo-commit', {{
      method: 'PUT', headers: {{ 'Content-Type': 'application/json' }}, body: JSON.stringify({{ enabled: !enabled }}),
    }});
    if (res.ok) loadDocsRepoCommitSettings();
  }};
}}

loadDocs();
loadDocsRepoCommitSettings();
loadPlanBadge();
</script>
"""


# ---------------------------------------------------------------------------
# Settings page - team/seats, API tokens, alert webhook.
# ---------------------------------------------------------------------------
SETTINGS_LOCKED_PREVIEW = (
    '<div class="settings-grid">'
    '<div><div class="settings-block-label">Team</div>'
    '<div class="token-row"><div><div class="token-label">you</div><div class="token-meta">2 of 3 seats used</div></div>'
    '<button class="btn">Remove</button></div>'
    '<div class="settings-block-label" style="margin-top:14px;">API tokens</div>'
    '<div class="token-row"><div><div class="token-label">CI pipeline</div><div class="token-meta">created by you &middot; used 3 hours ago</div></div>'
    '<button class="btn">Revoke</button></div></div>'
    '<div><div class="settings-block-label">Alert webhook</div>'
    '<input class="field" value="https://hooks.slack.com/services/..." readonly></div>'
    "</div>"
)

# A function, not a plain module-level constant like the other _HTML pages
# above: its script block needs get_settings().paddle_environment /
# .paddle_client_token (for Paddle.Initialize()) baked in once, and calling
# get_settings() at real module-import time would make importing this file
# require a fully configured settings environment (DATABASE_URL, etc.) just
# to load the module - a regression from every other page in this file.
# lru_cache defers that call to the first real request, after the app has
# actually started with a real settings environment, while still computing
# the page only once for the process's lifetime, matching the other pages'
# "built once" shape.
@lru_cache(maxsize=1)
def _settings_html() -> str:
    return _page_head("Settings — {repo} — Aletheore") + _shell(
    "settings",
    _topbar("Settings")
    + """
    <section class="section">
      <div class="section-head">
        <div class="section-title"><i class="ti ti-key" aria-hidden="true"></i>Settings</div>
      </div>
      <div class="section-body" id="settings-body"><div class="empty-state">Loading&hellip;</div></div>
    </section>
"""
) + f"""
<script src="https://cdn.paddle.com/paddle/v2/paddle.js"></script>
<script>
{FETCH_HELPERS}
{PAGE_HEAD_JS}
{CONFIRM_UPGRADE_JS}

// Only the settings page's own buyCredit() (a one-time credit top-up) needs
// a client-side Paddle.Checkout.open() call - seat purchases go through a
// server-side POST to /seats/buy instead. paddle_client_token is Paddle's
// client-side publishable token, already sent to every browser today via
// the /subscribe page's own Paddle.Initialize() call - not a secret being
// newly exposed here.
// cdn.paddle.com/paddle/v2/paddle.js is a third-party script an adblocker or
// corporate proxy can legitimately block - guarded so a blocked load only
// disables the credit top-up button (buyCredit() re-checks the same guard),
// instead of throwing on this unconditional top-level statement and taking
// down the rest of this script block, including the loadSettings() call at
// the bottom that renders the whole page.
if (typeof Paddle !== "undefined") {{
  Paddle.Environment.set("{get_settings().paddle_environment}");
  // eventCallback is global (Paddle.Initialize() runs once for the whole
  // page) - buyCredit() is the only flow that opens a checkout here, so
  // topup-status is the only element it needs to drive. Without this,
  // buyCredit()'s "Opening checkout..." status never changes again: there
  // was no handler for the overlay actually loading, the user closing it,
  // a mid-checkout error, or a completed payment, so the text just sat
  // there regardless of what happened next.
  Paddle.Initialize({{
    token: "{get_settings().paddle_client_token}",
    eventCallback: function (event) {{
      const status = document.getElementById('topup-status');
      if (!status || !event || !event.name) return;
      if (event.name === 'checkout.loaded') {{
        status.textContent = '';
      }} else if (event.name === 'checkout.completed') {{
        window._creditCheckoutCompleted = true;
        status.textContent = 'Purchase complete - your balance updates once the payment is confirmed.';
        status.style.color = 'var(--success)';
      }} else if (event.name === 'checkout.closed' && !window._creditCheckoutCompleted) {{
        status.textContent = '';
      }} else if (event.name === 'checkout.error') {{
        status.textContent = 'Checkout error - try again.';
        status.style.color = 'var(--critical)';
      }}
    }},
  }});
}}

async function revokeToken(tokenId, btn) {{
  btn.disabled = true;
  // Same stuck-button gap as removeTarget above. Re-enabling in finally on
  // the success path too is harmless: the row (and this button with it)
  // is removed from the DOM right before finally runs.
  try {{
    const res = await fetch(adminBase + '/tokens/' + tokenId, {{ method: 'DELETE' }});
    if (res.ok) {{ btn.closest('.token-row').remove(); }}
  }} finally {{
    btn.disabled = false;
  }}
}}

function renderTokenRows(tokens) {{
  let rows = '';
  (tokens || []).forEach(function (t) {{
    if (t.revoked_at) return;
    rows += '<div class="token-row"><div><div class="token-label">' + escapeHtml(t.label) + '</div>' +
      '<div class="token-meta">created by ' + escapeHtml(t.created_by_github_login) + ' &middot; ' +
      (t.last_used_at ? 'used ' + relativeTime(t.last_used_at) : 'never used') + '</div></div>' +
      '<button class="btn" onclick="revokeToken(' + t.id + ', this)">Revoke</button></div>';
  }});
  return rows || '<div class="token-meta" style="padding:7px 0;">No active tokens.</div>';
}}

async function refreshTokenList() {{
  const res = await apiGet(adminBase);
  if (!res || !res.ok) return;
  const data = await res.json();
  document.getElementById('token-list').innerHTML = renderTokenRows(data.tokens);
}}

function renderMemberRows(members) {{
  let rows = '';
  (members || []).forEach(function (m) {{
    rows += '<div class="token-row"><div><div class="token-label">' + escapeHtml(m.github_login) + '</div>' +
      '<div class="token-meta">added by ' + escapeHtml(m.added_by_github_login) + ' &middot; ' + relativeTime(m.added_at) + '</div></div>' +
      '<button class="btn" data-login="' + escapeHtml(m.github_login) + '" onclick="removeMember(this)">Remove</button></div>';
  }});
  return rows || '<div class="token-meta" style="padding:7px 0;">No members yet.</div>';
}}

async function refreshMembers() {{
  const res = await apiGet(adminBase);
  if (!res || !res.ok) return;
  const data = await res.json();
  document.getElementById('member-list').innerHTML = renderMemberRows(data.members);
  document.getElementById('seat-usage').textContent = (data.members || []).length + ' of ' + data.seat_limit + ' seats used';
}}

async function removeMember(btn) {{
  btn.disabled = true;
  const res = await fetch(adminBase + '/members/' + encodeURIComponent(btn.dataset.login), {{ method: 'DELETE' }});
  if (res.ok) {{ refreshMembers(); }} else {{ btn.disabled = false; }}
}}

async function addMember() {{
  const input = document.getElementById('new-member-login');
  const login = input.value.trim();
  if (!login) return;
  const status = document.getElementById('member-status');
  const res = await fetch(adminBase + '/members', {{
    method: 'POST', headers: {{ 'Content-Type': 'application/json' }}, body: JSON.stringify({{ github_login: login }}),
  }});
  const data = await res.json().catch(function () {{ return {{}}; }});
  if (!res.ok) {{ status.textContent = data.detail || 'Could not add member.'; status.style.color = 'var(--critical)'; return; }}
  input.value = '';
  status.textContent = '';
  refreshMembers();
}}

async function generateToken(btn) {{
  const input = document.getElementById('new-token-label');
  const label = input.value.trim();
  const out = document.getElementById('token-reveal');
  if (!label) {{ out.innerHTML = '<div class="error-banner">Give the token a label first.</div>'; input.focus(); return; }}
  btn.disabled = true;
  // Real gap found by Flash Review on the disabled-button change itself:
  // re-enabling only on the explicit !res.ok branch and the success path
  // left the button stuck disabled forever if fetch() itself rejected
  // (network drop, timeout) or res.json() threw on a malformed response -
  // the function would exit via an unhandled exception with no code path
  // left to run btn.disabled = false. try/finally re-enables on every
  // exit, not just the two branches that were reachable normally.
  try {{
    const res = await fetch(adminBase + '/tokens', {{
      method: 'POST', headers: {{ 'Content-Type': 'application/json' }}, body: JSON.stringify({{ label: label }}),
    }});
    if (!res.ok) {{ out.innerHTML = '<div class="error-banner">Could not create token.</div>'; return; }}
    const data = await res.json();
    input.value = '';
    out.innerHTML = '<div class="token-reveal">' + escapeHtml(data.token) + '<br><span style="color:var(--slate-600);font-family:var(--font-sans);">Copy this now - it will not be shown again.</span></div>';
    refreshTokenList();
  }} finally {{
    btn.disabled = false;
  }}
}}

async function saveLlmSuggestions(checkbox) {{
  const status = document.getElementById('llm-suggestions-status');
  checkbox.disabled = true;
  const res = await fetch(adminBase + '/llm-suggestions', {{
    method: 'PUT', headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify({{ enabled: checkbox.checked }}),
  }});
  const data = await res.json().catch(function () {{ return {{}}; }});
  checkbox.disabled = false;
  if (!res.ok) {{
    checkbox.checked = !checkbox.checked;
    status.textContent = data.detail || 'Could not save.';
    status.style.color = 'var(--critical)';
    return;
  }}
  status.textContent = checkbox.checked
    ? 'Audits will include the model\\'s second opinion.'
    : 'Audits will contain only evidence-backed findings.';
  status.style.color = 'var(--success)';
}}

async function saveWebhook() {{
  const input = document.getElementById('webhook-url-input');
  const status = document.getElementById('webhook-status');
  const res = await fetch(adminBase + '/webhook-url', {{
    method: 'PUT', headers: {{ 'Content-Type': 'application/json' }}, body: JSON.stringify({{ webhook_url: input.value.trim() || null }}),
  }});
  const data = await res.json().catch(function () {{ return {{}}; }});
  status.textContent = res.ok ? 'Saved.' : (data.detail || 'Could not save.');
  status.style.color = res.ok ? 'var(--success)' : 'var(--critical)';
}}

async function sendTestNotification() {{
  const status = document.getElementById('webhook-status');
  status.textContent = 'Sending...';
  status.style.color = 'var(--slate-600)';
  const res = await fetch(adminBase + '/webhook-url/test', {{ method: 'POST' }});
  const data = await res.json().catch(function () {{ return {{}}; }});
  status.textContent = res.ok ? 'Test notification sent.' : (data.detail || 'Could not send test notification.');
  status.style.color = res.ok ? 'var(--success)' : 'var(--critical)';
}}

async function saveAlertEmail() {{
  const input = document.getElementById('alert-email-input');
  const status = document.getElementById('alert-email-status');
  const res = await fetch(adminBase + '/alert-email', {{
    method: 'PUT', headers: {{ 'Content-Type': 'application/json' }}, body: JSON.stringify({{ alert_email: input.value.trim() || null }}),
  }});
  const data = await res.json().catch(function () {{ return {{}}; }});
  status.textContent = res.ok ? 'Saved.' : (data.detail || 'Could not save.');
  status.style.color = res.ok ? 'var(--success)' : 'var(--critical)';
}}

async function sendTestAlertEmail() {{
  const status = document.getElementById('alert-email-status');
  status.textContent = 'Sending...';
  status.style.color = 'var(--slate-600)';
  const res = await fetch(adminBase + '/alert-email/test', {{ method: 'POST' }});
  const data = await res.json().catch(function () {{ return {{}}; }});
  status.textContent = res.ok ? 'Test email sent.' : (data.detail || 'Could not send test email.');
  status.style.color = res.ok ? 'var(--success)' : 'var(--critical)';
}}

async function savePushoverKey() {{
  const input = document.getElementById('pushover-key-input');
  const status = document.getElementById('pushover-key-status');
  const res = await fetch(adminBase + '/pushover-user-key', {{
    method: 'PUT', headers: {{ 'Content-Type': 'application/json' }}, body: JSON.stringify({{ pushover_user_key: input.value.trim() || null }}),
  }});
  const data = await res.json().catch(function () {{ return {{}}; }});
  status.textContent = res.ok ? 'Saved.' : (data.detail || 'Could not save.');
  status.style.color = res.ok ? 'var(--success)' : 'var(--critical)';
}}

async function sendTestPushover() {{
  const status = document.getElementById('pushover-key-status');
  status.textContent = 'Sending...';
  status.style.color = 'var(--slate-600)';
  const res = await fetch(adminBase + '/pushover-user-key/test', {{ method: 'POST' }});
  const data = await res.json().catch(function () {{ return {{}}; }});
  status.textContent = res.ok ? 'Test notification sent.' : (data.detail || 'Could not send test notification.');
  status.style.color = res.ok ? 'var(--success)' : 'var(--critical)';
}}

{BILLING_ACTIONS_JS}
window._reloadUsage = loadSettings;

// The danger zone renders on every plan, including free and lapsed - the
// settings page 402s those customers out of everything else, but locking
// someone out of erasing their own data because their card failed is not
// defensible. It hangs off its own endpoint for the same reason: the main
// /admin GET is plan-gated, this one isn't.
// Same reasoning as loadDangerZone: gated on session + admin rights only,
// no plan or seat check - a payment-failed customer still owns their data
// and needs to be able to leave with it, not just delete it.
function loadExportZone() {{
  const host = document.getElementById('export-zone');
  if (!host) return;
  host.innerHTML =
    '<div class="settings-block">' +
      '<div class="settings-block-label">Export your data</div>' +
      '<div class="settings-block-hint">Download everything stored for this installation - ' +
      'connected repos and their latest findings, team members, health check targets, and ' +
      'usage - as one JSON file. Never includes API tokens themselves or your alert webhook URL.</div>' +
      '<a class="btn" href="' + adminBase + '/export-data" download>Download my data</a>' +
    '</div>';
}}

async function loadDangerZone() {{
  const host = document.getElementById('danger-zone');
  if (!host) return;
  const res = await fetch(adminBase + '/deletion-preview');
  if (!res.ok) return;
  const data = await res.json();
  window._deleteConfirmPhrase = data.account_login;
  const repos = data.repos || [];
  // Deletion is installation-wide but this page is repo-scoped. Naming the
  // other repos is the only honest way to show the real blast radius.
  const repoLine = repos.length
    ? 'This deletes scan history, evidence, findings, and documentation for all ' +
      repos.length + ' repositor' + (repos.length === 1 ? 'y' : 'ies') + ' in this installation: ' +
      repos.map(escapeHtml).join(', ') + '.'
    : 'This deletes everything stored for this installation.';
  host.innerHTML =
    '<div class="danger-zone">' +
      '<div class="settings-block-label">Delete all data</div>' +
      '<div class="settings-block-hint">' + repoLine + '</div>' +
      '<div class="danger-repo-list">API tokens, team seats, and alert settings go too. ' +
      'Members who belong to no other Aletheore installation have their stored email address and ' +
      'sessions erased as well. This cannot be undone.</div>' +
      '<div class="form-row" style="margin-top:10px;">' +
        '<input class="field" id="delete-confirm-input" autocomplete="off" ' +
        'placeholder="Type ' + escapeHtml(data.account_login) + ' to confirm" ' +
        'oninput="syncDeleteButton()">' +
        '<button class="btn" id="send-otp-btn" disabled onclick="requestDeletionOtp()">Send code</button>' +
      '</div>' +
      // Typing the org name only proves you can see this page - it says
      // nothing about who's holding the session. The code, sent to the
      // account's own verified email, is what actually gates the button.
      '<div id="otp-row" class="form-row" style="margin-top:10px;display:none;">' +
        '<input class="field" id="delete-otp-input" autocomplete="off" inputmode="numeric" ' +
        'maxlength="6" placeholder="6-digit code from your email" oninput="syncDeleteButton()">' +
        '<button class="btn btn-danger" id="delete-all-btn" disabled onclick="deleteAllData()">Delete</button>' +
      '</div>' +
      '<div id="delete-status" class="settings-block-hint"></div>' +
    '</div>';
}}

function syncDeleteButton() {{
  const confirmInput = document.getElementById('delete-confirm-input');
  const otpInput = document.getElementById('delete-otp-input');
  const sendBtn = document.getElementById('send-otp-btn');
  const deleteBtn = document.getElementById('delete-all-btn');
  if (!confirmInput || !sendBtn) return;
  const confirmed = confirmInput.value.trim() === window._deleteConfirmPhrase;
  sendBtn.disabled = !confirmed;
  if (deleteBtn) {{
    deleteBtn.disabled = !confirmed || !otpInput || otpInput.value.trim().length !== 6;
  }}
}}

async function requestDeletionOtp() {{
  const btn = document.getElementById('send-otp-btn');
  const status = document.getElementById('delete-status');
  btn.disabled = true;
  status.textContent = 'Sending code...';
  status.style.color = 'var(--slate-600)';
  // Real gap found via audit: same stuck-button shape buySeat/removeSeat
  // were fixed for (see buySeat's comment) - on a genuine network failure
  // (fetch() itself rejects, before res/data exist) this exited via an
  // unhandled exception and the button stayed disabled forever with no
  // recovery short of a page reload. try/finally + syncDeleteButton()
  // covers every exit path uniformly instead of only the res.ok-but-
  // rejected branch; syncDeleteButton() re-derives the real disabled
  // state from the current inputs, so calling it here is correct on
  // success too, not just on error.
  try {{
    const res = await fetch(adminBase + '/delete-all-data/request-otp', {{ method: 'POST' }});
    const data = await res.json().catch(function () {{ return {{}}; }});
    if (!res.ok) {{
      status.textContent = data.detail || 'Could not send a code.';
      status.style.color = 'var(--critical)';
      return;
    }}
    status.textContent = 'Code sent to ' + (data.sent_to || 'your email') + ' - expires in 10 minutes.';
    status.style.color = 'var(--slate-600)';
    document.getElementById('otp-row').style.display = '';
    document.getElementById('delete-otp-input').focus();
  }} finally {{
    syncDeleteButton();
  }}
}}

async function deleteAllData() {{
  const confirmInput = document.getElementById('delete-confirm-input');
  const otpInput = document.getElementById('delete-otp-input');
  const btn = document.getElementById('delete-all-btn');
  const status = document.getElementById('delete-status');
  btn.disabled = true;
  status.textContent = 'Deleting...';
  status.style.color = 'var(--slate-600)';
  // Same stuck-button gap and same fix as requestDeletionOtp above - on a
  // real-money-adjacent, irreversible action, a fetch() rejection must not
  // leave this permanently disabled with no recovery.
  try {{
    const res = await fetch(adminBase + '/delete-all-data', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{
        confirm: confirmInput.value.trim(),
        otp_code: otpInput.value.trim(),
      }}),
    }});
    const data = await res.json().catch(function () {{ return {{}}; }});
    if (!res.ok) {{
      status.textContent = data.detail || 'Could not delete your data.';
      status.style.color = 'var(--critical)';
      // Real gap found by GLM-5.3-Flash reviewing this exact PR with a
      // wider diff-context window: the backend's OTP consume is atomic
      // (claim-and-invalidate in one step, to prevent a replay race - see
      // admin.py's comment on consume_deletion_otp_code), so ANY failure
      // response other than a rate limit (429 - the one status this
      // endpoint can return before ever touching the code) means the
      // submitted code is now dead, even on a downstream failure (Paddle
      // unreachable, 502) that has nothing to do with the code itself.
      // Leaving the stale code sitting in the input implied a same-code
      // retry would work; it never will. Send the flow back to "request a
      // new code" instead of a broken "try again".
      if (res.status !== 429) {{
        otpInput.value = '';
        document.getElementById('otp-row').style.display = 'none';
      }}
      return;
    }}
    // Everything this page reads is gone, including possibly this session -
    // there is nothing left here to re-render, so leave for the marketing site.
    status.textContent = 'Deleted. Signing you out...';
    status.style.color = 'var(--success)';
    window.location.href = '/auth/logout';
  }} finally {{
    syncDeleteButton();
  }}
}}

async function loadSettings() {{
  const body = document.getElementById('settings-body');
  const res = await apiGet(adminBase);
  if (!res) return;
  if (res.status === 402) {{
    body.innerHTML = lockedFeature(
      'API tokens, webhooks, and team seats are paid features',
      'Upgrade to configure them for this repository.',
      {SETTINGS_LOCKED_PREVIEW!r}
    ) +
      // A lapsed/failed-payment subscription lands here too (that's what
      // "plan == free" means to this route) - the one person who needs to
      // fix their card must not be locked out of doing so by the same gate
      // that's blocking everything else on this page.
      '<div class="settings-block-hint" style="text-align:center;margin-top:12px;">' +
      'Already subscribed? <a href="#" onclick="openBillingPortal(); return false;">Manage billing</a>' +
      '</div>' +
      '<div id="export-zone"></div>' +
      '<div id="danger-zone"></div>';
    loadExportZone();
    loadDangerZone();
    return;
  }}
  if (!res.ok) {{
    body.innerHTML = '<div class="empty-state">Settings unavailable.</div>';
    return;
  }}
  const data = await res.json();
  const installation = data.installation;
  window._hasActiveSubscription = !!installation.paddle_subscription_id;
  window._extraSeats = data.extra_seats || 0;
  // Per-installation, only known after this per-request JSON fetch resolves
  // (unlike the Paddle client token/environment, which are static app-wide
  // values baked into the page's own <script> at module-import time) -
  // buyCredit() reads this at click time to authorize its checkout call.
  window._checkoutInstallationToken = data.checkout_installation_token;
  // Static app-wide price id, not per-session like the token above - set
  // once here and never re-fetched. Falsy (null) until the backend's
  // CREDIT_TOPUP_PRICE_ID constant lands; the buy-flow UI below is gated
  // on it so there's no dead, clickable button in the meantime.
  window._creditTopupPriceId = data.credit_topup_price_id;

  const seatBillingHtml = window._hasActiveSubscription
    ? '<div class="form-row">' +
      '<button class="btn" onclick="buySeat(this)">Buy extra seat (${EXTRA_SEAT_PRICE_USD}/mo)</button>' +
      (window._extraSeats > 0 ? '<button class="btn" onclick="removeSeat(this)" style="margin-left:6px;">Remove a seat</button>' : '') +
      '<button class="btn" onclick="openBillingPortal()" style="margin-left:6px;">Manage billing</button>' +
      '</div><div id="seat-billing-status" class="settings-block-hint"></div>'
    : '<div class="settings-block-hint">Extra seats need an active subscription - subscribe first to buy one.</div>';

  const baseCredit = data.base_credit_remaining_usd || 0;
  const topupCredit = data.topup_credit_balance_usd || 0;
  const combinedCredit = baseCredit + topupCredit;
  const usageHtml =
    '<section class="settings-section" id="usage-section">' +
      '<h2>Usage</h2>' +
      '<div class="settings-block">' +
        '<div class="settings-block-label">Credit balance</div>' +
        '<div class="settings-block-hint">$' + baseCredit.toFixed(2) + ' included this month' +
          (topupCredit > 0 ? ' + $' + topupCredit.toFixed(2) + ' purchased (never expires)' : '') +
        '</div>' +
        '<div class="settings-block-hint">$' + combinedCredit.toFixed(2) + ' total available for AI reviews and builds</div>' +
        (data.credit_topup_price_id
          ? '<div class="form-row" style="margin-top: 10px;">' +
              '<div class="stepper">' +
              '<button type="button" onclick="document.getElementById(&#39;topup-amount&#39;).stepDown()" aria-label="Decrease amount">&minus;</button>' +
              '<input type="number" id="topup-amount" min="5" max="1000" step="5" value="10">' +
              '<button type="button" onclick="document.getElementById(&#39;topup-amount&#39;).stepUp()" aria-label="Increase amount">+</button>' +
            '</div>' +
              '<button class="btn" onclick="buyCredit(this)" style="margin-left: 6px;">Buy more credit</button>' +
            '</div>' +
            '<div id="topup-status" class="settings-block-hint"></div>'
          : '<div class="settings-block-hint">Buying additional credit is coming soon.</div>') +
      '</div>' +
    '</section>';

  // Managed audit content sits in the LEFT column deliberately, not with
  // Alert channels/Endpoint health where it reads more naturally - Alert
  // channels alone (3 webhook/email/Pushover forms) is taller than the
  // other 4 cards combined, so grid's one implicit row sizes to whichever
  // column holds it regardless of align-items, and the other column just
  // shows blank space below its shorter content. Pairing Managed audit
  // content with Team/API tokens instead keeps both columns close to
  // even height; moving it back re-lopsides the layout.
  body.innerHTML =
    '<div class="settings-grid">' +
      '<div>' +
        '<div class="settings-block">' +
          '<div class="settings-block-label">Team &middot; <span id="seat-usage">' + (data.members || []).length + ' of ' + data.seat_limit + ' seats used</span></div>' +
          '<div id="member-list">' + renderMemberRows(data.members) + '</div>' +
          '<div class="form-row"><input class="field" id="new-member-login" placeholder="GitHub username">' +
          '<button class="btn" onclick="addMember()">Add</button></div>' +
          '<div id="member-status" class="settings-block-hint"></div>' +
          seatBillingHtml +
        '</div>' +
        '<div class="settings-block">' +
          '<div class="settings-block-label">API tokens</div>' +
          '<div id="token-list">' + renderTokenRows(data.tokens) + '</div>' +
          '<div class="form-row"><input class="field" id="new-token-label" placeholder="Token label, e.g. CI pipeline">' +
          '<button class="btn" onclick="generateToken(this)">Generate</button></div>' +
          '<div id="token-reveal"></div>' +
          '<div class="settings-block-hint">Used to authenticate the CLI (<code>aletheore login</code> or <code>ALETHEORE_API_TOKEN</code>) and the MCP server\\'s <code>aletheore_managed_audit</code> tool against this installation\\'s hosted managed audits, and to send runtime events from your app into Aletheore. Give each token a label so you can tell them apart later, and revoke one any time without affecting the others.</div>' +
        '</div>' +
        '<div class="settings-block">' +
          '<div class="settings-block-label">Managed audit content</div>' +
          '<label style="display:flex;align-items:center;gap:7px;font-size:12.5px;">' +
          '<input type="checkbox" id="llm-suggestions-toggle"' +
          (installation.llm_suggestions_enabled === false ? '' : ' checked') +
          ' onchange="saveLlmSuggestions(this)">' +
          'Include the model\\'s second opinion' +
          '</label>' +
          '<div id="llm-suggestions-status" class="settings-block-hint"></div>' +
          '<div class="settings-block-hint">Every finding in an audit is tied to a citation in your code. ' +
          'This one optional section is not: it is the model\\'s own overall rating and improvement ideas, ' +
          'appended after the evidence-backed findings and labelled as such. Turn it off to have audits ' +
          'contain only cited findings - the signed report and its verification page will then confirm ' +
          'the report is fully evidence-backed.</div>' +
        '</div>' +
      '</div>' +
      '<div>' +
        '<div class="settings-block">' +
          '<div class="settings-block-label">Alert channels</div>' +
          '<div class="settings-block-hint" style="margin-top:0;margin-bottom:14px;">New critical findings and endpoint-monitoring alerts go out on any combination you configure below - each channel is independent.</div>' +
          '<div class="alert-channel">' +
            '<div class="alert-channel-label">Slack / Teams</div>' +
            '<input class="field" id="webhook-url-input" placeholder="Slack or Teams webhook URL" value="' + escapeHtml(installation.webhook_url || '') + '">' +
            '<div class="form-row"><button class="btn" onclick="saveWebhook()">Save</button><button class="btn" onclick="sendTestNotification()" style="margin-left:6px;">Send test</button><span id="webhook-status" class="settings-block-hint"></span></div>' +
            '<div class="settings-help-links">' +
              '<a href="https://api.slack.com/messaging/webhooks" target="_blank" rel="noopener">Get a Slack webhook &rarr;</a>' +
              '<a href="https://support.microsoft.com/en-us/office/create-incoming-webhooks-with-workflows-for-microsoft-teams-8ae491c7-0394-4861-ba59-055e33f75498" target="_blank" rel="noopener">Get a Teams webhook &rarr;</a>' +
            '</div>' +
          '</div>' +
          '<div class="alert-channel">' +
            '<div class="alert-channel-label">Email</div>' +
            '<input class="field" id="alert-email-input" placeholder="ops@yourcompany.com" value="' + escapeHtml(installation.alert_email || '') + '">' +
            '<div class="form-row"><button class="btn" onclick="saveAlertEmail()">Save</button><button class="btn" onclick="sendTestAlertEmail()" style="margin-left:6px;">Send test</button><span id="alert-email-status" class="settings-block-hint"></span></div>' +
          '</div>' +
          '<div class="alert-channel">' +
            '<div class="alert-channel-label">Pushover</div>' +
            '<input class="field" id="pushover-key-input" placeholder="Your Pushover user key" value="' + escapeHtml(installation.pushover_user_key || '') + '">' +
            '<div class="form-row"><button class="btn" onclick="savePushoverKey()">Save</button><button class="btn" onclick="sendTestPushover()" style="margin-left:6px;">Send test</button><span id="pushover-key-status" class="settings-block-hint"></span></div>' +
            '<div class="settings-block-hint">A down alert repeats until you acknowledge it - the other two channels send once.</div>' +
            '<div class="settings-help-links">' +
              '<a href="https://pushover.net" target="_blank" rel="noopener">Get your Pushover user key &rarr;</a>' +
            '</div>' +
          '</div>' +
        '</div>' +
        '<div class="settings-block">' +
          '<div class="settings-block-label">Endpoint health targets</div>' +
          '<div class="settings-block-hint">Configure staging/production URLs and see live results on the <a data-href="/health">Endpoint health</a> page.</div>' +
        '</div>' +
      '</div>' +
    '</div>' +
    usageHtml +
    '<div id="export-zone"></div>' +
    '<div id="danger-zone"></div>';
  loadExportZone();
  loadDangerZone();
  document.querySelectorAll('[data-href]').forEach(function (el) {{ el.href = pageBase + el.dataset.href; }});
}}

loadSettings();
loadPlanBadge();
</script>
"""


def _no_store_html(content: str) -> HTMLResponse:
    # Every page here either shows session-specific data or depends on
    # auth state (the sign-in page itself redirects once logged in) - a
    # browser-cached or bfcache-restored copy would let someone hit Back
    # after Sign out and see the previous session's page without a fresh
    # request ever reaching the server. no-store excludes the page from
    # bfcache entirely, forcing a real reload that re-checks the session.
    return HTMLResponse(content, headers={"Cache-Control": "no-store"})


_VALID_PLANS = ("air", "flash")
_VALID_INTERVALS = ("month", "year")


_PLAN_DISPLAY_NAMES = {
    "free": "Aletheore Community",
    "flash": "Aletheore Flash",
    "air": "Aletheore AIR",
}


def _plan_display_name(plan: str) -> str:
    # Falls back to AIR for an unrecognized value rather than raising -
    # matches this function's pre-existing fail-open shape (a binary
    # free/else check used to mean "anything that isn't literally 'free'
    # is AIR"), now explicit about the values it knows rather than
    # accidentally correct only by omission.
    return _PLAN_DISPLAY_NAMES.get(plan, _PLAN_DISPLAY_NAMES["air"])


def _subscribe_page(title: str, body: str) -> str:
    return _page_head(f"{title} — Aletheore") + f"""
<div class="claim-page">
  <div class="claim-card">
    {body}
  </div>
</div>
"""


def _subscribe_install_prompt_page(plan: str, next_path: str) -> str:
    install_url = github_app_install_url(next_path)
    return _subscribe_page(
        "Install the GitHub App",
        f"""
        <h1>Install the Aletheore GitHub App</h1>
        <p>Install the app on a GitHub organization to activate your {escape(_plan_display_name(plan))} plan.</p>
        <a class="btn btn-accent" href="{escape(install_url)}">Install the Aletheore GitHub App</a>
        <p><a href="/dashboard">Cancel</a></p>
        """,
    )


def _subscribe_checkout_page(plan: str, price_id: str, installations: list[dict]) -> str:
    settings = get_settings()
    # Signed here, not the raw installation_id: the browser fully controls
    # what Paddle.Checkout.open() actually sends (devtools can call it
    # directly with any custom_data), and the webhook has no other way to
    # know the payer was authorized to name this installation - see
    # sign_checkout_installation_id. Minted once per installation this
    # session was already verified to administer
    # (_administered_installation_ids_for_session_or_401, in the caller),
    # so a token can only ever exist for an installation this user
    # legitimately administers.
    tokens = {
        installation["installation_id"]: sign_checkout_installation_id(
            installation["installation_id"], settings.session_secret
        )
        for installation in installations
    }

    pw_customer_id: str | None = None
    if len(installations) == 1:
        installation = installations[0]
        pw_customer_id = installation.get("paddle_customer_id")
        continue_attrs = f'data-installation-token="{tokens[installation["installation_id"]]}"'
        body = f"""
        <h1>Subscribe to {escape(_plan_display_name(plan))}</h1>
        <p>{escape(installation["account_login"])} is currently on {escape(_plan_display_name(installation["plan"]))}.</p>
        <button class="btn btn-accent" id="continue-checkout" {continue_attrs}>Continue to checkout</button>
        <p><a href="/dashboard">Cancel</a></p>
        """
    else:
        options = "\n".join(
            (
                '<label class="claim-option">'
                f'<input type="radio" name="installation_token" value="{tokens[installation["installation_id"]]}"'
                f'{" checked" if index == 0 else ""}> '
                f'{escape(installation["account_login"])} '
                f'(currently {escape(_plan_display_name(installation["plan"]))})'
                "</label>"
            )
            for index, installation in enumerate(installations)
        )
        body = f"""
        <h1>Subscribe to {escape(_plan_display_name(plan))}</h1>
        <p>Choose which installation this subscription applies to.</p>
        <div class="claim-options">{options}</div>
        <button class="btn btn-accent" id="continue-checkout">Continue to checkout</button>
        <p><a href="/dashboard">Cancel</a></p>
        """

    # pwCustomer (Paddle Retain) only makes sense for a known, already-Paddle
    # customer - only wireable here when there's exactly one installation to
    # check out for, since Paddle.Initialize() runs once for the whole page,
    # before the customer (if there's a choice) has picked which installation.
    pw_customer_config = f', pwCustomer: {{ id: "{pw_customer_id}" }}' if pw_customer_id else ""
    return _subscribe_page("Subscribe", body) + f"""
<script src="https://cdn.paddle.com/paddle/v2/paddle.js"></script>
<script>
Paddle.Environment.set("{settings.paddle_environment}");
Paddle.Initialize({{ token: "{settings.paddle_client_token}"{pw_customer_config} }});
document.getElementById("continue-checkout").addEventListener("click", (event) => {{
  const btn = event.currentTarget;
  const selected = document.querySelector('input[name="installation_token"]:checked');
  const installationToken = selected ? selected.value : btn.dataset.installationToken;
  Paddle.Checkout.open({{
    items: [{{ priceId: "{price_id}", quantity: 1 }}],
    customData: {{ installation_token: installationToken }},
    settings: {{
      displayMode: "overlay",
      variant: "one-page",
      successUrl: "{"https://app.aletheore.com/dashboard" if plan == "air" else "https://www.aletheore.com/?subscribed=flash"}",
    }},
  }});
}});
</script>
"""


_CREDITS_JS = """
const installationId = __INSTALLATION_ID__;
const creditsApi = '/app/installations/' + installationId + '/credits';
function setStatus(text, color) {
  const el = document.getElementById('topup-status');
  el.textContent = text;
  el.style.color = color || '';
}
function renderInstallsList(siblings, currentId) {
  const list = document.getElementById('installs-list');
  if (siblings.length === 0) { list.innerHTML = ''; return; }
  list.innerHTML = siblings.map(function (s) {
    const label = escapeHtml(s.account_login);
    if (s.plan === 'free') {
      // No dashboard exists for a free installation - an inert row (not a
      // dead link) is more honest than the mockup's own href="#" placeholder.
      return '<li><span class="nav-item disabled"><span class="nav-dot"></span>' + label + '<span class="install-tag">free</span></span></li>';
    }
    const isActive = s.installation_id === currentId;
    // An AIR sibling has no single repo to deep-link to from an
    // installation-scoped page - /dashboard (the org/repo picker) is the
    // real entry point for it, same as everywhere else AIR is reached.
    const href = s.plan === 'flash' ? '/credits/' + s.installation_id : '/dashboard';
    return '<li><a class="nav-item' + (isActive ? ' active' : '') + '" href="' + href + '">' +
      '<span class="nav-dot paid"></span>' + label +
      (s.plan === 'air' ? '<span class="install-tag">AIR</span>' : '') +
    '</a></li>';
  }).join('');
}
async function loadCredits() {
  const res = await fetch(creditsApi);
  if (res.status === 401) { window.location.href = '/auth/logout'; return; }
  if (res.status === 404) {
    document.getElementById('top-error').innerHTML = '<div class="error-banner">This installation has no paid Aletheore plan, or your GitHub account does not administer it. <a href="/dashboard">Back to your organizations</a></div>';
    return;
  }
  if (!res.ok) {
    // A transient 5xx must not tell a paying customer they have no plan.
    document.getElementById('top-error').innerHTML = '<div class="error-banner">We could not load your credit balance right now. Please reload in a moment.</div>';
    return;
  }
  const data = await res.json();
  window._creditTopupPriceId = data.credit_topup_price_id;

  document.title = data.account_login + ' - Aletheore';
  document.getElementById('install-name').textContent = data.account_login;
  document.getElementById('plan-pill').textContent = planShortName(data.plan);
  renderInstallsList(data.sibling_installations || [], data.installation_id);

  const base = data.base_credit_remaining_usd || 0;
  const topup = data.topup_credit_balance_usd || 0;
  const allotment = data.base_credit_allotment_usd || 0;
  const pct = allotment > 0 ? Math.max(0, Math.min(100, Math.round((base / allotment) * 100))) : 0;
  document.getElementById('credit-figure').innerHTML =
    '$' + base.toFixed(2) + (allotment > 0 ? ' <span class="of">of $' + allotment.toFixed(2) + '</span>' : '');
  document.getElementById('credit-meter-fill').style.width = pct + '%';
  const avg = data.average_cost_per_review_usd;
  let subText;
  if (avg && avg > 0) {
    const reviewsLeft = Math.floor((base + topup) / avg);
    subText = '~' + reviewsLeft + (reviewsLeft === 1 ? ' review' : ' reviews') + ' left this month, at your recent average cost per review';
  } else {
    subText = data.flash_review_count_this_month > 0 ? 'Credit available for automatic reviews' : 'No completed reviews yet this month';
  }
  if (topup > 0) subText += ' \\u00b7 $' + topup.toFixed(2) + ' purchased credit also available';
  document.getElementById('credit-sub').textContent = subText;
  document.getElementById('credit-hero').style.display = '';

  const renewsAt = data.subscription_renews_at
    ? new Date(data.subscription_renews_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
    : null;
  document.getElementById('billing-cadence-line').textContent =
    renewsAt ? 'Billed monthly \\u00b7 next charge ' + renewsAt : 'No active subscription';

  // The mockup's own "1 repo on Flash, 1 on the free tier" line assumes
  // repo-level plan granularity a GitHub App installation doesn't have -
  // plan is set per installation here, and one installation can cover
  // several repos. The honest equivalent with our real data model is a
  // plan breakdown across every installation this session administers.
  const siblings = data.sibling_installations || [];
  const planCounts = {};
  siblings.forEach(function (s) { planCounts[s.plan] = (planCounts[s.plan] || 0) + 1; });
  const summaryParts = Object.keys(planCounts).sort().map(function (p) {
    const n = planCounts[p];
    return n + ' ' + planShortName(p) + (n === 1 ? ' install' : ' installs');
  });
  document.getElementById('sibling-summary-line').textContent = summaryParts.join(', ');

  document.getElementById('topup-button').addEventListener('click', function () { buyCredit(this); });
  document.getElementById('billing-portal-btn').addEventListener('click', openInstallationBillingPortal);
  document.getElementById('alert-email-save').addEventListener('click', saveAlertEmail);
  loadAlertEmail();
  loadReviewHistory();
}
async function loadAlertEmail() {
  // Not part of loadCredits()'s own response (get_credits deliberately
  // doesn't return it - see get_installation_alert_email) - a separate,
  // more tightly-gated fetch.
  const res = await fetch('/app/installations/' + installationId + '/alert-email');
  if (!res.ok) return;
  const data = await res.json();
  document.getElementById('alert-email-input').value = data.alert_email || '';
}
async function saveAlertEmail() {
  const input = document.getElementById('alert-email-input');
  const status = document.getElementById('alert-email-status');
  const value = input.value.trim();
  status.textContent = 'Saving...';
  status.style.color = '';
  const res = await fetch('/app/installations/' + installationId + '/alert-email', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ alert_email: value || null }),
  });
  const data = await res.json().catch(function () { return {}; });
  if (!res.ok) {
    status.textContent = data.detail || 'Could not save.';
    status.style.color = 'var(--critical)';
    return;
  }
  status.textContent = value ? 'Saved.' : 'Cleared - you will not get a low-credit email.';
  status.style.color = 'var(--success)';
}
async function openInstallationBillingPortal() {
  const status = document.getElementById('billing-portal-status');
  status.textContent = 'Opening billing portal...';
  status.style.color = '';
  const res = await fetch('/app/installations/' + installationId + '/billing-portal');
  const data = await res.json().catch(function () { return {}; });
  if (res.ok && data.url) {
    window.location.href = data.url;
    return;
  }
  status.textContent = data.detail || 'Could not open the billing portal.';
  status.style.color = 'var(--critical)';
}
async function loadReviewHistory() {
  const body = document.getElementById('review-history-body');
  const res = await fetch('/app/installations/' + installationId + '/review-history');
  if (!res.ok) { body.innerHTML = '<div class="empty-state">Could not load review history.</div>'; return; }
  const data = await res.json();
  const reviews = data.reviews || [];
  if (reviews.length === 0) {
    body.innerHTML = '<div class="empty-state">No reviews recorded yet.</div>';
    return;
  }
  // flash_review_history has no PR-title column (see migration 069) - the
  // real per-repo path plus PR number, which we do have, stands in for the
  // mockup's invented title text as the row's primary identifier.
  const statusClass = { posted: 'commented', clean: 'clean', skipped: 'skipped', failed: 'skipped' };
  const metaText = { clean: 'clean' };
  body.innerHTML = '<div class="review-list">' + reviews.map(function (r) {
    let meta = metaText[r.outcome];
    if (r.outcome === 'posted') meta = r.finding_count + (r.finding_count === 1 ? ' finding' : ' findings');
    else if (!meta) meta = escapeHtml(r.skip_reason || r.outcome);
    const prUrl = 'https://github.com/' + encodeURIComponent(r.repo_full_name).replace('%2F', '/') + '/pull/' + r.pr_number;
    return '<a class="review-row" href="' + prUrl + '" target="_blank" rel="noopener">' +
      '<span class="review-status ' + (statusClass[r.outcome] || 'skipped') + '"></span>' +
      '<span class="review-title">' + escapeHtml(r.repo_full_name) + '<span class="repo">#' + r.pr_number + '</span></span>' +
      '<span class="review-meta">' + meta + '</span>' +
      '<span class="review-cost">' + compactRelativeTime(r.reviewed_at) + '</span>' +
    '</a>';
  }).join('') + '</div>';
}
async function buyCredit(btn) {
  if (typeof Paddle === 'undefined') {
    setStatus('Checkout is unavailable right now - try disabling any ad/script blocker and reload.');
    return;
  }
  // Number() plus an integer check rejects "7.9" and "1e5" instead of quietly
  // charging a different amount than what is on screen.
  const rawAmount = Number(document.getElementById('topup-amount').value);
  const amount = Number.isInteger(rawAmount) ? rawAmount : NaN;
  if (!amount || amount < 5 || amount > 1000) {
    setStatus('Enter an amount between $5 and $1000.');
    return;
  }
  btn.disabled = true;
  setStatus('Opening checkout...');
  try {
    window._creditCheckoutCompleted = false;
    // The checkout token has a 30-minute TTL, so it is re-fetched at click time
    // instead of reusing the page-load copy.
    const res = await fetch(creditsApi);
    if (!res.ok) { setStatus('Could not start checkout - try again.', 'var(--critical)'); return; }
    const data = await res.json();
    Paddle.Checkout.open({
      items: [{ priceId: data.credit_topup_price_id, quantity: amount }],
      customData: { installation_token: data.checkout_installation_token },
      ...(data.paddle_customer_id ? { customer: { id: data.paddle_customer_id } } : {}),
      settings: {
        displayMode: 'overlay',
        variant: 'one-page',
        successUrl: 'https://app.aletheore.com/credits/' + installationId,
      },
    });
  } finally {
    btn.disabled = false;
  }
}
if (typeof Paddle !== 'undefined') {
  // Paddle's publishable client token and environment ride on data attributes of the
  // page wrapper (rendered server-side), not string literals in this script.
  const paddleConfig = document.getElementById('credits-root').dataset;
  Paddle.Environment.set(paddleConfig.paddleEnv);
  Paddle.Initialize({
    token: paddleConfig.paddleClientToken,
    eventCallback: function (event) {
      if (!event || !event.name || !document.getElementById('topup-status')) return;
      if (event.name === 'checkout.loaded') {
        setStatus('');
      } else if (event.name === 'checkout.completed') {
        window._creditCheckoutCompleted = true;
        setStatus('Purchase complete - your balance updates once the payment is confirmed.', 'var(--success)');
      } else if (event.name === 'checkout.closed' && !window._creditCheckoutCompleted) {
        setStatus('');
      } else if (event.name === 'checkout.error') {
        setStatus('Checkout error - try again.', 'var(--critical)');
      }
    },
  });
}
loadCredits();
"""


def _credits_page(installation_id: int) -> str:
    """Flash's entire managed dashboard: credit balance and top-up checkout,
    review history, the low-credit alert email, and an AIR upgrade cross-sell
    - matching flash.html's own full-shell layout, not the settings page's
    AIR-only tabs. Exists because a Flash installation has no other managed
    dashboard (settings.py's own settings page is AIR-only), so without this
    a Flash customer has no way to buy more credit, see what Flash Review has
    done, or set a low-credit warning address. Data comes from
    /app/installations/{id}/credits and friends, which do the real
    authorization; nothing sensitive is baked into this HTML."""
    settings = get_settings()
    script = (
        _CREDITS_JS.replace("__INSTALLATION_ID__", str(int(installation_id)))
    )
    return f"""<!DOCTYPE html>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Aletheore</title>
{ICONS_LINK}
{STYLE}
<div class="shell" id="credits-root" data-paddle-env="{escape(settings.paddle_environment)}" data-paddle-client-token="{escape(settings.paddle_client_token)}">
  <nav class="sidebar" aria-label="Dashboard navigation">
    <div class="brand"><span class="brand-mark">A</span><span class="brand-name">Aletheore</span></div>
    <div>
      <div class="nav-group-label">Your installs</div>
      <ul class="nav-list" id="installs-list"><li><a class="nav-item" aria-hidden="true">&hellip;</a></li></ul>
    </div>
    <div style="margin-top:auto;">
      <div class="nav-group-label">Account</div>
      <ul class="nav-list">
        <li><a class="nav-item" href="/credits/{installation_id}"><i class="ti ti-settings" aria-hidden="true"></i>Settings</a></li>
        <li><a class="nav-item" href="/auth/logout"><i class="ti ti-logout" aria-hidden="true"></i>Sign out</a></li>
      </ul>
    </div>
  </nav>
  <main class="main">
    <div class="topbar">
      <div>
        <h1 class="h1" style="margin-top:0"><span id="install-name">&hellip;</span><span class="plan-pill" id="plan-pill"></span></h1>
        <div class="page-sub">Automatic PR reviews on every push</div>
      </div>
    </div>
    <div id="top-error"></div>
    <div class="credit-hero" id="credit-hero" style="display:none">
      <div>
        <div class="credit-figure" id="credit-figure"></div>
        <div class="credit-meter"><div class="credit-meter-fill" id="credit-meter-fill"></div></div>
        <div class="credit-sub" id="credit-sub"></div>
      </div>
      <div class="credit-actions">
        <div class="qty-row">
          <span class="qty-prefix">$</span>
          <div class="stepper">
            <button type="button" onclick="document.getElementById(&#39;topup-amount&#39;).stepDown()" aria-label="Decrease amount">&minus;</button>
            <input type="number" id="topup-amount" min="5" max="1000" step="5" value="10">
            <button type="button" onclick="document.getElementById(&#39;topup-amount&#39;).stepUp()" aria-label="Increase amount">+</button>
          </div>
          <button class="btn btn-accent" id="topup-button">Buy credit</button>
        </div>
        <div id="topup-status" class="settings-block-hint"></div>
        <button class="btn btn-small" id="billing-portal-btn">Manage billing</button>
        <div id="billing-portal-status" class="settings-block-hint"></div>
      </div>
    </div>

    <div class="plain-section-head">
      <h2>Recent reviews</h2>
      <div class="count">last 30 days</div>
    </div>
    <div id="review-history-body"><div class="empty-state">Loading&hellip;</div></div>

    <div class="settings-grid">
      <div class="settings-block">
        <div class="settings-block-label">Notify when credit runs low</div>
        <div class="form-row">
          <input class="field" id="alert-email-input" type="email" placeholder="you@example.com">
          <button class="btn" id="alert-email-save">Save</button>
        </div>
        <div id="alert-email-status" class="settings-block-hint"></div>
        <div class="status-line"><span class="status-dot"></span>Reviews pause silently below $0 - this is the only warning you'll get before that happens.</div>
      </div>
      <div class="settings-block">
        <div class="settings-block-label">This install</div>
        <div class="settings-block-hint" id="billing-cadence-line"></div>
        <div class="settings-block-hint" id="sibling-summary-line"></div>
      </div>
    </div>

    <div class="upgrade-card">
      <div>
        <h3>AIR adds AIRview, Docs, managed audits and endpoint monitoring</h3>
        <p>Same evidence-grounded reviews, plus a generated architecture map, always-current docs, and uptime checks across your repo's API. $18 of shared AI credit a month.</p>
      </div>
      <a class="btn" href="{PRICING_URL}" target="_blank" rel="noopener">Compare plans</a>
    </div>
  </main>
</div>
<script src="https://cdn.paddle.com/paddle/v2/paddle.js"></script>
<script>
{FETCH_HELPERS}
{script}
</script>
"""


@frontend_router.get("/credits/{installation_id}", response_class=HTMLResponse)
async def credits_page(installation_id: int, request: Request):
    # GitHub installation ids are positive 64-bit integers; anything else is not an installation.
    if not 0 < installation_id < 2**63:
        raise HTTPException(status_code=404, detail="no such installation")
    session = await get_current_session(request)
    if session is None:
        # The only variable part of this same-site redirect is a validated integer.
        return RedirectResponse(url="/auth/login?next=%2Fcredits%2F" + str(installation_id), status_code=307)
    return _no_store_html(_credits_page(installation_id))


@frontend_router.get("/subscribe", response_class=HTMLResponse)
async def subscribe_page(request: Request, plan: str = "", interval: str = ""):
    # Validated against the real (plan, interval) -> price_id mapping
    # directly, not plan and interval as two independent sets - flash
    # only has a monthly price (no annual yet), so "flash" being in
    # _VALID_PLANS and "year" being in _VALID_INTERVALS would otherwise
    # both pass while resolve_price_id_for_plan("flash", "year") returns
    # None, sending a customer to a checkout page with no real price
    # attached instead of a clean 400.
    if plan not in _VALID_PLANS or interval not in _VALID_INTERVALS:
        raise HTTPException(status_code=400, detail="invalid plan or interval")
    if resolve_price_id_for_plan(plan, interval) is None:
        raise HTTPException(status_code=400, detail="invalid plan or interval")

    next_path = f"/subscribe?plan={plan}&interval={interval}"
    encoded_next = quote(next_path, safe="")

    session = await get_current_session(request)
    if session is None:
        return RedirectResponse(url=f"/auth/login?next={encoded_next}", status_code=307)

    pool = request.app.state.db_pool
    try:
        administered_ids = await _administered_installation_ids_for_session_or_401(pool, session)
    except HTTPException as exc:
        if exc.status_code == 401:
            # The stored GitHub token is dead - _administered_installation_ids_for_session_or_401
            # already tried a transparent refresh-and-retry and, having no
            # refresh_token or failing anyway, deleted the session server-side.
            # Clear the now-stale cookie too and send them through a fresh
            # sign-in rather than a raw 401.
            response = RedirectResponse(url=f"/auth/login?next={encoded_next}", status_code=307)
            response.delete_cookie(SESSION_COOKIE_NAME)
            return response
        raise

    if not administered_ids:
        return _no_store_html(_subscribe_install_prompt_page(plan, next_path))

    installations = await list_installations_for_ids(pool, list(administered_ids))
    price_id = resolve_price_id_for_plan(plan, interval)
    return _no_store_html(_subscribe_checkout_page(plan, price_id, installations))


@frontend_router.get("/", response_class=HTMLResponse)
async def signin_page(request: Request):
    session = await get_current_session(request)
    if session is not None:
        return RedirectResponse(url="/dashboard", status_code=307)
    return _no_store_html(SIGNIN_HTML)


@frontend_router.get("/dashboard", response_class=HTMLResponse)
async def repo_picker_page(request: Request):
    session = await get_current_session(request)
    if session is None:
        return RedirectResponse(url="/", status_code=307)
    return _no_store_html(PICKER_HTML)


async def _require_session_or_redirect(request: Request):
    session = await get_current_session(request)
    if session is None:
        return RedirectResponse(url="/", status_code=307)
    return None


@frontend_router.get("/dashboard/{org}/{repo}", response_class=HTMLResponse)
async def dashboard_overview_page(org: str, repo: str, request: Request):
    redirect = await _require_session_or_redirect(request)
    if redirect is not None:
        return redirect
    return _no_store_html(_overview_html())


@frontend_router.get("/dashboard/{org}/{repo}/security", response_class=HTMLResponse)
async def dashboard_security_page(org: str, repo: str, request: Request):
    redirect = await _require_session_or_redirect(request)
    if redirect is not None:
        return redirect
    return _no_store_html(SECURITY_HTML)


@frontend_router.get("/dashboard/{org}/{repo}/dead-code", response_class=HTMLResponse)
async def dashboard_deadcode_page(org: str, repo: str, request: Request):
    redirect = await _require_session_or_redirect(request)
    if redirect is not None:
        return redirect
    return _no_store_html(DEADCODE_HTML)


@frontend_router.get("/dashboard/{org}/{repo}/health", response_class=HTMLResponse)
async def dashboard_health_page(org: str, repo: str, request: Request):
    redirect = await _require_session_or_redirect(request)
    if redirect is not None:
        return redirect
    return _no_store_html(HEALTH_HTML)


@frontend_router.get("/dashboard/{org}/{repo}/wiki", response_class=HTMLResponse)
async def dashboard_wiki_page(org: str, repo: str, request: Request):
    redirect = await _require_session_or_redirect(request)
    if redirect is not None:
        return redirect
    return _no_store_html(WIKI_HTML)


@frontend_router.get("/dashboard/{org}/{repo}/docs", response_class=HTMLResponse)
async def dashboard_docs_page(org: str, repo: str, request: Request):
    redirect = await _require_session_or_redirect(request)
    if redirect is not None:
        return redirect
    return _no_store_html(DOCS_HTML)


@frontend_router.get("/dashboard/{org}/{repo}/settings", response_class=HTMLResponse)
async def dashboard_settings_page(org: str, repo: str, request: Request):
    redirect = await _require_session_or_redirect(request)
    if redirect is not None:
        return redirect
    return _no_store_html(_settings_html())
