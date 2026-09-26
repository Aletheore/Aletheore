import { describe, expect, it } from "vitest";
import ld from "@/data/jsonld.json";
import { credit, footnotes, plans } from "@/data/pricing";

const flat = JSON.stringify({ plans, credit, footnotes });

describe("pricing data", () => {
  it("has the three published plans at the published prices", () => {
    expect(plans.map((p) => [p.name, p.price])).toEqual([["Community", "$0"], ["Flash", "$8"], ["AIR", "$29.99"]]);
  });
  it("agrees with the published JSON-LD offers", () => {
    const offers = Object.fromEntries((ld.offers as { name: string; price: string }[]).map((o) => [o.name, o.price]));
    expect(offers["Aletheore Flash"]).toBe("8");
    expect(offers["Aletheore AIR"]).toBe("29.99");
  });
  it("states the credit allotments used in the meters", () => {
    expect(credit.meters.map((m) => m.value)).toEqual([5, 18]);
  });
  it("carries no em or en dashes", () => {
    expect(flat).not.toMatch(/[–—]/);
  });
  it("keeps the benchmark numbers on the Flash card in line with Experiment 8", () => {
    expect(flat).toContain("about 53% of human-identified bugs");
    expect(flat).toContain("about 93%");
  });
  it("keeps the footnote marks the cards refer to", () => {
    expect(footnotes.map((f) => f.mark)).toEqual(["*", "**"]);
  });
});
