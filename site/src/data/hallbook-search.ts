import { hallbook, type HbFind } from "@/data/hallbook";

export const STATUS_RECORDED = "recorded examples";
export const STATUS_LIVE = "live · local";

/**
 * Live search is only on when a Hallbook API is wired in (HALLBOOK_API at build time, see next.config.ts).
 * Production has no /api/hallbook/* route, so it must never send a request there.
 * The literal process.env access is required so Next inlines the value.
 */
export function liveSearchEnabled(flag: string | undefined = process.env.NEXT_PUBLIC_HALLBOOK_LIVE): boolean {
  return flag === "1";
}

export type Lookup = { res: HbFind | null; status: string };

/** One lookup. Without live mode this answers from the recorded examples and sends no request. */
export async function lookup(text: string, live: boolean, fetchImpl: typeof fetch = fetch): Promise<Lookup> {
  const known = hallbook.examples[text];
  if (!live) return known ? { res: known, status: STATUS_RECORDED } : { res: null, status: `${STATUS_RECORDED} · pick one of the examples` };
  try {
    const r = await fetchImpl(`/api/hallbook/find?q=${encodeURIComponent(text)}`);
    if (!r.ok) throw new Error("api");
    return { res: (await r.json()) as HbFind, status: STATUS_LIVE };
  } catch {
    return known ? { res: known, status: "recorded answer" } : { res: null, status: "live search needs the local Hallbook server" };
  }
}
