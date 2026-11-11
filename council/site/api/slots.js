// The free-tunnel counter for the page: the registry's real public number, passed through with a short cache.
// The page cannot ask the registry directly (a different origin), so this tiny route does it. If the registry
// cannot be read, it answers 502 and the page says "could not be read": it never invents a number.

const REGISTRY_STATUS_URL = "https://registry.aletheorecouncil.com/v1/status";

/** The two numbers, or null if the answer is not what the registry sends. */
export function parseStatus(raw) {
  if (!raw || typeof raw !== "object") return null;
  const { in_use, total } = raw;
  if (!Number.isInteger(in_use) || !Number.isInteger(total) || total < 1 || total > 1000 || in_use < 0 || in_use > total) return null;
  return { in_use, total };
}

export default async function handler(req, res) {
  try {
    const upstream = await fetch(REGISTRY_STATUS_URL, { signal: AbortSignal.timeout(4000) });
    if (!upstream.ok) throw new Error(`registry answered ${upstream.status}`);
    const status = parseStatus(await upstream.json());
    if (!status) throw new Error("unexpected answer");
    res.setHeader("Cache-Control", "s-maxage=60, stale-while-revalidate=300");
    res.status(200).json(status);
  } catch {
    res.setHeader("Cache-Control", "no-store");
    res.status(502).json({ error: "registry unavailable" });
  }
}
