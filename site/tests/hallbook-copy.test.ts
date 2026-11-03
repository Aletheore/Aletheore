// The /hallbook page and the website assistant share one source for Hallbook's wording. This proves the page still renders it.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import HallbookPage from "@/app/hallbook/page";
import { hallbookCopy } from "@/data/hallbook-copy";
import { plain } from "@/lib/companion/knowledge";

vi.mock("@/components/scenes/graph-canvas", () => ({ GraphCanvas: () => null }));

it("renders every shared Hallbook string on the page", () => {
  const text = plain(renderToStaticMarkup(createElement(HallbookPage)));
  const flat = (value: string) => value.replace(/\s+/g, " ").trim();
  const expected = [
    hallbookCopy.noSetup,
    hallbookCopy.customBefore + hallbookCopy.customMarked,
    hallbookCopy.addCommand,
    hallbookCopy.vettingIntro,
    ...hallbookCopy.vettingChecks.flatMap(([title, body]) => [title, body]),
    `${hallbookCopy.benchmark.question} ${hallbookCopy.benchmark.body} ${hallbookCopy.benchmark.marked}`,
    hallbookCopy.access.before + hallbookCopy.access.marked + hallbookCopy.access.after,
  ];
  for (const piece of expected) expect(text, piece.slice(0, 60)).toContain(flat(piece));
});
