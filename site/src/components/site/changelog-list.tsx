"use client";
import * as Tabs from "@radix-ui/react-tabs";
import { useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { changelogEntries, type ChangelogTag } from "@/data/changelog";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 8;

const tagStyle: Record<ChangelogTag, string> = {
  security: "border-stamp text-stamp",
  fix: "border-success text-success",
  feature: "border-line-strong text-ink",
  perf: "border-line-strong text-muted",
};

const tagLabel: Record<ChangelogTag, string> = {
  security: "Security",
  fix: "Fix",
  feature: "Feature",
  perf: "Performance",
};

function formatDate(iso: string): string {
  // timeZone UTC matches the UTC midnight parsed above. Without it the day shown depended on the
  // runtime's timezone: a visitor west of UTC saw the previous day, and the server-rendered text
  // could differ from what the browser rendered on hydration.
  return new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-US", {
    timeZone: "UTC",
    year: "numeric",
    month: "long",
    day: "numeric",
  });
}

export function ChangelogList() {
  const securityCount = changelogEntries.filter((e) => e.tags.includes("security")).length;

  return (
    <Tabs.Root defaultValue="all">
      <Tabs.List aria-label="Filter the changelog" className="mb-8 flex gap-2 border-b border-line">
        <Tabs.Trigger
          value="all"
          className="border-b-2 border-transparent px-1 pb-3 font-display text-[13px] text-faint transition-colors hover:text-ink focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-stamp data-[state=active]:border-stamp data-[state=active]:text-ink"
        >
          All changes
        </Tabs.Trigger>
        <Tabs.Trigger
          value="security"
          className="border-b-2 border-transparent px-1 pb-3 font-display text-[13px] text-faint transition-colors hover:text-ink focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-stamp data-[state=active]:border-stamp data-[state=active]:text-ink"
        >
          Security ({securityCount})
        </Tabs.Trigger>
      </Tabs.List>

      <Tabs.Content value="all">
        <Entries entries={changelogEntries} />
      </Tabs.Content>
      <Tabs.Content value="security">
        <Entries entries={changelogEntries.filter((e) => e.tags.includes("security"))} />
      </Tabs.Content>
    </Tabs.Root>
  );
}

function Entries({ entries }: { entries: typeof changelogEntries }) {
  const [page, setPage] = useState(1);

  if (entries.length === 0) {
    return <p className="text-[13.5px] text-faint">No entries in this filter yet.</p>;
  }

  const pageCount = Math.max(1, Math.ceil(entries.length / PAGE_SIZE));
  const safePage = Math.min(page, pageCount);
  const pageEntries = entries.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE);

  return (
    <div>
      <ol className="flex flex-col gap-6">
        {pageEntries.map((entry) => (
          <li key={`${entry.date}-${entry.title}`} className="border border-line bg-raised p-5">
            <div className="flex flex-wrap items-center gap-2.5">
              <time dateTime={entry.date} className="font-display text-[12px] text-faint">
                {formatDate(entry.date)}
              </time>
              {entry.tags.map((t) => (
                <span
                  key={t}
                  className={cn(
                    "inline-block rounded-[3px] border px-[7px] py-[2px] font-display text-[10.5px] uppercase tracking-[0.03em]",
                    tagStyle[t],
                  )}
                >
                  {tagLabel[t]}
                </span>
              ))}
            </div>
            <h3 className="mt-2.5 font-display text-[15.5px] font-bold leading-[1.3]">{entry.title}</h3>
            <p className="mt-2 max-w-[74ch] text-[13.5px] leading-[1.7] text-muted">{entry.body}</p>
            {entry.responseTime ? (
              <p className="mt-2.5 font-display text-[11.5px] text-success">
                Time from discovery to a fix being live: {entry.responseTime}.
              </p>
            ) : null}
            {entry.link ? (
              <a href={entry.link.href} className="mt-2.5 inline-block text-[12.5px] text-ink underline underline-offset-[3px] hover:text-stamp">
                {entry.link.label}
              </a>
            ) : null}
          </li>
        ))}
      </ol>
      {pageCount > 1 ? (
        <div className="mt-8 flex items-center justify-between border-t border-line pt-5">
          <Button
            variant="secondary"
            size="sm"
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={safePage === 1}
          >
            <ChevronLeft className="size-3.5" aria-hidden="true" />
            Previous
          </Button>
          <span className="font-display text-[12px] text-faint">
            Page {safePage} of {pageCount}
          </span>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => setPage((p) => Math.min(pageCount, p + 1))}
            disabled={safePage === pageCount}
          >
            Next
            <ChevronRight className="size-3.5" aria-hidden="true" />
          </Button>
        </div>
      ) : null}
    </div>
  );
}
