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

from html import escape
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app_server.admin import _administered_installation_ids_for_session_or_401
from app_server.auth import SESSION_COOKIE_NAME, get_current_session
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
  --ink-900: #1A1A1A;
  --ink-700: #4A443B;
  --slate-50: #F5F0E6;
  --slate-100: #ECE4D3;
  --slate-200: #DED3BC;
  --slate-400: #8A8377;
  --slate-500: #7A7266;
  --slate-600: #6B6459;
  --paper: #FFFFFF;
  --accent: #E0863A;
  --accent-strong: #C96F26;
  --accent-soft: #FBEAD9;
  --accent-soft-strong: #F5D9B8;
  --success: #3F7D4A;
  --success-soft: #E6F0E3;
  --warning: #A9821A;
  --warning-soft: #F6EFD7;
  --critical: #B23A34;
  --critical-soft: #F8E4E2;
  --border: rgba(26, 26, 26, 0.1);
  --border-strong: rgba(26, 26, 26, 0.2);
  --shadow-card: 0 1px 2px rgba(26, 26, 26, 0.05);
  --shadow-card-hover: 0 4px 14px rgba(26, 26, 26, 0.09);
  --shadow-lift: 0 18px 44px rgba(26, 26, 26, 0.08);
  --font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  --font-mono: ui-monospace, "SF Mono", "Cascadia Code", "Roboto Mono", Menlo, monospace;
  --page-bg: var(--slate-50);
}
@media (prefers-color-scheme: dark) {
  :root {
    --page-bg: #17140F; --paper: #201B14; --slate-50: #17140F; --slate-100: #221D15;
    --slate-200: #332B1F; --slate-400: #8A8377; --slate-500: #A49B8D; --slate-600: #B9B1A4;
    --ink-900: #F3EEE3; --ink-700: #D8D2C5;
    --accent: #E0863A; --accent-strong: #EFA262; --accent-soft: #3A2A18; --accent-soft-strong: #4A3620;
    --success: #6FBE7E; --success-soft: #23331F; --warning: #D2A83C; --warning-soft: #3A301A;
    --critical: #E37972; --critical-soft: #3A211D;
    --border: rgba(243, 238, 227, 0.12); --border-strong: rgba(243, 238, 227, 0.22);
    --shadow-card: 0 1px 2px rgba(0, 0, 0, 0.35); --shadow-card-hover: 0 6px 18px rgba(0, 0, 0, 0.45);
    --shadow-lift: 0 22px 70px rgba(0, 0, 0, 0.38);
  }
}
* { box-sizing: border-box; }
body { margin: 0; font-family: var(--font-sans); color: var(--ink-900); background-color: var(--page-bg);
  background-image:
    radial-gradient(circle at 12% 0%, rgba(224, 134, 58, 0.12), transparent 30%),
    radial-gradient(var(--border-strong) 1px, transparent 1px);
  background-size: auto, 22px 22px; }
a { color: var(--accent); }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0,0,0,0); white-space: nowrap; border: 0; }

