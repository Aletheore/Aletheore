import { describe, expect, it } from "vitest";
import { stepForProgress } from "@/lib/scroll-steps";

describe("stepForProgress", () => {
  it("maps progress evenly onto steps", () => {
    expect(stepForProgress(0, 6)).toBe(0);
    expect(stepForProgress(0.17, 6)).toBe(1);
    expect(stepForProgress(0.5, 6)).toBe(3);
    expect(stepForProgress(0.99, 6)).toBe(5);
  });
  it("clamps progress at and beyond the ends", () => {
    expect(stepForProgress(1, 6)).toBe(5);
    expect(stepForProgress(1.7, 6)).toBe(5);
    expect(stepForProgress(-0.4, 6)).toBe(0);
  });
  it("treats NaN and infinity as the first step", () => {
    expect(stepForProgress(NaN, 6)).toBe(0);
    expect(stepForProgress(Infinity, 6)).toBe(0);
  });
  it("handles a single step and zero steps", () => {
    expect(stepForProgress(0.9, 1)).toBe(0);
    expect(stepForProgress(0.9, 0)).toBe(0);
  });
});
