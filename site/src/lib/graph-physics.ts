export type SimNode = {
  id: string; label: string; hub: boolean; cluster: string;
  x: number; y: number; vx: number; vy: number; fx: number | null; fy: number | null;
};
export type Sim = { nodes: SimNode[]; edges: [SimNode, SimNode][]; width: number; height: number; heat: number; tick: number };

export function clusterKey(id: string): string {
  const parts = id.split("/");
  return parts.length > 2 ? parts.slice(0, 2).join("/") : parts[0];
}

export function createSim(
  input: { nodes: { id: string; label: string; hub?: boolean }[]; edges: [number, number][] },
  width: number,
  height: number,
): Sim {
  const clusters = [...new Set(input.nodes.map((n) => clusterKey(n.id)))].sort();
  const anchors = new Map<string, { x: number; y: number }>();
  clusters.forEach((key, i) => {
    const angle = (i / clusters.length) * Math.PI * 2;
    const radius = Math.min(width, height) * 0.34;
    anchors.set(key, { x: width / 2 + Math.cos(angle) * radius * 1.35, y: height / 2 + Math.sin(angle) * radius });
  });
  const seen = new Map<string, number>();
  const nodes: SimNode[] = input.nodes.map((n) => {
    const cluster = clusterKey(n.id);
    const anchor = anchors.get(cluster)!;
    const within = (seen.get(cluster) ?? 0) + 1;
    seen.set(cluster, within);
    const angle = within * 2.4, spread = 18 + within * 7;
    return {
      id: n.id, label: n.label, hub: Boolean(n.hub), cluster,
      x: anchor.x + Math.cos(angle) * spread, y: anchor.y + Math.sin(angle) * spread,
      vx: 0, vy: 0, fx: null, fy: null,
    };
  });
  const edges = input.edges.filter(([a, b]) => nodes[a] && nodes[b]).map(([a, b]) => [nodes[a], nodes[b]] as [SimNode, SimNode]);
  return { nodes, edges, width, height, heat: 1, tick: 0 };
}

export function reheat(sim: Sim, heat = 0.3): void {
  sim.heat = Math.max(sim.heat, heat);
  sim.tick = 0;
}

export function isSettled(totalSpeed: number, tick: number): boolean {
  return totalSpeed <= 0.3 || tick >= 900;
}

export function stepSim(sim: Sim): number {
  sim.tick++;
  sim.heat *= 0.99;
  const alpha = sim.heat;
  const { nodes, edges, width: W, height: H } = sim;
  for (let i = 0; i < nodes.length; i++) {
    for (let j = i + 1; j < nodes.length; j++) {
      const a = nodes[i], b = nodes[j];
      const dx = a.x - b.x, dy = a.y - b.y;
      const d2 = dx * dx + dy * dy || 0.01;
      const d = Math.sqrt(d2);
      const boost = a.hub || b.hub ? 1.7 : 1;
      const f = (2000 * boost * alpha) / d2;
      const fx = (dx / d) * f, fy = (dy / d) * f;
      a.vx += fx; a.vy += fy; b.vx -= fx; b.vy -= fy;
    }
  }
  for (const [a, b] of edges) {
    const dx = b.x - a.x, dy = b.y - a.y;
    const d = Math.sqrt(dx * dx + dy * dy) || 0.01;
    const f = (d - 130) * 0.02 * alpha;
    const fx = (dx / d) * f, fy = (dy / d) * f;
    a.vx += fx; a.vy += fy; b.vx -= fx; b.vy -= fy;
  }
  let total = 0;
  for (const n of nodes) {
    n.vx += (W / 2 - n.x) * 0.001 * alpha;
    n.vy += (H / 2 - n.y) * 0.001 * alpha;
    if (n.fx != null && n.fy != null) { n.x = n.fx; n.y = n.fy; n.vx = 0; n.vy = 0; continue; }
    n.vx *= 0.82; n.vy *= 0.82;
    n.vx = Math.max(-12, Math.min(12, n.vx));
    n.vy = Math.max(-12, Math.min(12, n.vy));
    const nx = n.x + n.vx, ny = n.y + n.vy;
    if (nx <= 16 || nx >= W - 16) n.vx = 0;
    if (ny <= 16 || ny >= H - 16) n.vy = 0;
    n.x = Math.max(16, Math.min(W - 16, nx));
    n.y = Math.max(16, Math.min(H - 16, ny));
    total += Math.abs(n.vx) + Math.abs(n.vy);
  }
  return total;
}
