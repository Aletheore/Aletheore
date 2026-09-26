export type GraphInput = { nodes: { id: string; label: string }[]; edges: [string, string][] };
export type GraphNode = { id: string; label: string; hub: boolean; degree: number; x: number; y: number; z: number };
export type Graph3D = { nodes: GraphNode[]; edges: [number, number][] };

export type LayoutOptions = {
  seed?: number;
  iterations?: number;
  repulsion?: number;
  spring?: number;
  rest?: number;
  center?: number;
  damping?: number;
  maxSpeed?: number;
  hubDegree?: number;
  hubBoost?: number;
};

export function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function layout3d(input: GraphInput, options: LayoutOptions = {}): Graph3D {
  const {
    seed = 7, iterations = 420, repulsion = 900, spring = 0.02, rest = 60,
    center = 0.0015, damping = 0.82, maxSpeed = 8, hubDegree = 12, hubBoost = 1.7,
  } = options;
  const rand = mulberry32(seed);
  const index = new Map(input.nodes.map((n, i) => [n.id, i] as const));
  const edges: [number, number][] = [];
  for (const [a, b] of input.edges) {
    const i = index.get(a), j = index.get(b);
    if (i !== undefined && j !== undefined && i !== j) edges.push([i, j]);
  }
  const n = input.nodes.length;
  const degree = new Array<number>(n).fill(0);
  for (const [i, j] of edges) { degree[i]++; degree[j]++; }

  const pos = new Float64Array(n * 3);
  const vel = new Float64Array(n * 3);
  for (let i = 0; i < n; i++) {
    const y = 1 - ((i + 0.5) / n) * 2;
    const r = Math.sqrt(Math.max(0, 1 - y * y));
    const phi = i * Math.PI * (3 - Math.sqrt(5));
    const R = 120 + rand() * 40;
    pos[i * 3] = Math.cos(phi) * r * R;
    pos[i * 3 + 1] = y * R;
    pos[i * 3 + 2] = Math.sin(phi) * r * R;
  }

  let heat = 1;
  for (let t = 0; t < iterations; t++) {
    heat *= 0.985;
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        const dx = pos[i * 3] - pos[j * 3], dy = pos[i * 3 + 1] - pos[j * 3 + 1], dz = pos[i * 3 + 2] - pos[j * 3 + 2];
        const d2 = dx * dx + dy * dy + dz * dz || 0.01;
        const d = Math.sqrt(d2);
        const boost = degree[i] >= hubDegree || degree[j] >= hubDegree ? hubBoost : 1;
        const f = (repulsion * boost * heat) / d2;
        const fx = (dx / d) * f, fy = (dy / d) * f, fz = (dz / d) * f;
        vel[i * 3] += fx; vel[i * 3 + 1] += fy; vel[i * 3 + 2] += fz;
        vel[j * 3] -= fx; vel[j * 3 + 1] -= fy; vel[j * 3 + 2] -= fz;
      }
    }
    for (const [i, j] of edges) {
      const dx = pos[j * 3] - pos[i * 3], dy = pos[j * 3 + 1] - pos[i * 3 + 1], dz = pos[j * 3 + 2] - pos[i * 3 + 2];
      const d = Math.sqrt(dx * dx + dy * dy + dz * dz) || 0.01;
      const f = (d - rest) * spring * heat;
      const fx = (dx / d) * f, fy = (dy / d) * f, fz = (dz / d) * f;
      vel[i * 3] += fx; vel[i * 3 + 1] += fy; vel[i * 3 + 2] += fz;
      vel[j * 3] -= fx; vel[j * 3 + 1] -= fy; vel[j * 3 + 2] -= fz;
    }
    for (let i = 0; i < n; i++) {
      for (let k = 0; k < 3; k++) {
        const idx = i * 3 + k;
        vel[idx] += -pos[idx] * center * heat;
        vel[idx] *= damping;
        vel[idx] = Math.max(-maxSpeed, Math.min(maxSpeed, vel[idx]));
        pos[idx] += vel[idx];
      }
    }
  }

  let maxR = 0;
  for (let i = 0; i < n; i++) maxR = Math.max(maxR, Math.hypot(pos[i * 3], pos[i * 3 + 1], pos[i * 3 + 2]));
  const s = maxR > 0 ? 1 / maxR : 1;
  const nodes: GraphNode[] = input.nodes.map((nd, i) => ({
    id: nd.id, label: nd.label, degree: degree[i], hub: degree[i] >= hubDegree,
    x: pos[i * 3] * s, y: pos[i * 3 + 1] * s, z: pos[i * 3 + 2] * s,
  }));
  return { nodes, edges };
}

export type Projection = { rotY: number; rotX: number; distance: number; fov: number; width: number; height: number };

export function project(p: { x: number; y: number; z: number }, o: Projection) {
  const cy = Math.cos(o.rotY), sy = Math.sin(o.rotY), cx = Math.cos(o.rotX), sx = Math.sin(o.rotX);
  const x1 = p.x * cy + p.z * sy;
  const z1 = -p.x * sy + p.z * cy;
  const y2 = p.y * cx - z1 * sx;
  const z2 = p.y * sx + z1 * cx;
  const scale = o.distance / (o.distance - z2);
  return {
    x: o.width / 2 + (x1 * o.fov * scale) / o.distance,
    y: o.height / 2 - (y2 * o.fov * scale) / o.distance,
    depth: z2,
    scale,
  };
}
