"""Server-rendered HTML shell for the managed dashboard.

No build step, matching this codebase's existing convention (see
aletheore.dashboard.DASHBOARD_HTML for the local dashboard's identical
approach) - each page is a static string with an embedded <script> that
fetches JSON from the real app_server/admin.py APIs and renders it
client-side. org/repo are read from the URL path in JS rather than
interpolated server-side, so these strings never need to survive a
str.format() pass against CSS full of literal braces.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app_server.auth import get_current_session

frontend_router = APIRouter()

PRICING_URL = "https://www.aletheore.com/pricing"

ICONS_LINK = (
    '<link rel="stylesheet" '
    'href="https://cdn.jsdelivr.net/npm/@tabler/icons-webfont@2.44.0/dist/tabler-icons.min.css">'
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
    --shadow-card: 0 1px 2px rgba(0, 0, 0, 0.35);
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
.picker-wrap { max-width: 760px; margin: 0 auto; padding: 3rem 1.5rem; }
.picker-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 1.5rem; }
.picker-head h1 { font-size: 20px; font-weight: 500; margin: 0; }
.picker-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 12px; }
.picker-card { display: block; text-decoration: none; color: var(--ink-900); background: var(--paper); border: 1px solid var(--border);
  border-radius: 12px; padding: 14px 16px; box-shadow: var(--shadow-card); }
.picker-card:hover { border-color: var(--border-strong); }
.picker-org { font-size: 12px; color: var(--slate-600); }
.picker-repo { font-size: 14.5px; font-weight: 500; margin-top: 2px; }
.picker-plan { display: inline-block; margin-top: 10px; font-size: 11px; font-weight: 500; padding: 2px 9px; border-radius: 99px; background: var(--slate-100); color: var(--slate-600); }
.picker-plan.paid { background: var(--accent-soft); color: var(--accent-strong); }

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
.form-row { display: flex; gap: 8px; margin-top: 8px; }
.form-row .field { flex: 1; }
.token-reveal { font-family: var(--font-mono); font-size: 12px; background: var(--warning-soft); color: var(--ink-900);
  border-radius: 7px; padding: 10px 12px; margin: 8px 0; word-break: break-all; }

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
.plan-badge-wrap { margin-top: auto; }
.plan-card { background: var(--paper); border: 1px solid var(--border); border-radius: 9px; padding: 10px 11px; }
.plan-name { font-size: 12px; font-weight: 500; display: flex; align-items: center; gap: 6px; text-transform: capitalize; }
.plan-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--accent); }
.plan-sub { font-size: 11px; color: var(--slate-600); margin-top: 3px; line-height: 1.5; }

.main { padding: 1.4rem 1.7rem 3rem; min-width: 0; }
.topbar { display: flex; align-items: baseline; justify-content: space-between; gap: 1rem; margin-bottom: 1.4rem; flex-wrap: wrap; }
.breadcrumb { font-size: 12px; color: var(--slate-600); }
.breadcrumb b { color: var(--ink-900); font-weight: 500; }
.h1 { font-size: 20px; font-weight: 500; margin: 2px 0 0; }
.topbar-right { font-size: 12px; color: var(--slate-600); }

.stat-strip { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; margin-bottom: 1.6rem; }
.stat-card { background: var(--slate-100); border-radius: 10px; padding: 12px 14px; }
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
.finding-cite { font-family: var(--font-mono); font-size: 11.5px; color: var(--slate-600); }
.sev-stripe { display: inline-block; width: 3px; height: 13px; border-radius: 2px; margin-right: 8px; vertical-align: -2px; }
.sev-stripe.critical { background: var(--critical); }
.sev-stripe.warning { background: var(--warning); }

.deadcode-list, .dep-list { display: flex; flex-direction: column; }
.deadcode-row { display: flex; align-items: center; gap: 12px; padding: 9px 0; border-bottom: 1px solid var(--border); font-size: 13px; }
.deadcode-row:last-child { border-bottom: none; }
.deadcode-path { font-family: var(--font-mono); font-size: 12.5px; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.deadcode-meta { font-size: 11.5px; color: var(--slate-600); white-space: nowrap; }

.health-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
.health-row { display: flex; align-items: center; gap: 10px; padding: 9px 10px; background: var(--slate-100); border-radius: 8px; }
.health-status { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.health-status.up { background: var(--success); }
.health-status.down { background: var(--critical); }
.health-endpoint { font-family: var(--font-mono); font-size: 12px; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.health-latency { font-family: var(--font-mono); font-variant-numeric: tabular-nums; font-size: 11.5px; color: var(--slate-600); }
.health-checked { font-size: 10.5px; color: var(--slate-400); white-space: nowrap; }

.wiki-banner { display: flex; align-items: center; justify-content: space-between; gap: 1rem; background: var(--accent-soft); border-radius: 10px; padding: 12px 15px; margin: 10px 0 14px; flex-wrap: wrap; }
.wiki-banner-text { font-size: 12.5px; color: var(--accent-strong); line-height: 1.5; max-width: 46ch; }
.wiki-banner-text b { font-weight: 500; }
.diagram-wrap { overflow-x: auto; padding: 6px 0 2px; }
.diagram-wrap .mermaid { display: flex; justify-content: center; }
.subsystem-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
.subsystem-card { border: 1px solid var(--border); border-radius: 9px; padding: 11px 12px; text-align: left; background: var(--paper); cursor: pointer; font-family: var(--font-sans); }
.subsystem-card:hover { border-color: var(--border-strong); }
.subsystem-name { font-size: 13px; font-weight: 500; margin-bottom: 3px; }
.subsystem-desc { font-size: 12px; color: var(--slate-600); line-height: 1.55; }
.subsystem-files { font-family: var(--font-mono); font-size: 10.5px; color: var(--slate-400); margin-top: 8px; }
.subsystem-detail { border-top: 1px solid var(--border); margin-top: 14px; padding-top: 14px; }
.subsystem-detail-file { margin-bottom: 12px; }
.subsystem-detail-path { font-family: var(--font-mono); font-size: 12.5px; font-weight: 500; }
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
  body.innerHTML = '<div class="picker-grid"></div>';
  const grid = body.querySelector('.picker-grid');
  data.repos.forEach(function (r) {{
    const a = document.createElement('a');
    a.className = 'picker-card';
    a.href = '/dashboard/' + encodeURIComponent(r.org) + '/' + encodeURIComponent(r.repo);
    a.innerHTML = '<div class="picker-org">' + escapeHtml(r.org) + '</div>' +
      '<div class="picker-repo">' + escapeHtml(r.repo) + '</div>' +
      '<span class="picker-plan' + (r.plan !== 'free' ? ' paid' : '') + '">' + escapeHtml(r.plan) + ' plan</span>';
    grid.appendChild(a);
  }});
}})();
</script>
"""

