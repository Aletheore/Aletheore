import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import legal from "@/data/legal.json";
import { internalPaths } from "@/data/site-links";

const h2 = (html: string) => (html.match(/<h2>/g) ?? []).length;

describe("ported policy pages", () => {
  it("keep every section of the published pages", () => {
    expect(h2(legal.privacy.html)).toBe(5);
    expect(h2(legal.terms.html)).toBe(5);
    expect(h2(legal.refund.html)).toBe(3);
    expect(h2(legal.security.html)).toBe(7);
  });
  it("keep the dated headers and the 14-day refund window", () => {
    expect(legal.privacy.updated).toBe("Last updated: August 2026");
    expect(legal.terms.updated).toBe("Last updated: July 2026");
    expect(legal.refund.html).toContain("14 days");
  });
  it("state the current prices in the terms", () => {
    expect(legal.terms.html).toContain("$8/month");
    expect(legal.terms.html).toContain("$29.99/month or $299.90/year");
    expect(legal.terms.html).toContain("$6.99/month");
  });
  it("disclose the free-tier provider on the privacy page (PR 811)", () => {
    expect(legal.privacy.html).toContain("indierouter.ai");
  });
  it("link internally by clean paths, never .html", () => {
    for (const p of Object.values(legal)) {
      expect(p.html).not.toMatch(/href="[a-z]+\.html"/);
      for (const m of p.html.matchAll(/href="(\/[^"]*)"/g)) expect(internalPaths as readonly string[]).toContain(m[1]);
    }
  });
  it("carry no em or en dashes", () => {
    expect(JSON.stringify(legal)).not.toMatch(/[–—]/);
    for (const f of ["dogfooding", "status"]) expect(readFileSync(`src/app/${f}/page.tsx`, "utf8")).not.toMatch(/[–—]/);
  });
});

describe("routes", () => {
  it("have a page for every internal path", () => {
    for (const p of internalPaths) {
      const dir = p === "/" ? "src/app/page.tsx" : `src/app${p}/page.tsx`;
      expect(() => readFileSync(dir, "utf8"), p).not.toThrow();
    }
  });
});
