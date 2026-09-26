import { describe, expect, it } from "vitest";
import graph from "@/data/graph.json";

describe("graph.json", () => {
  it("has a real-sized graph", () => {
    expect(graph.nodes.length).toBeGreaterThan(50);
    expect(graph.edges.length).toBeGreaterThan(50);
  });
  it("has no dangling or self edges", () => {
    for (const [a, b] of graph.edges) {
      expect(a).not.toBe(b);
      expect(graph.nodes[a]).toBeDefined();
      expect(graph.nodes[b]).toBeDefined();
    }
  });
  it("records provenance", () => {
    expect(graph.source).toMatch(/aletheore scan/);
    expect(graph.commit).toMatch(/^[0-9a-f]{7,}$/);
    expect(new Date(graph.scannedAt).getTime()).not.toBeNaN();
  });
  it("has finite positions inside the unit sphere and at least one hub", () => {
    for (const n of graph.nodes) expect(Math.hypot(n.x, n.y, n.z)).toBeLessThanOrEqual(1.001);
    expect(graph.nodes.some((n) => n.hub)).toBe(true);
  });
});
