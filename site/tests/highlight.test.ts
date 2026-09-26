import { describe, expect, it } from "vitest";
import { sceneForStep } from "@/lib/highlight";

// a.py imports b.py; c.py and d.py import a.py; e.py is unrelated.
const graph = {
  nodes: [{ id: "a.py" }, { id: "b.py" }, { id: "c.py" }, { id: "d.py" }, { id: "e.py" }],
  edges: [[0, 1], [2, 0], [3, 0], [4, 4]] as [number, number][],
};
const chain = { file: "a.py", ownerCommits: 49, commitFiles: ["a.py", "e.py"] };

describe("sceneForStep", () => {
  it("finds the file first, alone, from the overview distance", () => {
    expect(sceneForStep(graph, chain, 0)).toEqual({ ids: ["a.py"], edges: "none", zoom: 0, orbit: 0 });
  });
  it("moves in on the handler", () => {
    const s = sceneForStep(graph, chain, 1);
    expect(s.ids).toEqual(["a.py"]);
    expect(s.zoom).toBeGreaterThan(sceneForStep(graph, chain, 0).zoom);
  });
  it("lights what the symbol's file imports at the symbol step", () => {
    const s = sceneForStep(graph, chain, 2);
    expect(s.ids).toEqual(["a.py", "b.py"]);
    expect(s.edges).toBe("out");
  });
  it("circles one dot per commit at the owner step", () => {
    const s = sceneForStep(graph, chain, 3);
    expect(s.orbit).toBe(49);
    expect(s.ids).toEqual(["a.py"]);
  });
  it("lights the files the last commit touched", () => {
    expect(sceneForStep(graph, chain, 4).ids).toEqual(["a.py", "e.py"]);
  });
  it("lights the dependents at the verdict and pulls back to show the blast radius", () => {
    const s = sceneForStep(graph, chain, 5);
    expect([...s.ids].sort()).toEqual(["a.py", "c.py", "d.py"]);
    expect(s.edges).toBe("in");
    expect(s.zoom).toBeLessThan(sceneForStep(graph, chain, 3).zoom);
  });
  it("gives every step a different picture", () => {
    const shots = [0, 1, 2, 3, 4, 5].map((i) => JSON.stringify(sceneForStep(graph, chain, i)));
    expect(new Set(shots).size).toBe(6);
  });
  it("returns an empty scene for out-of-range steps or a missing file", () => {
    expect(sceneForStep(graph, chain, -1).ids).toEqual([]);
    expect(sceneForStep(graph, chain, 9).ids).toEqual([]);
    expect(sceneForStep(graph, { file: "nope.py" }, 0).ids).toEqual([]);
  });
});
