import { describe, expect, it } from "vitest";
import { exampleQueries, hallbook } from "@/data/hallbook";

describe("hallbook data snapshot", () => {
  it("shelf counts add up to the total", () => {
    const sum = hallbook.stats.shelves.reduce((n, s) => n + s.count, 0);
    expect(sum).toBe(hallbook.stats.total);
    expect(hallbook.stats.total).toBeGreaterThan(1000);
  });

  it("every example has a confidence, and a confident one carries the skill", () => {
    expect(exampleQueries.length).toBeGreaterThan(0);
    for (const q of exampleQueries) {
      const r = hallbook.examples[q];
      expect(["none", "low", "high"]).toContain(r.confidence);
      if (r.confidence === "high") {
        expect(r.top?.id).toMatch(/^[a-z0-9-]+\/[a-z0-9._-]+$/);
        expect(r.chars).toBeGreaterThan(0);
        expect(r.preview?.length).toBeGreaterThan(0);
      }
      if (r.confidence === "low") expect((r.candidates ?? []).length).toBeGreaterThan(0);
    }
  });

  it("the sample skill file has the lines the vetting section annotates", () => {
    const head = hallbook.skill.head;
    expect(head).toMatch(/^source_url:/m);
    expect(head).toMatch(/^license:/m);
  });
});
