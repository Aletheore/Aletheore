# Companion admission and spending controls

Implemented. Public activation is a single environment switch (`COMPANION_LIVE_ENABLED=1` with a provider key) on the website, to be flipped only after the activation checklist below is complete.

## Policy

- Per network address: 5 questions in a rolling minute, 40 in a rolling 24 hours, and one in-flight question. IPv6 addresses within one /64 share a bucket; mapped IPv4 addresses normalize to their IPv4 bucket.
- Whole website: 30 questions in a rolling minute and two in-flight questions.
- Budget: INR 20 per UTC day and INR 500 per UTC month, counted by admission time. These are conservative application accounting ceilings, not a promise about the entire hosting/provider invoice.
- Every question reserves INR 5 before the scope classifier or answer selector runs. That exceeds two complete 262,144-token input contexts plus at most 290 output tokens at the checked INR 6.73/22.43 per-million rates, with 18% GST and rounding headroom.
- Successful responses with complete, consistent reported usage settle at the token-derived cost, rounded up to paise plus two additional paise for per-call rounding. Unknown, failed, timed-out or cancelled usage retains the full INR 5. Missing usage never means zero cost.
- A provider response reporting tokens beyond the expected context/output contract disables new admissions. An observed cost beyond the reservation also disables admissions. No automatic provider retries.

## Deployment shape

Website server -> authenticated HTTPS endpoint on the existing app server -> its private Redis. Redis stays private. No Upstash account or new publicly accessible Redis port is required.

The website checks an exact hash of the limiter policy scripts in every backend acknowledgement. Mismatched deployments fail closed before inference.

The backend exposes only `reserve`, `settle` and `disable` at `/v1/internal/companion-limits`. It rejects arbitrary EVAL scripts, keys, budget changes, caller-selected hold amounts, malformed IDs, non-integer costs and extra fields. Authentication uses a distinct high-entropy `COMPANION_LIMIT_SERVICE_TOKEN` with a constant-time comparison. The token is never passed to the browser or model. The full model API key is available only to the website server and its inference provider.

All Vercel instances, domains, previews and backend workers must use the same Redis ledger namespace. A Redis Lua script checks and reserves all capacities in one atomic execution using Redis TIME. Daily/monthly bucket changes are derived from Redis time, not request headers or instance clocks. Lua execution is atomic but not transactional rollback: reservation writes may conservatively consume capacity on failures, and settlement marks a refund as processed before adjusting balances so partial failures cannot create a repeatable refund.

Reservations use server-generated UUIDs and bind to a hashed client bucket. Duplicate admission IDs are rejected. Duplicate settlement is idempotent. Settling an older period never subtracts from a newer period. Uncertain calls keep their 60-second concurrency lease and full charge; the model deadline is 20 seconds. A new caller/process cannot reset rate or budget state.

The ledger never expires and is never initialized by request code. Missing/corrupt counters, missing configuration, backend errors, Redis errors, ambiguous transport results and disabled accounting all stop model calls. Redis must use persistent storage, no eviction, and reliable backups. The compose configuration now supplies a stable named data volume, AOF with fsync on every write, and refusal to load a truncated AOF. **Before first deployment of this volume change, stop writers and migrate the existing Redis data into the named volume; verify the loaded queues and accounting before restarting writers. Do not discard the old container/data first.** If state is lost or restored from an old snapshot, keep chat disabled and reconcile with provider billing; do not initialize a fresh ledger and silently erase prior spending.

## Identity and abuse limits

In verified Vercel production runtime, use only the platform-managed `x-vercel-forwarded-for` value; reject missing, invalid or multiple values. Never use cookie IDs, user-provided keys, `X-Real-IP`, or arbitrary forwarded-header chains. Local development intentionally shares one fixed bucket and ignores all proxy headers. Configure a separate required `COMPANION_LIMIT_HMAC_SECRET` (at least 32 random characters) on every website instance. Keep it stable and backed up independently of the service credential: rotating the service token must not reset network quotas. HMAC-key rotation intentionally changes network identities and requires an operator-planned quota transition; it never resets the global spending ledger.

Network-address limits are not user authentication. A distributed attacker or VPN rotation can evade an individual bucket; the shared concurrency and spending controls still apply. Shared NAT users can encounter a common limit. Origin checks are an additional browser boundary, not bot authentication. Use Vercel WAF/rate rules before functions to protect against raw traffic and hosting costs; application model accounting cannot bound all infrastructure expenses.

## Activation using the existing server

1. Generate a separate random service token of at least 32 characters. Configure it as `COMPANION_LIMIT_SERVICE_TOKEN` on the app server and the website server. Independently generate and retain `COMPANION_LIMIT_HMAC_SECRET` on the website; do not rotate it with the service token. Set website `COMPANION_LIMIT_SERVICE_URL=https://app.aletheore.com/v1/internal/companion-limits`. Never prefix credentials with `NEXT_PUBLIC_`.
2. Deploy the backend router and the JSON Lua snapshot included in its existing Docker image. The backend uses its existing `REDIS_URL` through a dedicated async pool (four connections, 0.5-second socket/connect timeouts, no retries and a 1.5-second operation deadline); keep that Redis private, durable and configured with eviction disabled.
3. Initialize exactly once on first use from inside the server environment: `python scripts/companion_limits.py initialize-first-use`. The operator tool requires `REDIS_URL` and refuses to overwrite an existing ledger. `status` is read-only; `disable` stops model admission without wiping accounting. There is intentionally no HTTP initialization/reset endpoint.
4. Use a dedicated IndieRouter credential/account allowance for this chatbot and set a provider-side hard spend cap. Other uses of the same key/account and changes in prices/billing behavior are outside the application ledger. IndieRouter documents that uncapped overage can continue after included credits, so prepaid credits alone are not an adequate stop.
5. Verify the service credential, proxy header provenance, origin, rate responses, outage behavior, persistent counters and deployed paths in a Vercel preview. Verify provider rates and billing units before enabling real traffic. Update privacy disclosures for transient IP-based abuse controls and provider message processing. Only then approve public enablement by setting `COMPANION_LIVE_ENABLED=1` on the website and redeploying (Vercel applies environment changes to new deployments only). There is no separate code gate: the switch is `site/src/lib/companion/enabled.ts`, shared by the route and the layout. To stop model spending immediately without a deploy, run `python scripts/companion_limits.py disable` on the app server; admissions then fail closed.

Current root .env has the model credential but no deployed limiter service credential. Local end-to-end tests use the real backend route with an isolated private Redis socket, not production server access. No remote server configuration or provider billing setting was changed.

## Reproducibility

- Ordinary website tests: `npm --prefix site test` (no paid inference).
- Real Redis Lua tests: set `COMPANION_TEST_REDIS_SOCKET` to an isolated test Redis Unix socket, then run `npm --prefix site test -- --run tests/companion-limits.test.ts`. Alternatively set `COMPANION_TEST_REDIS_URL` for an isolated Docker Redis accessible over a test-only loopback port. The Redis CLI is selected from `COMPANION_REDIS_CLI` or the executable PATH. Never point this test at production Redis.
- Backend auth/schema tests: `PYTHONPATH=github-app python -m pytest -q github-app/tests/test_companion_limits.py`.
- Backend and website script snapshots must be byte-equivalent; a regression test checks parity. Change the backend JSON and mirror it to the website JSON together.
- Standalone model quality/injection evaluation remains an explicit operator tool with a bounded token budget. It does not use the public chat endpoint; its deliberate test spending is separate from website limits.
