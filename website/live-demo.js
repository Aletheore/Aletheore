// Live "paste a repo" demo (index.html #live-demo). Calls the isolated,
// unauthenticated /v1/demo-scan API on app.aletheore.com - deterministic
// scan only, no LLM calls, cloned source destroyed server-side after the
// scan. See github-app/app_server/demo_scan_api.py.
const DEMO_API_BASE = "https://app.aletheore.com";
const POLL_INTERVAL_MS = 2000;

function escapeHtml(value) {
  const div = document.createElement("div");
  div.textContent = value == null ? "" : String(value);
  return div.innerHTML;
}

function setStatus(message, isError) {
  const el = document.getElementById("live-demo-status");
  el.textContent = message;
  el.hidden = !message;
  el.classList.toggle("is-error", Boolean(isError));
}

function queueStatusMessage(body) {
  // Only one worker runs demo scans (see demo_scan_worker.py) - without
  // this, a visitor waiting behind someone else's scan saw the exact same
  // "scanning" message as the person actually being scanned, with no way
  // to tell they were just waiting in line.
  if (body.status === "queued") {
    const position = body.queue_position;
    if (position === null || position === undefined || position <= 0) {
      return "You're next in line - starting shortly...";
    }
    return `Waiting in queue - ${position} request${position === 1 ? "" : "s"} ahead of you...`;
  }
  return "Scanning your repo in an isolated sandbox...";
}

function resetButton() {
  const button = document.getElementById("live-demo-submit");
  button.disabled = false;
  button.textContent = "Scan it";
}

function renderResults(result) {
  const el = document.getElementById("live-demo-results");
  const languages =
    (result.languages || []).map((lang) => `${lang.name} (${Number(lang.lines || 0).toLocaleString()} lines)`).join(", ") ||
    "none detected";

  const deadCodeSample = (result.dead_code?.sample || [])
    .map((path) => `<li>${escapeHtml(path)}</li>`)
    .join("");
  const secretsSample = (result.secrets?.sample || [])
    .map((f) => `<li>${escapeHtml(f.path)}:${escapeHtml(f.line)} — ${escapeHtml(f.pattern)}</li>`)
    .join("");

  el.innerHTML = `
    <dl>
      <div><dt>Dead code found</dt><dd>${result.dead_code?.unreachable_module_count ?? 0}</dd></div>
      <div><dt>Secret patterns matched</dt><dd>${result.secrets?.finding_count ?? 0}</dd></div>
      <div><dt>License issues</dt><dd>${result.dependency_licenses?.issue_count ?? 0}</dd></div>
      <div><dt>API endpoints mapped</dt><dd>${result.api_endpoints?.count ?? 0}</dd></div>
      <div><dt>Architecture clusters</dt><dd>${result.architecture?.cluster_count ?? 0}</dd></div>
    </dl>
    <p class="live-demo-status" style="margin-top:16px;">Languages: ${escapeHtml(languages)}</p>
    ${deadCodeSample ? `<ul class="live-demo-sample">${deadCodeSample}</ul>` : ""}
    ${secretsSample ? `<ul class="live-demo-sample">${secretsSample}</ul>` : ""}
    <p class="live-demo-held-back">${escapeHtml(result.held_back?.message)}</p>
  `;
  el.hidden = false;
}

async function pollJob(jobId) {
  let response;
  try {
    response = await fetch(`${DEMO_API_BASE}/v1/demo-scan/${jobId}`);
  } catch (err) {
    setStatus("Lost connection checking the scan status - try again shortly.", true);
    resetButton();
    return;
  }

  if (!response.ok) {
    setStatus("Something went wrong checking the scan status.", true);
    resetButton();
    return;
  }

  const body = await response.json();
  if (body.status === "finished") {
    setStatus("");
    renderResults(body.result);
    resetButton();
    return;
  }
  if (body.status === "failed") {
    setStatus(body.detail || "Scan failed.", true);
    resetButton();
    return;
  }
  setStatus(queueStatusMessage(body));
  setTimeout(() => pollJob(jobId), POLL_INTERVAL_MS);
}

async function handleSubmit(event) {
  event.preventDefault();
  const input = document.getElementById("live-demo-url");
  const button = document.getElementById("live-demo-submit");
  const resultsEl = document.getElementById("live-demo-results");
  resultsEl.hidden = true;
  resultsEl.innerHTML = "";
  button.disabled = true;
  button.textContent = "Starting...";
  setStatus("Starting scan...");

  let response;
  try {
    response = await fetch(`${DEMO_API_BASE}/v1/demo-scan`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ repo_url: input.value.trim() }),
    });
  } catch (err) {
    setStatus("Could not reach the demo service - try again shortly.", true);
    resetButton();
    return;
  }

  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    setStatus(body.detail || "That didn't work - check the URL and try again.", true);
    resetButton();
    return;
  }

  button.textContent = "Scanning...";
  pollJob(body.job_id);
}

document.addEventListener("DOMContentLoaded", () => {
  const form = document.getElementById("live-demo-form");
  if (form) form.addEventListener("submit", handleSubmit);
});
