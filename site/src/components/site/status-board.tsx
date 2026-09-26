"use client";
import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";
import {
  bannerFor, bannerForHttp, formatLatency, formatUptime, latestCheck, REFRESH_INTERVAL_MS, sortEndpoints, STATUS_API, statusLabel,
  unreachableBanner, type Banner, type Endpoint,
} from "@/lib/status";

const LOADING: Banner = { state: "loading", title: "Checking status…", subtitle: "Loading the latest results from the public status API." };

const tone: Record<Banner["state"], string> = {
  ok: "border-success/40 bg-[#EAF1EA]",
  degraded: "border-[#B7791F]/50 bg-[#FBF1DD]",
  down: "border-stamp/50 bg-stamp-soft",
  loading: "border-line bg-raised",
};
const dot: Record<Banner["state"], string> = { ok: "bg-success", degraded: "bg-[#B7791F]", down: "bg-stamp", loading: "bg-faint animate-pulse" };

export function StatusBoard() {
  const [banner, setBanner] = useState<Banner>(LOADING);
  const [endpoints, setEndpoints] = useState<Endpoint[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function refresh() {
      let res: Response;
      try {
        res = await fetch(STATUS_API);
      } catch {
        if (!cancelled) setBanner(unreachableBanner);
        return;
      }
      const http = bannerForHttp(res.status);
      if (http) {
        if (!cancelled) setBanner(http);
        return;
      }
      const body = (await res.json().catch(() => null)) as { endpoints?: Endpoint[] } | null;
      const list = body && Array.isArray(body.endpoints) ? body.endpoints : [];
      if (cancelled) return;
      setBanner(bannerFor(list));
      setEndpoints(list);
    }
    void refresh();
    const id = setInterval(refresh, REFRESH_INTERVAL_MS);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  const checked = endpoints ? latestCheck(endpoints) : 0;
  return (
    <div>
      <div className={cn("flex items-start gap-4 border p-6", tone[banner.state])} role="status" aria-live="polite">
        <span className={cn("mt-1.5 size-3 flex-none rounded-full", dot[banner.state])} aria-hidden="true" />
        <div>
          <h2 className="font-display text-[20px] font-bold">{banner.title}</h2>
          <p className="mt-1 text-[13.5px] text-muted">{banner.subtitle}</p>
        </div>
      </div>

      <div className="mt-3 flex flex-wrap justify-between gap-2 font-display text-[12px] text-faint">
        <p>{checked ? `Last checked ${new Date(checked).toLocaleString()}` : " "}</p>
        <p>{endpoints ? `${endpoints.length} endpoints monitored` : " "}</p>
      </div>

      <div className="mt-4 overflow-x-auto border border-line bg-raised" role="region" aria-label="Endpoint status" tabIndex={0}>
        <table className="w-full border-collapse text-left text-[13px]">
          <thead>
            <tr className="border-b border-line bg-paper font-display text-[11.5px] text-muted">
              <th className="px-4 py-2.5 font-semibold" scope="col">Endpoint</th>
              <th className="px-4 py-2.5 font-semibold" scope="col">Status</th>
              <th className="px-4 py-2.5 text-right font-semibold" scope="col">Latency</th>
              <th className="px-4 py-2.5 text-right font-semibold" scope="col">7-day uptime</th>
            </tr>
          </thead>
          <tbody>
            {endpoints === null ? (
              <tr><td colSpan={4} className="px-4 py-6 text-muted">Loading…</td></tr>
            ) : endpoints.length === 0 ? (
              <tr><td colSpan={4} className="px-4 py-6 text-muted">No endpoints reported.</td></tr>
            ) : (
              sortEndpoints(endpoints).map((e) => (
                <tr key={`${e.method} ${e.path}`} className="border-b border-dashed border-line last:border-b-0">
                  <td className="px-4 py-2.5"><code className="font-display text-[12px]">{e.method} {e.path}</code></td>
                  <td className="px-4 py-2.5">
                    <span className={cn("inline-block rounded-full px-2.5 py-0.5 font-display text-[11px] font-semibold", e.reachable ? "bg-[#DCEBDD] text-success" : "bg-stamp-soft text-stamp")}>{statusLabel(e)}</span>
                  </td>
                  <td className="px-4 py-2.5 text-right tabular-nums">{formatLatency(e)}</td>
                  <td className="px-4 py-2.5 text-right tabular-nums">{formatUptime(e)}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
