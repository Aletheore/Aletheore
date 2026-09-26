"use client";
import * as Tabs from "@radix-ui/react-tabs";
import { Check, Copy } from "lucide-react";
import { useState } from "react";
import type { TermLine } from "@/data/developers";

type Term = { id: string; label: string; copy: string; lines: TermLine[] };

function CopyButton({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setDone(true);
          setTimeout(() => setDone(false), 1600);
        } catch {
          /* clipboard unavailable: leave the text selectable */
        }
      }}
      className="inline-flex items-center gap-1.5 rounded-[3px] px-2 py-1 font-display text-[11px] text-[#bdb6a6] transition-colors hover:text-paper focus-visible:outline-2 focus-visible:outline-stamp"
      aria-label={done ? "Copied" : "Copy commands"}
    >
      {done ? <Check className="size-3.5" aria-hidden="true" /> : <Copy className="size-3.5" aria-hidden="true" />}
      {done ? "Copied" : "Copy"}
    </button>
  );
}

export function Terminal({ terms }: { terms: Term[] }) {
  return (
    <Tabs.Root defaultValue={terms[0].id} className="overflow-hidden rounded-[6px] bg-ink text-[#e8e2d4] shadow-[0_18px_40px_-24px_rgba(22,20,15,0.6)]">
      <div className="flex items-center justify-between border-b border-white/10 px-3 pt-2">
        <Tabs.List aria-label="Terminal examples" className="flex gap-1">
          {terms.map((t) => (
            <Tabs.Trigger
              key={t.id}
              value={t.id}
              className="rounded-t-[4px] px-3 py-2 font-display text-[11.5px] text-[#9a9384] transition-colors hover:text-paper focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-stamp data-[state=active]:bg-white/10 data-[state=active]:text-paper"
            >
              {t.label}
            </Tabs.Trigger>
          ))}
        </Tabs.List>
      </div>
      {terms.map((t) => (
        <Tabs.Content key={t.id} value={t.id} className="relative">
          <div className="absolute right-2 top-2"><CopyButton text={t.copy} /></div>
          <pre className="overflow-x-auto p-5 pr-24 font-display text-[12.5px] leading-[1.75]">
            {t.lines.map((l, i) => (
              <div key={i} className={l.kind === "cmd" ? "term-line text-paper" : "term-line text-[#b6b09f]"} style={{ animationDelay: `${Math.min(i, 24) * 45}ms` }}>
                {l.kind === "cmd" ? <span className="mr-2 text-stamp" aria-hidden="true">$</span> : null}
                {l.text}
              </div>
            ))}
          </pre>
        </Tabs.Content>
      ))}
    </Tabs.Root>
  );
}
