import { describe, expect, it } from "vitest";
import ld from "@/data/jsonld.json";
import { siteMetadata as metadata } from "@/lib/site-metadata";

describe("homepage metadata", () => {
  it("keeps the published description", () => {
    expect(metadata.description).toMatch(/^Evidence-grounded GitHub audits:/);
  });
  it("uses the canonical www host", () => {
    expect(String(metadata.metadataBase)).toBe("https://www.aletheore.com/");
  });
  it("keeps the three published offers with their prices", () => {
    const prices = Object.fromEntries((ld.offers as { name: string; price: string }[]).map((o) => [o.name, o.price]));
    expect(prices).toMatchObject({ "Aletheore Community": "0", "Aletheore Flash": "8", "Aletheore AIR": "29.99" });
  });
});
