import { describe, expect, it } from "vitest";
import { effectiveInterval, previewItems, subscribeUrl, TIERS } from "@/lib/pricing";
import { subscribedPlan, SUBSCRIBED_PLAN_NAMES } from "@/lib/subscribed";

// The hosted backend accepts exactly these
// (plan, interval) pairs and price ids. A pair outside this set gets a 400 from /subscribe.
const BACKEND: Record<string, string> = {
  "lite:month": "pri_01m7zbvbsck8s6rnxpv66652cw",
  "air:month": "pri_01m7d0qgz9jv3dfye9jxvcv5n9",
  "air:year": "pri_01m7d0qh2fgmr0nt92cj77nqva",
  "flash:month": "pri_01m7d0qgwddyg8rgbcj2ppm1ek",
};

describe("checkout wiring against the backend", () => {
  it("uses exactly the price ids the backend maps to plans", () => {
    const ours: Record<string, string> = {};
    for (const [tier, t] of Object.entries(TIERS)) for (const [interval, id] of Object.entries(t.priceId)) ours[`${tier}:${interval}`] = id!;
    expect(ours).toEqual(BACKEND);
  });
  it("only ever links to a (plan, interval) pair the backend accepts", () => {
    for (const tier of ["lite", "flash", "air"] as const) {
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
  it("recognizes the return from a Flash Lite checkout and names the plan the customer bought", () => {
    expect(subscribedPlan("?subscribed=lite")).toBe("lite");
    expect(SUBSCRIBED_PLAN_NAMES.lite).toBe("Aletheore Flash Lite");
    expect(SUBSCRIBED_PLAN_NAMES.flash).toBe("Aletheore Flash");
    expect(subscribedPlan("?subscribed=LITE")).toBeNull();
  });
  it("recognizes the return from a Flash checkout", () => {
    expect(subscribedPlan("?subscribed=flash")).toBe("flash");
    expect(subscribedPlan("?subscribed=air")).toBeNull();
    expect(subscribedPlan("")).toBeNull();
  });
});
