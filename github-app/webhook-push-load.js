// Tier 2: real end-to-end load — app-server ingest -> Redis enqueue ->
// scan-worker execution. Built from the exact payload shape in this repo's
// own tests/test_push_webhook.py::_payload, so it exercises the real code
// path, not a guessed one.
//
// IMPORTANT - the one real external dependency this can't avoid:
// scan_worker.jobs.run_push_scan_job (the job this enqueues) checks out the
// real repo using a real GitHub App installation token, same as production.
// Point INSTALLATION_ID/REPO_FULL_NAME at a real installation on a
// throwaway/test repo YOU control - never a real customer's - since this
// will genuinely trigger real scans against it, at the volume you configure
// below. This is NOT the same as webhook-ingest.js (which used an
// unhandled "ping" event specifically so nothing downstream fires) -
// this script deliberately DOES fire the real scan-worker path, because
// that's the CPU-heavy work you're trying to load-test.
//
// Un-truncated commits array (small, realistic) means _changed_files_for_push
// reads straight from the payload - no compare-commits API call, no extra
// GitHub API round trip beyond the installation-token exchange scan-worker
// itself needs for the checkout.

import http from 'k6/http';
import crypto from 'k6/crypto';
import { check, sleep } from 'k6';

const SECRET = __ENV.GITHUB_WEBHOOK_SECRET;
const TARGET = __ENV.TARGET_URL || 'http://localhost:8000/webhook';
const INSTALLATION_ID = parseInt(__ENV.INSTALLATION_ID || '0', 10);
const REPO_FULL_NAME = __ENV.REPO_FULL_NAME || 'your-org/your-throwaway-test-repo';
const DEFAULT_BRANCH = __ENV.DEFAULT_BRANCH || 'main';

export const options = {
  stages: [
    { duration: '30s', target: 5 },   // scan jobs are heavier than plain
    { duration: '1m', target: 5 },    // ingestion - start much lower than
    { duration: '30s', target: 15 },  // the ping-event test's 100 VUs
    { duration: '1m', target: 15 },   // watch docker stats on scan-worker
    { duration: '30s', target: 0 },   // specifically during this stage
  ],
};

function buildPushPayload(afterSha) {
  return {
    ref: `refs/heads/${DEFAULT_BRANCH}`,
    before: 'base' + Math.random().toString(16).slice(2, 10),
    after: afterSha,
    deleted: false,
    installation: { id: INSTALLATION_ID },
    repository: { full_name: REPO_FULL_NAME, default_branch: DEFAULT_BRANCH },
    // Deliberately un-truncated (no "size" key, 2 small commits) so this
    // never calls the compare-commits API - see push.py's
    // _push_payload_commits_truncated. Matches _payload()'s own default
    // commits shape in tests/test_push_webhook.py exactly.
    commits: [
      { added: ['loadtest_new.py'], removed: [], modified: ['app.py'] },
      { added: [], removed: ['loadtest_old.py'], modified: ['app.py'] },
    ],
  };
}

export default function () {
  if (!INSTALLATION_ID) {
    throw new Error('Set -e INSTALLATION_ID=<a real installation id you control>');
  }
  if (!SECRET) {
    throw new Error('Set -e GITHUB_WEBHOOK_SECRET=<the webhook secret this app-server checks against>');
  }

  const afterSha = crypto.randomBytes(20).toString('hex'); // unique per request
  const body = JSON.stringify(buildPushPayload(afterSha));
  const signature = 'sha256=' + crypto.hmac('sha256', SECRET, body, 'hex');
  const deliveryId = crypto.randomBytes(16).toString('hex');

  const res = http.post(TARGET, body, {
    headers: {
      'Content-Type': 'application/json',
      'X-Hub-Signature-256': signature,
      'X-GitHub-Delivery': deliveryId,
      'X-GitHub-Event': 'push',
    },
  });

  const ok = check(res, { 'accepted (200)': (r) => r.status === 200 });
  if (!ok) {
    console.error(`unexpected response: status=${res.status} body=${res.body}`);
  }
  sleep(1); // real pushes aren't back-to-back; avoid falsely flooding the queue
}