/* ---- Sign-in ---- */
.signin { min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 4rem 1.5rem;
  background:
    radial-gradient(circle at 50% 0%, rgba(224, 134, 58, 0.16), transparent 38%),
    linear-gradient(180deg, #17140F, #0E0C09); }
.signin-card { width: 100%; max-width: 430px; background: linear-gradient(180deg, rgba(255,255,255,0.07), rgba(255,255,255,0.025)); border: 1px solid rgba(237,241,238,0.13); border-radius: 18px; padding: 2.5rem 2.2rem; text-align: center; box-shadow: 0 30px 90px rgba(0,0,0,0.34); }
.wordmark { font-family: var(--font-sans); font-weight: 760; font-size: 25px; color: #F2F5F3; margin: 0 0 7px; }
.tagline { font-size: 13.5px; color: #B9B1A4; margin: 0 0 2rem; line-height: 1.6; }
.gh-btn { width: 100%; display: flex; align-items: center; justify-content: center; gap: 10px; background: #F2F5F3; color: #14181B;
  border: none; border-radius: 9px; font-family: var(--font-sans); font-size: 14px; font-weight: 650; padding: 12px 16px; cursor: pointer; text-decoration: none; }
.gh-btn:hover { background: #FFFFFF; }
.gh-btn:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.scope-note { margin-top: 1.5rem; padding-top: 1.25rem; border-top: 1px solid rgba(237,241,238,0.1); font-size: 12px; color: #9A9388; line-height: 1.6; text-align: left; }
.scope-note code { font-family: var(--font-mono); font-size: 11px; color: #A7B2AC; }

/* ---- Repo picker ---- */
.picker-wrap { max-width: 980px; margin: 0 auto; padding: 3.4rem 1.75rem; }
.picker-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 2rem; }
.picker-head h1 { font-size: 28px; font-weight: 720; margin: 0; }
.picker-org-group { margin-bottom: 2rem; }
.picker-org-label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--slate-400); font-weight: 500; margin-bottom: 10px; }
.picker-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 14px; }
.picker-card { display: flex; align-items: center; gap: 12px; text-decoration: none; color: var(--ink-900); background: linear-gradient(180deg, var(--paper), var(--slate-50)); border: 1px solid var(--border);
  border-radius: 13px; padding: 17px 18px; box-shadow: var(--shadow-card); transition: box-shadow 0.15s ease, border-color 0.15s ease, transform 0.15s ease; }
.picker-card:hover { border-color: var(--border-strong); box-shadow: var(--shadow-card-hover); transform: translateY(-1px); }
.picker-card-icon { width: 40px; height: 40px; border-radius: 9px; background: var(--accent-soft); color: var(--accent-strong);
  display: flex; align-items: center; justify-content: center; font-size: 18px; flex-shrink: 0; }
.picker-card-body { min-width: 0; flex: 1; }
.picker-repo { font-size: 15px; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.picker-plan { display: inline-block; margin-top: 7px; font-size: 11px; font-weight: 500; padding: 2px 9px; border-radius: 99px; background: var(--slate-100); color: var(--slate-600); }
.picker-plan.paid { background: var(--accent-soft); color: var(--accent-strong); }
.picker-card-arrow { color: var(--slate-400); font-size: 16px; flex-shrink: 0; }
.picker-card-pending { cursor: default; }
.picker-card-pending:hover { border-color: var(--border); box-shadow: var(--shadow-card); transform: none; }
.picker-pending-note { margin-top: 4px; font-size: 11.5px; color: var(--slate-500); }

/* ---- Shared UI atoms ---- */
.btn { font-family: var(--font-sans); font-size: 12.5px; font-weight: 650; border-radius: 8px; padding: 8px 12px;
  border: 1px solid var(--border-strong); background: var(--paper); color: var(--ink-900); cursor: pointer; display: inline-flex; align-items: center; gap: 6px; }
.btn:hover { background: var(--slate-100); }
.btn:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
.btn-accent { background: var(--accent); color: #FFFFFF; border-color: var(--accent); }
.btn-accent:hover { background: var(--accent-strong); }
.chip { display: inline-flex; align-items: center; gap: 5px; font-size: 11.5px; font-weight: 500; padding: 2px 9px; border-radius: 99px; }
.chip.critical { background: var(--critical-soft); color: var(--critical); }
.chip.warning { background: var(--warning-soft); color: var(--warning); }
.chip.success { background: var(--success-soft); color: var(--success); }
.chip.neutral { background: var(--slate-100); color: var(--slate-600); }
.field { width: 100%; font-family: var(--font-mono); font-size: 12px; padding: 8px 10px; border: 1px solid var(--border-strong); border-radius: 7px; background: var(--slate-100); color: var(--ink-900); }
.field:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
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
.shell { display: grid; grid-template-columns: 238px minmax(0, 1fr); min-height: 100vh; }
.sidebar { background: linear-gradient(180deg, rgba(255,255,255,0.48), rgba(255,255,255,0.18)), var(--slate-100); border-right: 1px solid var(--border); padding: 1rem; display: flex; flex-direction: column; gap: 1.45rem; position: sticky; top: 0; height: 100vh; }
.org-switch { display: flex; align-items: center; gap: 9px; padding: 9px; border: 1px solid var(--border); border-radius: 10px; background: var(--paper); text-decoration: none; color: inherit; box-shadow: var(--shadow-card); }
.org-avatar { width: 22px; height: 22px; border-radius: 6px; background: var(--accent-soft); color: var(--accent-strong); font-size: 11px; font-weight: 500; display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
.org-switch-label { font-size: 13px; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.org-switch-sub { font-size: 11px; color: var(--slate-600); }
.nav-group-label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--slate-400); padding: 0 8px; margin-bottom: 6px; }
.nav-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 1px; }
.nav-item { display: flex; align-items: center; gap: 9px; padding: 8px 9px; border-radius: 8px; font-size: 13.5px; color: var(--ink-700); text-decoration: none; }
.nav-item i { font-size: 16px; color: color-mix(in srgb, var(--ink-700) 78%, var(--accent) 22%); opacity: 0.95; }
.nav-item:hover { background: var(--paper); }
.nav-item.active { background: var(--accent-soft); color: var(--accent-strong); font-weight: 500; }
.nav-item.active i { color: var(--accent-strong); }
.plan-badge-wrap { margin-top: auto; }
.plan-card { background: var(--paper); border: 1px solid var(--border); border-radius: 11px; padding: 12px; box-shadow: var(--shadow-card); }
.plan-name { font-size: 12px; font-weight: 500; display: flex; align-items: center; gap: 6px; text-transform: capitalize; }
.plan-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--accent); }
.plan-sub { font-size: 11px; color: var(--slate-600); margin-top: 3px; line-height: 1.5; }

.main { padding: 1.7rem 2rem 3.25rem; min-width: 0; }
.topbar { display: flex; align-items: baseline; justify-content: space-between; gap: 1rem; margin-bottom: 1.4rem; flex-wrap: wrap; }
.breadcrumb { font-size: 12px; color: var(--slate-600); }
.breadcrumb b { color: var(--ink-900); font-weight: 500; }
.breadcrumb a { color: var(--slate-600); text-decoration: none; }
.breadcrumb a:hover { color: var(--ink-900); }
.h1 { font-size: 26px; font-weight: 720; margin: 3px 0 0; }
.topbar-right { font-size: 12px; color: var(--slate-600); }

.dashboard-summary { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 18px; align-items: center;
  margin-bottom: 1.15rem; border: 1px solid var(--border); border-radius: 16px;
  background:
    radial-gradient(circle at 92% 0%, color-mix(in srgb, var(--accent) 18%, transparent), transparent 36%),
    linear-gradient(180deg, var(--paper), color-mix(in srgb, var(--paper) 84%, var(--slate-50)));
  padding: 18px; box-shadow: var(--shadow-lift); }
.dashboard-summary-kicker { font-size: 11px; font-weight: 720; letter-spacing: 0.06em; text-transform: uppercase; color: var(--accent-strong); }
.dashboard-summary h2 { margin: 5px 0 6px; font-size: 22px; line-height: 1.2; }
.dashboard-summary p { margin: 0; color: var(--slate-600); font-size: 13px; line-height: 1.55; max-width: 72ch; }
.summary-chip-row { display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-end; }
.summary-chip { display: inline-flex; align-items: center; gap: 7px; border: 1px solid var(--border); border-radius: 999px;
  background: color-mix(in srgb, var(--paper) 72%, var(--slate-50)); padding: 7px 10px; color: var(--ink-700); font-size: 12px; white-space: nowrap; }
.summary-chip i { color: var(--accent-strong); font-size: 14px; }

.stat-strip { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-bottom: 1.7rem; }
.stat-card { background: linear-gradient(180deg, var(--paper), var(--slate-50)); border: 1px solid var(--border); border-radius: 12px; padding: 15px; text-decoration: none; color: inherit; display: block; transition: border-color 0.15s ease, box-shadow 0.15s ease, transform 0.15s ease; box-shadow: var(--shadow-card); }
a.stat-card:hover { border-color: var(--border-strong); box-shadow: var(--shadow-card-hover); transform: translateY(-1px); }
.stat-label { font-size: 12px; color: var(--slate-600); }
.stat-value { font-family: var(--font-mono); font-variant-numeric: tabular-nums; font-size: 27px; font-weight: 720; margin-top: 5px; }
.stat-value.critical { color: var(--critical); }
.stat-value.warning { color: var(--warning); }
.stat-value.success { color: var(--success); }
.stat-delta { font-size: 11.5px; color: var(--slate-600); margin-top: 3px; }

.section { background: linear-gradient(180deg, var(--paper), color-mix(in srgb, var(--paper) 88%, var(--slate-50))); border: 1px solid var(--border); border-radius: 14px; margin-bottom: 1.15rem; box-shadow: var(--shadow-card); scroll-margin-top: 1rem; overflow: hidden; }
.section-head { display: flex; align-items: center; justify-content: space-between; padding: 15px 18px; border-bottom: 1px solid var(--border); gap: 1rem; flex-wrap: wrap; background: color-mix(in srgb, var(--paper) 78%, var(--slate-50)); }
.section-title { font-size: 14.5px; font-weight: 500; display: flex; align-items: center; gap: 8px; }
.section-title i { font-size: 16px; color: var(--slate-400); }
.section-sub { font-size: 12px; color: var(--slate-600); }
.section-body { padding: 8px 18px 16px; }

table.findings { width: 100%; border-collapse: collapse; font-size: 13px; }
table.findings th { text-align: left; font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em; color: var(--slate-400); font-weight: 500; padding: 8px 8px; border-bottom: 1px solid var(--border); }
table.findings td { padding: 10px 8px; border-bottom: 1px solid var(--border); vertical-align: top; }
table.findings tr:last-child td { border-bottom: none; }
.finding-title { font-weight: 500; }
.finding-cite { font-family: var(--font-mono); font-size: 11.5px; color: var(--slate-600); overflow-wrap: anywhere; }
.sev-stripe { display: inline-block; width: 3px; height: 13px; border-radius: 2px; margin-right: 8px; vertical-align: -2px; }
.sev-stripe.critical { background: var(--critical); }
.sev-stripe.warning { background: var(--warning); }

.deadcode-list, .dep-list { display: flex; flex-direction: column; }
.deadcode-row { display: flex; align-items: baseline; gap: 12px; padding: 10px 0; border-bottom: 1px solid var(--border); font-size: 13px; flex-wrap: wrap; }
.deadcode-row:last-child { border-bottom: none; }
.deadcode-path { font-family: var(--font-mono); font-size: 12.5px; flex: 1 1 320px; min-width: 0; overflow-wrap: anywhere; }
.deadcode-meta { font-size: 11.5px; color: var(--slate-600); }

.health-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
.health-row { display: flex; align-items: center; gap: 10px; padding: 10px 11px; background: var(--slate-100); border: 1px solid var(--border); border-radius: 9px; }
.health-status { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.health-status.up { background: var(--success); }
.health-status.down { background: var(--critical); }
.health-endpoint { font-family: var(--font-mono); font-size: 12px; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.health-latency { font-family: var(--font-mono); font-variant-numeric: tabular-nums; font-size: 11.5px; color: var(--slate-600); }
.health-checked { font-size: 10.5px; color: var(--slate-400); white-space: nowrap; }
.health-target-group { margin-bottom: 1.2rem; }
.health-target-group:last-child { margin-bottom: 0; }
.health-target-group-label { font-size: 12px; font-weight: 500; margin-bottom: 8px; display: flex; align-items: center; gap: 8px; }
.health-history { grid-column: 1 / -1; background: var(--slate-50); border-radius: 8px; padding: 8px 10px; margin: -4px 0 4px; }
.health-history-list { display: flex; flex-direction: column; gap: 5px; }
.health-history-row { display: flex; align-items: center; gap: 10px; font-size: 11.5px; }

.wiki-banner { display: flex; align-items: center; justify-content: space-between; gap: 1rem; background: linear-gradient(90deg, var(--accent-soft), color-mix(in srgb, var(--accent-soft) 62%, var(--paper))); border: 1px solid rgba(224, 134, 58, 0.22); border-radius: 12px; padding: 13px 15px; margin: 10px 0 14px; flex-wrap: wrap; }
.wiki-banner-text { font-size: 12.5px; color: var(--accent-strong); line-height: 1.5; max-width: 46ch; }
.wiki-banner-text b { font-weight: 500; }
.docs-overview { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 18px; align-items: center; border: 1px solid rgba(224, 134, 58, 0.22);
  border-radius: 14px; padding: 16px; margin: 10px 0 16px; background:
    radial-gradient(circle at 92% 12%, color-mix(in srgb, var(--accent) 16%, transparent), transparent 36%),
    linear-gradient(135deg, color-mix(in srgb, var(--accent-soft) 46%, var(--paper)), color-mix(in srgb, var(--paper) 86%, var(--slate-50))); }
.docs-overview-kicker { font-size: 11px; font-weight: 720; letter-spacing: 0.06em; text-transform: uppercase; color: var(--accent-strong); }
.docs-overview h2 { margin: 5px 0 5px; font-size: 20px; line-height: 1.2; }
.docs-overview p { margin: 0; color: var(--slate-600); font-size: 13px; line-height: 1.55; max-width: 72ch; }
.docs-overview-stats { display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-end; }
.docs-stat-pill { min-width: 104px; border: 1px solid var(--border); border-radius: 12px; background: color-mix(in srgb, var(--paper) 78%, var(--slate-50));
  padding: 10px 12px; box-shadow: var(--shadow-card); }
.docs-stat-value { font-family: var(--font-mono); font-size: 18px; font-weight: 720; color: var(--ink-900); }
.docs-stat-label { margin-top: 2px; font-size: 11px; color: var(--slate-600); }
.docs-status-banner { border: 1px solid var(--border); border-radius: 12px; padding: 12px 14px; margin: 0 0 14px; font-size: 12.5px; line-height: 1.55; }
.docs-status-banner.failed { border-color: color-mix(in srgb, var(--critical) 34%, transparent); background: var(--critical-soft); color: var(--critical); }
.docs-status-banner.partial { border-color: color-mix(in srgb, var(--warning) 34%, transparent); background: var(--warning-soft); color: var(--warning); }
.docs-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; align-items: start; }
.docs-module-card { border: 1px solid var(--border); border-radius: 13px; background: linear-gradient(180deg, var(--paper), color-mix(in srgb, var(--paper) 88%, var(--slate-50)));
  box-shadow: var(--shadow-card); overflow: hidden; transition: border-color 0.15s ease, box-shadow 0.15s ease; }
.docs-module-card[open] { grid-column: 1 / -1; border-color: color-mix(in srgb, var(--accent) 32%, var(--border)); box-shadow: var(--shadow-card-hover); }
.docs-module-summary { list-style: none; cursor: pointer; padding: 14px 15px; display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 14px; align-items: center; }
.docs-module-summary::-webkit-details-marker { display: none; }
.docs-module-title { display: flex; align-items: center; gap: 9px; min-width: 0; }
.docs-module-title i { color: var(--accent-strong); font-size: 17px; flex-shrink: 0; }
.docs-module-path { font-family: var(--font-mono); font-size: 12.5px; font-weight: 650; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.docs-module-sub { margin-top: 4px; font-size: 11.5px; color: var(--slate-600); }
.docs-module-meta { display: flex; align-items: center; gap: 7px; justify-content: flex-end; flex-wrap: wrap; }
.docs-chip { display: inline-flex; align-items: center; gap: 5px; border-radius: 999px; padding: 4px 8px; background: var(--slate-100); color: var(--slate-600); font-size: 11px; white-space: nowrap; }
.docs-chip.ai { background: var(--accent-soft); color: var(--accent-strong); }
.docs-module-chevron { color: var(--slate-400); font-size: 16px; transition: transform 0.15s ease; }
.docs-module-card[open] .docs-module-chevron { transform: rotate(180deg); }
.docs-module-content { border-top: 1px solid var(--border); background: color-mix(in srgb, var(--slate-50) 78%, var(--paper)); padding: 14px; }
.docs-module-body { white-space: pre-wrap; font-size: 12px; line-height: 1.7; padding: 14px; margin: 0; font-family: var(--font-mono); color: var(--ink-700);
  overflow-x: auto; border: 1px solid var(--border); border-radius: 10px; background: color-mix(in srgb, var(--paper) 84%, var(--slate-50)); }
.docs-commit-card { display: flex; align-items: center; justify-content: space-between; gap: 16px; border: 1px solid var(--border); border-radius: 12px; padding: 14px;
  background: linear-gradient(135deg, color-mix(in srgb, var(--paper) 88%, var(--slate-50)), var(--paper)); }
.docs-commit-copy { min-width: 0; }
.docs-commit-title { font-size: 13.5px; font-weight: 650; margin-bottom: 4px; }
.docs-commit-desc { font-size: 12.5px; color: var(--slate-600); line-height: 1.55; }
.docs-commit-desc a { font-weight: 650; }
.diagram-wrap { overflow-x: auto; border: 1px solid var(--border); border-radius: 12px; background:
  radial-gradient(circle at 50% 20%, color-mix(in srgb, var(--accent) 10%, transparent), transparent 36%),
  var(--slate-50); padding: 14px; }
.diagram-wrap .mermaid { display: flex; justify-content: center; min-width: max-content; }
.diagram-wrap.diagram-zoomable { cursor: zoom-in; }
.diagram-wrap.diagram-zoomable::after { content: "Click to open full diagram"; display: block; margin-top: 8px; color: var(--slate-400); font-size: 11px; text-align: center; }
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
.subsystem-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
.subsystem-card { border: 1px solid var(--border); border-radius: 10px; padding: 12px; text-align: left; background: var(--paper); cursor: pointer; font-family: var(--font-sans); box-shadow: var(--shadow-card); }
.subsystem-card:hover { border-color: var(--border-strong); }
.subsystem-name { font-size: 13px; font-weight: 500; margin-bottom: 3px; color: var(--accent-strong); }
.subsystem-desc { font-size: 12px; color: var(--slate-600); line-height: 1.55; }
.subsystem-files { font-family: var(--font-mono); font-size: 10.5px; color: var(--slate-400); margin-top: 8px; }
.subsystem-detail { border-top: 1px solid var(--border); margin-top: 14px; padding-top: 14px; }
.subsystem-detail-file { margin-bottom: 10px; border: 1px solid var(--border); border-radius: 10px; background: var(--slate-50); padding: 11px; }
.subsystem-detail-path { font-family: var(--font-mono); font-size: 12.5px; font-weight: 500; overflow-wrap: anywhere; }
.subsystem-detail-role { font-size: 12.5px; color: var(--slate-600); margin: 3px 0 6px; }
.subsystem-detail-symbol { font-family: var(--font-mono); font-size: 11.5px; color: var(--ink-700); padding: 2px 0 2px 14px; }
.subsystem-detail-symbol .line { color: var(--slate-400); }

.settings-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px; }
.settings-block { margin-bottom: 18px; }
.settings-block-label { font-size: 12px; font-weight: 500; margin-bottom: 7px; }
.settings-block-hint { font-size: 11px; color: var(--slate-600); margin-top: 6px; }
.danger-zone { margin-top: 24px; border: 1px solid var(--critical); border-radius: 6px; padding: 14px 16px; }
.danger-zone .settings-block-label { color: var(--critical); }
.danger-zone .btn-danger { background: var(--critical); border-color: var(--critical); color: #fff; }
.danger-zone .btn-danger[disabled] { opacity: 0.5; cursor: not-allowed; }
.danger-repo-list { font-size: 11px; color: var(--slate-600); margin-top: 6px; word-break: break-all; }
.token-row { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 7px 0; border-bottom: 1px solid var(--border); font-size: 12.5px; }
.token-row:last-child { border-bottom: none; }
.token-label { font-weight: 500; }
.token-meta { font-size: 11px; color: var(--slate-600); }
.claim-page { min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 3rem 1.5rem;
  background:
    radial-gradient(circle at 50% 0%, rgba(224, 134, 58, 0.13), transparent 36%),
    radial-gradient(var(--border-strong) 1px, transparent 1px); background-size: auto, 22px 22px; }
.claim-card { width: 100%; max-width: 560px; background: var(--paper); border: 1px solid var(--border); border-radius: 16px; padding: 2.15rem; box-shadow: var(--shadow-lift); }
.claim-card h1 { margin: 0 0 0.7rem; font-size: 26px; font-weight: 720; }
.claim-card p { color: var(--slate-600); line-height: 1.6; font-size: 14px; margin: 0 0 1.2rem; }
.claim-options { display: flex; flex-direction: column; gap: 8px; margin: 1rem 0; }
.claim-option { display: flex; align-items: center; gap: 9px; padding: 10px 12px; border: 1px solid var(--border); border-radius: 9px; }
.claim-option input { accent-color: var(--accent); }

@media (max-width: 720px) {
  .shell { grid-template-columns: 1fr; }
  .sidebar { position: static; height: auto; flex-direction: column; overflow: visible; }
  .nav-list { flex-direction: row; flex-wrap: wrap; }
  .nav-item { white-space: nowrap; }
  .main { padding: 1.2rem 1rem 2.5rem; }
  .dashboard-summary { grid-template-columns: 1fr; }
  .summary-chip-row { justify-content: flex-start; }
  .stat-strip { grid-template-columns: repeat(2, minmax(0,1fr)); }
  .health-grid, .subsystem-grid, .settings-grid, .docs-grid { grid-template-columns: 1fr; }
  .docs-overview, .docs-module-summary, .docs-commit-card { grid-template-columns: 1fr; }
  .docs-overview-stats, .docs-module-meta { justify-content: flex-start; }
  .picker-head { align-items: flex-start; gap: 1rem; flex-direction: column; }
  .diagram-zoom-toolbar { left: 14px; right: 14px; transform: none; justify-content: center; flex-wrap: wrap; border-radius: 14px; }
  .diagram-zoom-hint { order: 2; width: 100%; text-align: center; }
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
  return f.ecosystem + '\x1f' + f.package + '\x1f' + f.advisory_id;
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
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
  });
}
function planDisplayName(plan) {
  return plan === 'free' ? 'Aletheore Community' : 'Aletheore AIR';
}
"""

SIGNIN_HTML = f"""<!DOCTYPE html>
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
  if (data.repos.length === 0) {{
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
}})();
</script>
"""

_NAV_ITEMS = [
    ("overview", "", "ti-layout-dashboard", "Overview"),
    ("security", "/security", "ti-shield-check", "Security findings"),
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
    <a class="org-switch" href="/dashboard">
      <span class="org-avatar" id="org-avatar"></span>
      <div style="min-width:0;">
        <div class="org-switch-label" id="side-repo"></div>
        <div class="org-switch-sub" id="side-org"></div>
      </div>
      <i class="ti ti-chevron-down" style="margin-left:auto;color:var(--slate-400);" aria-hidden="true"></i>
    </a>
    <div>
      <div class="nav-group-label">Repository</div>
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

document.getElementById('side-org').textContent = org;
document.getElementById('side-repo').textContent = repo;
document.getElementById('org-avatar').textContent = org.slice(0, 2).toLowerCase();
document.querySelectorAll('.nav-item[data-href]').forEach(function (el) {
  el.href = pageBase + el.dataset.href;
});
const cOrg = document.getElementById('crumb-org');
const cRepo = document.getElementById('crumb-repo');
if (cOrg) cOrg.textContent = org;
if (cRepo) { cRepo.textContent = repo; cRepo.href = pageBase; }
document.title = document.title.replace('{repo}', repo).replace('{org}', org);

async function loadPlanBadge() {
  const res = await apiGet(adminBase);
  const nameEl = document.getElementById('plan-name');
  const subEl = document.getElementById('plan-sub');
  if (!res) return null;
  if (res.status === 402) {
    nameEl.textContent = planDisplayName('free');
    subEl.textContent = 'Upgrade for AIRview and settings.';
    return 'free';
  }
  if (!res.ok) { nameEl.textContent = ''; subEl.textContent = ''; return null; }
  const data = await res.json();
  nameEl.textContent = planDisplayName(data.installation.plan);
  subEl.textContent = data.installation.plan === 'free' ? 'Upgrade for AIRview and settings.' : 'AIRview and priority scans included.';
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


def _page_head(title: str) -> str:
    return f"""<!DOCTYPE html>
<title>{title}</title>
{ICONS_LINK}
{MERMAID_SCRIPT}
{STYLE}"""


def _topbar(h1: str, right_id: str = "") -> str:
    right = f'<div class="topbar-right" id="{right_id}"></div>' if right_id else ""
    return f"""
    <div class="topbar">
      <div>
        <div class="breadcrumb"><a id="crumb-org" href="/dashboard"></a> <span style="color:var(--slate-400);">/</span> <b><a id="crumb-repo"></a></b></div>
        <h1 class="h1">{h1}</h1>
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
OVERVIEW_HTML = _page_head("Overview — {repo} — Aletheore") + _shell(
    "overview",
    _topbar("Overview", "last-scanned")
    + """
    <div id="top-error"></div>
    <div class="dashboard-summary">
      <div>
        <div class="dashboard-summary-kicker">Repository watch</div>
        <h2 id="summary-title">Evidence is loading</h2>
        <p id="summary-copy">Aletheore is reading the latest AIR packet for this repository. Findings, code ownership, endpoint health, and AIRview all resolve back to scanner evidence.</p>
      </div>
      <div class="summary-chip-row">
        <span class="summary-chip"><i class="ti ti-shield-check" aria-hidden="true"></i><span id="summary-risk">Risk loading</span></span>
        <span class="summary-chip"><i class="ti ti-git-branch" aria-hidden="true"></i><span id="summary-scans">Scans loading</span></span>
        <span class="summary-chip"><i class="ti ti-book-2" aria-hidden="true"></i>AIRview</span>
      </div>
    </div>
    <div class="stat-strip" id="stat-strip">
      <a class="stat-card" data-href="/security"><div class="stat-label">Open findings</div><div class="stat-value" id="stat-findings">&ndash;</div><div class="stat-delta" id="stat-findings-sub"></div></a>
      <a class="stat-card" data-href="/dead-code"><div class="stat-label">Dead code</div><div class="stat-value" id="stat-deadcode">&ndash;</div><div class="stat-delta" id="stat-deadcode-sub"></div></a>
      <a class="stat-card" data-href="/health"><div class="stat-label">Endpoint uptime</div><div class="stat-value" id="stat-uptime">&ndash;</div><div class="stat-delta" id="stat-uptime-sub"></div></a>
      <div class="stat-card"><div class="stat-label">Modules scanned</div><div class="stat-value" id="stat-modules">&ndash;</div><div class="stat-delta" id="stat-modules-sub"></div></div>
    </div>
    <section class="section" id="recent-security">
      <div class="section-head">
        <div class="section-title"><i class="ti ti-shield-check" aria-hidden="true"></i>Recent security findings</div>
        <a class="btn" data-href="/security">View all<i class="ti ti-arrow-right" style="font-size:13px;" aria-hidden="true"></i></a>
      </div>
      <div class="section-body" id="recent-security-body"><div class="empty-state">Loading&hellip;</div></div>
    </section>
"""
) + f"""
<script>
{FETCH_HELPERS}
{PAGE_HEAD_JS}
{CONFIRM_UPGRADE_JS}
document.querySelectorAll('[data-href]').forEach(function (el) {{
  if (el.tagName === 'A' && el.dataset.href) el.href = pageBase + el.dataset.href;
}});

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
    document.getElementById('summary-title').textContent = repo + ' is waiting for its first scan';
    document.getElementById('summary-copy').textContent = 'Open a pull request or trigger a managed scan to populate evidence, health, and AIRview.';
    document.getElementById('summary-risk').textContent = 'No evidence yet';
    document.getElementById('summary-scans').textContent = '0 scans';
    document.getElementById('recent-security-body').innerHTML = '<div class="empty-state">No scans yet - findings will appear after the first pull request is scanned.</div>';
    return;
  }}
  const latest = history[0];
  const evidence = latest.evidence || {{}};
  document.getElementById('last-scanned').textContent = 'Last scanned ' + relativeTime(latest.scanned_at);

  const dismissedKeys = data.dismissed_finding_keys || {{ secret: [], vulnerability: [] }};
  const security = evidence.security || {{}};
  const secretFindings = ((security.secrets || {{}}).findings || []).filter(function (f) {{
    return !f.likely_placeholder && !f.accepted && dismissedKeys.secret.indexOf(findingIdentityKey('secret', f)) === -1;
  }});
  const vulnFindings = ((security.dependency_vulnerabilities || {{}}).findings || []).filter(function (f) {{
    return dismissedKeys.vulnerability.indexOf(findingIdentityKey('vulnerability', f)) === -1;
  }});
  const totalFindings = secretFindings.length + vulnFindings.length;
  document.getElementById('summary-title').textContent =
    totalFindings === 0 ? repo + ' is clean in the latest scan' : repo + ' has ' + totalFindings + ' open finding' + (totalFindings === 1 ? '' : 's');
  document.getElementById('summary-copy').textContent =
    'Latest evidence covers ' + (((evidence.repository || {{}}).modules || []).length) + ' modules, source-mapped findings, dependency signals, and repository history. Use the left rail to drill into the exact file, line, owner, dependency, and risk.';
  document.getElementById('summary-risk').textContent = totalFindings === 0 ? 'No open findings' : totalFindings + ' open findings';
  document.getElementById('summary-scans').textContent = history.length + ' scan' + (history.length === 1 ? '' : 's');

  document.getElementById('stat-findings').textContent = totalFindings;
  document.getElementById('stat-findings').className = 'stat-value' + (totalFindings > 0 ? ' critical' : ' success');
  document.getElementById('stat-findings-sub').textContent = secretFindings.length + ' secret, ' + vulnFindings.length + ' dependency';

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
  if (securePreview.length === 0 && vulnPreview.length === 0) {{
    recentBody.innerHTML = '<div class="empty-state">No open findings.</div>';
  }} else {{
    let rows = '';
    securePreview.forEach(function (f) {{
      rows += '<tr><td><span class="sev-stripe critical"></span><span class="finding-title">Possible ' + escapeHtml(f.pattern) + ' secret</span></td>' +
        '<td class="finding-cite">' + escapeHtml(f.path) + ':' + f.line + '</td>' +
        '<td><span class="chip critical">Critical</span></td></tr>';
    }});
    vulnPreview.forEach(function (f) {{
      rows += '<tr><td><span class="sev-stripe warning"></span><span class="finding-title">' + escapeHtml(f.advisory_id) + ': ' + escapeHtml(f.summary || 'known vulnerability') + '</span></td>' +
        '<td class="finding-cite">' + escapeHtml(f.package) + '@' + escapeHtml(f.installed_version) + '</td>' +
        '<td><span class="chip warning">Warning</span></td></tr>';
    }});
    recentBody.innerHTML = '<table class="findings"><thead><tr><th>Finding</th><th>Evidence</th><th>Severity</th></tr></thead><tbody>' + rows + '</tbody></table>';
  }}
}}

async function loadUptimeStat() {{
  const res = await apiGet(base + '/health');
  if (!res || !res.ok) return;
  const data = await res.json();
  const endpoints = data.endpoints || [];
  if (endpoints.length === 0) {{
    document.getElementById('stat-uptime').textContent = 'Not configured';
    document.getElementById('stat-uptime-sub').textContent = 'Add a target in Endpoint health';
    return;
  }}
  const up = endpoints.filter(function (e) {{ return e.reachable; }}).length;
  const pct = Math.round((up / endpoints.length) * 100);
  document.getElementById('stat-uptime').textContent = pct + '%';
  document.getElementById('stat-uptime').className = 'stat-value' + (pct === 100 ? ' success' : pct < 90 ? ' critical' : ' warning');
  document.getElementById('stat-uptime-sub').textContent = up + ' of ' + endpoints.length + ' endpoints up';
}}

loadOverview();
loadUptimeStat();
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

function toggleDismissedFindings(event, dismissedSecretFindings, dismissedVulnFindings) {{
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
  const dismissedKeys = data.dismissed_finding_keys || {{ secret: [], vulnerability: [] }};
  const evidence = history[0].evidence || {{}};
  const security = evidence.security || {{}};
  const allSecretFindings = ((security.secrets || {{}}).findings || []).filter(function (f) {{ return !f.likely_placeholder && !f.accepted; }});
  const allVulnFindings = (security.dependency_vulnerabilities || {{}}).findings || [];

  const secretFindings = allSecretFindings.filter(function (f) {{ return dismissedKeys.secret.indexOf(findingIdentityKey('secret', f)) === -1; }});
  const vulnFindings = allVulnFindings.filter(function (f) {{ return dismissedKeys.vulnerability.indexOf(findingIdentityKey('vulnerability', f)) === -1; }});
  const dismissedSecretFindings = allSecretFindings.filter(function (f) {{ return dismissedKeys.secret.indexOf(findingIdentityKey('secret', f)) !== -1; }});
  const dismissedVulnFindings = allVulnFindings.filter(function (f) {{ return dismissedKeys.vulnerability.indexOf(findingIdentityKey('vulnerability', f)) !== -1; }});
  const dismissedCount = dismissedSecretFindings.length + dismissedVulnFindings.length;

  if (secretFindings.length === 0 && vulnFindings.length === 0) {{
    body.innerHTML = '<div class="empty-state">No open findings.</div>';
    if (dismissedCount > 0) {{
      body.innerHTML += '<p class="section-sub" style="margin-top:16px"><a href="#" onclick="toggleDismissedFindings(event, window._dismissedSecretFindings, window._dismissedVulnFindings)">Show dismissed (' + dismissedCount + ')</a></p>' +
        '<div id="dismissed-findings-body" style="display:none"></div>';
    }}
    window._dismissedSecretFindings = dismissedSecretFindings;
    window._dismissedVulnFindings = dismissedVulnFindings;
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
  body.innerHTML = '<table class="findings"><thead><tr><th>Finding</th><th>Evidence</th><th>Severity</th><th></th></tr></thead><tbody>' + rows + '</tbody></table>';
  if (dismissedCount > 0) {{
    body.innerHTML += '<p class="section-sub" style="margin-top:16px"><a href="#" onclick="toggleDismissedFindings(event, window._dismissedSecretFindings, window._dismissedVulnFindings)">Show dismissed (' + dismissedCount + ')</a></p>' +
      '<div id="dismissed-findings-body" style="display:none"></div>';
  }}
  window._dismissedSecretFindings = dismissedSecretFindings;
  window._dismissedVulnFindings = dismissedVulnFindings;
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
    _topbar("Endpoint health")
    + """
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
        <div class="section-title"><i class="ti ti-activity" aria-hidden="true"></i>Results</div>
        <span class="section-sub">Most recent check per endpoint, per target</span>
      </div>
      <div class="section-body" id="health-body"><div class="empty-state">Loading&hellip;</div></div>
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
  const res = await fetch(adminBase + '/health-targets/' + btn.dataset.targetId, {{ method: 'DELETE' }});
  if (res.ok) {{ loadTargets(); loadResults(); }} else {{ btn.disabled = false; }}
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
  statusApiBody.innerHTML = '<div class="copy-box"><input class="field" id="status-url-field" value="' + escapeHtml(statusUrl) + '" readonly>' +
    '<button class="btn" id="copy-status-url">Copy</button></div>' +
    '<div class="settings-block-hint">Unauthenticated and CORS-enabled - safe to call from a public status page.</div>';
  document.getElementById('copy-status-url').addEventListener('click', function () {{
    navigator.clipboard.writeText(statusUrl).then(function () {{
      const btn = document.getElementById('copy-status-url');
      btn.textContent = 'Copied';
      setTimeout(function () {{ btn.textContent = 'Copy'; }}, 1500);
    }});
  }});
}}

