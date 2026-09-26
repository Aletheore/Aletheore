"use client";
import * as Tabs from "@radix-ui/react-tabs";
import { Check } from "lucide-react";
import { home } from "@/content/home";

export function Surfaces() {
  const intro = home.surfacesIntro;
  return (
    <section aria-labelledby="surfaces-title" className="mx-auto max-w-[1080px] px-8 py-16">
      <p className="eyebrow eyebrow--section mb-3">{intro.eyebrow}</p>
      <h2 id="surfaces-title" className="font-display text-[clamp(24px,3.2vw,34px)] font-bold leading-[1.1] tracking-[-0.015em]">{intro.title}</h2>
      <p className="mt-4 max-w-[60ch] text-[15px] leading-[1.65] text-muted">{intro.intro}</p>
      <Tabs.Root defaultValue={home.surfaces[0].id} className="crop-marks mt-10 border border-line bg-raised">
        <Tabs.List aria-label="Surfaces" className="flex flex-wrap border-b border-line">
          {home.surfaces.map((s) => (
            <Tabs.Trigger
              key={s.id} value={s.id}
              className="border-r border-line px-5 py-3 font-display text-[12.5px] text-muted transition-colors last:border-r-0 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-stamp data-[state=active]:bg-ink data-[state=active]:text-paper"
            >
              {s.title}
            </Tabs.Trigger>
          ))}
        </Tabs.List>
        {home.surfaces.map((s) => (
          <Tabs.Content key={s.id} value={s.id} className="grid gap-8 p-7 md:grid-cols-[1.1fr_0.9fr]">
            <div>
              <h3 className="font-display text-xl font-bold">{s.title}</h3>
              <p className="mt-3 text-[14.5px] leading-[1.65] text-muted">{s.body}</p>
              <ul className="mt-5 flex flex-col gap-2.5">
                {s.bullets.map((b) => (
                  <li key={b} className="flex items-start gap-2.5 text-[13.5px]">
                    <Check className="mt-0.5 size-4 flex-none text-stamp" aria-hidden="true" />{b}
                  </li>
                ))}
              </ul>
            </div>
            <div aria-hidden="true" className="rounded-[4px] border border-dashed border-line-strong bg-paper p-4 font-display text-[11.5px] leading-[1.7] text-muted">
              <p className="text-faint">{`// ${s.title}`}</p>
              {s.bullets.map((b, i) => (<p key={b}><span className="text-stamp">{`0${i + 1}`}</span> {b}</p>))}
            </div>
          </Tabs.Content>
        ))}
      </Tabs.Root>
    </section>
  );
}
