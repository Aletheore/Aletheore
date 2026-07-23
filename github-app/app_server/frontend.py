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

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app_server.auth import get_current_session

frontend_router = APIRouter()

PRICING_URL = "https://www.aletheore.com/pricing"

ICONS_LINK = (
    '<link rel="stylesheet" '
    'href="https://cdn.jsdelivr.net/npm/@tabler/icons-webfont@3.45.0/dist/tabler-icons.min.css">'
)
MERMAID_SCRIPT = '<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>'

STYLE = """
<style>
:root {
  --ink-900: #1A1A1A;
  --ink-700: #4A443B;
  --slate-50: #F5F0E6;
  --slate-100: #ECE4D3;
  --slate-200: #DED3BC;
  --slate-400: #8A8377;
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
  --font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  --font-mono: ui-monospace, "SF Mono", "Cascadia Code", "Roboto Mono", Menlo, monospace;
  --page-bg: var(--slate-50);
}
@media (prefers-color-scheme: dark) {
  :root {
    --page-bg: #17140F; --paper: #201B14; --slate-50: #17140F; --slate-100: #221D15;
    --slate-200: #332B1F; --slate-400: #8A8377; --slate-600: #B9B1A4;
    --ink-900: #F3EEE3; --ink-700: #D8D2C5;
    --accent: #E0863A; --accent-strong: #EFA262; --accent-soft: #3A2A18; --accent-soft-strong: #4A3620;
    --success: #6FBE7E; --success-soft: #23331F; --warning: #D2A83C; --warning-soft: #3A301A;
    --critical: #E37972; --critical-soft: #3A211D;
    --border: rgba(243, 238, 227, 0.12); --border-strong: rgba(243, 238, 227, 0.22);
    --shadow-card: 0 1px 2px rgba(0, 0, 0, 0.35); --shadow-card-hover: 0 6px 18px rgba(0, 0, 0, 0.45);
  }
}
* { box-sizing: border-box; }
body { margin: 0; font-family: var(--font-sans); color: var(--ink-900); background: var(--page-bg); }
a { color: var(--accent); }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0,0,0,0); white-space: nowrap; border: 0; }

/* ---- Sign-in ---- */
.signin { min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 4rem 1.5rem;
  background-image: radial-gradient(var(--border-strong) 1px, transparent 1px); background-size: 22px 22px; background-color: #101416; }
.signin-card { width: 100%; max-width: 380px; background: #171C1F; border: 1px solid rgba(237,241,238,0.1); border-radius: 14px; padding: 2.25rem 2rem; text-align: center; }
.wordmark { font-family: var(--font-sans); font-weight: 700; font-size: 22px; color: #F2F5F3; margin: 0 0 4px; }
.tagline { font-size: 13px; color: #93A19A; margin: 0 0 2rem; line-height: 1.5; }
.gh-btn { width: 100%; display: flex; align-items: center; justify-content: center; gap: 10px; background: #F2F5F3; color: #14181B;
  border: none; border-radius: 8px; font-family: var(--font-sans); font-size: 14px; font-weight: 500; padding: 11px 16px; cursor: pointer; text-decoration: none; }
.gh-btn:hover { background: #FFFFFF; }
.gh-btn:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.scope-note { margin-top: 1.5rem; padding-top: 1.25rem; border-top: 1px solid rgba(237,241,238,0.1); font-size: 12px; color: #6B7975; line-height: 1.6; text-align: left; }
.scope-note code { font-family: var(--font-mono); font-size: 11px; color: #A7B2AC; }

/* ---- Repo picker ---- */
.picker-wrap { max-width: 920px; margin: 0 auto; padding: 3rem 1.75rem; }
.picker-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 2rem; }
.picker-head h1 { font-size: 22px; font-weight: 500; margin: 0; }
.picker-org-group { margin-bottom: 2rem; }
.picker-org-label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--slate-400); font-weight: 500; margin-bottom: 10px; }
.picker-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 14px; }
.picker-card { display: flex; align-items: center; gap: 12px; text-decoration: none; color: var(--ink-900); background: var(--paper); border: 1px solid var(--border);
  border-radius: 12px; padding: 16px 18px; box-shadow: var(--shadow-card); transition: box-shadow 0.15s ease, border-color 0.15s ease, transform 0.15s ease; }
.picker-card:hover { border-color: var(--border-strong); box-shadow: var(--shadow-card-hover); transform: translateY(-1px); }
.picker-card-icon { width: 40px; height: 40px; border-radius: 9px; background: var(--accent-soft); color: var(--accent-strong);
  display: flex; align-items: center; justify-content: center; font-size: 18px; flex-shrink: 0; }
.picker-card-body { min-width: 0; flex: 1; }
.picker-repo { font-size: 15px; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.picker-plan { display: inline-block; margin-top: 7px; font-size: 11px; font-weight: 500; padding: 2px 9px; border-radius: 99px; background: var(--slate-100); color: var(--slate-600); }
.picker-plan.paid { background: var(--accent-soft); color: var(--accent-strong); }
.picker-card-arrow { color: var(--slate-400); font-size: 16px; flex-shrink: 0; }

/* ---- Shared UI atoms ---- */
.btn { font-family: var(--font-sans); font-size: 12.5px; font-weight: 500; border-radius: 7px; padding: 7px 12px;
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
.shell { display: grid; grid-template-columns: 216px minmax(0, 1fr); min-height: 100vh; }
.sidebar { background: var(--slate-100); border-right: 1px solid var(--border); padding: 1.1rem 0.85rem; display: flex; flex-direction: column; gap: 1.4rem; position: sticky; top: 0; height: 100vh; }
.org-switch { display: flex; align-items: center; gap: 9px; padding: 7px 8px; border: 1px solid var(--border); border-radius: 8px; background: var(--paper); text-decoration: none; color: inherit; }
.org-avatar { width: 22px; height: 22px; border-radius: 6px; background: var(--accent-soft); color: var(--accent-strong); font-size: 11px; font-weight: 500; display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
.org-switch-label { font-size: 13px; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.org-switch-sub { font-size: 11px; color: var(--slate-600); }
.nav-group-label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--slate-400); padding: 0 8px; margin-bottom: 6px; }
.nav-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 1px; }
.nav-item { display: flex; align-items: center; gap: 9px; padding: 7px 8px; border-radius: 7px; font-size: 13.5px; color: var(--ink-700); text-decoration: none; }
.nav-item i { font-size: 16px; color: var(--slate-400); }
.nav-item:hover { background: var(--paper); }
.nav-item.active { background: var(--accent-soft); color: var(--accent-strong); font-weight: 500; }
.nav-item.active i { color: var(--accent-strong); }
.plan-badge-wrap { margin-top: auto; }
.plan-card { background: var(--paper); border: 1px solid var(--border); border-radius: 9px; padding: 10px 11px; }
.plan-name { font-size: 12px; font-weight: 500; display: flex; align-items: center; gap: 6px; text-transform: capitalize; }
.plan-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--accent); }
.plan-sub { font-size: 11px; color: var(--slate-600); margin-top: 3px; line-height: 1.5; }

.main { padding: 1.4rem 1.7rem 3rem; min-width: 0; }
.topbar { display: flex; align-items: baseline; justify-content: space-between; gap: 1rem; margin-bottom: 1.4rem; flex-wrap: wrap; }
.breadcrumb { font-size: 12px; color: var(--slate-600); }
.breadcrumb b { color: var(--ink-900); font-weight: 500; }
.breadcrumb a { color: var(--slate-600); text-decoration: none; }
.breadcrumb a:hover { color: var(--ink-900); }
.h1 { font-size: 20px; font-weight: 500; margin: 2px 0 0; }
.topbar-right { font-size: 12px; color: var(--slate-600); }

.stat-strip { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; margin-bottom: 1.6rem; }
.stat-card { background: var(--slate-100); border-radius: 10px; padding: 12px 14px; text-decoration: none; color: inherit; display: block; transition: background 0.15s ease; }
a.stat-card:hover { background: var(--slate-200); }
.stat-label { font-size: 12px; color: var(--slate-600); }
.stat-value { font-family: var(--font-mono); font-variant-numeric: tabular-nums; font-size: 23px; font-weight: 500; margin-top: 4px; }
.stat-value.critical { color: var(--critical); }
.stat-value.warning { color: var(--warning); }
.stat-value.success { color: var(--success); }
.stat-delta { font-size: 11.5px; color: var(--slate-600); margin-top: 3px; }

.section { background: var(--paper); border: 1px solid var(--border); border-radius: 12px; margin-bottom: 1.1rem; box-shadow: var(--shadow-card); scroll-margin-top: 1rem; }
.section-head { display: flex; align-items: center; justify-content: space-between; padding: 13px 16px; border-bottom: 1px solid var(--border); gap: 1rem; flex-wrap: wrap; }
.section-title { font-size: 14.5px; font-weight: 500; display: flex; align-items: center; gap: 8px; }
.section-title i { font-size: 16px; color: var(--slate-400); }
.section-sub { font-size: 12px; color: var(--slate-600); }
.section-body { padding: 4px 16px 14px; }

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
.health-row { display: flex; align-items: center; gap: 10px; padding: 9px 10px; background: var(--slate-100); border-radius: 8px; }
.health-status { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.health-status.up { background: var(--success); }
.health-status.down { background: var(--critical); }
.health-endpoint { font-family: var(--font-mono); font-size: 12px; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.health-latency { font-family: var(--font-mono); font-variant-numeric: tabular-nums; font-size: 11.5px; color: var(--slate-600); }
.health-checked { font-size: 10.5px; color: var(--slate-400); white-space: nowrap; }
.health-target-group { margin-bottom: 1.2rem; }
.health-target-group:last-child { margin-bottom: 0; }
.health-target-group-label { font-size: 12px; font-weight: 500; margin-bottom: 8px; display: flex; align-items: center; gap: 8px; }

.wiki-banner { display: flex; align-items: center; justify-content: space-between; gap: 1rem; background: var(--accent-soft); border-radius: 10px; padding: 12px 15px; margin: 10px 0 14px; flex-wrap: wrap; }
.wiki-banner-text { font-size: 12.5px; color: var(--accent-strong); line-height: 1.5; max-width: 46ch; }
.wiki-banner-text b { font-weight: 500; }
.diagram-wrap { overflow-x: auto; padding: 6px 0 2px; }
.diagram-wrap .mermaid { display: flex; justify-content: center; }
.diagram-wrap.diagram-zoomable { cursor: zoom-in; }
.diagram-zoom-overlay { position: fixed; inset: 0; z-index: 1000; background: rgba(10, 8, 4, 0.82);
  overflow: auto; padding: 100px 40px; cursor: zoom-out; }
.diagram-zoom-content { cursor: default; display: table; margin: 0 auto; }
.diagram-zoom-content svg { max-width: none; display: block; }
.diagram-zoom-hint { position: fixed; top: 18px; left: 50%; transform: translateX(-50%); z-index: 1001;
  font-size: 12px; color: #F5F0E6; background: rgba(0, 0, 0, 0.45); padding: 6px 13px; border-radius: 99px; pointer-events: none; }
.subsystem-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
.subsystem-card { border: 1px solid var(--border); border-radius: 9px; padding: 11px 12px; text-align: left; background: var(--paper); cursor: pointer; font-family: var(--font-sans); }
.subsystem-card:hover { border-color: var(--border-strong); }
.subsystem-name { font-size: 13px; font-weight: 500; margin-bottom: 3px; }
.subsystem-desc { font-size: 12px; color: var(--slate-600); line-height: 1.55; }
.subsystem-files { font-family: var(--font-mono); font-size: 10.5px; color: var(--slate-400); margin-top: 8px; }
.subsystem-detail { border-top: 1px solid var(--border); margin-top: 14px; padding-top: 14px; }
.subsystem-detail-file { margin-bottom: 12px; }
.subsystem-detail-path { font-family: var(--font-mono); font-size: 12.5px; font-weight: 500; overflow-wrap: anywhere; }
.subsystem-detail-role { font-size: 12.5px; color: var(--slate-600); margin: 3px 0 6px; }
.subsystem-detail-symbol { font-family: var(--font-mono); font-size: 11.5px; color: var(--ink-700); padding: 2px 0 2px 14px; }
.subsystem-detail-symbol .line { color: var(--slate-400); }

.settings-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px; }
.settings-block { margin-bottom: 18px; }
.settings-block-label { font-size: 12px; font-weight: 500; margin-bottom: 7px; }
.settings-block-hint { font-size: 11px; color: var(--slate-600); margin-top: 6px; }
.token-row { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 7px 0; border-bottom: 1px solid var(--border); font-size: 12.5px; }
.token-row:last-child { border-bottom: none; }
.token-label { font-weight: 500; }
.token-meta { font-size: 11px; color: var(--slate-600); }

@media (max-width: 720px) {
  .shell { grid-template-columns: 1fr; }
  .sidebar { position: static; height: auto; flex-direction: row; overflow-x: auto; }
  .stat-strip { grid-template-columns: repeat(2, minmax(0,1fr)); }
  .health-grid, .subsystem-grid, .settings-grid { grid-template-columns: 1fr; }
}
</style>
"""