async function loadResults() {{
  const res = await apiGet(base + '/health');
  if (!res) return;
  const body = document.getElementById('health-body');
  if (!res.ok) {{ body.innerHTML = '<div class="empty-state">Health data unavailable.</div>'; return; }}
  const data = await res.json();
  const endpoints = data.endpoints || [];
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
      html += '<div class="health-row" id="' + rowId + '" style="cursor:pointer;" onclick="toggleEndpointHistory(\\'' + rowId + '\\')">' +
        '<span class="health-status ' + (e.reachable ? 'up' : 'down') + '"></span>' +
        '<span class="health-endpoint">' + escapeHtml(e.method) + ' ' + escapeHtml(e.path) + '</span>' +
        '<span class="health-latency"' + (e.reachable ? '' : ' style="color:var(--critical);"') + '>' + (e.reachable ? Math.round(e.latency_ms) + 'ms' : (e.status_code || 'unreachable')) + '</span>' +
        '<span class="health-checked">' + relativeTime(e.checked_at) + '</span></div>' +
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
      const location = e.file ? escapeHtml(e.file) + (e.line ? ':' + e.line : '') : '';
      staleHtml += '<div class="health-row"><span class="chip warning">Never reachable</span>' +
        '<span class="health-endpoint">' + escapeHtml(e.method) + ' ' + escapeHtml(e.path) + '</span>' +
        (location ? '<span class="health-checked">' + location + '</span>' : '') +
        '<span class="health-checked">' + e.check_count + ' checks</span></div>';
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
    html += '<div class="health-history-row"><span class="health-status ' + (c.reachable ? 'up' : 'down') + '"></span>' +
      '<span class="health-latency"' + (c.reachable ? '' : ' style="color:var(--critical);"') + '>' +
      (c.reachable ? Math.round(c.latency_ms) + 'ms' : (c.status_code || 'unreachable')) + '</span>' +
      '<span class="health-checked">' + relativeTime(c.checked_at) + '</span></div>';
  }});
  html += '</div>';
  panel.innerHTML = html;
}}

