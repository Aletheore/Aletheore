"use client";
import { useEffect, useRef, useState } from "react";
import { useReducedMotion } from "@/components/scenes/use-reduced-motion";
import { EXAMPLE_LABEL, exampleQueries, hallbook, type HbCard, type HbFind } from "@/data/hallbook";
import { liveSearchEnabled, lookup, STATUS_RECORDED } from "@/data/hallbook-search";
import { Mark } from "./mark";
import { Odometer } from "./odometer";

const LIVE = liveSearchEnabled();
const POS = { none: "16.6%", low: "49.8%", high: "83%" } as const;
const NOTE = {
  none: ["NONE", "Nothing relevant. A good agent carries on without a skill."],
  low: ["LOW", "No clear match. Candidates only. The agent checks them and carries on if none fit."],
  high: ["HIGH", "Confident. The whole skill is handed to the agent in this one call."],
} as const;
const ASKS = [
  "my stripe webhook handler processes events twice",
  "review this diff for security problems",
  "write a clear commit message for these changes",
  "the nightly job drifts by an hour twice a year",
];

function Card({ s, i, top, extra }: { s: HbCard; i: number; top?: boolean; extra?: React.ReactNode }) {
  const tilt = (i % 2 ? 1 : -1) * (0.6 + (i % 3) * 0.5);
  return (
    <article className={`hb-card${top ? " hb-card--top" : ""}`} style={{ ["--i" as string]: i, ["--r" as string]: top ? "0deg" : `${tilt}deg` }}>
      <div>
        <p className="hb-call">{s.id}</p>
        <h3 className="font-display text-[17px] font-bold leading-[1.2] tracking-[-0.01em]">{s.title}</h3>
        <p className="mt-2 line-clamp-4 text-[12.5px] leading-[1.55] text-muted">{s.description}</p>
      </div>
      {extra}
      {s.alternates?.length ? <span className="hb-alt" title={s.alternates.join(", ")}>+{s.alternates.length} alternate{s.alternates.length > 1 ? "s" : ""}</span> : null}
      <span className="hb-lic">{s.license || "licence"}</span>
    </article>
  );
}

