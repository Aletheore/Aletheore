import { afterEach, describe, expect, it, vi } from "vitest";

async function load() {
  vi.resetModules();
  return import("@/data/site-links");
}

describe("hallbook launch switch", () => {
  afterEach(() => vi.unstubAllEnvs());

  it("keeps Hallbook out of the nav until it has launched", async () => {
    vi.stubEnv("NEXT_PUBLIC_HALLBOOK_LAUNCHED", "");
    const { navLinks } = await load();
    expect(navLinks.map((l) => l.href)).not.toContain("/hallbook");
  });

  it("adds Hallbook to the nav once launched, in the same place as before", async () => {
    vi.stubEnv("NEXT_PUBLIC_HALLBOOK_LAUNCHED", "1");
    const { navLinks } = await load();
    const hrefs = navLinks.map((l) => l.href);
    expect(hrefs).toContain("/hallbook");
    expect(hrefs.indexOf("/hallbook")).toBe(hrefs.indexOf("/developers") + 1);
  });

  it("the page stays a known route either way, so a direct visit works", async () => {
    vi.stubEnv("NEXT_PUBLIC_HALLBOOK_LAUNCHED", "");
    const { internalPaths } = await load();
    expect(internalPaths).toContain("/hallbook");
  });

  it("the sitemap and llms.txt do not list the page before launch", async () => {
    const { readFileSync } = await import("node:fs");
    const { join } = await import("node:path");
    for (const f of ["public/sitemap.xml", "public/llms.txt"]) {
      expect(readFileSync(join(__dirname, "..", f), "utf8")).not.toMatch(/hallbook/i);
    }
  });

  it("the page says the commands arrive with the release, and the flag is read from one place", async () => {
    const { readFileSync } = await import("node:fs");
    const { join } = await import("node:path");
    const page = readFileSync(join(__dirname, "..", "src/app/hallbook/page.tsx"), "utf8");
    expect(page).toMatch(/arrives with the next Aletheore release/);
    expect(page).toMatch(/HALLBOOK_LAUNCHED/);
    expect(readFileSync(join(__dirname, "..", "src/data/launch.ts"), "utf8")).toMatch(/NEXT_PUBLIC_HALLBOOK_LAUNCHED/);
  });
});
