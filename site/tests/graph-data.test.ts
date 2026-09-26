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
  it("gives every node the full GraphNode shape (graphData is a cast, so this is the type check)", () => {
    for (const n of graph.nodes) {
      expect(typeof n.id).toBe("string");
      expect(typeof n.label).toBe("string");
      expect(typeof n.hub).toBe("boolean");
      for (const k of ["degree", "x", "y", "z"] as const) expect(Number.isFinite(n[k]), `${n.id}.${k}`).toBe(true);
    }
    expect(typeof graph.source).toBe("string");
    expect(Number.isNaN(Date.parse(graph.scannedAt))).toBe(false);
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
