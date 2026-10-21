import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { exampleQueries, hallbook, receipts } from "@/data/hallbook";

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

describe("hallbook receipts", () => {
  const metrics = [receipts.findFirst, receipts.findTopFive, receipts.confidentPrecision, receipts.useSaid, receipts.useAgentsMd, receipts.useNothing];
  const all = [...metrics, receipts.heldoutQueries, receipts.riskyHits, receipts.heldBack, receipts.writesToAgentFolders, receipts.agentBenchmark];

  it("every number carries a source, and unverified ones say so", () => {
    for (const r of all) {
      expect(r.source.length).toBeGreaterThan(10);
      if (r.unverified !== undefined) expect(r.unverified).toBe(true);
    }
  });

  it("metrics are percentages with a display string, and 'before' sits below ours", () => {
    for (const m of metrics) {
      expect(m.value).toBeGreaterThanOrEqual(0);
      expect(m.value).toBeLessThanOrEqual(100);
      expect(m.display.length).toBeGreaterThan(0);
    }
    for (const m of [receipts.findFirst, receipts.findTopFive]) expect(m.before!.value).toBeLessThan(m.value);
  });

  it("the verified counts match the files they cite", () => {
    expect(receipts.heldoutQueries.value).toBe(994);
    expect(receipts.riskyHits.value).toBe(53);
    expect(receipts.heldBack.value).toBe(6);
  });
});

describe("hallbook page copy", () => {
  const read = (rel: string) => readFileSync(join(__dirname, "..", rel), "utf8");

  it("the page does not hardcode a measured figure; they come from the sourced receipts", () => {
    const page = read("src/app/hallbook/page.tsx");
    // Percentages and the old hardcoded counts must live in src/data/hallbook.ts, next to their source.
    expect(page).not.toMatch(/\b\d{1,3}(\.\d)?\s?%/);
    expect(page).not.toMatch(/\b1,1\d\d\b/);
  });

  it("the catalogue snapshot only lists allowlisted licences", () => {
    const allowed = new Set(["MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC"]);
    for (const [licence] of hallbook.stats.licenses) expect(allowed.has(licence)).toBe(true);
  });

  it("the privacy copy says custom skills sync to the account in plain text", () => {
    const page = read("src/app/hallbook/page.tsx");
    expect(page).toMatch(/plain text/);
    expect(page).not.toMatch(/No download, no sign-in, no network call\./);
  });
});
