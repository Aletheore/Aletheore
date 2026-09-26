import { Hero } from "@/components/scenes/hero";
import { LiveGraph } from "@/components/scenes/live-graph";
import { ResolutionScene } from "@/components/scenes/resolution-scene";

export default function Home() {
  return (
    <>
      <Hero />
      <ResolutionScene />
      <LiveGraph />
    </>
  );
}
