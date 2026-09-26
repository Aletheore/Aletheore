import { describe, expect, it } from "vitest";
import { barWidth } from "@/lib/bars";

describe("barWidth", () => {
  it("scales against the maximum", () => {
    expect(barWidth(50, 100)).toBe(50);
    expect(barWidth(23.5, 44)).toBeCloseTo(53.409, 2);
  });
  it("clamps to 0..100", () => {
    expect(barWidth(-4, 100)).toBe(0);
    expect(barWidth(180, 100)).toBe(100);
  });
  it("never returns NaN or Infinity", () => {
    expect(barWidth(5, 0)).toBe(0);
    expect(barWidth(Number.NaN, 100)).toBe(0);
    expect(barWidth(Number.POSITIVE_INFINITY, 100)).toBe(0);
  });
});
