"use client";
import { useEffect, useRef, useState } from "react";
import { useReducedMotion } from "@/components/scenes/use-reduced-motion";

const AGENTS = ["Claude Code", "Codex", "OpenCode"];
const SKILLS = ["debugging", "code review", "stripe", "seo", "terraform", "testing"];

/** Left: every agent holds its own copy of every skill. Right: every agent asks one place. Lines draw in when scrolled into view. */
export function ConnectDiagram() {
  const ref = useRef<HTMLDivElement>(null);
  const [seen, setSeen] = useState(false);
  const reduced = useReducedMotion();
  const on = seen || reduced;
  useEffect(() => {
    const el = ref.current;
    if (!el || reduced) return;
    const io = new IntersectionObserver((es) => { if (es[0].isIntersecting) { setSeen(true); io.disconnect(); } }, { threshold: 0.3 });
    io.observe(el);
    return () => io.disconnect();
  }, [reduced]);
  const ay = (i: number) => 52 + i * 78;      // agent rows
  const sy = (j: number) => 30 + j * 40;      // skill rows
  return (
    <div ref={ref} className={`hb-diagram${on ? " hb-diagram--on" : ""}`} role="img"
      aria-label="Left: three agents each hold their own copy of six skills, eighteen copies to keep in step. Right: the same three agents all ask Aletheore, which holds every skill once.">
      <svg viewBox="0 0 1000 330" className="w-full" fontFamily="var(--font-display)">
        {/* LEFT: the usual way */}
        <text x="0" y="14" className="fill-faint" fontSize="12" letterSpacing="1.5">THE USUAL WAY</text>
        {AGENTS.map((a, i) => SKILLS.map((_, j) => (
          <line key={`${i}-${j}`} x1="130" y1={ay(i)} x2="330" y2={sy(j) + 12} pathLength="1" className="hb-line hb-line--tangle" style={{ transitionDelay: `${(i * 6 + j) * 28}ms` }} />
        )))}
        {AGENTS.map((a, i) => (
          <g key={a}><rect x="0" y={ay(i) - 18} width="130" height="36" rx="4" className="fill-raised stroke-line-strong" strokeWidth="1.2" />
            <text x="65" y={ay(i) + 4} textAnchor="middle" fontSize="12" className="fill-ink">{a}</text></g>
        ))}
        {SKILLS.map((s, j) => (
          <g key={s}><rect x="330" y={sy(j)} width="150" height="24" rx="3" className="fill-paper stroke-line-strong" strokeWidth="1" strokeDasharray="3 3" />
            <text x="405" y={sy(j) + 16} textAnchor="middle" fontSize="11" className="fill-muted">{s}</text></g>
        ))}
        <text x="0" y="318" className="fill-muted" fontSize="12">3 agents × 6 skills = 18 copies to keep in step</text>

        {/* RIGHT: with Hallbook */}
        <text x="540" y="14" className="fill-stamp" fontSize="12" letterSpacing="1.5">WITH HALLBOOK</text>
        {AGENTS.map((a, i) => (
          <line key={`h${i}`} x1="670" y1={ay(i)} x2="770" y2="165" pathLength="1" className="hb-line hb-line--hub" style={{ transitionDelay: `${i * 140 + 300}ms` }} />
        ))}
        {AGENTS.map((a, i) => (
          <g key={`ha${i}`}><rect x="540" y={ay(i) - 18} width="130" height="36" rx="4" className="fill-raised stroke-line-strong" strokeWidth="1.2" />
            <text x="605" y={ay(i) + 4} textAnchor="middle" fontSize="12" className="fill-ink">{a}</text></g>
        ))}
        <g className="hb-hub"><rect x="770" y="125" width="230" height="80" rx="6" className="fill-ink" />
          <text x="885" y="160" textAnchor="middle" fontSize="15" fontWeight="700" className="fill-paper">Aletheore</text>
          <text x="885" y="182" textAnchor="middle" fontSize="11" className="fill-paper" opacity="0.7">every skill, once</text></g>
        <text x="540" y="318" className="fill-ink" fontSize="12" fontWeight="600">3 agents, 1 connection, 0 copies</text>
      </svg>
    </div>
  );
}
