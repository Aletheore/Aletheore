import { home } from "@/content/home";

export function Why() {
  const w = home.why;
  return (
    <section className="mx-auto max-w-[1080px] px-8 py-16">
      <div className="rule-dashed pt-12">
        <p className="eyebrow eyebrow--section mb-3">{w.eyebrow}</p>
        <h2 className="max-w-[24ch] font-display text-[clamp(26px,3.6vw,40px)] font-bold leading-[1.1] tracking-[-0.015em]">{w.title}</h2>
        <p className="mt-5 max-w-[62ch] text-[15.5px] leading-[1.7] text-muted">{w.body}</p>
      </div>
    </section>
  );
}
