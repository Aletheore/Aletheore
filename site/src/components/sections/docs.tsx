import { home } from "@/content/home";

export function Docs() {
  const d = home.docs;
  return (
    <section aria-labelledby="docs-title" className="mx-auto max-w-[1080px] px-8 py-16">
      <p className="eyebrow eyebrow--section mb-3">{d.eyebrow}</p>
      <h2 id="docs-title" className="max-w-[26ch] font-display text-[clamp(24px,3.2vw,34px)] font-bold leading-[1.1] tracking-[-0.015em]">{d.title}</h2>
      <p className="mt-4 max-w-[68ch] text-[15px] leading-[1.7] text-muted">{d.body}</p>
      <div className="rule-dashed mt-12 pt-10">
        <p className="eyebrow eyebrow--section mb-3">{d.honestEyebrow}</p>
        <h3 className="font-display text-[22px] font-bold tracking-[-0.01em]">{d.honestTitle}</h3>
        <p className="mt-3 max-w-[60ch] text-[14.5px] leading-[1.65] text-muted">{d.honestBody}</p>
        <div className="mt-8 grid gap-px border border-line bg-line md:grid-cols-2">
          {d.points.map((p) => (
            <article key={p.title} className="bg-raised p-6">
              <h4 className="font-display text-[14px] font-bold">{p.title}</h4>
              <p className="mt-2 text-[13px] leading-[1.6] text-muted">{p.body}</p>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}
