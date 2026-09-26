import { describe, expect, it } from "vitest";
import { easeOutCubic, formatCounter, parseCounter } from "@/lib/counter";

describe("counter", () => {
  it("parses grouped integers", () => {
    expect(parseCounter("1,463,552")).toEqual({ target: 1463552, decimals: 0, grouped: true, suffix: "" });
  });
  it("parses decimals with a percent suffix", () => {
    expect(parseCounter("45.5%")).toEqual({ target: 45.5, decimals: 1, grouped: false, suffix: "%" });
  });
  it("leaves compound values such as durations static", () => {
    expect(parseCounter("4m 20s")).toBeNull();
  });
  it("formats a mid-animation value the way the final value is written", () => {
    const spec = parseCounter("64,634")!;
    expect(formatCounter(spec.target, spec)).toBe("64,634");
    expect(formatCounter(31234.6, spec)).toBe("31,235");
    expect(formatCounter(22.75, parseCounter("45.5%")!)).toBe("22.8%");
    expect(formatCounter(0, spec)).toBe("0");
  });
  it("eases from 0 to 1 without overshoot", () => {
    expect(easeOutCubic(0)).toBe(0);
    expect(easeOutCubic(1)).toBe(1);
    for (let t = 0; t <= 1; t += 0.05) expect(easeOutCubic(t)).toBeLessThanOrEqual(1);
  });
});
