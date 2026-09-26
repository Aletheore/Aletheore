"use client";
import { useEffect, useRef } from "react";
import { useReducedMotion } from "@/components/scenes/use-reduced-motion";
import { barWidth } from "@/lib/bars";
import { cn } from "@/lib/utils";

export type BarRow = {
  label: string;
  value: number;
  /** Text shown at the end of the bar, e.g. "53.4%". */
  display: string;
  note?: string;
  /** Marks our own rows in the accent colour. */
  ours?: boolean;
};

/**
 * Horizontal bars. The server HTML carries the real widths; on first entry into view they grow from 0.
 * Reduced motion keeps them static.
 */
export function BarList({ rows, max = 100, caption }: { rows: BarRow[]; max?: number; caption: string }) {
  const list = useRef<HTMLUListElement>(null);
  const reduced = useReducedMotion();

  useEffect(() => {
    const el = list.current;
    if (!el || reduced) return;
    const fills = [...el.querySelectorAll<HTMLElement>("[data-fill]")];
    fills.forEach((f) => { f.style.transition = "none"; f.style.width = "0%"; });
    void el.offsetWidth;
    const io = new IntersectionObserver(
      ([entry]) => {
        if (!entry.isIntersecting) return;
        io.disconnect();
        fills.forEach((f, i) => {
          f.style.transition = `width 900ms cubic-bezier(0.22, 1, 0.36, 1) ${i * 70}ms`;
          f.style.width = f.dataset.width ?? "0%";
        });
      },
      { threshold: 0.35 },
    );
    io.observe(el);
    return () => {
      io.disconnect();
      fills.forEach((f) => { f.style.transition = "none"; f.style.width = f.dataset.width ?? "0%"; });
    };
  }, [reduced, rows, max]);

  return (
    <figure>
      <figcaption className="mb-3 font-display text-[12.5px] text-muted">{caption}</figcaption>
      <ul ref={list} className="flex flex-col gap-2.5">
        {rows.map((r) => {
          const w = `${barWidth(r.value, max).toFixed(2)}%`;
          return (
            <li key={r.label} className="grid grid-cols-[minmax(0,9.5rem)_1fr_auto] items-center gap-3 text-[13px] sm:grid-cols-[11rem_1fr_auto]">
              <span className={cn("truncate", r.ours ? "font-semibold text-ink" : "text-muted")} title={r.label}>{r.label}</span>
              <span className="h-3 rounded-[2px] bg-line/70" aria-hidden="true">
                <span
                  data-fill
                  data-width={w}
                  className={cn("block h-full rounded-[2px]", r.ours ? "bg-stamp" : "bg-ink/70")}
                  style={{ width: w }}
                />
              </span>
              <span className="min-w-[4.5rem] text-right font-display font-semibold tabular-nums">{r.display}</span>
              {r.note ? <span className="col-span-3 -mt-1.5 pl-0 text-[11.5px] text-faint sm:pl-[12rem]">{r.note}</span> : null}
            </li>
          );
        })}
      </ul>
    </figure>
  );
}
