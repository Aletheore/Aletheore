import { describe, expect, it } from "vitest";
import { effectiveInterval, previewItems, subscribeUrl } from "@/lib/pricing";

describe("pricing logic", () => {
  it("previews both tiers monthly", () => {
    expect(previewItems("month").map((i) => i.tier)).toEqual(["flash", "air"]);
  });
  it("leaves Flash out of a yearly preview (it has no yearly price)", () => {
    expect(previewItems("year").map((i) => i.tier)).toEqual(["air"]);
    for (const i of previewItems("year")) expect(i.priceId).toMatch(/^pri_/);
  });
  it("builds the checkout URL on app.aletheore.com", () => {
    expect(subscribeUrl("air", "year")).toBe("https://app.aletheore.com/subscribe?plan=air&interval=year");
  });
  it("sends Flash to monthly checkout even when yearly is selected", () => {
    expect(effectiveInterval("flash", "year")).toBe("month");
    expect(effectiveInterval("air", "year")).toBe("year");
  });
});
