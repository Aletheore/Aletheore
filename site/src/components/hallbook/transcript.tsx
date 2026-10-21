"use client";
import { useEffect, useRef, useState } from "react";
import { hallbook } from "@/data/hallbook";

const Q = "plan a zero downtime database migration";

/** A recorded call: the answer is the real one from the catalogue (data/hallbook.json), typed out when scrolled into view. */
export function Transcript() {
  // A missing recording (a regenerated snapshot without this query) renders as a no-answer rather than crashing the page.
  const d = hallbook.examples[Q] ?? { confidence: "none" as const };
  const lines: { who?: "you" | "agent"; text: string; typed?: boolean }[] = [
    { who: "you", text: `Use the Aletheore skills available to you. ${Q}.`, typed: true },
    { who: "agent", text: `aletheore_find_skill("${Q}")`, typed: true },
    { text: `  confidence: ${d.confidence}` },
    { text: `  top: ${d.top?.id}  (${d.top?.license})` },
    { text: `  skill: ${(d.chars ?? 0).toLocaleString()} characters, in this same response` },
    { who: "agent", text: "Following the skill. Adapting it to this project.", typed: true },
  ];
  const box = useRef<HTMLDivElement>(null);
  const [shown, setShown] = useState<string[]>([]);
  const started = useRef(false);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
    const go = async () => {
      if (started.current) return;
      started.current = true;
      const out: string[] = [];
      for (const l of lines) {
        const prefix = l.who === "you" ? "you     " : l.who === "agent" ? "agent   " : "";
        if (l.typed && !reduced) {
          for (let i = 1; i <= l.text.length; i++) { setShown([...out, prefix + l.text.slice(0, i)]); await new Promise((r) => setTimeout(r, 14)); }
        }
        out.push(prefix + l.text); setShown([...out]);
        if (!reduced) await new Promise((r) => setTimeout(r, l.typed ? 380 : 260));
      }
    };
    const io = new IntersectionObserver((es) => { if (es[0].isIntersecting) { go(); io.disconnect(); } }, { threshold: 0.3 });
    io.observe(el);
    return () => io.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div ref={box} className="min-h-[250px] overflow-x-auto rounded-[6px] bg-ink p-5 font-display text-[12.5px] leading-[1.85] text-[#e8e2d4] shadow-[0_18px_40px_-24px_rgba(22,20,15,0.6)]" aria-label="Recorded example of an agent asking Hallbook for a skill">
      {shown.map((l, i) => (
        <div key={i} className={l.startsWith("you") ? "text-[#9fd3b8]" : l.startsWith("agent") ? "text-[#f0c987]" : "text-[#8fb7e8]"}><pre className="m-0 whitespace-pre-wrap font-[inherit]">{l}</pre></div>
      ))}
      <span className="hb-cursor" aria-hidden="true" />
    </div>
  );
}
