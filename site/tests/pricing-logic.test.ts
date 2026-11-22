import { describe, expect, it } from "vitest";
import { effectiveInterval, previewItems, subscribeUrl, TIERS } from "@/lib/pricing";

describe("pricing logic", () => {
  it("previews every tier monthly", () => {
    expect(previewItems("month").map((i) => i.tier)).toEqual(["lite", "flash", "air"]);
  });
  it("leaves Flash Lite and Flash out of a yearly preview (neither has a yearly price)", () => {
    expect(previewItems("year").map((i) => i.tier)).toEqual(["air"]);
    for (const i of previewItems("year")) expect(i.priceId).toMatch(/^pri_/);
  });
  it("builds the checkout URL on app.aletheore.com", () => {
    expect(subscribeUrl("air", "year")).toBe("https://app.aletheore.com/subscribe?plan=air&interval=year");
    expect(subscribeUrl("lite", "month")).toBe("https://app.aletheore.com/subscribe?plan=lite&interval=month");
  });
  it("sends the monthly-only tiers to monthly checkout even when yearly is selected", () => {
    expect(effectiveInterval("lite", "year")).toBe("month");
    expect(effectiveInterval("flash", "year")).toBe("month");
    expect(effectiveInterval("air", "year")).toBe("year");
  });
  it("uses the live Paddle price for Flash Lite ($6 a month, created 2026-11-21)", () => {
    expect(TIERS.lite.priceId.month).toBe("pri_01m7zbvbsck8s6rnxpv66652cw");
    expect(TIERS.lite.priceId.year).toBeUndefined();
    expect(TIERS.lite.name).toBe("Aletheore Flash Lite");
  });
  it("has a distinct Paddle price for every tier and interval", () => {
    const ids = Object.values(TIERS).flatMap((t) => Object.values(t.priceId));
    expect(new Set(ids).size).toBe(ids.length);
  });
});
