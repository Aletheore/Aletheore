"use client";
import { useEffect, useRef, useState } from "react";
import { hasWebGL } from "@/lib/capabilities";
import { cn } from "@/lib/utils";
import { useReducedMotion } from "./use-reduced-motion";

type CanvasGraph = { nodes: { id: string; x: number; y: number; z: number; hub: boolean }[]; edges: [number, number][] };
type Props = {
  graph: CanvasGraph;
  activeIds?: readonly string[];
  interactive?: boolean;
  className?: string;
  staticSrc: string;
  alt: string;
};

const INK = 0x16140f;
const STAMP = 0xa33327;

export function GraphCanvas({ graph, activeIds = [], interactive = false, className, staticSrc, alt }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const applyRef = useRef<((ids: readonly string[]) => void) | null>(null);
  const idsRef = useRef<readonly string[]>(activeIds);
  const reduced = useReducedMotion();
  const [ready, setReady] = useState(false);

  useEffect(() => {
    idsRef.current = activeIds;
    applyRef.current?.(activeIds);
  }, [activeIds]);

  useEffect(() => {
    const el = host.current;
    if (!el || reduced || !hasWebGL()) return;
    let disposed = false;
    let cleanup: (() => void) | null = null;

    (async () => {
      const THREE = await import("three");
      if (disposed) return;
      const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: "low-power" });
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
      renderer.domElement.style.cssText = "position:absolute;inset:0;width:100%;height:100%;display:block";
      el.appendChild(renderer.domElement);

      const scene = new THREE.Scene();
      const camera = new THREE.PerspectiveCamera(38, 1, 0.1, 100);
      camera.position.set(0, 0, 4.2);
      const group = new THREE.Group();
      group.rotation.set(-0.35, 0.6, 0);
      scene.add(group);

      const R = 1.35;
      const n = graph.nodes.length;
      const pos = new Float32Array(n * 3);
      const col = new Float32Array(n * 3);
      const base = new THREE.Color(INK), hot = new THREE.Color(STAMP);
      graph.nodes.forEach((nd, i) => {
        pos.set([nd.x * R, nd.y * R, nd.z * R], i * 3);
        col.set([base.r, base.g, base.b], i * 3);
      });
      const pointsGeo = new THREE.BufferGeometry();
      pointsGeo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
      pointsGeo.setAttribute("color", new THREE.BufferAttribute(col, 3));
      const sprite = document.createElement("canvas");
      sprite.width = sprite.height = 64;
      const sctx = sprite.getContext("2d")!;
      sctx.beginPath();
      sctx.arc(32, 32, 30, 0, Math.PI * 2);
      sctx.fillStyle = "#fff";
      sctx.fill();
      const map = new THREE.CanvasTexture(sprite);
      const points = new THREE.Points(
        pointsGeo,
        new THREE.PointsMaterial({ size: 0.045, vertexColors: true, map, transparent: true, alphaTest: 0.4, sizeAttenuation: true }),
      );
      group.add(points);

      const baseLines = new Float32Array(graph.edges.length * 6);
      graph.edges.forEach(([a, b], i) => {
        baseLines.set([pos[a * 3], pos[a * 3 + 1], pos[a * 3 + 2], pos[b * 3], pos[b * 3 + 1], pos[b * 3 + 2]], i * 6);
      });
      const linesGeo = new THREE.BufferGeometry();
      linesGeo.setAttribute("position", new THREE.BufferAttribute(baseLines, 3));
      const lines = new THREE.LineSegments(linesGeo, new THREE.LineBasicMaterial({ color: INK, transparent: true, opacity: 0.22 }));
      group.add(lines);

      const hotBuf = new Float32Array(graph.edges.length * 6);
      const hotGeo = new THREE.BufferGeometry();
      hotGeo.setAttribute("position", new THREE.BufferAttribute(hotBuf, 3));
      hotGeo.setDrawRange(0, 0);
      const hotLines = new THREE.LineSegments(hotGeo, new THREE.LineBasicMaterial({ color: STAMP, transparent: true, opacity: 0.9 }));
      group.add(hotLines);

      const indexOf = new Map(graph.nodes.map((nd, i) => [nd.id, i] as const));
      const apply = (ids: readonly string[]) => {
        const active = new Set(ids.map((id) => indexOf.get(id)).filter((v): v is number => v !== undefined));
        graph.nodes.forEach((_, i) => {
          const c = active.has(i) ? hot : base;
          col.set([c.r, c.g, c.b], i * 3);
        });
        (pointsGeo.getAttribute("color") as import("three").BufferAttribute).needsUpdate = true;
        let k = 0;
        graph.edges.forEach(([a, b], e) => {
          if (active.has(a) && active.has(b)) {
            hotBuf.set(baseLines.subarray(e * 6, e * 6 + 6), k * 6);
            k++;
          }
        });
        hotGeo.setDrawRange(0, k * 2);
        (hotGeo.getAttribute("position") as import("three").BufferAttribute).needsUpdate = true;
        points.material.size = active.size > 0 ? 0.05 : 0.045;
      };
      applyRef.current = apply;
      apply(idsRef.current);

      const resize = () => {
        const { clientWidth: w, clientHeight: h } = el;
        if (!w || !h) return;
        renderer.setSize(w, h, false);
        camera.aspect = w / h;
        camera.updateProjectionMatrix();
      };
      const ro = new ResizeObserver(resize);
      ro.observe(el);
      resize();

      let tx = 0, ty = 0;
      const onMove = (e: PointerEvent) => {
        const r = el.getBoundingClientRect();
        tx = ((e.clientY - r.top) / r.height - 0.5) * 0.25;
        ty = ((e.clientX - r.left) / r.width - 0.5) * 0.35;
      };
      if (interactive) el.addEventListener("pointermove", onMove);

      let visible = true;
      const io = new IntersectionObserver(([entry]) => {
        visible = entry.isIntersecting;
      }, { threshold: 0 });
      io.observe(el);

      const onLost = (e: Event) => {
        e.preventDefault();
        setReady(false);
      };
      renderer.domElement.addEventListener("webglcontextlost", onLost);

      let spin = 0;
      renderer.setAnimationLoop(() => {
        if (!visible) return;
        spin += 0.0014;
        group.rotation.y = 0.6 + spin + ty;
        group.rotation.x = -0.35 + tx;
        renderer.render(scene, camera);
      });
      setReady(true);

      cleanup = () => {
        renderer.setAnimationLoop(null);
        ro.disconnect();
        io.disconnect();
        if (interactive) el.removeEventListener("pointermove", onMove);
        renderer.domElement.removeEventListener("webglcontextlost", onLost);
        pointsGeo.dispose();
        linesGeo.dispose();
        hotGeo.dispose();
        map.dispose();
        points.material.dispose();
        (lines.material as import("three").Material).dispose();
        (hotLines.material as import("three").Material).dispose();
        renderer.dispose();
        renderer.forceContextLoss();
        renderer.domElement.remove();
        applyRef.current = null;
        setReady(false);
      };
    })().catch(() => setReady(false));

    return () => {
      disposed = true;
      cleanup?.();
    };
  }, [graph, interactive, reduced]);

  return (
    <div ref={host} className={cn("relative aspect-square w-full", className)} data-webgl-ready={ready ? "true" : "false"}>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={staticSrc} alt={alt} className={cn("absolute inset-0 h-full w-full object-contain transition-opacity duration-500", ready && "opacity-0")} />
    </div>
  );
}
