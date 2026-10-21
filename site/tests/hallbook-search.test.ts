import { describe, expect, it, vi } from "vitest";
import { exampleQueries } from "@/data/hallbook";
import { liveSearchEnabled, lookup, STATUS_RECORDED } from "@/data/hallbook-search";
import nextConfig from "../next.config";

describe("hallbook search without a live API", () => {
  it("is off unless the build flag is exactly 1", () => {
    expect(liveSearchEnabled(undefined)).toBe(false);
    expect(liveSearchEnabled("")).toBe(false);
    expect(liveSearchEnabled("1")).toBe(true);
  });

  it("sends no request, for an example or any other text", async () => {
    const f = vi.fn();
    const known = await lookup(exampleQueries[0], false, f as unknown as typeof fetch);
    const other = await lookup("something nobody recorded", false, f as unknown as typeof fetch);
    expect(f).not.toHaveBeenCalled();
    expect(known.status).toBe(STATUS_RECORDED);
    expect(known.res).not.toBeNull();
    expect(other.res).toBeNull();
    expect(other.status).toContain(STATUS_RECORDED);
    expect(other.status).not.toContain("local Hallbook server");
  });

  it("fetches in live mode", async () => {
    const f = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ confidence: "none" }) });
    const out = await lookup("anything", true, f as unknown as typeof fetch);
    expect(f).toHaveBeenCalledOnce();
    expect(out.res?.confidence).toBe("none");
  });

  it("next.config only turns the flag on when HALLBOOK_API is set", () => {
    expect(process.env.HALLBOOK_API).toBeUndefined();
    expect((nextConfig.env as Record<string, string>).NEXT_PUBLIC_HALLBOOK_LIVE).toBe("");
  });
});
