"use client";
import { useEffect, useRef, useState } from "react";
import { hasWebGL } from "@/lib/capabilities";
import { cn } from "@/lib/utils";
import { useReducedMotion } from "./use-reduced-motion";

type CanvasGraph = { nodes: { id: string; x: number; y: number; z: number; hub: boolean }[]; edges: [number, number][] };
type Props = {
  graph: CanvasGraph;
  activeIds?: readonly string[];
  /** Label pinned to the first active node while the scene is focused on it. */
  activeLabel?: string;
  /** Rotate and zoom toward the active nodes and dim everything else. */
  focus?: boolean;
  interactive?: boolean;
  className?: string;
  staticSrc: string;
  alt: string;
};

const INK = 0x16140f;
const STAMP = 0xa33327;
const FAINT = 0xc9c3b5;

export function GraphCanvas({ graph, activeIds = [], activeLabel, focus = false, interactive = false, className, staticSrc, alt }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const applyRef = useRef<((ids: readonly string[]) => void) | null>(null);
  const idsRef = useRef<readonly string[]>(activeIds);
  const labelRef = useRef<HTMLSpanElement>(null);
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
      const base = new THREE.Color(INK), hot = new THREE.Color(STAMP), faint = new THREE.Color(FAINT);
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

      // Active nodes get their own larger points plus a pulsing ring, so the current step is unmistakable.
      const ring = document.createElement("canvas");
      ring.width = ring.height = 128;
      const rctx = ring.getContext("2d")!;
      rctx.lineWidth = 9;
      rctx.strokeStyle = "#fff";
      rctx.beginPath();
      rctx.arc(64, 64, 52, 0, Math.PI * 2);
      rctx.stroke();
      const ringMap = new THREE.CanvasTexture(ring);
      const hotPos = new Float32Array(n * 3);
      const hotPointsGeo = new THREE.BufferGeometry();
      hotPointsGeo.setAttribute("position", new THREE.BufferAttribute(hotPos, 3));
      hotPointsGeo.setDrawRange(0, 0);
      const hotPoints = new THREE.Points(
        hotPointsGeo,
        new THREE.PointsMaterial({ size: 0.095, color: STAMP, map, transparent: true, alphaTest: 0.4, sizeAttenuation: true }),
      );
      const haloPos = new Float32Array(3);
      const haloGeo = new THREE.BufferGeometry();
      haloGeo.setAttribute("position", new THREE.BufferAttribute(haloPos, 3));
      haloGeo.setDrawRange(0, 0);
      const halo = new THREE.Points(
        haloGeo,
        new THREE.PointsMaterial({ size: 0.24, color: STAMP, map: ringMap, transparent: true, alphaTest: 0.2, sizeAttenuation: true, depthWrite: false }),
      );
      group.add(hotPoints, halo);
      const focusCentre = new THREE.Vector3();
      let focusAmount = 0, focusTarget = 0, primary = -1;

      const indexOf = new Map(graph.nodes.map((nd, i) => [nd.id, i] as const));
      const neighbours = new Set<number>();
      const apply = (ids: readonly string[]) => {
        const active = new Set(ids.map((id) => indexOf.get(id)).filter((v): v is number => v !== undefined));
        primary = ids.length ? (indexOf.get(ids[0]) ?? -1) : -1;
        const focused = focus && active.size > 0;
        neighbours.clear();
        if (focused) {
          for (const [a, b] of graph.edges) {
            if (a === primary) neighbours.add(b);
            if (b === primary) neighbours.add(a);
          }
        }
        let h = 0;
        haloGeo.setDrawRange(0, primary >= 0 && focused ? 1 : 0);
        if (primary >= 0) haloPos.set([pos[primary * 3], pos[primary * 3 + 1], pos[primary * 3 + 2]]);
        (haloGeo.getAttribute("position") as import("three").BufferAttribute).needsUpdate = true;
        focusCentre.set(0, 0, 0);
        graph.nodes.forEach((_, i) => {
          const isActive = active.has(i);
          const c = isActive ? hot : focused ? (neighbours.has(i) ? base : faint) : base;
          col.set([c.r, c.g, c.b], i * 3);
          if (isActive) {
            hotPos.set([pos[i * 3], pos[i * 3 + 1], pos[i * 3 + 2]], h * 3);
            focusCentre.x += pos[i * 3];
            focusCentre.y += pos[i * 3 + 1];
            focusCentre.z += pos[i * 3 + 2];
            h++;
          }
        });
        if (h) focusCentre.divideScalar(h);
        hotPointsGeo.setDrawRange(0, h);
        (hotPointsGeo.getAttribute("position") as import("three").BufferAttribute).needsUpdate = true;
        (pointsGeo.getAttribute("color") as import("three").BufferAttribute).needsUpdate = true;
        let k = 0;
        graph.edges.forEach(([a, b], e) => {
          const both = active.has(a) && active.has(b);
          const touchesPrimary = focused && (a === primary || b === primary);
          if (both || touchesPrimary) {
            hotBuf.set(baseLines.subarray(e * 6, e * 6 + 6), k * 6);
            k++;
          }
        });
        hotGeo.setDrawRange(0, k * 2);
        (hotGeo.getAttribute("position") as import("three").BufferAttribute).needsUpdate = true;
        (lines.material as import("three").LineBasicMaterial).opacity = focused ? 0.08 : 0.22;
        focusTarget = focused && focusCentre.length() > 0.05 ? 1 : 0;
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
      const spinQ = new THREE.Quaternion(), targetQ = new THREE.Quaternion(), euler = new THREE.Euler();
      const toFront = new THREE.Vector3(), zAxis = new THREE.Vector3(0, 0, 1), screen = new THREE.Vector3();
      renderer.setAnimationLoop((now) => {
        if (!visible) return;
        focusAmount += (focusTarget - focusAmount) * 0.06;
        if (focusTarget === 0 && focusAmount < 0.001) focusAmount = 0;
        spin += 0.0014 * (1 - focusAmount);
        euler.set(-0.35 + tx, 0.6 + spin + ty, 0);
        spinQ.setFromEuler(euler);
        if (focusAmount > 0) {
          toFront.copy(focusCentre).normalize();
          targetQ.setFromUnitVectors(toFront, zAxis);
          group.quaternion.copy(spinQ).slerp(targetQ, focusAmount);
        } else {
          group.quaternion.copy(spinQ);
        }
        camera.position.z = 4.2 - 1.5 * focusAmount;
        const pulse = 1 + 0.18 * Math.sin(now / 320);
        (halo.material as import("three").PointsMaterial).size = 0.24 * pulse;
        renderer.render(scene, camera);
        const tag = labelRef.current;
        if (tag) {
          if (focusAmount > 0.6 && primary >= 0) {
            screen.set(pos[primary * 3], pos[primary * 3 + 1], pos[primary * 3 + 2]);
            group.localToWorld(screen).project(camera);
            tag.style.transform = `translate(${((screen.x + 1) / 2) * el.clientWidth + 16}px, ${((1 - screen.y) / 2) * el.clientHeight - 28}px)`;
            tag.style.opacity = "1";
          } else {
            tag.style.opacity = "0";
          }
        }
      });
      setReady(true);

      cleanup = () => {
        renderer.setAnimationLoop(null);
        ro.disconnect();
        io.disconnect();
        if (interactive) el.removeEventListener("pointermove", onMove);
        renderer.domElement.removeEventListener("webglcontextlost", onLost);
        pointsGeo.dispose();
        hotPointsGeo.dispose();
        haloGeo.dispose();
        ringMap.dispose();
        hotPoints.material.dispose();
        halo.material.dispose();
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
  }, [graph, interactive, reduced, focus]);

  return (
    <div ref={host} className={cn("relative aspect-square w-full", className)} data-webgl-ready={ready ? "true" : "false"}>
      {activeLabel ? (
        <span
          ref={labelRef}
          aria-hidden="true"
          className="pointer-events-none absolute left-0 top-0 whitespace-nowrap rounded-[3px] bg-stamp px-2 py-1 font-display text-[11px] font-semibold text-paper opacity-0 transition-opacity duration-300"
        >
          {activeLabel}
        </span>
      ) : null}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={staticSrc} alt={alt} className={cn("absolute inset-0 h-full w-full object-contain transition-opacity duration-500", ready && "opacity-0")} />
    </div>
  );
}
