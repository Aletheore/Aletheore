"use client";
import { useEffect, useRef, useState } from "react";
import { hallbook } from "@/data/hallbook";

/** A real SKILL.md header from the catalogue. The numbered marks pop in when it scrolls into view. */
export function AnnotatedSkill() {
  const ref = useRef<HTMLDivElement>(null);
  const [on, setOn] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const io = new IntersectionObserver((es) => { if (es[0].isIntersecting) { setOn(true); io.disconnect(); } }, { threshold: 0.25 });
    io.observe(el);
    return () => io.disconnect();
  }, []);
  const lines = hallbook.skill.head.split("\n");
  const marks: Record<number, number> = {};
  let fm = 0;
  lines.forEach((l, i) => {
    if (l.trim() === "---") fm++;
    if (/^source_url:/.test(l) && !marks[1]) marks[1] = i;
    else if (/^license:/.test(l) && !marks[2]) marks[2] = i;
    else if (/^aletheore:/.test(l) && !marks[4]) marks[4] = i;
    else if (fm >= 2 && /^#/.test(l) && !marks[3]) marks[3] = i;
  });
  const at = Object.fromEntries(Object.entries(marks).map(([k, v]) => [v, Number(k)]));
  return (
    <div ref={ref} className={`hb-sheet${on ? " hb-sheet--on" : ""}`} role="img" aria-label={`Header of the ${hallbook.skill.id} skill file with four checks marked`}>
      {lines.map((l, i) => (
        <span key={i} className={`hb-ln${at[i] ? " hb-hl" : ""}`}>
          {at[i] ? <b className="hb-mk" style={{ transitionDelay: `${at[i] * 260 + 400}ms` }}>{at[i]}</b> : null}
          {(l.length > 92 ? l.slice(0, 92) + "…" : l) || " "}
        </span>
      ))}
    </div>
  );
}
