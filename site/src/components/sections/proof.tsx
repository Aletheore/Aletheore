import Link from "next/link";
import { prefetchFor } from "@/data/site-links";
import { Counter } from "@/components/site/counter";
import { Badge } from "@/components/ui/badge";
import { home } from "@/content/home";

export function Proof() {
  const p = home.proof;
  return (
    <section aria-labelledby="proof-title" className="mx-auto max-w-[1080px] px-8 py-16">
      <p className="eyebrow eyebrow--section mb-3">{p.eyebrow}</p>
      <h2 id="proof-title" className="font-display text-[clamp(24px,3.2vw,34px)] font-bold leading-[1.1] tracking-[-0.015em]">{p.title}</h2>
      <p className="mt-4 max-w-[62ch] text-[15px] leading-[1.65] text-muted">
        {p.intro} <Link prefetch={prefetchFor(p.writeup.href)} href={p.writeup.href} className="text-ink underline">{p.writeup.label} &rarr;</Link>
      </p>
      <div className="mt-10 grid gap-px border border-line bg-line md:grid-cols-3">
        {p.repos.map((r) => (
          <article key={r.name} className="bg-raised p-6">
            <div className="flex items-baseline justify-between"><h3 className="font-display text-lg font-bold">{r.name}</h3><Badge>@ {r.commit}</Badge></div>
            <p className="mt-1 text-[12px] text-faint">{r.meta}</p>
            <dl className="mt-4 grid grid-cols-[1fr_auto] gap-x-4 gap-y-1.5 text-[13px]">
              {r.stats.map(([label, value]) => (<div key={label} className="contents"><dt className="text-muted">{label}</dt><dd className="text-right font-display font-semibold tabular-nums"><Counter value={value} /></dd></div>))}
            </dl>
          </article>
        ))}
      </div>
      <div className="crop-marks mt-12 border border-line bg-raised p-7">
        <p className="eyebrow eyebrow--section mb-2">{p.linux.eyebrow}</p>
        <h3 className="font-display text-2xl font-bold">{p.linux.title}</h3>
        <p className="mt-3 max-w-[64ch] text-[14px] leading-[1.65] text-muted">{p.linux.body}</p>
        <dl className="mt-6 grid gap-x-8 gap-y-3 sm:grid-cols-2 lg:grid-cols-3">
          {p.linux.stats.map((s) => (<div key={s.label} className="flex justify-between border-b border-dashed border-line-strong pb-2 text-[13px]"><dt className="text-muted">{s.label}</dt><dd className="font-display font-semibold tabular-nums"><Counter value={s.value} /></dd></div>))}
        </dl>
      </div>
      <div className="mt-12">
        <h3 className="font-display text-[20px] font-bold">{p.toon.title}</h3>
        <p className="mt-2 max-w-[62ch] text-[14px] leading-[1.65] text-muted">{p.toon.body}</p>
        <div className="mt-5 flex gap-10">
          {p.toon.stats.map((s) => (<div key={s.label}><div className="font-display text-[34px] font-bold tabular-nums text-stamp"><Counter value={s.value} /></div><div className="text-[12px] text-muted">{s.label}</div></div>))}
        </div>
        <p className="mt-6 text-[13.5px] text-muted">
          How it measures up against a real competitor, wins and losses both: <Link prefetch={prefetchFor(p.benchmarks.href)} href={p.benchmarks.href} className="text-ink underline">{p.benchmarks.label} &rarr;</Link>
        </p>
      </div>
    </section>
  );
}
