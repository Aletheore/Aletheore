"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { evidenceChain } from "@/data/evidence-chain";
import { graphData as graph } from "@/data/graph";
import { refreshAfterFonts } from "@/lib/fonts";
import { highlightForStep } from "@/lib/highlight";
import { stepForProgress } from "@/lib/scroll-steps";
import { cn } from "@/lib/utils";
import { GraphCanvas } from "./graph-canvas";
import { useReducedMotion } from "./use-reduced-motion";

export function ResolutionScene() {
  const root = useRef<HTMLElement>(null);
  const [scrollStep, setActive] = useState(0);
  const reduced = useReducedMotion();
  const steps = evidenceChain.steps;
  // Without the pinned scroll (reduced motion, phones) the scene rests on its final step, the full chain.
  const active = reduced ? steps.length - 1 : scrollStep;
  const activeIds = useMemo(() => highlightForStep(graph, evidenceChain, active), [active]);

  useEffect(() => {
    const el = root.current;
    if (!el) return;
    if (reduced) return;
    let cancelled = false;
    let revert: (() => void) | null = null;
    (async () => {
      const { gsap } = await import("gsap");
      const { ScrollTrigger } = await import("gsap/ScrollTrigger");
      if (cancelled) return;
      gsap.registerPlugin(ScrollTrigger);
      const mm = gsap.matchMedia();
      mm.add("(min-width: 768px)", () => {
        const trigger = ScrollTrigger.create({
          trigger: el,
          start: "top top",
          end: () => `+=${steps.length * 55}%`,
          pin: true,
          anticipatePin: 1,
          onUpdate: (self) =>
            setActive((prev) => {
              const next = stepForProgress(self.progress, steps.length);
              return next === prev ? prev : next;
            }),
        });
        return () => trigger.kill();
      });
      mm.add("(max-width: 767px)", () => {
        setActive(steps.length - 1);
      });
      void refreshAfterFonts(document.fonts, () => ScrollTrigger.refresh());
      revert = () => mm.revert();
    })();
    return () => {
      cancelled = true;
      revert?.();
    };
  }, [reduced, steps.length]);

  return (
    // ScrollTrigger wraps the pinned element in a spacer, so React must own a parent above it: removing the
    // section directly from its old parent would throw once the spacer sits in between.
    <div>
    <section
      ref={root}
      data-active-step={active}
      aria-labelledby="resolution-title"
      className="relative mx-auto w-full max-w-[1080px] px-8 py-16 md:flex md:min-h-screen md:items-center md:py-0"
    >
      <div className="grid w-full gap-8 md:grid-cols-[1fr_1fr] md:items-center">
        <div>
          <p className="eyebrow eyebrow--section mb-3">How resolution works</p>
          <h2 id="resolution-title" className="font-display text-[clamp(26px,3.6vw,38px)] font-bold leading-[1.1] tracking-[-0.015em]">
            Not &quot;it broke.&quot; A chain you can walk.
          </h2>
          <p className="mt-4 max-w-[48ch] text-[15px] leading-[1.65] text-muted">
            Every alert Aletheore raises is a resolved path, not a guess. This one is real: it was read from a scan of
            Aletheore&apos;s own repository.
          </p>
          <ol className="mt-8 flex flex-col gap-2">
            {steps.map((s, i) => (
              <li
                key={s.id}
                aria-current={i === active ? "step" : undefined}
                className={cn(
                  "rounded-[4px] border px-4 py-3 transition-colors duration-300",
                  i === active ? "border-stamp bg-stamp-soft" : "border-line bg-raised",
                )}
              >
                <div className="flex items-baseline justify-between gap-4">
                  <span className="font-display text-[11px] text-faint">{s.label}</span>
                  <span className={cn("font-display text-[13px] font-semibold", i === active && "text-stamp")}>{s.value}</span>
                </div>
                <p className="mt-1 text-[12.5px] leading-[1.55] text-muted">{s.detail}</p>
              </li>
            ))}
          </ol>
        </div>
        <div className="crop-marks mx-2 md:mx-0">
          <GraphCanvas
            graph={graph}
            activeIds={activeIds}
            focus
            activeLabel={steps[active].value}
            staticSrc="/hero-graph-static.svg"
            alt="The same dependency graph with the modules on the current step of the chain highlighted"
          />
        </div>
      </div>
    </section>
    </div>
  );
}
