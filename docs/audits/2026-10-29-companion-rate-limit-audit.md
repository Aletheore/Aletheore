# Companion rate and spend-limit audit — 2026-10-29

## Implemented controls

- Five requests per rolling minute and forty per rolling 24 hours for a network bucket; one in-flight question per bucket. IPv6 /64 and mapped IPv4 canonicalization prevent address-spelling/privacy-address resets. Cookies and caller IDs are not quota identities.
- Thirty requests per rolling minute and two in-flight questions globally.
- Shared INR 20/day and INR 500/month ceilings, by UTC admission period, with INR 5 reserved atomically before either model request. Every caller and instance uses one persistent Redis ledger. At most two inference calls per admitted question; no automatic retries or redirects.
- Token-reported usage is checked for missing/inconsistent values and context/output contract violations. Successful usage is charged with GST and rounding headroom; ambiguous failures retain the full reservation. A contract/cost violation disables admissions.
- Authenticated backend gate uses existing private server Redis; no Upstash service or public Redis port required. Narrow operation schema, fixed server keys/hold, client-bound random reservations, idempotent settlement and an exact website/backend policy-hash match.
- Missing/corrupt ledger, disabled accounting, invalid config, backend/storage failure, malformed acknowledgements and policy mismatches all deny paid inference. No request-path initialization and no process-local fallback.
- Settlement marks refunds as processed before counter adjustment; partial Lua failures cannot repeat a refund. Old-period settlements cannot reduce new-period balances. Unknown usage cannot later be reclassified as a free completion.

## Evidence

- Final website suite with real Redis enabled: **267 passed**, one explicitly opted-in paid-model test skipped. This includes 18 real Lua integration cases and the HTTP-route/unit guardrails. No paid evaluation is triggered by ordinary test runs.
- Focused backend suite: **19 passed**, including separate bearer auth, rejected commands/overrides, script parity, Redis failure behavior and deployment persistence assertions.
- Typecheck, ESLint, Ruff and whitespace checks passed.
- Real Redis 8.10.1 on an isolated private Unix socket, AOF enabled, fsync always, eviction disabled; independent caller functions exercised shared scripts concurrently. Contention cases used 80 distinct clients, 40 same-IP clients, and 60 clients competing for one remaining hold.
- HTTP website -> real FastAPI limiter -> real Redis -> GLM path: **5 allowed, 12 throttled**. Rotating cookies, X-User-ID and arbitrary X-Forwarded-For did not reset the local bucket. All 429 responses supplied Retry-After.
- Backend-offline HTTP check: 503; accounting unchanged: True. No unprotected fallback was used.
- Real Redis shutdown/restart and AOF reload retained the accounting: True. Restored full path subsequently returned a documented AIR-price answer.
- Source audit found exactly one application call site for answerQuestion, behind acquirePermit in the server route. Model/service credentials are never accepted in visitor input or sent in model messages.

## Deployment gap repaired

The compose Redis service lacked a stable named volume and explicit durable-write settings. Added aletheore_redis_data:/data, appendonly yes, appendfsync always, maxmemory-policy noeviction, and aof-load-truncated no. Before applying the new named volume to the existing server, stop all Redis writers, create and back up a fresh complete data snapshot, and migrate the existing data. Validate queues/accounting before restarting writers. Do not prune volumes or discard the old container/data before verifying the migration. No remote server was changed in this work.

Docker is installed locally but its daemon was not running. Tests used real isolated native Redis, not a mocked Redis or a Docker Redis 7 image. The backend image's COPY includes the new router, policy JSON and operator script. Docker/Vercel deployed-path verification remains a launch requirement.

## Activation and boundaries

The localhost preview now uses the authenticated backend and isolated shared Redis. Public production inference remains deliberately disabled. Deploy/configure the backend and the same distinct COMPANION_LIMIT_SERVICE_TOKEN on backend and website, initialize the first-use ledger once, verify persistent/no-eviction Redis and the Vercel-managed client header in preview, and set a dedicated provider-side hard spend cap before public activation.

The ceilings are conservative application accounting at the checked model prices, not an unconditional guarantee about every provider/hosting invoice. Provider price/billing changes, other applications sharing a credential, catastrophic data loss/restoring stale snapshots, or privileged operator changes require separate controls. A distributed attacker can change network identities and exhaust the permitted budget, but cannot create a new global budget through those identities. Use upstream Vercel WAF controls for raw traffic/hosting-cost exposure. No universal no-bypass claim is made.

Configuration/migration detail: ../superpowers/specs/2026-10-29-companion-spend-limits.md.
Synthetic HTTP/outage/restart artifacts remain under /private/tmp/aletheore-companion-*-eval.json. No private repository contents or real credential values appear in these reports.