DASHBOARD_HTML = f"""<!DOCTYPE html>
<title>Dashboard — Aletheore</title>
{ICONS_LINK}
{MERMAID_SCRIPT}
{STYLE}
<div class="shell">
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
      <ul class="nav-list">
        <li><a class="nav-item" href="#overview"><i class="ti ti-layout-dashboard" aria-hidden="true"></i>Overview</a></li>
        <li><a class="nav-item" href="#security"><i class="ti ti-shield-check" aria-hidden="true"></i>Security findings</a></li>
        <li><a class="nav-item" href="#deadcode"><i class="ti ti-trash" aria-hidden="true"></i>Dead code</a></li>
        <li><a class="nav-item" href="#health"><i class="ti ti-activity" aria-hidden="true"></i>Endpoint health</a></li>
        <li><a class="nav-item" href="#wiki"><i class="ti ti-book-2" aria-hidden="true"></i>Live wiki</a></li>
      </ul>
    </div>
    <div>
      <div class="nav-group-label">Account</div>
      <ul class="nav-list">
        <li><a class="nav-item" href="#settings"><i class="ti ti-settings" aria-hidden="true"></i>Settings</a></li>
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

  <main class="main">
    <div class="topbar">
      <div>
        <div class="breadcrumb"><span id="crumb-org"></span> <span style="color:var(--slate-400);">/</span> <b id="crumb-repo"></b></div>
        <h1 class="h1">Overview</h1>
      </div>
      <div class="topbar-right" id="last-scanned"></div>
    </div>

    <div id="top-error"></div>

    <div class="stat-strip" id="stat-strip">
      <div class="stat-card"><div class="stat-label">Open findings</div><div class="stat-value" id="stat-findings">&ndash;</div><div class="stat-delta" id="stat-findings-sub"></div></div>
      <div class="stat-card"><div class="stat-label">Dead code</div><div class="stat-value" id="stat-deadcode">&ndash;</div><div class="stat-delta" id="stat-deadcode-sub"></div></div>
      <div class="stat-card"><div class="stat-label">Endpoint uptime</div><div class="stat-value" id="stat-uptime">&ndash;</div><div class="stat-delta" id="stat-uptime-sub"></div></div>
      <div class="stat-card"><div class="stat-label">Modules scanned</div><div class="stat-value" id="stat-modules">&ndash;</div><div class="stat-delta" id="stat-modules-sub"></div></div>
    </div>

    <section class="section" id="overview"></section>

    <section class="section" id="security">
      <div class="section-head">
        <div class="section-title"><i class="ti ti-shield-check" aria-hidden="true"></i>Security findings</div>
        <span class="section-sub">Every claim below cites the exact file and line it was found at.</span>
      </div>
      <div class="section-body" id="security-body"><div class="empty-state">Loading&hellip;</div></div>
    </section>

    <section class="section" id="deadcode">
      <div class="section-head">
        <div class="section-title"><i class="ti ti-trash" aria-hidden="true"></i>Dead code</div>
        <span class="section-sub">Modules nothing else in the repo imports</span>
      </div>
      <div class="section-body" id="deadcode-body"><div class="empty-state">Loading&hellip;</div></div>
    </section>

    <section class="section" id="health">
      <div class="section-head">
        <div class="section-title"><i class="ti ti-activity" aria-hidden="true"></i>Endpoint health</div>
        <span class="section-sub">Most recent check per endpoint</span>
      </div>
      <div class="section-body" id="health-body"><div class="empty-state">Loading&hellip;</div></div>
    </section>

    <section class="section" id="wiki">
      <div class="section-head">
        <div class="section-title"><i class="ti ti-book-2" aria-hidden="true"></i>Live wiki</div>
        <span class="section-sub">Regenerated automatically on every push</span>
      </div>
      <div class="section-body" id="wiki-body"><div class="empty-state">Loading&hellip;</div></div>
    </section>

    <section class="section" id="settings">
      <div class="section-head">
        <div class="section-title"><i class="ti ti-key" aria-hidden="true"></i>Settings</div>
      </div>
      <div class="section-body" id="settings-body"><div class="empty-state">Loading&hellip;</div></div>
    </section>
  </main>
</div>

<script>
{FETCH_HELPERS}
const parts = window.location.pathname.split('/').filter(Boolean);
const org = decodeURIComponent(parts[1]);
const repo = decodeURIComponent(parts[2]);
const base = '/app/' + encodeURIComponent(org) + '/' + encodeURIComponent(repo);
const adminBase = '/admin/' + encodeURIComponent(org) + '/' + encodeURIComponent(repo);

document.getElementById('side-org').textContent = org;
document.getElementById('side-repo').textContent = repo;
document.getElementById('org-avatar').textContent = org.slice(0, 2).toLowerCase();
document.getElementById('crumb-org').textContent = org;
document.getElementById('crumb-repo').textContent = repo;
document.title = org + '/' + repo + ' — Aletheore';

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
  }} catch (e) {{
    container.remove();
  }}
}}

function confirmUpgrade() {{
  if (window.confirm('Upgrade to Pro to unlock this feature. Continue to the pricing page?')) {{
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
      '<button class="btn btn-accent" onclick="confirmUpgrade()">Upgrade to Pro</button>' +
    '</div>' +
  '</div>';
}}

async function loadOverview() {{
  const res = await apiGet(base);
  if (!res) return;
  if (!res.ok) {{
    document.getElementById('top-error').innerHTML =
      '<div class="error-banner">' + (res.status === 403 ? "You don't administer this repository." : 'Repository not found.') + '</div>';
    document.getElementById('security-body').innerHTML = '<div class="empty-state">Unavailable.</div>';
    document.getElementById('deadcode-body').innerHTML = '<div class="empty-state">Unavailable.</div>';
    return;
  }}
  const data = await res.json();
  const history = data.history || [];
  if (history.length === 0) {{
    document.getElementById('last-scanned').textContent = 'No scans yet';
    document.getElementById('security-body').innerHTML = '<div class="empty-state">No scans yet - findings will appear after the first pull request is scanned.</div>';
    document.getElementById('deadcode-body').innerHTML = '<div class="empty-state">No scans yet.</div>';
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

  renderSecurity(secretFindings, vulnFindings);
  renderDeadCode(unreachable, unusedDeps);
}}

function renderSecurity(secretFindings, vulnFindings) {{
  const body = document.getElementById('security-body');
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

function renderDeadCode(unreachable, unusedDeps) {{
  const body = document.getElementById('deadcode-body');
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

async function loadHealth() {{
  const res = await apiGet(base + '/health');
  if (!res) return;
  const body = document.getElementById('health-body');
  if (!res.ok) {{ body.innerHTML = '<div class="empty-state">Health data unavailable.</div>'; return; }}
  const data = await res.json();
  const endpoints = data.endpoints || [];
  if (endpoints.length === 0) {{
    body.innerHTML = '<div class="empty-state">No endpoint health checks configured yet - set a health check base URL in Settings.</div>';
    document.getElementById('stat-uptime').textContent = 'n/a';
    return;
  }}
  const up = endpoints.filter(function (e) {{ return e.reachable; }}).length;
  const pct = Math.round((up / endpoints.length) * 100);
  document.getElementById('stat-uptime').textContent = pct + '%';
  document.getElementById('stat-uptime').className = 'stat-value' + (pct === 100 ? ' success' : pct < 90 ? ' critical' : ' warning');
  document.getElementById('stat-uptime-sub').textContent = up + ' of ' + endpoints.length + ' endpoints up';

  let html = '<div class="health-grid">';
  endpoints.forEach(function (e) {{
    html += '<div class="health-row"><span class="health-status ' + (e.reachable ? 'up' : 'down') + '"></span>' +
      '<span class="health-endpoint">' + escapeHtml(e.method) + ' ' + escapeHtml(e.path) + '</span>' +
      '<span class="health-latency"' + (e.reachable ? '' : ' style="color:var(--critical);"') + '>' + (e.reachable ? Math.round(e.latency_ms) + 'ms' : (e.status_code || 'unreachable')) + '</span>' +
      '<span class="health-checked">' + relativeTime(e.checked_at) + '</span></div>';
  }});
  html += '</div>';
  body.innerHTML = html;
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

const WIKI_LOCKED_PREVIEW =
  '<div class="diagram-wrap"><svg width="400" height="70" viewBox="0 0 400 70"><g font-size="12">' +
  '<rect x="10" y="16" width="110" height="38" rx="7" fill="var(--accent-soft)" stroke="var(--accent)"></rect>' +
  '<text x="65" y="39" text-anchor="middle" fill="var(--accent-strong)">Checkout API</text>' +
  '<rect x="200" y="16" width="110" height="38" rx="7" fill="var(--accent-soft)" stroke="var(--accent)"></rect>' +
  '<text x="255" y="39" text-anchor="middle" fill="var(--accent-strong)">Payments</text>' +
  '<path d="M120,35 L200,35" stroke="var(--slate-400)" stroke-width="1.3"></path>' +
  '</g></svg></div>' +
  '<div class="subsystem-grid">' +
  '<div class="subsystem-card"><div class="subsystem-name">Checkout API</div><div class="subsystem-desc">Validates carts and creates a payment session before handing off downstream.</div></div>' +
  '<div class="subsystem-card"><div class="subsystem-name">Payments</div><div class="subsystem-desc">Wraps the payment SDK and reconciles session state with webhook ingest.</div></div>' +
  '</div>';

async function loadWiki(plan) {{
  const body = document.getElementById('wiki-body');
  if (plan === 'free') {{
    body.innerHTML = lockedFeature(
      'Live wiki is a paid feature',
      'An LLM-written wiki of this repo, with real dependency diagrams grounded in the scanner\\'s own evidence.',
      WIKI_LOCKED_PREVIEW
    );
    return;
  }}
  const res = await apiGet(base + '/wiki');
  if (!res) return;
  if (res.status === 402) {{
    body.innerHTML = lockedFeature(
      'Live wiki is a paid feature',
      'An LLM-written wiki of this repo, with real dependency diagrams grounded in the scanner\\'s own evidence.',
      WIKI_LOCKED_PREVIEW
    );
    return;
  }}
  if (!res.ok) {{ body.innerHTML = '<div class="empty-state">Wiki unavailable.</div>'; return; }}
  const data = await res.json();
  if (!data.overview) {{
    body.innerHTML = '<div class="empty-state">The wiki hasn\\'t been built yet - it generates automatically shortly after upgrading.</div>';
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

async function saveHealthCheck() {{
  const urlInput = document.getElementById('health-url-input');
  const thresholdInput = document.getElementById('health-threshold-input');
  const status = document.getElementById('health-config-status');
  const res = await fetch(adminBase + '/health-check-url', {{
    method: 'PUT', headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify({{
      health_check_base_url: urlInput.value.trim() || null,
      health_check_latency_threshold_ms: thresholdInput.value ? parseInt(thresholdInput.value, 10) : null,
    }}),
  }});
  const data = await res.json().catch(function () {{ return {{}}; }});
  status.textContent = res.ok ? 'Saved.' : (data.detail || 'Could not save.');
  status.style.color = res.ok ? 'var(--success)' : 'var(--critical)';
}}

const SETTINGS_LOCKED_PREVIEW =
  '<div class="settings-grid">' +
  '<div><div class="settings-block-label">API tokens</div>' +
  '<div class="token-row"><div><div class="token-label">CI pipeline</div><div class="token-meta">created by you &middot; used 3 hours ago</div></div>' +
  '<button class="btn">Revoke</button></div></div>' +
  '<div><div class="settings-block-label">Alert webhook</div>' +
  '<input class="field" value="https://hooks.slack.com/services/..." readonly></div>' +
  '</div>';

async function loadSettings() {{
  const body = document.getElementById('settings-body');
  const res = await apiGet(adminBase);
  if (!res) return;
  if (res.status === 402) {{
    body.innerHTML = lockedFeature(
      'API tokens, webhooks, and health checks are paid features',
      'Upgrade to configure them for this repository.',
      SETTINGS_LOCKED_PREVIEW
    );
    document.getElementById('plan-name').textContent = 'free';
    document.getElementById('plan-sub').textContent = 'Upgrade for live wiki and settings.';
    loadWiki('free');
    return;
  }}
  if (!res.ok) {{
    body.innerHTML = '<div class="empty-state">Settings unavailable.</div>';
    document.getElementById('wiki-body').innerHTML = '<div class="empty-state">Unavailable.</div>';
    return;
  }}
  const data = await res.json();
  const installation = data.installation;
  document.getElementById('plan-name').textContent = installation.plan + ' plan';
  document.getElementById('plan-sub').textContent = installation.plan === 'free' ? 'Upgrade for live wiki and settings.' : 'Live wiki and priority scans included.';
  loadWiki(installation.plan);

  body.innerHTML =
    '<div class="settings-grid">' +
      '<div>' +
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
          '<div class="settings-block-label">Health check</div>' +
          '<input class="field" id="health-url-input" placeholder="https://api.example.com" value="' + escapeHtml(installation.health_check_base_url || '') + '" style="margin-bottom:6px;">' +
          '<input class="field" id="health-threshold-input" type="number" placeholder="Latency threshold, ms" value="' + (installation.health_check_latency_threshold_ms || '') + '">' +
          '<div class="form-row"><button class="btn" onclick="saveHealthCheck()">Save</button><span id="health-config-status" class="settings-block-hint"></span></div>' +
        '</div>' +
      '</div>' +
    '</div>';
}}

loadOverview();
loadHealth();
loadSettings();
</script>
"""


@frontend_router.get("/", response_class=HTMLResponse)
async def signin_page(request: Request):
    session = await get_current_session(request)
    if session is not None:
        return RedirectResponse(url="/dashboard", status_code=307)
    return HTMLResponse(SIGNIN_HTML)


@frontend_router.get("/dashboard", response_class=HTMLResponse)
async def repo_picker_page(request: Request):
    session = await get_current_session(request)
    if session is None:
        return RedirectResponse(url="/", status_code=307)
    return HTMLResponse(PICKER_HTML)


@frontend_router.get("/dashboard/{org}/{repo}", response_class=HTMLResponse)
async def dashboard_page(org: str, repo: str, request: Request):
    session = await get_current_session(request)
    if session is None:
        return RedirectResponse(url="/", status_code=307)
    return HTMLResponse(DASHBOARD_HTML)