loadTargets();
loadResults();
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
    <section class="section">
      <div class="section-head">
        <div class="section-title"><i class="ti ti-book-2" aria-hidden="true"></i>AIRview</div>
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
async function renderDiagram(container, text) {{
  if (!text || !window.mermaid) {{ container.remove(); return; }}
  try {{
    const id = 'mmd-' + (mermaidSeq++);
    const {{ svg }} = await mermaid.render(id, text);
    container.innerHTML = svg;
    const wrap = container.closest('.diagram-wrap');
    if (wrap) {{
      wrap.classList.add('diagram-zoomable');
      wrap.onclick = function () {{ openDiagramZoom(svg); }};
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
    filesHtml += '<div class="subsystem-detail-file"><div class="subsystem-detail-path">' + escapeHtml(f.path) + '</div>' +
      '<div class="subsystem-detail-role">' + escapeHtml(f.role) + '</div>' + symbolsHtml + '</div>';
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
    '<div class="wiki-banner"><div class="wiki-banner-text"><b>Built once by a frontier model, kept current by a fast one.</b> Every diagram edge below is a real import in this repo, never inferred.</div></div>' +
    '<div class="diagram-wrap"><div class="mermaid" id="overview-diagram"></div></div>' +
    '<div class="subsystem-grid" id="subsystem-grid"></div>';
  body.innerHTML = html;
  renderDiagram(document.getElementById('overview-diagram'), data.overview.diagram_mermaid);
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
    _topbar("Docs")
    + """
    <section class="section">
      <div class="section-head">
        <div class="section-title"><i class="ti ti-file-text" aria-hidden="true"></i>Docs</div>
        <span class="section-sub">Regenerated automatically on every push</span>
        <a class="btn" id="docs-download-link" href="#" download style="display:none"><i class="ti ti-download" aria-hidden="true"></i>Download</a>
      </div>
      <div class="section-body" id="docs-body"><div class="empty-state">Loading&hellip;</div></div>
    </section>

    <section class="section" id="docs-repo-commit-section" style="display:none">
      <div class="section-head">
        <div class="section-title"><i class="ti ti-git-pull-request" aria-hidden="true"></i>Commit to repo</div>
        <span class="section-sub">Also push this reference into your repo as .aletheore/docs/API.md</span>
      </div>
      <div class="section-body" id="docs-repo-commit-body"><div class="empty-state">Loading&hellip;</div></div>
    </section>
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

function docsLineCount(markdown) {{
  return markdown.split('\\n').filter(function (line) {{ return line.trim().length > 0; }}).length;
}}

function docsHasAiText(markdown) {{
  return markdown.indexOf('AI-generated') !== -1 || markdown.indexOf('AI-polished') !== -1;
}}

function renderDocsOverview(modulePaths, modules) {{
  const symbolCount = modulePaths.reduce(function (total, path) {{
    return total + docsSymbolCount(modules[path] || '');
  }}, 0);
  const aiCount = modulePaths.filter(function (path) {{ return docsHasAiText(modules[path] || ''); }}).length;
  return '<div class="docs-overview">' +
    '<div>' +
      '<div class="docs-overview-kicker">Aletheore Docs</div>' +
      '<h2>Evidence-grounded API reference</h2>' +
      '<p>Public functions and classes are grouped by source file with signatures, docstrings, generated descriptions, and file:line citations kept visibly grounded in repository evidence.</p>' +
    '</div>' +
    '<div class="docs-overview-stats">' +
      '<div class="docs-stat-pill"><div class="docs-stat-value">' + modulePaths.length + '</div><div class="docs-stat-label">modules</div></div>' +
      '<div class="docs-stat-pill"><div class="docs-stat-value">' + symbolCount + '</div><div class="docs-stat-label">symbols</div></div>' +
      '<div class="docs-stat-pill"><div class="docs-stat-value">' + aiCount + '</div><div class="docs-stat-label">AI-assisted files</div></div>' +
    '</div>' +
  '</div>';
}}

function renderDocsModule(modulePath, markdown) {{
  const details = document.createElement('details');
  details.className = 'docs-module-card';
  const summary = document.createElement('summary');
  summary.className = 'docs-module-summary';
  const symbols = docsSymbolCount(markdown);
  const lines = docsLineCount(markdown);
  const hasAi = docsHasAiText(markdown);
  summary.innerHTML =
    '<div>' +
      '<div class="docs-module-title"><i class="ti ti-file-code" aria-hidden="true"></i><span class="docs-module-path">' + escapeHtml(modulePath) + '</span></div>' +
      '<div class="docs-module-sub">' + symbols + ' public symbol' + (symbols === 1 ? '' : 's') + ' documented from source evidence</div>' +
    '</div>' +
    '<div class="docs-module-meta">' +
      '<span class="docs-chip">' + lines + ' lines</span>' +
      (hasAi ? '<span class="docs-chip ai"><i class="ti ti-sparkles" aria-hidden="true"></i>AI marked</span>' : '') +
      '<i class="ti ti-chevron-down docs-module-chevron" aria-hidden="true"></i>' +
    '</div>';
  const content = document.createElement('div');
  content.className = 'docs-module-content';
  const pre = document.createElement('pre');
  pre.className = 'docs-module-body';
  pre.textContent = markdown;
  details.appendChild(summary);
  content.appendChild(pre);
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
  body.innerHTML = renderDocsOverview(modulePaths, data.modules || {{}}) + staleBanner;
  const list = document.createElement('div');
  list.className = 'docs-grid';
  modulePaths.sort().forEach(function (path) {{
    list.appendChild(renderDocsModule(path, data.modules[path]));
  }});
  body.appendChild(list);
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

SETTINGS_HTML = _page_head("Settings — {repo} — Aletheore") + _shell(
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
<script>
{FETCH_HELPERS}
{PAGE_HEAD_JS}
{CONFIRM_UPGRADE_JS}

async function revokeToken(tokenId, btn) {{
  btn.disabled = true;
  const res = await fetch(adminBase + '/tokens/' + tokenId, {{ method: 'DELETE' }});
  if (res.ok) {{ btn.closest('.token-row').remove(); }} else {{ btn.disabled = false; }}
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

async function generateToken() {{
  const input = document.getElementById('new-token-label');
  const label = input.value.trim();
  const out = document.getElementById('token-reveal');
  if (!label) {{ out.innerHTML = '<div class="error-banner">Give the token a label first.</div>'; input.focus(); return; }}
  const res = await fetch(adminBase + '/tokens', {{
    method: 'POST', headers: {{ 'Content-Type': 'application/json' }}, body: JSON.stringify({{ label: label }}),
  }});
  if (!res.ok) {{ out.innerHTML = '<div class="error-banner">Could not create token.</div>'; return; }}
  const data = await res.json();
  input.value = '';
  out.innerHTML = '<div class="token-reveal">' + escapeHtml(data.token) + '<br><span style="color:var(--slate-600);font-family:var(--font-sans);">Copy this now - it will not be shown again.</span></div>';
  refreshTokenList();
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

async function buySeat() {{
  const status = document.getElementById('seat-billing-status');
  status.textContent = 'Updating billing...';
  status.style.color = 'var(--slate-600)';
  const res = await fetch(adminBase + '/seats/buy', {{ method: 'POST' }});
  const data = await res.json().catch(function () {{ return {{}}; }});
  if (res.ok) {{
    status.textContent = 'Seat added - billing updated. Refreshing...';
    status.style.color = 'var(--success)';
    loadSettings();
  }} else {{
    status.textContent = data.detail || 'Could not buy a seat.';
    status.style.color = 'var(--critical)';
  }}
}}

async function removeSeat() {{
  const status = document.getElementById('seat-billing-status');
  status.textContent = 'Updating billing...';
  status.style.color = 'var(--slate-600)';
  const res = await fetch(adminBase + '/seats/remove', {{ method: 'POST' }});
  const data = await res.json().catch(function () {{ return {{}}; }});
  if (res.ok) {{
    status.textContent = 'Seat removed - billing updated. Refreshing...';
    status.style.color = 'var(--success)';
    loadSettings();
  }} else {{
    status.textContent = data.detail || 'Could not remove a seat.';
    status.style.color = 'var(--critical)';
  }}
}}

async function openBillingPortal() {{
  const status = document.getElementById('seat-billing-status');
  if (status) {{ status.textContent = 'Opening billing portal...'; status.style.color = 'var(--slate-600)'; }}
  const res = await fetch(adminBase + '/billing-portal');
  const data = await res.json().catch(function () {{ return {{}}; }});
  if (res.ok && data.url) {{
    window.location.href = data.url;
    return;
  }}
  if (status) {{
    status.textContent = data.detail || 'Could not open the billing portal.';
    status.style.color = 'var(--critical)';
  }}
}}

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
  const res = await fetch(adminBase + '/delete-all-data/request-otp', {{ method: 'POST' }});
  const data = await res.json().catch(function () {{ return {{}}; }});
  if (!res.ok) {{
    status.textContent = data.detail || 'Could not send a code.';
    status.style.color = 'var(--critical)';
    syncDeleteButton();
    return;
  }}
  status.textContent = 'Code sent to ' + (data.sent_to || 'your email') + ' - expires in 10 minutes.';
  status.style.color = 'var(--slate-600)';
  document.getElementById('otp-row').style.display = '';
  document.getElementById('delete-otp-input').focus();
}}

async function deleteAllData() {{
  const confirmInput = document.getElementById('delete-confirm-input');
  const otpInput = document.getElementById('delete-otp-input');
  const btn = document.getElementById('delete-all-btn');
  const status = document.getElementById('delete-status');
  btn.disabled = true;
  status.textContent = 'Deleting...';
  status.style.color = 'var(--slate-600)';
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
    syncDeleteButton();
    return;
  }}
  // Everything this page reads is gone, including possibly this session -
  // there is nothing left here to re-render, so leave for the marketing site.
  status.textContent = 'Deleted. Signing you out...';
  status.style.color = 'var(--success)';
  window.location.href = '/auth/logout';
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

  // llm_spend and flash_review_monthly_count were already tracked
  // internally for the hard spend cap (see app_server/llm_cost.py) - this
  // is the first place a customer actually sees what their AI review
  // usage is costing/producing, previously invisible to them.
  const llmSpend = data.llm_spend_month_to_date || 0;
  const llmCap = data.llm_spend_cap || 0;
  const flashReviews = data.flash_reviews_month_to_date || 0;
  const spendPct = llmCap > 0 ? Math.min(100, Math.round((llmSpend / llmCap) * 100)) : 0;
  const usageHtml =
    '<div class="settings-block">' +
      '<div class="settings-block-label">AI usage this month</div>' +
      '<div class="settings-block-hint">' + flashReviews + ' automated PR review' + (flashReviews === 1 ? '' : 's') + '</div>' +
      '<div class="settings-block-hint">$' + llmSpend.toFixed(2) + ' of $' + llmCap.toFixed(2) + ' spend cap used (' + spendPct + '%)</div>' +
    '</div>';

  const seatBillingHtml = window._hasActiveSubscription
    ? '<div class="form-row">' +
      '<button class="btn" onclick="buySeat()">Buy extra seat (${EXTRA_SEAT_PRICE_USD}/mo)</button>' +
      (window._extraSeats > 0 ? '<button class="btn" onclick="removeSeat()" style="margin-left:6px;">Remove a seat</button>' : '') +
      '<button class="btn" onclick="openBillingPortal()" style="margin-left:6px;">Manage billing</button>' +
      '</div><div id="seat-billing-status" class="settings-block-hint"></div>'
    : '<div class="settings-block-hint">Extra seats need an active subscription - subscribe first to buy one.</div>';

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
        usageHtml +
        '<div class="settings-block">' +
          '<div class="settings-block-label">API tokens</div>' +
          '<div id="token-list">' + renderTokenRows(data.tokens) + '</div>' +
          '<div class="form-row"><input class="field" id="new-token-label" placeholder="Token label, e.g. CI pipeline">' +
          '<button class="btn" onclick="generateToken()">Generate</button></div>' +
          '<div id="token-reveal"></div>' +
          '<div class="settings-block-hint">Used to authenticate the CLI (<code>aletheore login</code> or <code>ALETHEORE_API_TOKEN</code>) and the MCP server\\'s <code>aletheore_managed_audit</code> tool against this installation\\'s hosted managed audits, and to send runtime events from your app into Aletheore. Give each token a label so you can tell them apart later, and revoke one any time without affecting the others.</div>' +
        '</div>' +
      '</div>' +
      '<div>' +
        '<div class="settings-block">' +
          '<div class="settings-block-label">Alert webhook</div>' +
          '<input class="field" id="webhook-url-input" placeholder="Slack or Teams webhook URL" value="' + escapeHtml(installation.webhook_url || '') + '">' +
          '<div class="form-row"><button class="btn" onclick="saveWebhook()">Save</button><button class="btn" onclick="sendTestNotification()" style="margin-left:6px;">Send test</button><span id="webhook-status" class="settings-block-hint"></span></div>' +
          '<div class="settings-block-hint">New critical findings are posted here shortly after a scan finishes. Paste a Slack incoming-webhook URL or a Teams workflow webhook URL - both are auto-detected.</div>' +
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
        '<div class="settings-block">' +
          '<div class="settings-block-label">Endpoint health targets</div>' +
          '<div class="settings-block-hint">Configure staging/production URLs and see live results on the <a data-href="/health">Endpoint health</a> page.</div>' +
        '</div>' +
      '</div>' +
    '</div>' +
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


_VALID_PLANS = ("air",)
_VALID_INTERVALS = ("month", "year")


def _plan_display_name(plan: str) -> str:
    return "Aletheore Community" if plan == "free" else "Aletheore AIR"


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
    pw_customer_id: str | None = None
    if len(installations) == 1:
        installation = installations[0]
        pw_customer_id = installation.get("paddle_customer_id")
        continue_attrs = f'data-installation-id="{installation["installation_id"]}"'
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
                f'<input type="radio" name="installation_id" value="{installation["installation_id"]}"'
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

    settings = get_settings()
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
  const selected = document.querySelector('input[name="installation_id"]:checked');
  const installationId = selected ? selected.value : btn.dataset.installationId;
  Paddle.Checkout.open({{
    items: [{{ priceId: "{price_id}", quantity: 1 }}],
    customData: {{ installation_id: installationId }},
    settings: {{
      displayMode: "overlay",
      variant: "one-page",
      successUrl: "https://app.aletheore.com/dashboard",
    }},
  }});
}});
</script>
"""


@frontend_router.get("/subscribe", response_class=HTMLResponse)
async def subscribe_page(request: Request, plan: str = "", interval: str = ""):
    if plan not in _VALID_PLANS or interval not in _VALID_INTERVALS:
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
    return _no_store_html(OVERVIEW_HTML)


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
    return _no_store_html(SETTINGS_HTML)
