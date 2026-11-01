import { describe, expect, it } from "vitest";
import { companionEnabled } from "@/lib/companion/enabled";

describe("companionEnabled", () => {
  it("is on only for the exact flag value and a non-empty key", () => {
    expect(companionEnabled({ COMPANION_LIVE_ENABLED: "1", INDIEROUTER_API_KEY: "k" })).toBe(true);
    for (const flag of [undefined, "", "0", "true", "yes", " 1"]) {
      expect(companionEnabled({ COMPANION_LIVE_ENABLED: flag, INDIEROUTER_API_KEY: "k" })).toBe(false);
    }
    expect(companionEnabled({ COMPANION_LIVE_ENABLED: "1", INDIEROUTER_API_KEY: "" })).toBe(false);
    expect(companionEnabled({ COMPANION_LIVE_ENABLED: "1" })).toBe(false);
  });
  it("does not depend on NODE_ENV", () => {
    for (const nodeEnv of ["development", "production", "test"]) {
      expect(companionEnabled({ NODE_ENV: nodeEnv, COMPANION_LIVE_ENABLED: "1", INDIEROUTER_API_KEY: "k" })).toBe(true);
    }
  });
});
