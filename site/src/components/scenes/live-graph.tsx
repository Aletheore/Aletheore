"use client";
import { useEffect, useRef, useState } from "react";
import { graphData as graph } from "@/data/graph";
import { createSim, isSettled, reheat, stepSim, type Sim } from "@/lib/graph-physics";
import { useReducedMotion } from "./use-reduced-motion";

const W = 1100, H = 560;

export function LiveGraph() {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [hover, setHover] = useState<string | null>(null);
  const hoverRef = useRef<number>(-1);
  const reduced = useReducedMotion();

  useEffect(() => {
    const el = canvas.current;
    if (!el) return;
    const ctx = el.getContext("2d")!;
    const sim: Sim = createSim({ nodes: graph.nodes, edges: graph.edges }, W, H);
    const neighbours = graph.nodes.map(() => new Set<number>());
    graph.edges.forEach(([a, b]) => { neighbours[a].add(b); neighbours[b].add(a); });
    if (reduced) { for (let i = 0; i < 900; i++) if (isSettled(stepSim(sim), sim.tick)) break; }

    let running = !reduced, dragging = -1, raf = 0, visible = true;
    const draw = () => {
      const dpr = Math.min(window.devicePixelRatio, 2);
      if (el.width !== W * dpr) { el.width = W * dpr; el.height = H * dpr; }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, W, H);
      const h = hoverRef.current;
      ctx.lineWidth = 1;
      for (const [a, b] of sim.edges) {
        const on = h >= 0 && (sim.nodes[h] === a || sim.nodes[h] === b);
        ctx.strokeStyle = on ? "#A33327" : "rgba(22,20,15,0.22)";
        ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
      }
      sim.nodes.forEach((n, i) => {
        const near = h >= 0 && (i === h || neighbours[h].has(i));
        ctx.fillStyle = near ? "#A33327" : h >= 0 ? "rgba(22,20,15,0.35)" : "#16140F";
        ctx.beginPath(); ctx.arc(n.x, n.y, n.hub ? 5.5 : 3.4, 0, Math.PI * 2); ctx.fill();
        if (n.hub || near) {
          ctx.font = "11px ui-monospace, monospace"; ctx.fillStyle = "#16140F";
          ctx.fillText(n.label, n.x + 8, n.y - 8);
        }
      });
    };
    const loop = () => {
      if (visible && running) {
        const speed = stepSim(sim);
        if (dragging < 0 && isSettled(speed, sim.tick)) running = false;
      }
      draw();
      raf = requestAnimationFrame(loop);
    };
    draw();
    if (!reduced) raf = requestAnimationFrame(loop);

    const at = (e: PointerEvent) => {
      const r = el.getBoundingClientRect();
      return { x: ((e.clientX - r.left) / r.width) * W, y: ((e.clientY - r.top) / r.height) * H };
    };
    const pick = (x: number, y: number) => {
      let best = -1, bd = 14 * 14;
      sim.nodes.forEach((n, i) => { const d = (n.x - x) ** 2 + (n.y - y) ** 2; if (d < bd) { bd = d; best = i; } });
      return best;
    };
    const onMove = (e: PointerEvent) => {
      const p = at(e);
      if (dragging >= 0) { sim.nodes[dragging].fx = p.x; sim.nodes[dragging].fy = p.y; return; }
      const i = pick(p.x, p.y);
      if (i !== hoverRef.current) { hoverRef.current = i; setHover(i >= 0 ? sim.nodes[i].id : null); if (reduced) draw(); }
    };
    const onDown = (e: PointerEvent) => {
      const i = pick(at(e).x, at(e).y);
      if (i < 0) return;
      dragging = i; el.setPointerCapture(e.pointerId); reheat(sim, 0.3); running = true;
      if (reduced) { running = false; }
    };
    const onUp = () => {
      if (dragging >= 0) { sim.nodes[dragging].fx = null; sim.nodes[dragging].fy = null; dragging = -1; }
    };
    const io = new IntersectionObserver(([en]) => { visible = en.isIntersecting; });
    io.observe(el);
    el.addEventListener("pointermove", onMove);
    el.addEventListener("pointerdown", onDown);
    el.addEventListener("pointerup", onUp);
    el.addEventListener("pointerleave", () => { hoverRef.current = -1; setHover(null); });
    return () => {
      cancelAnimationFrame(raf); io.disconnect();
      el.removeEventListener("pointermove", onMove); el.removeEventListener("pointerdown", onDown); el.removeEventListener("pointerup", onUp);
    };
  }, [reduced]);

  return (
    <section aria-labelledby="live-title" className="mx-auto max-w-[1080px] px-8 py-16">
      <p className="eyebrow eyebrow--section mb-3">AIRview, live</p>
      <h2 id="live-title" className="font-display text-[clamp(24px,3.2vw,34px)] font-bold leading-[1.1] tracking-[-0.015em]">
        This is a real dependency graph, not a screenshot.
      </h2>
      <p className="mt-4 max-w-[60ch] text-[15px] leading-[1.65] text-muted">
        AIRview builds this from your own repo&apos;s real imports: drag a node, hover to trace what depends on what. This demo is graphed from Aletheore&apos;s own module names.
      </p>
      <div className="crop-marks mt-8 border border-line bg-raised">
        <canvas
          ref={canvas}
          role="img"
          aria-label="Interactive dependency graph of the Aletheore repository. Hover a module to highlight what it imports and what imports it."
          className="block h-auto w-full touch-pan-y"
        />
      </div>
      <p className="mt-3 font-display text-[12px] text-faint" aria-live="polite">
        {hover ? hover : "Hover a module to see what it imports."}
      </p>
    </section>
  );
}
