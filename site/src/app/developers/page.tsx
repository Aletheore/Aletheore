import type { Metadata } from "next";
import { PageHead } from "@/components/site/page-head";
import { Section } from "@/components/site/section";
import { Terminal } from "@/components/site/terminal";
import { FileTree } from "@/components/site/file-tree";
import { FlowScene } from "@/components/site/flow-scene";
import { ToolExplorer } from "@/components/site/tool-explorer";
import {
  contract, depth, developersHead, flow, flowPanels, mcpHead, mcpToolNote, mcpTools, quickstart, terminalNote, terminals, toolGroups, toolInfo,
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

const scanLines = terminals.find((t) => t.id === "scan")!.lines;
const mcpLines = [
  { text: "aletheore mcp .", kind: "cmd" as const },
  ...["aletheore_overview", "aletheore_neighborhood", "aletheore_get_blast_radius", "aletheore_find_evidence_for_endpoint", "aletheore_ownership", "aletheore_hotspots", "aletheore_search_codebase", "aletheore_symbol_source", "aletheore_verify_citations"]
    .map((n) => toolInfo.find((t) => t.name === n))
    .filter((t): t is (typeof toolInfo)[number] => Boolean(t))
    .map((t) => ({ text: `${t.name.padEnd(40)}${t.group}` })),
  { text: `... ${mcpTools.defaults.length - 9} more, all read-only` },
];
const panels = {
  scan: { label: "aletheore scan .", lines: scanLines },
  app: flowPanels.app,
  mcp: { label: "aletheore mcp .", lines: mcpLines },
};

export default function DevelopersPage() {
  return (
    <>
      <PageHead eyebrow={developersHead.eyebrow} title={developersHead.title}>
        <p>{developersHead.intro}</p>
      </PageHead>

      <section aria-label="How the surfaces fit together" className="mx-auto max-w-[1080px] px-8 pb-10 pt-4">
        <FlowScene steps={flow} panels={panels} />
      </section>

      <Section id="quickstart" eyebrow={quickstart.eyebrow} title={quickstart.title} intro={quickstart.intro}>
        <Terminal terms={terminals} />
        <p className="mt-3 max-w-[76ch] text-[12px] leading-[1.6] text-faint">{terminalNote}</p>
        <div className="mt-10 grid gap-x-10 gap-y-8 md:grid-cols-3">
          {quickstart.commands.map((c) => (
            <article key={c.title} className="rule-dashed pt-5">
              <h3 className="font-display text-[14px] font-bold">{c.title}</h3>
              <pre className="mt-3 overflow-x-auto font-display text-[12px] leading-[1.75] text-ink"><code>{c.code}</code></pre>
              <p className="mt-3 text-[12.5px] leading-[1.6] text-muted">{c.body}</p>
            </article>
          ))}
        </div>
      </Section>

      <Section id="mcp" eyebrow={mcpHead.eyebrow} title={mcpHead.title} intro={mcpHead.intro}>
        <ToolExplorer tools={toolInfo} groups={toolGroups} />
        <p className="mt-8 max-w-[76ch] text-[12.5px] leading-[1.65] text-faint">{mcpToolNote}</p>
      </Section>

      <Section id="contract" eyebrow={contract.eyebrow} title={contract.title} intro={contract.intro}>
        <FileTree files={contract.files} readers={contract.readers} />
      </Section>

      <Section id="depth" eyebrow={depth.eyebrow} title={depth.title} className="pb-20">
        <ul className="flex flex-col">
          {depth.cards.map((c) => (
            <li key={c.title} className="rule-dashed grid gap-2 py-5 md:grid-cols-[8rem_14rem_1fr] md:gap-8">
              <span className="font-display text-[12px] text-stamp">{c.tag}</span>
              <h3 className="font-display text-[15px] font-bold">{c.title}</h3>
              <p className="max-w-[60ch] text-[13.5px] leading-[1.65] text-muted">{c.body}</p>
            </li>
          ))}
        </ul>
      </Section>
    </>
  );
}
