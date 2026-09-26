import { describe, expect, it } from "vitest";
import graph from "@/data/graph.json";
import { createSim, isSettled, reheat, stepSim } from "@/lib/graph-physics";

function run(max = 900) {
  const sim = createSim({ nodes: graph.nodes, edges: graph.edges as [number, number][] }, 1100, 560);
  let speed = Infinity, ticks = 0;
  while (ticks < max) { speed = stepSim(sim); ticks++; if (isSettled(speed, ticks)) break; }
  return { sim, speed, ticks };
}

describe("graph physics", () => {
  it("settles on its own before the tick cap (no perpetual dancing)", () => {
    const { speed, ticks } = run();
    expect(speed).toBeLessThanOrEqual(0.3);
    expect(ticks).toBeLessThan(900);
  });
  it("keeps every coordinate finite and inside the canvas", () => {
    const { sim } = run();
    for (const n of sim.nodes) {
      expect(Number.isFinite(n.x + n.y)).toBe(true);
      expect(n.x).toBeGreaterThanOrEqual(16);
      expect(n.x).toBeLessThanOrEqual(1100 - 16);
      expect(n.y).toBeGreaterThanOrEqual(16);
      expect(n.y).toBeLessThanOrEqual(560 - 16);
    }
  });
  it("does not stack nodes on top of each other", () => {
    const { sim } = run();
    let min = Infinity;
    for (let i = 0; i < sim.nodes.length; i++)
      for (let j = i + 1; j < sim.nodes.length; j++)
        min = Math.min(min, Math.hypot(sim.nodes[i].x - sim.nodes[j].x, sim.nodes[i].y - sim.nodes[j].y));
    expect(min).toBeGreaterThan(6);
  });
  it("keeps hub labels apart", () => {
    const { sim } = run();
    const hubs = sim.nodes.filter((n) => n.hub);
    let min = Infinity;
    for (let i = 0; i < hubs.length; i++)
      for (let j = i + 1; j < hubs.length; j++) min = Math.min(min, Math.hypot(hubs[i].x - hubs[j].x, hubs[i].y - hubs[j].y));
    expect(min).toBeGreaterThan(30);
  });
  it("stays stable when reheated after a drag", () => {
    const { sim } = run();
    reheat(sim, 0.3);
    let speed = Infinity, ticks = 0;
    while (ticks < 900) { speed = stepSim(sim); ticks++; if (isSettled(speed, ticks)) break; }
    expect(speed).toBeLessThanOrEqual(0.3);
  });
});
