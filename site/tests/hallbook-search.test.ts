import { afterEach, describe, expect, it, vi } from "vitest";
import { exampleQueries } from "@/data/hallbook";
import { isHbFind, liveSearchEnabled, lookup, STATUS_RECORDED } from "@/data/hallbook-search";

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

  it("falls back to the recorded answer when the live body is not a lookup result", async () => {
    const q = exampleQueries[0];
    for (const body of [{ note: "x" }, { confidence: "maybe" }, { confidence: "low", candidates: "no" }, null]) {
      const f = vi.fn().mockResolvedValue({ ok: true, json: async () => body });
      const out = await lookup(q, true, f as unknown as typeof fetch);
      expect(out.status).toBe("recorded answer");
    }
    expect(isHbFind({ confidence: "high", top: { id: "a/b" }, alternatives: [] })).toBe(true);
  });

  describe("next.config flag", () => {
    afterEach(() => vi.unstubAllEnvs());
    const flag = async () => {
      vi.resetModules();
      return ((await import("../next.config")).default.env as Record<string, string>).NEXT_PUBLIC_HALLBOOK_LIVE;
    };

    it("is on when HALLBOOK_API is set", async () => {
      vi.stubEnv("HALLBOOK_API", "http://127.0.0.1:8780");
      expect(await flag()).toBe("1");
    });

    it("is empty when HALLBOOK_API is unset", async () => {
      vi.stubEnv("HALLBOOK_API", "");
      expect(await flag()).toBe("");
    });
  });
});