FETCH_HELPERS = """
async function apiGet(url) {
  const res = await fetch(url);
  if (res.status === 401) { window.location.href = '/'; return null; }
  return res;
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
    body.innerHTML = '<div class="empty-state">No repositories yet. Install the Aletheore GitHub App on an organization to get started.</div>';
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
      return '<a class="picker-card" href="/dashboard/' + encodeURIComponent(r.org) + '/' + encodeURIComponent(r.repo) + '">' +
        '<div class="picker-card-icon"><i class="ti ti-git-branch" aria-hidden="true"></i></div>' +
        '<div class="picker-card-body"><div class="picker-repo">' + escapeHtml(r.repo) + '</div>' +
        '<span class="picker-plan' + (r.plan !== 'free' ? ' paid' : '') + '">' + escapeHtml(r.plan) + ' plan</span></div>' +
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
    nameEl.textContent = 'free';
    subEl.textContent = 'Upgrade for AIRview and settings.';
    return 'free';
  }
  if (!res.ok) { nameEl.textContent = ''; subEl.textContent = ''; return null; }
  const data = await res.json();
  nameEl.textContent = data.installation.plan + ' plan';
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
    document.getElementById('recent-security-body').innerHTML = '<div class="empty-state">No scans yet - findings will appear after the first pull request is scanned.</div>';
    return;
  }}
  const latest = history[0];
  const evidence = latest.evidence || {{}};
  document.getElementById('last-scanned').textContent = 'Last scanned ' + relativeTime(latest.scanned_at);

  const security = evidence.security || {{}};
  const secretFindings = ((security.secrets || {{}}).findings || []).filter(function (f) {{ return !f.likely_placeholder && !f.accepted; }});
  const vulnFindings = (security.dependency_vulnerabilities || {{}}).findings || [];
  const totalFindings = secretFindings.length + vulnFindings.length;

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
  const preview = secretFindings.slice(0, 5);
  if (preview.length === 0 && vulnFindings.length === 0) {{
    recentBody.innerHTML = '<div class="empty-state">No open findings.</div>';
  }} else {{
    let rows = '';
    preview.forEach(function (f) {{
      rows += '<tr><td><span class="sev-stripe critical"></span><span class="finding-title">Possible ' + escapeHtml(f.pattern) + ' secret</span></td>' +
        '<td class="finding-cite">' + escapeHtml(f.path) + ':' + f.line + '</td>' +
        '<td><span class="chip critical">Critical</span></td></tr>';
    }});
    recentBody.innerHTML = '<table class="findings"><thead><tr><th>Finding</th><th>Evidence</th><th>Severity</th></tr></thead><tbody>' + rows + '</tbody></table>';
  }}
}}

async function loadUptimeStat() {{
  const res = await apiGet(base + '/health');
  if (!res || !res.ok) return;
  const data = await res.json();
  const endpoints = data.endpoints || [];
  if (endpoints.length === 0) {{ document.getElementById('stat-uptime').textContent = 'n/a'; return; }}
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

async function loadSecurity() {{
  const res = await apiGet(base);
  const body = document.getElementById('security-body');
  if (!res) return;
  if (!res.ok) {{ body.innerHTML = '<div class="empty-state">Unavailable.</div>'; return; }}
  const data = await res.json();
  const history = data.history || [];
  if (history.length === 0) {{ body.innerHTML = '<div class="empty-state">No scans yet.</div>'; return; }}
  const evidence = history[0].evidence || {{}};
  const security = evidence.security || {{}};
  const secretFindings = ((security.secrets || {{}}).findings || []).filter(function (f) {{ return !f.likely_placeholder && !f.accepted; }});
  const vulnFindings = (security.dependency_vulnerabilities || {{}}).findings || [];

  if (secretFindings.length === 0 && vulnFindings.length === 0) {{
    body.innerHTML = '<div class="empty-state">No open findings.</div>';
    return;
  }}
  let rows = '';
  secretFindings.forEach(function (f) {{
    rows += '<tr><td><span class="sev-stripe critical"></span><span class="finding-title">Possible ' + escapeHtml(f.pattern) + ' secret</span></td>' +
      '<td class="finding-cite">' + escapeHtml(f.path) + ':' + f.line + '</td>' +
      '<td><span class="chip critical">Critical</span></td></tr>';
  }});
  vulnFindings.forEach(function (f) {{
    rows += '<tr><td><span class="sev-stripe warning"></span><span class="finding-title">' + escapeHtml(f.advisory_id) + ': ' + escapeHtml(f.summary || 'known vulnerability') + '</span></td>' +
      '<td class="finding-cite">' + escapeHtml(f.package) + '@' + escapeHtml(f.installed_version) + '</td>' +
      '<td><span class="chip warning">Warning</span></td></tr>';
  }});
  body.innerHTML = '<table class="findings"><thead><tr><th>Finding</th><th>Evidence</th><th>Severity</th></tr></thead><tbody>' + rows + '</tbody></table>';
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
  Object.keys(groups).sort().forEach(function (label) {{
    const rows = groups[label];
    const up = rows.filter(function (e) {{ return e.reachable; }}).length;
    html += '<div class="health-target-group"><div class="health-target-group-label">' + escapeHtml(label) +
      '<span class="chip ' + (up === rows.length ? 'success' : 'critical') + '">' + up + ' of ' + rows.length + ' up</span></div>' +
      '<div class="health-grid">';
    rows.forEach(function (e) {{
      html += '<div class="health-row"><span class="health-status ' + (e.reachable ? 'up' : 'down') + '"></span>' +
        '<span class="health-endpoint">' + escapeHtml(e.method) + ' ' + escapeHtml(e.path) + '</span>' +
        '<span class="health-latency"' + (e.reachable ? '' : ' style="color:var(--critical);"') + '>' + (e.reachable ? Math.round(e.latency_ms) + 'ms' : (e.status_code || 'unreachable')) + '</span>' +
        '<span class="health-checked">' + relativeTime(e.checked_at) + '</span></div>';
    }});
    html += '</div></div>';
  }});
  body.innerHTML = html;
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
  let scale = 1;
  const overlay = document.createElement('div');
  overlay.className = 'diagram-zoom-overlay';
  const content = document.createElement('div');
  content.className = 'diagram-zoom-content';
  content.innerHTML = svgHtml;
  const svg = content.querySelector('svg');
  if (svg) {{
    const vb = svg.viewBox && svg.viewBox.baseVal;
    const naturalWidth = (vb && vb.width) || parseFloat(svg.style.maxWidth) || 800;
    svg.style.width = naturalWidth + 'px';
    svg.style.maxWidth = 'none';
    svg.removeAttribute('height');
  }}
  content.style.transform = 'scale(' + scale + ')';
  const hint = document.createElement('div');
  hint.className = 'diagram-zoom-hint';
  hint.textContent = 'Scroll to zoom · click to close';
  overlay.appendChild(content);
  overlay.appendChild(hint);
  overlay.addEventListener('click', closeDiagramZoom);
  overlay.addEventListener('wheel', function (e) {{
    e.preventDefault();
    scale = Math.min(4, Math.max(0.5, scale + (e.deltaY < 0 ? 0.15 : -0.15)));
    content.style.transform = 'scale(' + scale + ')';
  }}, {{ passive: false }});
  document.body.appendChild(overlay);
  document.body.style.overflow = 'hidden';
  document.addEventListener('keydown', onDiagramZoomKeydown);
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
    body.innerHTML = '<div class="empty-state">AIRview hasn\\'t been built yet - it generates automatically shortly after upgrading.</div>';
    return;
  }}
  let html = '<div class="wiki-banner"><div class="wiki-banner-text"><b>Built once by a frontier model, kept current by a fast one.</b> Every diagram edge below is a real import in this repo, never inferred.</div></div>' +
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
  if (!label) return;
  const out = document.getElementById('token-reveal');
  const res = await fetch(adminBase + '/tokens', {{
    method: 'POST', headers: {{ 'Content-Type': 'application/json' }}, body: JSON.stringify({{ label: label }}),
  }});
  if (!res.ok) {{ out.innerHTML = '<div class="error-banner">Could not create token.</div>'; return; }}
  const data = await res.json();
  input.value = '';
  out.innerHTML = '<div class="token-reveal">' + escapeHtml(data.token) + '<br><span style="color:var(--slate-600);font-family:var(--font-sans);">Copy this now - it will not be shown again.</span></div>';
  refreshTokenList();
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

async function loadSettings() {{
  const body = document.getElementById('settings-body');
  const res = await apiGet(adminBase);
  if (!res) return;
  if (res.status === 402) {{
    body.innerHTML = lockedFeature(
      'API tokens, webhooks, and team seats are paid features',
      'Upgrade to configure them for this repository.',
      {SETTINGS_LOCKED_PREVIEW!r}
    );
    return;
  }}
  if (!res.ok) {{
    body.innerHTML = '<div class="empty-state">Settings unavailable.</div>';
    return;
  }}
  const data = await res.json();
  const installation = data.installation;

  body.innerHTML =
    '<div class="settings-grid">' +
      '<div>' +
        '<div class="settings-block">' +
          '<div class="settings-block-label">Team &middot; <span id="seat-usage">' + (data.members || []).length + ' of ' + data.seat_limit + ' seats used</span></div>' +
          '<div id="member-list">' + renderMemberRows(data.members) + '</div>' +
          '<div class="form-row"><input class="field" id="new-member-login" placeholder="GitHub username">' +
          '<button class="btn" onclick="addMember()">Add</button></div>' +
          '<div id="member-status" class="settings-block-hint"></div>' +
          '<div class="settings-block-hint">Extra seats beyond the plan\\'s limit aren\\'t billable yet - adding past the cap is blocked for now.</div>' +
        '</div>' +
        '<div class="settings-block">' +
          '<div class="settings-block-label">API tokens</div>' +
          '<div id="token-list">' + renderTokenRows(data.tokens) + '</div>' +
          '<div class="form-row"><input class="field" id="new-token-label" placeholder="Token label, e.g. CI pipeline">' +
          '<button class="btn" onclick="generateToken()">Generate</button></div>' +
          '<div id="token-reveal"></div>' +
        '</div>' +
      '</div>' +
      '<div>' +
        '<div class="settings-block">' +
          '<div class="settings-block-label">Alert webhook</div>' +
          '<input class="field" id="webhook-url-input" placeholder="https://hooks.slack.com/..." value="' + escapeHtml(installation.webhook_url || '') + '">' +
          '<div class="form-row"><button class="btn" onclick="saveWebhook()">Save</button><span id="webhook-status" class="settings-block-hint"></span></div>' +
          '<div class="settings-block-hint">New critical findings are posted here shortly after a scan finishes.</div>' +
        '</div>' +
        '<div class="settings-block">' +
          '<div class="settings-block-label">Endpoint health targets</div>' +
          '<div class="settings-block-hint">Configure staging/production URLs and see live results on the <a data-href="/health">Endpoint health</a> page.</div>' +
        '</div>' +
      '</div>' +
    '</div>';
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


@frontend_router.get("/dashboard/{org}/{repo}/settings", response_class=HTMLResponse)
async def dashboard_settings_page(org: str, repo: str, request: Request):
    redirect = await _require_session_or_redirect(request)
    if redirect is not None:
        return redirect
    return _no_store_html(SETTINGS_HTML)
