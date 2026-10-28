import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
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
    for (const [, { x, y }] of entries) {
      expect(x).toBeGreaterThanOrEqual(0);
      expect(x).toBeLessThanOrEqual(100);
      expect(y).toBeGreaterThanOrEqual(0);
      expect(y).toBeLessThanOrEqual(100);
    }
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

  it("keeps the mascot in the shared logo without replacing the official mark", () => {
    const logo = readFileSync(new URL("../src/components/site/logo.tsx", import.meta.url), "utf8");
    expect(logo).toContain("AletheoreMascot");
    expect(logo).toContain('src="/logo-mark.png"');
  });

  it("shows the typing/work loop beside the homepage evidence headline", () => {
    const hero = readFileSync(new URL("../src/components/scenes/hero.tsx", import.meta.url), "utf8");
    expect(hero).toContain('pose="typing" motion="typing"');
  });

  it("provides discreet state animations and a reduced-motion fallback", () => {
    const css = readFileSync(new URL("../src/app/globals.css", import.meta.url), "utf8");
    expect(css).toContain('data-motion="idle"');
    expect(css).toContain('data-motion="typing"');
    expect(css).toContain('data-motion="jump"');
    expect(css).toContain("@media (prefers-reduced-motion: reduce)");
    expect(css).toContain("aletheore-mascot-jump-poses");
  });
});
