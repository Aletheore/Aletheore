import { describe, expect, it } from "vitest";
import { effectiveInterval, previewItems, subscribeUrl, TIERS } from "@/lib/pricing";
import { subscribedPlan } from "@/lib/subscribed";

// The backend (github-app/app_server/paddle_pricing.py PLAN_INTERVAL_TO_PRICE_ID and frontend.py /subscribe) accepts exactly these
// (plan, interval) pairs and price ids. A pair outside this set gets a 400 from /subscribe.
const BACKEND: Record<string, string> = {
  "air:month": "pri_01kyhevc8bkcghfpwjymz16y2h",
  "air:year": "pri_01kyhevc9xn6z2nghmy8057jvp",
  "flash:month": "pri_01m1dj0m1netz6ze1mmckz73nm",
};

describe("checkout wiring against the backend", () => {
  it("uses exactly the price ids the backend maps to plans", () => {
    const ours: Record<string, string> = {};
    for (const [tier, t] of Object.entries(TIERS)) for (const [interval, id] of Object.entries(t.priceId)) ours[`${tier}:${interval}`] = id!;
    expect(ours).toEqual(BACKEND);
  });
  it("only ever links to a (plan, interval) pair the backend accepts", () => {
    for (const tier of ["flash", "air"] as const) {
      for (const interval of ["month", "year"] as const) {
        const eff = effectiveInterval(tier, interval);
        expect(BACKEND[`${tier}:${eff}`], `${tier} ${interval}`).toBeDefined();
        expect(subscribeUrl(tier, eff)).toBe(`https://app.aletheore.com/subscribe?plan=${tier}&interval=${eff}`);
      }
    }
  });
  it("previews only prices that exist", () => {
    for (const interval of ["month", "year"] as const) for (const i of previewItems(interval)) expect(Object.values(BACKEND)).toContain(i.priceId);
  });
  it("recognises the return from a Flash checkout", () => {
    expect(subscribedPlan("?subscribed=flash")).toBe("flash");
    expect(subscribedPlan("?subscribed=air")).toBeNull();
    expect(subscribedPlan("")).toBeNull();
  });
});
