import { home } from "@/content/home";

export function Cta() {
  const c = home.cta;
  return (
    <section className="mx-auto max-w-[1080px] px-8 py-16">
      <div className="grid gap-10 border border-line bg-raised p-8 md:grid-cols-[1.1fr_0.9fr] md:p-10">
        <div>
          <h2 className="font-display text-[clamp(24px,3.2vw,34px)] font-bold leading-[1.1] tracking-[-0.015em]">{c.title}</h2>
          <p className="mt-4 max-w-[54ch] text-[14.5px] leading-[1.7] text-muted">{c.body}</p>
          <ul className="mt-5 flex flex-col gap-2 text-[13.5px]">{c.points.map((x) => (<li key={x} className="flex gap-2.5"><span className="text-stamp">&rarr;</span>{x}</li>))}</ul>
        </div>
        <div className="overflow-x-auto rounded-[4px] bg-ink p-5" aria-label="Example output of aletheore scan">
          {c.terminal.map((l) => (<div key={l} className="whitespace-pre font-display text-[12px] leading-[1.75] text-[#D8D2C5]">{l}</div>))}
        </div>
      </div>
    </section>
  );
}
