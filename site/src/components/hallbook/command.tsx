"use client";
import { Check, Copy } from "lucide-react";
import { useState } from "react";

export function Command({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  const [failed, setFailed] = useState(false);
  return (
    <div className="my-2 flex max-w-[780px] items-center justify-between gap-3 rounded-[4px] border border-dashed border-white/25 px-4 py-3 font-display text-[13px] text-[#e8e2d4]">
      <code className="overflow-x-auto whitespace-nowrap">{text}</code>
      <button
        type="button" aria-label={done ? "Copied" : failed ? "Copy failed, select the command instead" : `Copy: ${text}`}
        onClick={async () => {
          try { await navigator.clipboard.writeText(text); setFailed(false); setDone(true); setTimeout(() => setDone(false), 1500); }
          catch { setDone(false); setFailed(true); setTimeout(() => setFailed(false), 2500); } // the command stays selectable
        }}
        className="inline-flex shrink-0 items-center gap-1.5 rounded-[3px] px-2 py-1 text-[11px] text-[#f0c987] transition-colors hover:text-paper focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-paper"
      >
        {done ? <Check className="size-3.5" aria-hidden="true" /> : <Copy className="size-3.5" aria-hidden="true" />}{done ? "Copied" : failed ? "Copy failed" : "Copy"}
      </button>
    </div>
  );
}
