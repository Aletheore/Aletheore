import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { AletheoreMascot } from "@/components/site/aletheore-mascot";
import { WebsiteCompanion } from "@/components/site/website-companion";
import { Logo } from "@/components/site/logo";
import { Hero } from "@/components/scenes/hero";

vi.mock("@/components/scenes/graph-canvas", () => ({ GraphCanvas: () => null }));
import { MASCOT_POSE_COORDINATES } from "@/lib/aletheore-mascot";

const PNG_SIGNATURE = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);

function pngInfo(path: URL) {
  const bytes = readFileSync(fileURLToPath(path));
  expect(bytes.subarray(0, 8)).toEqual(PNG_SIGNATURE);
  return {
    width: bytes.readUInt32BE(16),
    height: bytes.readUInt32BE(20),
    bitDepth: bytes[24],
    colorType: bytes[25],
  };
}

describe("Aletheore mascot assets and component contract", () => {
  it("maps every approved pose to one distinct cell in the four-by-four sheet", () => {
    const entries = Object.entries(MASCOT_POSE_COORDINATES);
    expect(entries.map(([pose]) => pose)).toEqual([
      "neutral", "typing", "working", "review",
      "explaining", "thinking", "jump-ready", "jump-air",
      "landing", "walking", "waving", "success",
      "caution", "reading", "curious", "resting",
    ]);
    const cells = entries.map(([, { x, y }]) => `${Math.round((x / 100) * 3)}:${Math.round((y / 100) * 3)}`);
    expect(new Set(cells).size).toBe(16);
    entries.forEach(([, { x, y }], index) => {
      expect(x).toBeCloseTo(((index % 4) * 100) / 3, 10);
      expect(y).toBeCloseTo((Math.floor(index / 4) * 100) / 3, 10);
    });
  });

  it("ships transparent master and pose-sheet PNGs", () => {
    for (const name of ["aletheore-master.png", "aletheore-poses.png"]) {
      const info = pngInfo(new URL(`../public/mascot/${name}`, import.meta.url));
      expect(info.width).toBeGreaterThanOrEqual(1000);
      expect(info.height).toBe(info.width);
      expect(info.bitDepth).toBe(8);
      expect(info.colorType).toBe(6); // RGBA keeps the background transparent.
    }
  });

  it("keeps the official logo free of duplicate companions", () => {
    const logo = renderToStaticMarkup(createElement(Logo));
    expect(logo).not.toContain('data-pose=');
    const src = logo.match(/<img[^>]+src="([^"]*)"/)![1];
    expect(decodeURIComponent(src)).toContain("/logo-mark.png");
  });

  it("keeps the homepage headline free of duplicate companions", () => {
    const hero = renderToStaticMarkup(createElement(Hero));
    expect(hero).not.toContain('data-pose=');
  });

  it("provides discreet state animations and a reduced-motion fallback", () => {
    const css = readFileSync(new URL("../src/app/globals.css", import.meta.url), "utf8");
    expect(css).toContain('data-motion="idle"');
    expect(css).toContain('data-motion="typing"');
    expect(css).toContain('data-motion="jump"');
    expect(css).toContain("@media (prefers-reduced-motion: reduce)");
    expect(css).toContain("aletheore-mascot-jump-poses");
    const reduced = [...css.matchAll(/@media \(prefers-reduced-motion: reduce\) \{([\s\S]*?)^\}/gm)]
      .map((match) => match[1]).find((block) => block.includes(".aletheore-mascot"));
    expect(reduced).toBeDefined();
    expect(reduced).toMatch(/animation:\s*none/);
    expect(reduced).not.toContain("background-position");
  });
});

// All poses retain their requested frame when animations are disabled.
it.each(Object.keys(MASCOT_POSE_COORDINATES))("renders the requested %s pose literally", (pose) => {
  const key = pose as keyof typeof MASCOT_POSE_COORDINATES;
  const { x, y } = MASCOT_POSE_COORDINATES[key];
  const html = renderToStaticMarkup(createElement(AletheoreMascot, { pose: key, motion: "jump", label: "Aletheore" }));
  expect(html).toContain(`background-position:${x}% ${y}%`);
  expect(html).toContain('role="img"');
  expect(html).toContain('aria-label="Aletheore"');
});

it("renders one accessible corner-companion trigger with its chat initially closed", () => {
  const html = renderToStaticMarkup(createElement(WebsiteCompanion));
  expect(html).toContain('aria-label="Chat with Aletheore"');
  expect(html).toContain('aria-expanded="false"');
  expect(html).toContain('class="companion-chat"');
  expect(html).not.toContain('<dialog open');
  expect(html).toContain('aria-haspopup="dialog"');
  expect(html).toContain('data-pose="neutral"');
  expect(html).toContain('<textarea');
  expect(html).toContain('AVAILABLE YET');
  expect(html).toContain('disabled=""');
});
