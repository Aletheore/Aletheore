// The one switch for the public website companion. Off unless COMPANION_LIVE_ENABLED is exactly "1"
// and a provider key is configured; nothing else (NODE_ENV, host) turns it on. Production still fails
// closed elsewhere: admission needs the Vercel-only client identity, the stable HMAC secret, the
// https limiter URL and the shared ledger, and `companion_limits.py disable` stops admissions instantly.
// Vercel applies environment changes to new deployments only, so flipping this means redeploying.
export function companionEnabled(env: Record<string, string | undefined> = process.env): boolean {
  return env.COMPANION_LIVE_ENABLED === "1" && Boolean(env.INDIEROUTER_API_KEY);
}
