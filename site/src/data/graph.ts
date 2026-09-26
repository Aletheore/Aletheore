import raw from "./graph.json";
import type { GraphNode } from "@/lib/graph3d";

export type GraphData = {
  source: string;
  scannedAt: string;
  commit: string;
  nodes: GraphNode[];
  edges: [number, number][];
};

export const graphData = raw as unknown as GraphData;
