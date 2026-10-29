import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { readFileSync } from "node:fs";
import { randomUUID } from "node:crypto";
import { acquirePermit, clientBucket, HOLD_MICRO_INR, LEDGER_KEY, LIMIT_PREFIX, LimitError, serviceEvalFromEnvironment, usageCost, type RedisEval } from "@/lib/companion/limits";
import { RESERVE_SCRIPT, SETTLE_SCRIPT } from "@/lib/companion/limit-scripts";
const run = promisify(execFile);
afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); });
function req(ip = "192.0.2.1", extra: Record<string, string> = {}) { return new Request("https://www.aletheore.com/api/companion", { headers: { "x-vercel-forwarded-for": ip, ...extra } }); }
function production() { vi.stubEnv("NODE_ENV", "production"); vi.stubEnv("VERCEL", "1"); vi.stubEnv("COMPANION_LIMIT_HMAC_SECRET", "test-only-stable-identity-secret-0123456789"); }

it("keeps the website and backend script snapshots identical", () => {
  const backend = JSON.parse(readFileSync(new URL("../../github-app/app_server/companion_limit_scripts.json", import.meta.url), "utf8"));
  expect(backend.RESERVE_SCRIPT).toBe(RESERVE_SCRIPT); expect(backend.SETTLE_SCRIPT).toBe(SETTLE_SCRIPT);
});
it("ignores spoofable headers and cookies when selecting a Vercel bucket", () => {
  production();
  expect(clientBucket(req())).toBe(clientBucket(req("192.0.2.1", { "x-forwarded-for": "198.51.100.7", "x-real-ip": "203.0.113.2", cookie: "user=another", "x-user-id": randomUUID() })));
  expect(clientBucket(req("192.0.2.2"))).not.toBe(clientBucket(req()));
});
it("puts local preview requests in one bucket regardless of forged proxy headers", () => {
  production(); vi.stubEnv("NODE_ENV", "development");
  expect(clientBucket(req("192.0.2.1"))).toBe(clientBucket(req("198.51.100.22")));
});
it("normalizes IPv6 spelling and privacy address rotation within /64", () => {
  production();
  expect(clientBucket(req("2001:db8:1234:5678::1"))).toBe(clientBucket(req("2001:0DB8:1234:5678:0000:0000:abcd:1234")));
  expect(clientBucket(req("::ffff:192.0.2.1"))).toBe(clientBucket(req("192.0.2.1")));
});
it.each(["", "1.2.3.4, 5.6.7.8", "192.000.002.1", "fe80::1%eth0", "unknown", "192.0.2.1:1234"])("fails closed for missing/ambiguous client identity: %s", (ip) => {
  production(); expect(() => clientBucket(req(ip))).toThrow(LimitError);
});
it("refuses to trust Vercel-looking headers outside the Vercel runtime", () => {
  production(); vi.stubEnv("VERCEL", "0"); expect(() => clientBucket(req())).toThrow(LimitError);
});
it("cannot fall back to a process-local counter if Redis configuration is absent", () => {
  vi.stubEnv("COMPANION_LIMIT_SERVICE_URL", ""); vi.stubEnv("COMPANION_LIMIT_SERVICE_TOKEN", ""); expect(() => serviceEvalFromEnvironment()).toThrow(LimitError);
});
it.each(["http://app.aletheore.com/v1/internal/companion-limits", "https://app.aletheore.com.evil.example/v1/internal/companion-limits", "https://user:pass@app.aletheore.com/v1/internal/companion-limits", "https://app.aletheore.com/v1/internal/companion-limits?token=oops", "https://attacker.invalid"])("rejects unsafe Redis destinations: %s", (url) => {
  production(); vi.stubEnv("COMPANION_LIMIT_SERVICE_URL", url); vi.stubEnv("COMPANION_LIMIT_SERVICE_TOKEN", "synthetic-service-token-at-least-32-chars"); expect(() => serviceEvalFromEnvironment()).toThrow(LimitError);
});
it.each([new Error("offline"), Response.json({ error: "OOM" }), Response.json({ result: null }), new Response("broken", { status: 500 })])("never admits work after a storage or protocol failure", async (reply) => {
  production(); vi.stubEnv("COMPANION_LIMIT_SERVICE_URL", "https://app.aletheore.com/v1/internal/companion-limits"); vi.stubEnv("COMPANION_LIMIT_SERVICE_TOKEN", "synthetic-service-token-at-least-32-chars");
  vi.stubGlobal("fetch", reply instanceof Error ? vi.fn().mockRejectedValue(reply) : vi.fn().mockResolvedValue(reply));
  await expect(acquirePermit(req())).rejects.toThrow(LimitError);
});
it("retains a full reservation when usage is absent, invalid, or untrusted", () => {
  expect(usageCost({ prompt_tokens: 100, completion_tokens: 10, total_tokens: 110 }, false)).toBe(-1);
  expect(usageCost({ prompt_tokens: 100, completion_tokens: 10, total_tokens: 1 }, true)).toBe(-1);
  expect(usageCost({ prompt_tokens: -100, completion_tokens: 10, total_tokens: -90 }, true)).toBe(-1);
  expect(usageCost({ prompt_tokens: 100, completion_tokens: 10, total_tokens: 110 }, true)).toBe(30000);
});
it("reserves more than two full model contexts plus capped output, GST and rounding", () => {
  expect(usageCost({ prompt_tokens: 262144 * 2, completion_tokens: 290, total_tokens: 262144 * 2 + 290 }, true)).toBeLessThan(HOLD_MICRO_INR);
});
it("reserves before giving the caller permission and propagates Retry-After", async () => {
  production(); const evaluate = vi.fn().mockResolvedValue([0,429,3600]);
  await expect(acquirePermit(req(), evaluate)).rejects.toMatchObject({ status: 429, retryAfter: 3600 });
  expect(evaluate.mock.calls[0][0]).toBe(RESERVE_SCRIPT);
});

