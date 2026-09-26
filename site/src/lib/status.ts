export type Endpoint = {
  method: string;
  path: string;
  reachable: boolean;
  status_code: number | null;
  latency_ms: number | null;
  checked_at: string;
  uptime_pct_7d: number | null;
};

export type Banner = { state: "ok" | "degraded" | "down" | "loading"; title: string; subtitle: string };

export const STATUS_API = "https://app.aletheore.com/v1/health/Aletheore/Aletheore";
export const REFRESH_INTERVAL_MS = 60_000;

export function formatLatency(e: Pick<Endpoint, "reachable" | "latency_ms">): string {
  if (!e.reachable || e.latency_ms == null) return "n/a";
  return `${Math.round(e.latency_ms)} ms`;
}

export function formatUptime(e: Pick<Endpoint, "uptime_pct_7d">): string {
  if (e.uptime_pct_7d == null) return "n/a";
  return `${(e.uptime_pct_7d * 100).toFixed(1)}%`;
}

export function statusLabel(e: Pick<Endpoint, "reachable" | "status_code">): string {
  if (!e.reachable) return "Down";
  return `Up${e.status_code != null ? ` (${e.status_code})` : ""}`;
}

export function sortEndpoints(endpoints: Endpoint[]): Endpoint[] {
  return [...endpoints].sort((a, b) => a.path.localeCompare(b.path));
}

export function bannerFor(endpoints: Endpoint[]): Banner {
  const down = endpoints.filter((e) => !e.reachable).length;
  if (down === 0) return { state: "ok", title: "All systems operational", subtitle: `${endpoints.length} endpoints monitored, all reachable.` };
  if (down === endpoints.length) return { state: "down", title: "Aletheore is down", subtitle: "All monitored endpoints are currently unreachable." };
  return { state: "degraded", title: "Partial outage", subtitle: `${down} of ${endpoints.length} monitored endpoints are currently unreachable.` };
}

export function bannerForHttp(status: number): Banner | null {
  if (status === 404) return { state: "degraded", title: "No health data yet", subtitle: "The next monitoring sweep hasn't reported in yet." };
  if (status >= 400) return { state: "degraded", title: "Status API returned an error", subtitle: `HTTP ${status}, retrying shortly.` };
  return null;
}

export const unreachableBanner: Banner = {
  state: "degraded",
  title: "Could not reach the status API",
  subtitle: "Retrying automatically every 60 seconds.",
};

export function latestCheck(endpoints: Endpoint[]): number {
  return endpoints.reduce((max, e) => Math.max(max, new Date(e.checked_at).getTime() || 0), 0);
}
