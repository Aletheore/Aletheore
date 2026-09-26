export type Scene = {
  /** The first id is the node the scene is about. */
  ids: string[];
  /** Which edges of that node to light. */
  edges: "none" | "out" | "in";
  /** 0 is the overview distance, 1 is close in. */
  zoom: number;
  /** Dots circling the node (one per commit at the owner step). */
  orbit: number;
};

type Graph = { nodes: { id: string }[]; edges: [number, number][] };
type Chain = { file: string; ownerCommits?: number; commitFiles?: string[] };

const EMPTY: Scene = { ids: [], edges: "none", zoom: 0, orbit: 0 };

/**
 * What the graph shows at each step of the evidence chain. Every set comes from the real scan: an edge [a, b] means
 * a imports b, so "out" is what the file imports and "in" is what depends on it.
 */
export function sceneForStep(graph: Graph, chain: Chain, step: number): Scene {
  const idx = graph.nodes.findIndex((n) => n.id === chain.file);
  if (step < 0 || step > 5 || idx === -1) return EMPTY;
  const file = chain.file;
  const linked = (dir: "out" | "in") => {
    const set = new Set<string>();
    for (const [from, to] of graph.edges) {
      if (dir === "out" && from === idx && to !== idx) set.add(graph.nodes[to].id);
      if (dir === "in" && to === idx && from !== idx) set.add(graph.nodes[from].id);
    }
    return [...set];
  };
  switch (step) {
    case 0: return { ids: [file], edges: "none", zoom: 0, orbit: 0 };
    case 1: return { ids: [file], edges: "none", zoom: 0.7, orbit: 0 };
    case 2: return { ids: [file, ...linked("out")], edges: "out", zoom: 0.45, orbit: 0 };
    case 3: return { ids: [file], edges: "none", zoom: 0.9, orbit: chain.ownerCommits ?? 0 };
    case 4: return { ids: [file, ...(chain.commitFiles ?? []).filter((f) => f !== file)], edges: "none", zoom: 0.8, orbit: 0 };
    default: return { ids: [file, ...linked("in")], edges: "in", zoom: 0.35, orbit: 0 };
  }
}
