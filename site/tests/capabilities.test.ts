import { describe, expect, it } from "vitest";
import { hasWebGL } from "@/lib/capabilities";

describe("hasWebGL", () => {
  it("is true when webgl2 is available", () => {
    expect(hasWebGL(() => ({ getContext: (k) => (k === "webgl2" ? {} : null) }))).toBe(true);
  });
  it("falls back to webgl", () => {
    expect(hasWebGL(() => ({ getContext: (k) => (k === "webgl" ? {} : null) }))).toBe(true);
  });
  it("is false when no context can be created", () => {
    expect(hasWebGL(() => ({ getContext: () => null }))).toBe(false);
  });
  it("is false when there is no canvas", () => {
    expect(hasWebGL(() => null)).toBe(false);
  });
  it("is false, not thrown, when getContext throws", () => {
    expect(hasWebGL(() => ({ getContext: () => { throw new Error("blocked"); } }))).toBe(false);
  });
});
