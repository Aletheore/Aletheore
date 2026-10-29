import { createHash, createHmac, randomUUID } from "node:crypto";
import { isIP } from "node:net";
import { RESERVE_SCRIPT, SETTLE_SCRIPT, DISABLE_SCRIPT } from "./limit-scripts";
import type { ChatUsage } from "./service";

export const LIMIT_POLICY = createHash("sha256").update(RESERVE_SCRIPT).update(SETTLE_SCRIPT).update(DISABLE_SCRIPT).digest("hex");
export const HOLD_MICRO_INR = 5_000_000;
export const LIMIT_PREFIX = "aletheore:{companion-budget}";
export const LEDGER_KEY = `${LIMIT_PREFIX}:ledger`;
export class LimitError extends Error {
  constructor(public status: number, public retryAfter = 60) { super("companion_capacity_unavailable"); }
}
export type RedisEval = (script: string, keys: string[], args: string[]) => Promise<unknown>;
export function serviceEvalFromEnvironment(): RedisEval {
  const endpoint = process.env.COMPANION_LIMIT_SERVICE_URL;
  const token = process.env.COMPANION_LIMIT_SERVICE_TOKEN;
  if (!endpoint || !token || token.length < 32) throw new LimitError(503);
  let url: URL;
  try { url = new URL(endpoint); } catch { throw new LimitError(503); }
  const local = process.env.NODE_ENV === "development" && url.protocol === "http:" && url.hostname === "127.0.0.1";
  if ((!local && (url.protocol !== "https:" || url.hostname !== "app.aletheore.com" || url.port)) || url.username || url.password || url.search || url.hash || url.pathname !== "/v1/internal/companion-limits") throw new LimitError(503);
  return async (script, _keys, args) => {
    let body: Record<string, unknown>;
    if (script === RESERVE_SCRIPT) body = { operation: "reserve", request_id: args[0], client_hash: args[2] };
    else if (script === SETTLE_SCRIPT) body = { operation: "settle", request_id: args[0], client_hash: args[2], cost: Number(args[1]) };
    else if (script === DISABLE_SCRIPT) body = { operation: "disable", request_id: args[0], client_hash: args[1] };
    else throw new LimitError(503);
    try {
      const response = await fetch(url, { method: "POST", redirect: "error", cache: "no-store", signal: AbortSignal.timeout(3000), headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: JSON.stringify(body) });
      if (!response.ok) throw new LimitError(503);
      const result = await response.json();
      if (!result || result.policy !== LIMIT_POLICY || !Array.isArray(result.result)) throw new LimitError(503);
      return result.result;
    } catch { throw new LimitError(503); }
  };
}
// Accept only Vercel's overwritten header in its actual runtime. No visitor IDs or caller-selected buckets.
export function clientBucket(request: Request): string {
  const secret = process.env.COMPANION_LIMIT_HMAC_SECRET;
  if (!secret || secret.length < 32) throw new LimitError(503);
  let identity = "local-preview";
  if (process.env.NODE_ENV !== "development") {
    if (process.env.VERCEL !== "1") throw new LimitError(503);
    const ip = request.headers.get("x-vercel-forwarded-for")?.trim();
    if (!ip || !/^[0-9a-fA-F:.]+$/.test(ip) || !isIP(ip)) throw new LimitError(503);
    // URL canonicalizes IPv6 spelling; group the /64 so IPv6 privacy-address rotation cannot mint buckets.
    if (isIP(ip) === 6) {
      const normalized = new URL(`http://[${ip}]/`).hostname.slice(1, -1);
      const [left, right = ""] = normalized.split("::");
      const a = left ? left.split(":") : [];
      const b = right ? right.split(":") : [];
      const groups = normalized.includes("::") ? [...a, ...Array(8 - a.length - b.length).fill("0"), ...b] : a;
      identity = groups.slice(0, 4).map((part) => part.padStart(4, "0")).join(":") + "::/64";
      // IPv4-mapped IPv6 normalizes to the same IPv4 bucket.
      if (groups.slice(0, 5).every((part) => parseInt(part, 16) === 0) && parseInt(groups[5], 16) === 65535) {
        const high = parseInt(groups[6], 16); const low = parseInt(groups[7], 16);
        identity = `${high >> 8}.${high & 255}.${low >> 8}.${low & 255}`;
      }
    } else identity = ip;
  }
  return createHmac("sha256", secret).update(identity).digest("hex");
}
// Pricing upper estimate includes 18% GST; cached input only makes it cheaper.
export function usageCost(usage: ChatUsage, reliable: boolean): number {
  if (!reliable || !Number.isSafeInteger(usage.prompt_tokens) || !Number.isSafeInteger(usage.completion_tokens) || usage.prompt_tokens < 0 || usage.completion_tokens < 0 || usage.total_tokens !== usage.prompt_tokens + usage.completion_tokens) return -1;
  const micro = Math.ceil((usage.prompt_tokens * 673 + usage.completion_tokens * 2243) * 118 / 10000);
  // Round up to paise and retain two extra paise for per-call billing rounding (at most two calls).
  return Math.ceil(micro / 10000) * 10000 + 20000;
}
export async function acquirePermit(request: Request, evaluate = serviceEvalFromEnvironment()) {
  const client = clientBucket(request);
  const id = randomUUID();
  const reservation = `${LIMIT_PREFIX}:reservation:${id}`;
  const clientRequests = `${LIMIT_PREFIX}:client:${client}`;
  const clientLeases = `${LIMIT_PREFIX}:active:${client}`;
  const leases = `${LIMIT_PREFIX}:active`;
  const result = await evaluate(RESERVE_SCRIPT, [LEDGER_KEY, `${LIMIT_PREFIX}:requests`, leases, clientRequests, clientLeases, reservation], [id, String(HOLD_MICRO_INR), client]);
  if (!Array.isArray(result) || result.length !== 3 || result[0] !== 1 || result[1] !== 200 || result[2] !== 0) throw new LimitError(Array.isArray(result) && result[1] === 429 ? 429 : 503, Array.isArray(result) && Number.isInteger(result[2]) ? Math.max(1, Math.min(3600, result[2])) : 60);
  return {
    async settle(usage: ChatUsage, reliable: boolean, violation = false) {
      const cost = usageCost(usage, reliable);
      if (violation || cost > HOLD_MICRO_INR) {
        await evaluate(DISABLE_SCRIPT, [LEDGER_KEY, reservation], [id, client]);
        throw new LimitError(503);
      }
      const result = await evaluate(SETTLE_SCRIPT, [LEDGER_KEY, leases, clientLeases, reservation], [id, String(cost), client]);
      if (!Array.isArray(result) || result.length !== 1 || result[0] !== 1) throw new LimitError(503);
    },
    async unknown() {
      const result = await evaluate(SETTLE_SCRIPT, [LEDGER_KEY, leases, clientLeases, reservation], [id, "-1", client]);
      if (!Array.isArray(result) || result.length !== 1 || result[0] !== 1) throw new LimitError(503);
    },
  };
}
