import Link from "next/link";
import { prefetchFor } from "@/data/site-links";
import { Button } from "@/components/ui/button";
import { home } from "@/content/home";

export function Teams() {
  const t = home.teams;
  return (
    <section aria-labelledby="teams-title" className="mx-auto max-w-[1080px] px-8 py-16">
      <p className="eyebrow eyebrow--section mb-3">{t.eyebrow}</p>
      <h2 id="teams-title" className="max-w-[24ch] font-display text-[clamp(24px,3.2vw,34px)] font-bold leading-[1.1] tracking-[-0.015em]">{t.title}</h2>
      <ol className="mt-10 grid gap-px border border-line bg-line md:grid-cols-4">
        {t.steps.map((s) => (
          <li key={s.k} className="bg-raised p-5">
            <p className="font-display text-[11px] text-stamp">{s.k}</p>
            <h3 className="mt-2 text-[14.5px] font-semibold">{s.title}</h3>
            <p className="mt-2 text-[12.5px] leading-[1.6] text-muted">{s.body}</p>
          </li>
        ))}
      </ol>
      <div className="mt-8 flex flex-wrap items-center gap-3">
        <Button asChild><a href={t.primary.href} rel="noopener">{t.primary.label}</a></Button>
        <Button asChild variant="secondary"><Link prefetch={prefetchFor(t.secondary.href)} href={t.secondary.href}>{t.secondary.label}</Link></Button>
        <span className="text-[12.5px] text-faint">{t.note}</span>
      </div>
    </section>
  );
}
