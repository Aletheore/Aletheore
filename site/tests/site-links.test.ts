import { describe, expect, it } from "vitest";
import { footerColumns, followOptions, getStartedHref, internalPaths, navLinks } from "@/data/site-links";

const all = [...navLinks, ...footerColumns.flatMap((c) => c.links)];

describe("site links", () => {
  it("never links to .html pages", () => {
    for (const l of all) expect(l.href.endsWith(".html")).toBe(false);
  });
  it("internal links point at real pages", () => {
    for (const l of all.filter((x) => !x.external && !x.href.startsWith("mailto:"))) {
      expect(internalPaths).toContain(l.href);
    }
  });
  it("external links are https or mailto", () => {
    for (const l of all.filter((x) => x.external)) expect(l.href).toMatch(/^(https:\/\/|mailto:)/);
  });
  it("footer keeps the four columns", () => {
    expect(footerColumns.map((c) => c.title)).toEqual(["Product", "Community", "Legal", "Contact"]);
  });
  it("follow options are LinkedIn and Instagram with the real URLs", () => {
    expect(followOptions.map((o) => o.href)).toEqual([
      "https://www.linkedin.com/company/aletheore",
      "https://www.instagram.com/aletheore/",
    ]);
  });
  it("get started goes to the app", () => {
    expect(getStartedHref).toBe("https://app.aletheore.com/");
  });
});
