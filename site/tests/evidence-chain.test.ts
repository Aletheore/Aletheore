import { describe, expect, it } from "vitest";
import graph from "@/data/graph.json";
import { evidenceChain } from "@/data/evidence-chain";

describe("evidence chain", () => {
  it("has the six steps in order", () => {
    expect(evidenceChain.steps.map((s) => s.id)).toEqual(["endpoint", "handler", "symbol", "owner", "touched", "verdict"]);
  });
  it("has non-empty text on every step", () => {
    for (const s of evidenceChain.steps) {
      expect(s.label.length).toBeGreaterThan(0);
      expect(s.value.length).toBeGreaterThan(0);
      expect(s.detail.length).toBeGreaterThan(0);
    }
  });
  it("points at a file that exists in the graph", () => {
    expect(graph.nodes.map((n) => n.id)).toContain(evidenceChain.file);
  });
  it("records provenance", () => {
    expect(evidenceChain.source).toMatch(/aletheore/i);
    expect(evidenceChain.commit).toMatch(/^[0-9a-f]{7,}$/);
  });
  it("never claims a verdict the data does not support", () => {
    expect(evidenceChain.steps[5].value).toMatch(/commits?/);
  });
});
