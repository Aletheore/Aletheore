import { describe, expect, it } from "vitest";
import { bannerFor, bannerForHttp, formatLatency, formatUptime, latestCheck, sortEndpoints, statusLabel, type Endpoint } from "@/lib/status";

const ep = (o: Partial<Endpoint>): Endpoint => ({ method: "GET", path: "/a", reachable: true, status_code: 200, latency_ms: 31.4, checked_at: "2026-09-26T09:46:39Z", uptime_pct_7d: 1, ...o });

describe("status logic", () => {
  it("reports all systems operational when every endpoint is up", () => {
    expect(bannerFor([ep({}), ep({ path: "/b" })])).toMatchObject({ state: "ok", subtitle: "2 endpoints monitored, all reachable." });
  });
  it("reports a partial outage and a full outage differently", () => {
    expect(bannerFor([ep({}), ep({ reachable: false })])).toMatchObject({ state: "degraded", subtitle: "1 of 2 monitored endpoints are currently unreachable." });
    expect(bannerFor([ep({ reachable: false })]).state).toBe("down");
  });
  it("formats latency and uptime, with n/a when unknown or unreachable", () => {
    expect(formatLatency(ep({ latency_ms: 72.6 }))).toBe("73 ms");
    expect(formatLatency(ep({ reachable: false }))).toBe("n/a");
    expect(formatUptime(ep({ uptime_pct_7d: 0.9987 }))).toBe("99.9%");
    expect(formatUptime(ep({ uptime_pct_7d: null }))).toBe("n/a");
  });
  it("labels a 405 as up, since the endpoint answered", () => {
    expect(statusLabel(ep({ status_code: 405 }))).toBe("Up (405)");
    expect(statusLabel(ep({ reachable: false, status_code: null }))).toBe("Down");
  });
  it("sorts by path without mutating the input", () => {
    const input = [ep({ path: "/z" }), ep({ path: "/a" })];
    expect(sortEndpoints(input).map((e) => e.path)).toEqual(["/a", "/z"]);
    expect(input[0].path).toBe("/z");
  });
  it("explains HTTP failures", () => {
    expect(bannerForHttp(404)?.title).toBe("No health data yet");
    expect(bannerForHttp(503)?.subtitle).toBe("HTTP 503, retrying shortly.");
    expect(bannerForHttp(200)).toBeNull();
  });
  it("finds the most recent check time", () => {
    expect(latestCheck([ep({ checked_at: "2026-09-26T09:00:00Z" }), ep({ checked_at: "2026-09-26T10:00:00Z" })])).toBe(Date.parse("2026-09-26T10:00:00Z"));
    expect(latestCheck([])).toBe(0);
  });
});
