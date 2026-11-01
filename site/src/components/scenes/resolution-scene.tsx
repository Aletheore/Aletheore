"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { evidenceChain } from "@/data/evidence-chain";
import { graphData as graph } from "@/data/graph";
import { refreshAfterFonts } from "@/lib/fonts";
import { sceneForStep } from "@/lib/highlight";
import { stepForProgress } from "@/lib/scroll-steps";
import { cn } from "@/lib/utils";
import { GraphCanvas } from "./graph-canvas";
import { useReducedMotion } from "./use-reduced-motion";

export function ResolutionScene() {
  const root = useRef<HTMLElement>(null);
  const [scrollStep, setActive] = useState(0);
  const [mobile, setMobile] = useState<boolean | null>(null);
  const reduced = useReducedMotion();
  const steps = evidenceChain.steps;
  const active = reduced && mobile !== true ? steps.length - 1 : scrollStep;
  const scene = useMemo(() => sceneForStep(graph, evidenceChain, active), [active]);

  useEffect(() => {
    const query = window.matchMedia("(max-width: 767px)");
    const update = () => setMobile(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);

  useEffect(() => {
    const el = root.current;
    if (!el || mobile === null) return;
    if (mobile) {
      const cards = Array.from(el.querySelectorAll<HTMLElement>("[data-chain-step]"));
      let frame = 0;
      let cancelled = false;
      const update = () => {
        frame = 0;
        const bounds = el.getBoundingClientRect();
        if (bounds.bottom < 0 || bounds.top > window.innerHeight) return;
        const target = window.innerHeight * 0.72;
        const nearest = cards.reduce((best, card, i) =>
          Math.abs(card.getBoundingClientRect().top - target) < Math.abs(cards[best].getBoundingClientRect().top - target) ? i : best, 0);
        setActive(nearest);
      };
      const onScroll = () => { if (!frame) frame = requestAnimationFrame(update); };
      update();
      refreshAfterFonts(document.fonts, () => { if (!cancelled) onScroll(); }).catch(() => {});
      window.addEventListener("scroll", onScroll, { passive: true });
      window.addEventListener("resize", onScroll);
      return () => { cancelled = true; window.removeEventListener("scroll", onScroll); window.removeEventListener("resize", onScroll); cancelAnimationFrame(frame); };
    }
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
      // fonts.ready rejects only if font loading itself fails; the refresh still ran, so there is nothing to handle.
      refreshAfterFonts(document.fonts, () => ScrollTrigger.refresh()).catch(() => {});
      revert = () => mm.revert();
    })();
    return () => {
      cancelled = true;
      revert?.();
    };
  }, [reduced, steps.length, mobile]);

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
      <div className="grid w-full grid-cols-1 gap-x-8 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] md:items-center">
        <div className="md:col-start-1 md:row-start-1">
          <p className="eyebrow eyebrow--section mb-3">How resolution works</p>
          <h2 id="resolution-title" className="font-display text-[clamp(26px,3.6vw,38px)] font-bold leading-[1.1] tracking-[-0.015em]">
            Not &quot;it broke.&quot; A chain you can walk.
          </h2>
          <p className="mt-4 max-w-[48ch] text-[15px] leading-[1.65] text-muted">
            Every alert Aletheore raises is a resolved path, not a guess. This one is real: it was read from a scan of
            Aletheore&apos;s own repository.
          </p>
        </div>
        <div className="sticky top-[76px] z-10 my-6 flex items-center gap-4 border border-line bg-paper p-3 shadow-sm md:relative md:top-auto md:col-start-2 md:row-span-2 md:row-start-1 md:my-0 md:block md:border-0 md:bg-transparent md:p-0 md:shadow-none">
          <div className="crop-marks w-28 shrink-0 md:mx-0 md:w-full">
            <GraphCanvas graph={graph} activeIds={scene.ids} edgeMode={scene.edges} zoom={scene.zoom} orbit={scene.orbit} focus activeLabel={steps[active].value} staticSrc="/hero-graph-static.svg" alt="Dependency graph highlighting the current step of the chain" />
          </div>
          <div className="min-w-0 md:hidden" aria-hidden="true">
            <p className="font-display text-[11px] text-stamp">{active + 1} / {steps.length} · {steps[active].label}</p>
            <p className="mt-1 break-words font-display text-[13px] font-semibold">{steps[active].value}</p>
            <p className="mt-1 text-[12px] text-muted">Scroll to follow the chain</p>
          </div>
        </div>
        <ol className="flex flex-col gap-2 md:col-start-1 md:row-start-2 md:mt-8">
            {steps.map((s, i) => (
              <li
                key={s.id}
                data-chain-step
                aria-current={i === active ? "step" : undefined}
                className={cn(
                  "rounded-[4px] border px-4 py-3 transition-colors duration-300",
                  i === active ? "border-stamp bg-stamp-soft" : "border-line bg-raised",
                )}
              >
                <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
                  <span className="font-display text-[11px] text-faint">{s.label}</span>
                  <span className={cn("break-all text-right font-display text-[13px] font-semibold", i === active && "text-stamp")}>{s.value}</span>
                </div>
                <p className="mt-1 text-[12.5px] leading-[1.55] text-muted">{s.detail}</p>
              </li>
            ))}
        </ol>
      </div>
    </section>
    </div>
  );
}
