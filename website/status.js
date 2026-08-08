// Aletheore's own status page. Reads the same public, unauthenticated
// status API every AIR customer gets for their own repo -
// GET /v1/health/{org}/{repo} - see github-app/app_server/dashboard.py.
// No separate backend, no synthetic data: this is Aletheore's endpoint
// health monitoring feature, dogfooded on Aletheore itself.
const STATUS_API = "https://app.aletheore.com/v1/health/Aletheore/Aletheore";
const REFRESH_INTERVAL_MS = 60000;

function escapeHtml(value) {
  const div = document.createElement("div");
  div.textContent = value == null ? "" : String(value);
  return div.innerHTML;
}

function setBanner(state, title, subtitle) {
  const banner = document.getElementById("status-banner");
  banner.className = `status-banner${state ? ` is-${state}` : ""}`;
  document.getElementById("status-banner-title").textContent = title;
  document.getElementById("status-banner-subtitle").textContent = subtitle;
}

function formatLatency(entry) {
  if (!entry.reachable || entry.latency_ms == null) return "—";
  return `${Math.round(entry.latency_ms)} ms`;
}

function formatUptime(entry) {
  if (entry.uptime_pct_7d == null) return "—";
  return `${(entry.uptime_pct_7d * 100).toFixed(1)}%`;
}

function renderTable(endpoints) {
  const body = document.getElementById("status-table-body");
  if (!endpoints.length) {
    body.innerHTML = '<tr><td colspan="4">No endpoints reported.</td></tr>';
    return;
  }
  const sorted = [...endpoints].sort((a, b) => a.path.localeCompare(b.path));
  body.innerHTML = sorted
    .map((entry) => {
      const pillClass = entry.reachable ? "is-up" : "is-down";
      const pillLabel = entry.reachable
        ? `Up${entry.status_code != null ? ` (${entry.status_code})` : ""}`
        : "Down";
      return `
        <tr>
          <td><code>${escapeHtml(entry.method)} ${escapeHtml(entry.path)}</code></td>
          <td><span class="status-pill ${pillClass}">${pillLabel}</span></td>
          <td>${formatLatency(entry)}</td>
          <td>${formatUptime(entry)}</td>
        </tr>
      `;
    })
    .join("");
}

function renderBanner(endpoints) {
  const downCount = endpoints.filter((e) => !e.reachable).length;
  if (downCount === 0) {
    setBanner(null, "All systems operational", `${endpoints.length} endpoints monitored, all reachable.`);
  } else if (downCount === endpoints.length) {
    setBanner("down", "Aletheore is down", "All monitored endpoints are currently unreachable.");
  } else {
    setBanner(
      "degraded",
      "Partial outage",
      `${downCount} of ${endpoints.length} monitored endpoints are currently unreachable.`
    );
  }
}

function renderMeta(endpoints) {
  const latest = endpoints.reduce((max, e) => {
    const checked = new Date(e.checked_at).getTime();
    return checked > max ? checked : max;
  }, 0);
  document.getElementById("status-updated").textContent = latest
    ? `Last checked ${new Date(latest).toLocaleString()}`
    : "";
  document.getElementById("status-count").textContent = `${endpoints.length} endpoints monitored`;
}

async function refreshStatus() {
  let response;
  try {
    response = await fetch(STATUS_API);
  } catch (err) {
    setBanner("degraded", "Could not reach the status API", "Retrying automatically every 60 seconds.");
    return;
  }

  if (response.status === 404) {
    setBanner("degraded", "No health data yet", "The next monitoring sweep hasn't reported in yet.");
    return;
  }

  if (!response.ok) {
    setBanner("degraded", "Status API returned an error", `HTTP ${response.status} - retrying shortly.`);
    return;
  }

  const body = await response.json().catch(() => null);
  const endpoints = body && Array.isArray(body.endpoints) ? body.endpoints : [];

  renderBanner(endpoints);
  renderMeta(endpoints);
  renderTable(endpoints);
}

document.addEventListener("DOMContentLoaded", () => {
  refreshStatus();
  setInterval(refreshStatus, REFRESH_INTERVAL_MS);
});