// Real Lua/Redis tests are explicit opt-in and use an isolated Unix socket, never a production database.
describe.skipIf(!process.env.COMPANION_TEST_REDIS_SOCKET && !process.env.COMPANION_TEST_REDIS_URL)("atomic shared Redis admission", () => {
  const connection = process.env.COMPANION_TEST_REDIS_SOCKET ? ["-s", process.env.COMPANION_TEST_REDIS_SOCKET] : ["-u", process.env.COMPANION_TEST_REDIS_URL!];
  let prefix: string;
  async function command(...args: string[]) {
    const result = await run(process.env.COMPANION_REDIS_CLI ?? "redis-cli", [...connection, "--json", ...args], { maxBuffer: 2_000_000 });
    return JSON.parse(result.stdout);
  }
  const evaluate: RedisEval = async (script, keys, args) => command("EVAL", script, String(keys.length), ...keys.map((key) => key.replace(LIMIT_PREFIX, prefix)), ...args);
  const key = (suffix: string) => `${prefix}:${suffix}`;
  async function init(dayCap = 20_000_000, monthCap = 500_000_000) {
    await command("HSET", key("ledger"), "version", "1", "enabled", "1", "day", "0", "month", "0", "day_used", "0", "month_used", "0", "day_cap", String(dayCap), "month_cap", String(monthCap));
  }
  beforeEach(() => { production(); prefix = `test:{companion-budget}:${randomUUID()}`; });
  it("denies an uninitialized, missing or corrupt ledger", async () => {
    await expect(acquirePermit(req(), evaluate)).rejects.toMatchObject({ status: 503 });
    await init(); await command("HDEL", key("ledger"), "month_used");
    await expect(acquirePermit(req(), evaluate)).rejects.toMatchObject({ status: 503 });
  });
  it("allows at most two simultaneous requests across 80 independent clients", async () => {
    await init();
    const attempts = await Promise.allSettled(Array.from({ length: 80 }, (_, i) => acquirePermit(req(`192.0.2.${i + 1}`), evaluate)));
    expect(attempts.filter((r) => r.status === "fulfilled")).toHaveLength(2);
    expect(await command("HGET", key("ledger"), "day_used")).toBe(String(HOLD_MICRO_INR * 2));
  });
  it("allows only one concurrent request from the same IP across new callers", async () => {
    await init();
    const attempts = await Promise.allSettled(Array.from({ length: 40 }, () => acquirePermit(req(), evaluate)));
    expect(attempts.filter((r) => r.status === "fulfilled")).toHaveLength(1);
  });
  it("cannot oversubscribe the last remaining budget with parallel callers", async () => {
    await init(HOLD_MICRO_INR, HOLD_MICRO_INR);
    const attempts = await Promise.allSettled(Array.from({ length: 60 }, (_, i) => acquirePermit(req(`198.51.100.${i + 1}`), evaluate)));
    expect(attempts.filter((r) => r.status === "fulfilled")).toHaveLength(1);
    expect(await command("HGET", key("ledger"), "month_used")).toBe(String(HOLD_MICRO_INR));
  });
  it("refunds at most once when settlement is retried concurrently", async () => {
    await init(); const permit = await acquirePermit(req(), evaluate);
    await Promise.all(Array.from({ length: 40 }, () => permit.settle({ prompt_tokens: 100, completion_tokens: 10, total_tokens: 110 }, true)));
    expect(await command("HGET", key("ledger"), "day_used")).toBe("30000");
    expect(await command("HGET", key("ledger"), "month_used")).toBe("30000");
  });
  it("cannot refund unknown/aborted usage by sending a later completion", async () => {
    await init(); const permit = await acquirePermit(req(), evaluate); await permit.unknown();
    await permit.settle({ prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 }, true);
    expect(await command("HGET", key("ledger"), "day_used")).toBe(String(HOLD_MICRO_INR));
    await expect(acquirePermit(req(), evaluate)).rejects.toMatchObject({ status: 429 });
  });
  it("continues enforcing IP minute limits after successful refunds and fresh instances", async () => {
    await init();
    for (let i = 0; i < 5; i++) { const permit = await acquirePermit(req(), evaluate); await permit.settle({ prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 }, true); }
    await expect(acquirePermit(req("192.0.2.1", { cookie: randomUUID(), "x-forwarded-for": "203.0.113.99" }), evaluate)).rejects.toMatchObject({ status: 429 });
  });
  it("does not refund a new day's/month's balance when an old request settles", async () => {
    await init(); const permit = await acquirePermit(req(), evaluate);
    const day = Number(await command("HGET", key("ledger"), "day")); const month = Number(await command("HGET", key("ledger"), "month"));
    await command("HSET", key("ledger"), "day", String(day+1), "month", String(month+1), "day_used", "1234", "month_used", "5678");
    await permit.settle({ prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 }, true);
    expect(await command("HGET", key("ledger"), "day_used")).toBe("1234"); expect(await command("HGET", key("ledger"), "month_used")).toBe("5678");
  });
  it("cannot bypass the daily budget by rotating IPs after crashes", async () => {
    await init();
    for (let i = 1; i <= 4; i++) {
      const permit = await acquirePermit(req(`203.0.113.${i}`), evaluate); await permit.unknown();
      await command("DEL", key("active")); // Simulate eventual lease expiry, not a spending-counter reset.
    }
    await expect(acquirePermit(req("203.0.113.99"), evaluate)).rejects.toMatchObject({ status: 429 });
    expect(await command("HGET", key("ledger"), "day_used")).toBe("20000000");
  });
  it("refuses future-dated/corrupt accounting instead of resetting it", async () => {
    await init(); await command("HSET", key("ledger"), "day", "9999999");
    await expect(acquirePermit(req(), evaluate)).rejects.toMatchObject({ status: 503 });
  });
  it("blocks globally if reported provider usage violates the contract", async () => {
    await init(); const permit = await acquirePermit(req(), evaluate);
    await expect(permit.settle({ prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 }, true, true)).rejects.toMatchObject({ status: 503 });
    await expect(acquirePermit(req("192.0.2.2"), evaluate)).rejects.toMatchObject({ status: 503 });
  });
  it("does not reset balances when another script instance admits work", async () => {
    await init(); const permit = await acquirePermit(req(), evaluate); await permit.unknown();
    const otherEval: RedisEval = (script, keys, args) => evaluate(script, keys, args);
    await acquirePermit(req("192.0.2.2"), otherEval);
    expect(await command("HGET", key("ledger"), "month_used")).toBe("10000000");
  });
  it("rejects duplicate reservation IDs before permitting a second provider call", async () => {
    await init(); const id = randomUUID();
    const keys = [LEDGER_KEY, `${LIMIT_PREFIX}:requests`, `${LIMIT_PREFIX}:active`, `${LIMIT_PREFIX}:client:one`, `${LIMIT_PREFIX}:active:one`, `${LIMIT_PREFIX}:reservation:${id}`];
    expect(await evaluate(RESERVE_SCRIPT, keys, [id, String(HOLD_MICRO_INR), "one"])).toEqual([1,200,0]);
    expect(await evaluate(RESERVE_SCRIPT, keys, [id, String(HOLD_MICRO_INR), "one"])).toEqual([0,503,60]);
  });
  it("keeps the daily IP allowance even when the short burst window has elapsed", async () => {
    await init();
    const client = clientBucket(req()); const now = Date.now();
    const entries = Array.from({ length: 40 }, (_, i) => [String(now - 120000 - i), `prior-${i}`]).flat();
    await command("ZADD", key(`client:${client}`), ...entries);
    await expect(acquirePermit(req(), evaluate)).rejects.toMatchObject({ status: 429 });
  });
  it("enforces the global rolling request count across new IP buckets", async () => {
    await init(); const now = Date.now();
    const entries = Array.from({ length: 30 }, (_, i) => [String(now - i), `prior-${i}`]).flat();
    await command("ZADD", key("requests"), ...entries);
    await expect(acquirePermit(req("203.0.113.88"), evaluate)).rejects.toMatchObject({ status: 429 });
  });
  it("does not reset a monthly allowance when only the day rolls over", async () => {
    await init(); const first = await acquirePermit(req(), evaluate); await first.unknown();
    const day = Number(await command("HGET", key("ledger"), "day"));
    await command("HSET", key("ledger"), "day", String(day - 1), "day_used", "20000000", "month_used", "500000000");
    await expect(acquirePermit(req("192.0.2.2"), evaluate)).rejects.toMatchObject({ status: 429 });
  });
  it("denies settlement with a different client bucket", async () => {
    await init(); const id = randomUUID();
    const keys = [LEDGER_KEY, `${LIMIT_PREFIX}:requests`, `${LIMIT_PREFIX}:active`, `${LIMIT_PREFIX}:client:one`, `${LIMIT_PREFIX}:active:one`, `${LIMIT_PREFIX}:reservation:${id}`];
    await evaluate(RESERVE_SCRIPT, keys, [id, String(HOLD_MICRO_INR), "one"]);
    expect(await evaluate(SETTLE_SCRIPT, [LEDGER_KEY, keys[2], keys[4], keys[5]], [id, "0", "other"])).toEqual([0]);
    expect(await command("HGET", key("ledger"), "day_used")).toBe(String(HOLD_MICRO_INR));
  });
  it("will not settle an absent reservation", async () => {
    await init();
    expect(await evaluate(SETTLE_SCRIPT, [LEDGER_KEY, `${LIMIT_PREFIX}:active`, `${LIMIT_PREFIX}:active:one`, `${LIMIT_PREFIX}:reservation:missing`], ["missing", "0"])).toEqual([0]);
  });
});

