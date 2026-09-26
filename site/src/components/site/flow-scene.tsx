"use client";
import { useEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";

export type FlowStep = { id: string; k: string; title: string; body: string };
export type PanelLine = { text: string; kind?: "cmd" | "out" };
export type Panel = { label: string; lines: PanelLine[] };

/**
 * Three steps on the left; the terminal on the right stays in view and changes with the step in the middle
 * of the screen. On narrow screens each step carries its own terminal instead.
 */
export function FlowScene({ steps, panels }: { steps: readonly FlowStep[]; panels: Record<string, Panel> }) {
  const [active, setActive] = useState(0);
  const refs = useRef<(HTMLLIElement | null)[]>([]);

  useEffect(() => {
    const els = refs.current.filter((e): e is HTMLLIElement => e !== null);
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) if (e.isIntersecting) setActive(Number((e.target as HTMLElement).dataset.index));
      },
      { rootMargin: "-42% 0px -42% 0px" },
    );
    els.forEach((e) => io.observe(e));
    return () => io.disconnect();
  }, []);

  const current = steps[active];
  return (
    <div className="grid gap-10 md:grid-cols-[1fr_1.05fr]">
      <ol className="flex flex-col">
        {steps.map((s, i) => (
          <li
            key={s.id}
            ref={(el) => { refs.current[i] = el; }}
            data-index={i}
            aria-current={i === active ? "step" : undefined}
            className={cn("relative border-l border-dashed border-line-strong pb-16 pl-8 transition-opacity duration-300 last:pb-4 md:min-h-[46vh]", i === active ? "opacity-100" : "md:opacity-40")}
          >
            <span className={cn("absolute -left-[5px] top-[7px] size-[9px] transition-colors", i === active ? "bg-stamp" : "bg-line-strong")} aria-hidden="true" />
            <p className="font-display text-[12.5px] text-stamp">{s.k}</p>
            <h3 className="mt-2 font-display text-[21px] font-bold leading-tight">{s.title}</h3>
            <p className="mt-3 max-w-[46ch] text-[14.5px] leading-[1.7] text-muted">{s.body}</p>
            <div className="mt-6 md:hidden"><PanelView panel={panels[s.id]} /></div>
          </li>
        ))}
      </ol>
      <div className="hidden md:block">
        <div className="sticky top-28">
          <PanelView key={current.id} panel={panels[current.id]} />
        </div>
      </div>
    </div>
  );
}

function PanelView({ panel }: { panel: Panel }) {
  return (
    <div className="overflow-hidden rounded-[6px] bg-ink text-[#e8e2d4] shadow-[0_18px_40px_-24px_rgba(22,20,15,0.6)]">
      <div className="flex items-center gap-2 border-b border-white/10 px-4 py-2.5">
        <span className="size-2 rounded-full bg-white/20" aria-hidden="true" />
        <span className="size-2 rounded-full bg-white/20" aria-hidden="true" />
        <span className="size-2 rounded-full bg-white/20" aria-hidden="true" />
        <span className="ml-2 font-display text-[11.5px] text-[#9a9384]">{panel.label}</span>
      </div>
      <pre className="max-h-[26rem] overflow-auto p-5 font-display text-[12px] leading-[1.75]">
        {panel.lines.map((l, i) => (
          <div key={i} className={cn("term-line", l.kind === "cmd" ? "text-paper" : "text-[#b6b09f]")} style={{ animationDelay: `${Math.min(i, 24) * 45}ms` }}>
            {l.kind === "cmd" ? <span className="mr-2 text-stamp" aria-hidden="true">$</span> : null}
            {l.text}
          </div>
        ))}
      </pre>
    </div>
  );
}
