import { describe, expect, it } from "vitest";

describe("toolchain", () => {
  it("resolves the @ alias to src", async () => {
    const mod = await import("@/lib/utils");
    expect(typeof mod.cn).toBe("function");
  });
});
