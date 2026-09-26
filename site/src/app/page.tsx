import { Hero } from "@/components/scenes/hero";
import { LiveGraph } from "@/components/scenes/live-graph";
import { ResolutionScene } from "@/components/scenes/resolution-scene";
import { Cta } from "@/components/sections/cta";
import { Docs } from "@/components/sections/docs";
import { Proof } from "@/components/sections/proof";
import { Surfaces } from "@/components/sections/surfaces";
import { Teams } from "@/components/sections/teams";
import { Why } from "@/components/sections/why";
import { home } from "@/content/home";

export default function Home() {
  return (
    <>
      <Hero />
      <div className="mx-auto max-w-[1080px] border-y border-dashed border-line-strong px-8 py-4">
        <p className="flex flex-wrap items-center gap-x-5 gap-y-1 font-display text-[12px] text-muted">
          <span className="text-faint">{home.evidenceStrip.label}</span>
          {home.evidenceStrip.items.map((i) => (<span key={i} className="text-ink">{i}</span>))}
        </p>
      </div>
      <ResolutionScene />
      <Surfaces />
      <LiveGraph />
      <Why />
      <Docs />
      <Teams />
      <Proof />
      <Cta />
    </>
  );
}
