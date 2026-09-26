"use client";
import { useState } from "react";
import type { ToolInfo } from "@/data/developers";
import { cn } from "@/lib/utils";

/** All MCP tools grouped by role. Pick one to read what it does, in the tool's own words. */
export function ToolExplorer({ tools, groups }: { tools: ToolInfo[]; groups: string[] }) {
  const [selected, setSelected] = useState(tools.find((t) => t.name === "aletheore_overview")?.name ?? tools[0].name);
  const tool = tools.find((t) => t.name === selected) ?? tools[0];

  return (
    <div className="grid gap-8 lg:grid-cols-[1.2fr_1fr]">
      <div className="order-2 grid gap-x-8 gap-y-6 sm:grid-cols-2 lg:order-1">
        {groups.map((g) => (
          <div key={g}>
            <h3 className="mb-2 font-display text-[11.5px] uppercase tracking-[0.06em] text-faint">
              {g} <span className="text-line-strong">{tools.filter((t) => t.group === g).length}</span>
            </h3>
            <ul className="flex flex-col">
              {tools.filter((t) => t.group === g).map((t) => (
                <li key={t.name}>
                  <button
                    type="button"
                    onClick={() => setSelected(t.name)}
                    onMouseEnter={() => setSelected(t.name)}
                    onFocus={() => setSelected(t.name)}
                    aria-pressed={selected === t.name}
                    className={cn(
                      "group flex w-full items-center gap-2 border-l-2 py-1 pl-3 text-left font-display text-[12px] transition-colors focus-visible:outline-2 focus-visible:outline-stamp",
                      selected === t.name ? "border-stamp text-ink" : "border-line text-muted hover:border-line-strong hover:text-ink",
                    )}
                  >
                    {t.name.replace("aletheore_", "")}
                    {t.optional ? <span className="text-[10px] text-stamp">opt-in</span> : null}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
      <div className="order-1 lg:order-2">
        <div className="sticky top-28 overflow-hidden rounded-[6px] bg-ink text-[#e8e2d4]" aria-live="polite">
          <div className="flex items-center justify-between border-b border-white/10 px-4 py-2.5 font-display text-[11.5px] text-[#9a9384]">
            <span>{tool.group}</span>
            <span>{tool.optional ? "opt-in" : "default"}</span>
          </div>
          <div key={tool.name} className="fade-up p-5">
            <p className="font-display text-[15px] font-bold text-paper">{tool.name}</p>
            <p className="mt-3 text-[13.5px] leading-[1.7] text-[#cfc9ba]">{tool.description}</p>
          </div>
        </div>
      </div>
    </div>
  );
}
