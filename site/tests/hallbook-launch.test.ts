import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it, vi } from "vitest";

const read = (rel: string) => readFileSync(fileURLToPath(new URL(`../${rel}`, import.meta.url)), "utf8");

async function load() {
  vi.resetModules();
  return import("@/data/site-links");
}

describe("hallbook launch switch", () => {
  afterEach(() => vi.unstubAllEnvs());

  it("is launched by default, and NEXT_PUBLIC_HALLBOOK_LAUNCHED=0 is the way back", async () => {
    vi.stubEnv("NEXT_PUBLIC_HALLBOOK_LAUNCHED", "");
    expect((await load()).navLinks.map((l) => l.href)).toContain("/hallbook");
  });

  it("keeps Hallbook out of the nav when switched off", async () => {
    vi.stubEnv("NEXT_PUBLIC_HALLBOOK_LAUNCHED", "0");
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
    vi.stubEnv("NEXT_PUBLIC_HALLBOOK_LAUNCHED", "0");
    const { internalPaths } = await load();
    expect(internalPaths).toContain("/hallbook");
  });

  it("the sitemap and llms.txt list the page exactly when it has launched", async () => {
    vi.resetModules();
    const { HALLBOOK_LAUNCHED } = await import("@/data/launch");
    for (const f of ["public/sitemap.xml", "public/llms.txt"]) {
      const text = read(f);
      if (HALLBOOK_LAUNCHED) expect(text).toMatch(/hallbook/i);
      else expect(text).not.toMatch(/hallbook/i);
    }
  });

  it("the page says the commands arrive with the release, and the flag is read from one place", async () => {
    const page = read("src/app/hallbook/page.tsx");
    expect(page).toMatch(/arrives with the next Aletheore release/);
    expect(page).toMatch(/HALLBOOK_LAUNCHED/);
    expect(read("src/data/launch.ts")).toMatch(/NEXT_PUBLIC_HALLBOOK_LAUNCHED/);
  });
});
