import { Button } from "@/components/ui/button";
import { evidenceChain } from "@/data/evidence-chain";
import { graphData as graph } from "@/data/graph";
import { getStartedHref } from "@/data/site-links";
import { GraphCanvas } from "./graph-canvas";

export function Hero() {
  const [endpoint, handler, symbol, owner, touched] = evidenceChain.steps;
  return (
    <section className="mx-auto grid max-w-[1080px] items-center gap-8 px-8 pb-10 pt-14 md:grid-cols-[1.05fr_0.95fr] md:pt-16">
      <div>
        <p className="eyebrow mb-3">Evidence-driven code intelligence</p>
        <h1 className="font-display text-[clamp(34px,5.4vw,52px)] font-bold leading-[1.06] tracking-[-0.02em]">
          AI code intelligence that has to <span className="text-stamp">show its work.</span>
        </h1>
        <p className="mt-5 max-w-[52ch] text-[16px] leading-[1.65] text-muted">
          Aletheore reviews, audits, and monitors repositories with every finding traced back to source evidence: file,
          line, symbol, owner, commit, dependency, and risk.
        </p>
        <div className="mt-7 flex flex-wrap gap-3">
          <Button asChild size="lg"><a href={getStartedHref}>Start Free Audit</a></Button>
          <Button asChild size="lg" variant="secondary"><a href="https://github.com/Aletheore/Aletheore" rel="noopener">View on GitHub</a></Button>
        </div>
        <p className="mt-5 flex flex-wrap gap-x-5 font-display text-[11.5px] text-faint">
          <span>Source-available</span><span>Local-first CLI</span><span>Hosted GitHub App</span>
        </p>
      </div>
      <div className="relative">
        <GraphCanvas
          graph={graph}
          interactive
          staticSrc="/hero-graph-static.svg"
          alt="Dependency graph of the Aletheore repository, drawn as a rotating wireframe"
        />
        <aside
          aria-label="A real resolved path from Aletheore's own repository"
          className="crop-marks absolute bottom-2 left-0 w-[min(100%,300px)] border border-line bg-raised/95 p-4 shadow-[0_18px_40px_rgba(22,20,15,0.08)] md:-left-6"
        >
          <p className="eyebrow mb-2">Resolved to code</p>
          <p className="font-display text-[13px] font-semibold">{endpoint.value}</p>
          <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[12px]">
            <dt className="text-faint">handled by</dt><dd className="font-display">{handler.value}</dd>
            <dt className="text-faint">symbol</dt><dd className="font-display">{symbol.value}</dd>
            <dt className="text-faint">owner</dt><dd className="font-display">{owner.value}</dd>
            <dt className="text-faint">commit</dt><dd className="font-display">{touched.value}</dd>
          </dl>
          <p className="mt-2 text-[10.5px] text-faint">From a scan of Aletheore&apos;s own repository.</p>
        </aside>
      </div>
    </section>
  );
}
