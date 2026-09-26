import { describe, expect, it } from "vitest";
import { highlightForStep } from "@/lib/highlight";

const graph = {
  nodes: [{ id: "app/a.py" }, { id: "app/b.py" }, { id: "app/c.py" }, { id: "lib/d.py" }],
  edges: [[0, 1], [2, 0], [3, 3]] as [number, number][],
};
const chain = { file: "app/a.py" } as const;

describe("highlightForStep", () => {
  it("lights only the handler file for the first three steps", () => {
    for (const s of [0, 1, 2]) expect(highlightForStep(graph, chain, s)).toEqual(["app/a.py"]);
  });
  it("lights the owner's directory at the owner step", () => {
    expect(highlightForStep(graph, chain, 3).sort()).toEqual(["app/a.py", "app/b.py", "app/c.py"]);
  });
  it("lights the file and its dependents at the touched and verdict steps", () => {
    expect(highlightForStep(graph, chain, 4).sort()).toEqual(["app/a.py", "app/c.py"]);
    expect(highlightForStep(graph, chain, 5).sort()).toEqual(["app/a.py", "app/c.py"]);
  });
  it("returns an empty list for out-of-range steps", () => {
    expect(highlightForStep(graph, chain, -1)).toEqual([]);
    expect(highlightForStep(graph, chain, 9)).toEqual([]);
  });
  it("returns an empty list when the file is not in the graph", () => {
    expect(highlightForStep(graph, { file: "nope.py" }, 0)).toEqual([]);
  });
});