export function Hero() {
  const [q, setQ] = useState("");
  const [typed, setTyped] = useState("");
  const [res, setRes] = useState<HbFind | null>(null);
  const [status, setStatus] = useState(STATUS_RECORDED);
  const [stamped, setStamped] = useState(false);
  const stop = useRef(false);
  const seq = useRef(0);
  const reduced = useReducedMotion();

  useEffect(() => {
    const t = setTimeout(() => setStamped(true), 1900);
    return () => clearTimeout(t);
  }, []);

  useEffect(() => {
    if (reduced) return;
    let alive = true;
    (async () => {
      let i = 0;
      while (alive && !stop.current) {
        const t = ASKS[i++ % ASKS.length];
        for (let c = 1; c <= t.length && alive && !stop.current; c++) { setTyped(t.slice(0, c)); await new Promise((r) => setTimeout(r, 38)); }
        await new Promise((r) => setTimeout(r, 1500));
        for (let c = t.length; c >= 0 && alive && !stop.current; c--) { setTyped(t.slice(0, c)); await new Promise((r) => setTimeout(r, 14)); }
      }
      // The user started typing mid-animation: leave a whole example as the placeholder, never a half-typed one.
      if (alive) setTyped(ASKS[0]);
    })();
    return () => { alive = false; };
  }, [reduced]);

  async function run(text: string) {
    const my = ++seq.current;
    if (text.trim().length < 3) { setRes(null); setStatus(STATUS_RECORDED); return; }
    setStatus(LIVE ? "asking…" : STATUS_RECORDED);
    try {
      const out = await lookup(text, LIVE);
      if (my === seq.current) { setRes(out.res); setStatus(out.status); }
    } catch {
      // A failed lookup must not leave "asking…" on screen.
      if (my === seq.current) { setRes(null); setStatus(STATUS_RECORDED); }
    }
  }
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);
  function onType(v: string) { stop.current = true; setQ(v); clearTimeout(timer.current); timer.current = setTimeout(() => run(v), 160); }

  const c = res?.confidence;
  const cards = c === "high" ? res?.alternatives ?? [] : c === "low" ? res?.candidates ?? [] : [];
  return (
    <header className="mx-auto max-w-[1080px] px-8 pb-6 pt-14 md:pt-16">
      <p className="eyebrow">A library of curated agent skills, bundled with Aletheore</p>
      <div className="relative">
        <div className="hb-count font-display font-bold" aria-hidden="false">
          <Odometer value={hallbook.stats.total} />
        </div>
        <div className={`hb-stamp${stamped ? " hb-stamp--hit" : ""}`} aria-hidden="true">
          CHECKED<small>LICENSE ALLOWLIST · PINNED SOURCE</small>
        </div>
      </div>
      <h1 className="mt-6 max-w-[24ch] text-balance font-display text-[clamp(26px,3.6vw,42px)] font-bold leading-[1.1] tracking-[-0.02em]">
        Skills your agent can read <span className="text-stamp">right now.</span>
      </h1>
      <p className="mt-4 max-w-[62ch] text-[15.5px] leading-[1.7] text-muted">
        <Mark>Searching the catalogue needs no download, no sign-in and no network call.</Mark> Hallbook lives inside Aletheore&apos;s MCP server. Your agent describes the task once and gets the right skill back, or an honest &ldquo;nothing here fits&rdquo;. {LIVE ? "This box runs the real ranking." : "This box replays recorded answers from the real ranking."}
      </p>

      <div className="crop-marks mt-9 max-w-[860px] bg-raised p-5">
        <div className="mb-4 flex justify-between"><span className="eyebrow">Request slip</span><span className="eyebrow text-faint">{status}</span></div>
        <label className="sr-only" htmlFor="hb-q">What is your agent about to do?</label>
        <input
          id="hb-q" value={q} onChange={(e) => onType(e.target.value)} placeholder={reduced ? ASKS[0] : typed} autoComplete="off" spellCheck={false}
          className="w-full border-0 border-b-2 border-ink bg-transparent py-1.5 font-display text-[clamp(17px,2.2vw,22px)] font-medium text-ink outline-none placeholder:text-faint focus:border-stamp"
        />
        <p className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-faint">
          <span>try:</span>
          {exampleQueries.map((e) => (
            <button key={e} type="button" onClick={() => { stop.current = true; clearTimeout(timer.current); setQ(e); run(e); }} className="underline decoration-dotted underline-offset-4 hover:text-stamp">
              {EXAMPLE_LABEL[e] ?? e}
            </button>
          ))}
        </p>
      </div>

      <div className={`hb-gauge mt-6 max-w-[860px]${c ? " hb-gauge--on" : ""}`}>
        <div className="relative grid h-[34px] grid-cols-3 border border-line-strong bg-raised text-center font-display text-[11px] font-medium leading-[32px] tracking-[0.14em] text-faint">
          <span>NONE</span><span className="border-x border-dashed border-line-strong">LOW</span><span>HIGH</span>
          <i className="hb-needle" style={{ left: c ? POS[c] : POS.low }} />
        </div>
        {c ? <p className="mt-2.5 font-display text-[12.5px] text-muted"><b className="text-ink">{NOTE[c][0]}.</b> {NOTE[c][1]}</p> : null}
      </div>

      <div className="mt-5 grid gap-4 [grid-template-columns:repeat(auto-fill,minmax(250px,1fr))]">
        {c === "high" && res?.top ? (
          <Card s={res.top} i={0} top extra={
            <div>
              <p className="mb-1.5 font-display text-[11px] font-medium tracking-[0.1em] text-success">HANDED TO THE AGENT · {(res.chars ?? 0).toLocaleString()} characters</p>
              <pre className="hb-preview">{res.preview}</pre>
            </div>} />
        ) : null}
        {cards.slice(0, c === "high" ? 3 : 5).map((s, i) => <Card key={s.id} s={s} i={i + 1} />)}
      </div>
    </header>
  );
}
