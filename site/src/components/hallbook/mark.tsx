"use client";
import { useEffect, useRef, useState } from "react";
import { useReducedMotion } from "@/components/scenes/use-reduced-motion";

/** A marker-pen highlight that sweeps across the phrase when it scrolls into view. */
export function Mark({ children }: { children: React.ReactNode }) {
  const ref = useRef<HTMLSpanElement>(null);
  const [seen, setSeen] = useState(false);
  const reduced = useReducedMotion();
  const on = seen || reduced;
  useEffect(() => {
    const el = ref.current;
    if (!el || reduced) return;
    const io = new IntersectionObserver((es) => { if (es[0].isIntersecting) { setSeen(true); io.disconnect(); } }, { threshold: 0.25 });
    io.observe(el);
    return () => io.disconnect();
  }, [reduced]);
  return <span ref={ref} className={`hb-marker${on ? " hb-marker--on" : ""}`}>{children}</span>;
}
