export function highlightForStep(
  graph: { nodes: { id: string }[]; edges: [number, number][] },
  chain: { file: string },
  step: number,
): string[] {
  if (step < 0 || step > 5) return [];
  const idx = graph.nodes.findIndex((n) => n.id === chain.file);
  if (idx === -1) return [];
  if (step <= 2) return [chain.file];
  if (step === 3) {
    const dir = chain.file.split("/").slice(0, -1).join("/");
    return graph.nodes.filter((n) => n.id.split("/").slice(0, -1).join("/") === dir).map((n) => n.id);
  }
  const dependents = new Set<string>([chain.file]);
  for (const [from, to] of graph.edges) {
    if (to === idx && from !== idx) dependents.add(graph.nodes[from].id);
  }
  return [...dependents];
}
