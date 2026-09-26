import { describe, expect, it } from "vitest";
import { layout3d, mulberry32, project, type GraphInput } from "@/lib/graph3d";

function ring(n: number): GraphInput {
  const nodes = Array.from({ length: n }, (_, i) => ({ id: `n${i}`, label: `n${i}` }));
  const edges = nodes.map((_, i) => [`n${i}`, `n${(i + 1) % n}`] as [string, string]);
  return { nodes, edges };
}

describe("mulberry32", () => {
  it("is deterministic and in [0,1)", () => {
    const a = mulberry32(7), b = mulberry32(7);
    for (let i = 0; i < 20; i++) {
      const v = a();
      expect(v).toBe(b());
      expect(v).toBeGreaterThanOrEqual(0);
      expect(v).toBeLessThan(1);
    }
  });
});

describe("layout3d", () => {
  it("is deterministic for a seed and differs across seeds", () => {
    const g = ring(24);
    const a = layout3d(g, { seed: 3 }), b = layout3d(g, { seed: 3 }), c = layout3d(g, { seed: 4 });
    expect(a.nodes.map((n) => n.x)).toEqual(b.nodes.map((n) => n.x));
    expect(a.nodes.map((n) => n.x)).not.toEqual(c.nodes.map((n) => n.x));
  });
  it("returns finite positions inside the unit sphere", () => {
    const { nodes } = layout3d(ring(40));
    for (const n of nodes) {
      expect(Number.isFinite(n.x + n.y + n.z)).toBe(true);
      expect(Math.hypot(n.x, n.y, n.z)).toBeLessThanOrEqual(1.0001);
    }
  });
  it("keeps nodes apart", () => {
    const { nodes } = layout3d(ring(40));
    let min = Infinity;
    for (let i = 0; i < nodes.length; i++)
      for (let j = i + 1; j < nodes.length; j++)
        min = Math.min(min, Math.hypot(nodes[i].x - nodes[j].x, nodes[i].y - nodes[j].y, nodes[i].z - nodes[j].z));
    expect(min).toBeGreaterThan(0.01);
  });
  it("drops edges with unknown endpoints and self loops", () => {
    const g: GraphInput = { nodes: [{ id: "a", label: "a" }, { id: "b", label: "b" }], edges: [["a", "b"], ["a", "zzz"], ["a", "a"]] };
    expect(layout3d(g).edges).toEqual([[0, 1]]);
  });
  it("marks high-degree nodes as hubs", () => {
    const nodes = Array.from({ length: 15 }, (_, i) => ({ id: `n${i}`, label: `n${i}` }));
    const edges = nodes.slice(1).map((n) => ["n0", n.id] as [string, string]);
    const out = layout3d({ nodes, edges });
    expect(out.nodes[0].hub).toBe(true);
    expect(out.nodes[1].hub).toBe(false);
  });
});

describe("project", () => {
  const o = { rotY: 0, rotX: 0, distance: 4, fov: 300, width: 800, height: 600 };
  it("puts the origin at the centre", () => {
    const p = project({ x: 0, y: 0, z: 0 }, o);
    expect(p.x).toBeCloseTo(400);
    expect(p.y).toBeCloseTo(300);
  });
  it("moves +x to the right and +y up on screen", () => {
    const p = project({ x: 1, y: 1, z: 0 }, o);
    expect(p.x).toBeGreaterThan(400);
    expect(p.y).toBeLessThan(300);
  });
  it("makes nearer points larger", () => {
    expect(project({ x: 0, y: 0, z: 1 }, o).scale).toBeGreaterThan(project({ x: 0, y: 0, z: -1 }, o).scale);
  });
});
