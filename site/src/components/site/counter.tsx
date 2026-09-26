"use client";
import { useEffect, useRef } from "react";
import { useReducedMotion } from "@/components/scenes/use-reduced-motion";
import { easeOutCubic, formatCounter, parseCounter } from "@/lib/counter";

/**
 * Renders the real final value in the server HTML, then counts up to it once when scrolled into view.
 * Reduced motion and values that are not a single number ("4m 20s") stay static.
 */
export function Counter({ value, className, duration = 1400 }: { value: string; className?: string; duration?: number }) {
  const el = useRef<HTMLSpanElement>(null);
  const reduced = useReducedMotion();

  useEffect(() => {
    const node = el.current;
    const spec = parseCounter(value);
    if (!node || !spec || spec.target === 0 || reduced) return;
    node.textContent = formatCounter(0, spec);
    let raf = 0;
    const io = new IntersectionObserver(
      ([entry]) => {
        if (!entry.isIntersecting) return;
        io.disconnect();
        const start = performance.now();
        const tick = (now: number) => {
          const t = Math.min(1, (now - start) / duration);
          node.textContent = formatCounter(spec.target * easeOutCubic(t), spec);
          if (t < 1) raf = requestAnimationFrame(tick);
        };
        raf = requestAnimationFrame(tick);
      },
      { threshold: 0.6 },
    );
    io.observe(node);
    return () => {
      io.disconnect();
      cancelAnimationFrame(raf);
      node.textContent = value;
    };
  }, [value, duration, reduced]);

  return (
    <span ref={el} className={className} aria-label={value}>
      {value}
    </span>
  );
}
