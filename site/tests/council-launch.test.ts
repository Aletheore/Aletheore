import { describe, expect, it } from "vitest";
import { navLinks, internalPaths } from "../src/data/site-links";
import { COUNCIL_LAUNCHED } from "../src/data/launch";

describe("Council page", () => {
  it("is a known internal path, and stays out of the nav until Council launches", () => {
    expect(internalPaths).toContain("/council");
    expect(navLinks.some((l) => l.href === "/council")).toBe(COUNCIL_LAUNCHED);
    expect(COUNCIL_LAUNCHED).toBe(process.env.NEXT_PUBLIC_COUNCIL_LAUNCHED === "1"); // off unless NEXT_PUBLIC_COUNCIL_LAUNCHED=1, so the launch-day flip keeps this green
  });
});
