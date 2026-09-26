import type { Metadata } from "next";
import { PageHead } from "@/components/site/page-head";
import { Section } from "@/components/site/section";
import { Terminal } from "@/components/site/terminal";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import {
  contract, depth, developersHead, flow, mcpHead, mcpToolNote, mcpTools, queryGroups, queryHead, quickstart, terminalNote, terminals,
} from "@/data/developers";

export const metadata: Metadata = {
  title: "Developers: CLI, MCP, AIR, and Source-Grounded Tools",
  description:
    "Developer guide for Aletheore: pip install, CLI commands, query subcommands, MCP tools, AIR evidence files, semantic search, AIRview, and managed audits.",
  alternates: { canonical: "/developers" },
  openGraph: {
    title: "Aletheore Developers: CLI, MCP, AIR, and Source-Grounded Tools",
    description: "Install Aletheore from PyPI, scan repositories locally, query AIR evidence, run MCP tools for coding agents, and use AIRview for architecture intelligence.",
    url: "/developers",
  },
};

export default function DevelopersPage() {
  return (
    <>
      <PageHead eyebrow={developersHead.eyebrow} title={developersHead.title}>
        <p>{developersHead.intro}</p>
      </PageHead>

      <section aria-label="How the surfaces fit together" className="mx-auto max-w-[1080px] px-8 pb-6">
        <ol className="relative ml-2 border-l border-dashed border-line-strong">
          {flow.map((s) => (
            <li key={s.k} className="relative grid gap-2 pb-10 pl-8 last:pb-0 md:grid-cols-[13rem_1fr] md:gap-8">
              <span className="absolute -left-[5px] top-[7px] size-[9px] bg-stamp" aria-hidden="true" />
              <p className="font-display text-[12.5px] text-stamp">{s.k}</p>
              <div>
                <h2 className="font-display text-[18px] font-bold">{s.title}</h2>
                <p className="mt-2 max-w-[58ch] text-[14.5px] leading-[1.65] text-muted">{s.body}</p>
              </div>
            </li>
          ))}
        </ol>
      </section>

      <Section id="quickstart" eyebrow={quickstart.eyebrow} title={quickstart.title} intro={quickstart.intro}>
        <Terminal terms={terminals} />
        <p className="mt-3 text-[12px] leading-[1.6] text-faint">{terminalNote}</p>
        <div className="mt-8 grid gap-px border border-line bg-line md:grid-cols-3">
          {quickstart.commands.map((c) => (
            <article key={c.title} className="bg-raised p-5">
              <h3 className="font-display text-[14px] font-bold">{c.title}</h3>
              <pre className="mt-3 overflow-x-auto rounded-[3px] bg-paper p-3 font-display text-[12px] leading-[1.7]"><code>{c.code}</code></pre>
              <p className="mt-3 text-[12.5px] leading-[1.6] text-muted">{c.body}</p>
            </article>
          ))}
        </div>
      </Section>

      <Section id="query" eyebrow={queryHead.eyebrow} title={queryHead.title}>
        <div className="grid gap-x-8 gap-y-7 sm:grid-cols-2 lg:grid-cols-3">
          {queryGroups.map(([group, kinds]) => (
            <div key={group}>
              <h3 className="mb-3 font-display text-[12px] uppercase tracking-[0.06em] text-faint">{group}</h3>
              <ul className="flex flex-wrap gap-1.5">
                {kinds.map((k) => (
                  <li key={k}><code className="inline-block rounded-[3px] border border-line bg-raised px-2 py-1 font-display text-[11.5px]">{k}</code></li>
                ))}
              </ul>
            </div>
          ))}
        </div>
        <p className="mt-6 text-[12px] text-faint">Run aletheore query with no argument to list every kind by category.</p>
      </Section>

      <Section id="mcp" eyebrow={mcpHead.eyebrow} title={mcpHead.title} intro={mcpHead.intro}>
        <div className="mb-8 max-w-[22rem]"><Terminal terms={[{ id: "mcp", label: "MCP server", copy: "aletheore mcp .", lines: [{ text: "aletheore mcp .", kind: "cmd" }] }]} /></div>
        <div className="grid gap-px border border-line bg-line sm:grid-cols-2">
          {mcpHead.features.map((f) => (
            <article key={f.name} className="bg-raised p-5">
              <h3 className="font-display text-[13.5px] font-bold">{f.name}</h3>
              <p className="mt-2 text-[13px] leading-[1.6] text-muted">{f.body}</p>
            </article>
          ))}
        </div>
        <ul className="mt-8 flex flex-wrap gap-1.5" aria-label={`${mcpTools.defaults.length} default MCP tools`}>
          {mcpTools.defaults.map((t) => (
            <li key={t}><span className="inline-block rounded-[3px] border border-line bg-raised px-2 py-1 font-display text-[11px] text-muted">{t}</span></li>
          ))}
          {mcpTools.optional.map((t) => (
            <li key={t.name} title={t.why}><span className="inline-block rounded-[3px] border border-dashed border-stamp px-2 py-1 font-display text-[11px] text-stamp">{t.name}*</span></li>
          ))}
        </ul>
        <p className="mt-4 max-w-[70ch] text-[12.5px] leading-[1.65] text-faint">{mcpToolNote}</p>
      </Section>

      <Section id="contract" eyebrow={contract.eyebrow} title={contract.title} intro={contract.intro}>
        <div className="grid gap-px border border-line bg-line md:grid-cols-3">
          {contract.files.map((f) => (
            <article key={f.name} className="bg-raised p-5">
              <h3 className="font-display text-[13.5px] font-bold">{f.name}</h3>
              <p className="mt-2 text-[13px] leading-[1.6] text-muted">{f.body}</p>
            </article>
          ))}
        </div>
      </Section>

      <Section id="depth" eyebrow={depth.eyebrow} title={depth.title} className="pb-20">
        <div className="grid gap-px border border-line bg-line sm:grid-cols-2 lg:grid-cols-6">
          {depth.cards.map((c, i) => (
            <article key={c.title} className={cn("bg-raised p-5", i < 3 && "lg:col-span-2", i === 3 && "lg:col-span-3", i === 4 && "sm:col-span-2 lg:col-span-3")}>
              <Badge>{c.tag}</Badge>
              <h3 className="mt-3 font-display text-[14.5px] font-bold">{c.title}</h3>
              <p className="mt-2 text-[13px] leading-[1.6] text-muted">{c.body}</p>
            </article>
          ))}
        </div>
      </Section>
    </>
  );
}
