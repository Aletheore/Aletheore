import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it, vi } from "vitest";

const read = (rel: string) => readFileSync(fileURLToPath(new URL(`../${rel}`, import.meta.url)), "utf8");

async function load() {
  vi.resetModules();
  return import("@/data/site-links");
}

describe("Council launch switch", () => {
  afterEach(() => vi.unstubAllEnvs());

  it("is launched by default: in the nav, and linked from the footer to aletheorecouncil.com", async () => {
    vi.stubEnv("NEXT_PUBLIC_COUNCIL_LAUNCHED", "");
    const { navLinks, footerColumns, internalPaths } = await load();
    expect(internalPaths).toContain("/council");
    expect(navLinks.map((l) => l.href)).toContain("/council");
    const footer = footerColumns.flatMap((c) => c.links);
    expect(footer).toContainEqual({ label: "Council", href: "https://aletheorecouncil.com", external: true });
  });

  it("NEXT_PUBLIC_COUNCIL_LAUNCHED=0 is the way back: out of the nav and the footer, still a known route", async () => {
    vi.stubEnv("NEXT_PUBLIC_COUNCIL_LAUNCHED", "0");
    const { navLinks, footerColumns, internalPaths } = await load();
    expect(navLinks.map((l) => l.href)).not.toContain("/council");
    expect(footerColumns.flatMap((c) => c.links).map((l) => l.href)).not.toContain("https://aletheorecouncil.com");
    expect(internalPaths).toContain("/council");
  });

  it("the sitemap and llms.txt list the page and the Council site when launched", async () => {
    vi.resetModules();
    const { COUNCIL_LAUNCHED } = await import("@/data/launch");
    for (const f of ["public/sitemap.xml", "public/llms.txt"]) {
      const text = read(f);
      if (COUNCIL_LAUNCHED) expect(text).toMatch(/aletheore\.com\/council/);
      else expect(text).not.toMatch(/aletheore\.com\/council/);
    }
  });

  it("links to the Council site are plain followed links, and the page says which release ships it", () => {
    expect(read("src/components/site/footer.tsx")).not.toMatch(/nofollow/);
    expect(read("src/components/site/header.tsx")).not.toMatch(/nofollow/);
    const page = read("src/app/council/page.tsx");
    expect(page).not.toMatch(/nofollow/);
    expect(page).toMatch(/ships in Aletheore 0\.13\.0/);
    expect(page).toMatch(/not had an independent security review/);
  });
});
