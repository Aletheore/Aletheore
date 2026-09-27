// Tier 1: safe on any environment, including production. Uses an
// unhandled event type ("ping") so it exercises the real signature
// verification + delivery-dedup + Postgres write path in main.py's
// /webhook route, without matching any of the if/elif branches that
// would call the real GitHub API or enqueue a real job. Zero GitHub API
// calls, zero worker/LLM cost - this measures app-server + Postgres +
// Redis raw ingestion throughput only.

import http from 'k6/http';
import crypto from 'k6/crypto';
import { check } from 'k6';

const SECRET = __ENV.GITHUB_WEBHOOK_SECRET;
const TARGET = __ENV.TARGET_URL || 'http://localhost:8000/webhook';

if (!SECRET) {
  throw new Error('Set -e GITHUB_WEBHOOK_SECRET=<the webhook secret this app-server checks against>');
}

export const options = {
  stages: [
    { duration: '30s', target: 20 },
    { duration: '1m', target: 20 },
    { duration: '30s', target: 100 },
    { duration: '1m', target: 100 }, // watch docker stats during this stage
    { duration: '30s', target: 0 },
  ],
};

export default function () {
  const body = JSON.stringify({ zen: 'Anything added dilutes everything else.' });
  const signature = 'sha256=' + crypto.hmac('sha256', SECRET, body, 'hex');
  const deliveryId = crypto.randomBytes(16).toString('hex'); // must be unique or it's dedup'd as a no-op

  const res = http.post(TARGET, body, {
    headers: {
      'Content-Type': 'application/json',
      'X-Hub-Signature-256': signature,
      'X-GitHub-Delivery': deliveryId,
      'X-GitHub-Event': 'ping',
    },
  });

  const ok = check(res, { 'status is 200': (r) => r.status === 200 });
  if (!ok) {
    console.error(`unexpected response: status=${res.status} body=${res.body}`);
  }
}