it.each([[1,503,0], [1,200], [1,200,0,"extra"], ["1",200,0], null].map((reply) => ({ reply })))("refuses malformed admission acknowledgements: $reply", async ({ reply }) => {
  production();
  await expect(acquirePermit(req(), vi.fn().mockResolvedValue(reply))).rejects.toMatchObject({ status: 503 });
});
it("disables admissions before sending a settlement amount beyond the hold", async () => {
  production(); const evaluate = vi.fn().mockResolvedValueOnce([1,200,0]).mockResolvedValueOnce([1]);
  const permit = await acquirePermit(req(), evaluate);
  await expect(permit.settle({ prompt_tokens: 2000000, completion_tokens: 0, total_tokens: 2000000 }, true)).rejects.toMatchObject({ status: 503 });
  expect(evaluate.mock.calls[1][0]).not.toBe(SETTLE_SCRIPT);
});

it("fails closed when backend and website policy snapshots differ", async () => {
  production(); vi.stubEnv("COMPANION_LIMIT_SERVICE_URL", "https://app.aletheore.com/v1/internal/companion-limits"); vi.stubEnv("COMPANION_LIMIT_SERVICE_TOKEN", "synthetic-service-token-at-least-32-chars");
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ result: [1,200,0], policy: "different-policy" })));
  await expect(acquirePermit(req())).rejects.toMatchObject({ status: 503 });
});

it("requires an independent stable HMAC secret even when a service token exists", () => {
  production(); vi.stubEnv("COMPANION_LIMIT_HMAC_SECRET", "");
  vi.stubEnv("COMPANION_LIMIT_SERVICE_TOKEN", "synthetic-service-token-at-least-32-chars");
  expect(() => clientBucket(req())).toThrow(LimitError);
});
it("retains quota identities when the backend service credential rotates", () => {
  production(); vi.stubEnv("COMPANION_LIMIT_SERVICE_TOKEN", "synthetic-first-service-token-0123456789");
  const bucket = clientBucket(req());
  vi.stubEnv("COMPANION_LIMIT_SERVICE_TOKEN", "synthetic-rotated-service-token-0123456789");
  expect(clientBucket(req())).toBe(bucket);
});
