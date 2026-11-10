import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("../src/app/globals.css", import.meta.url), "utf8");
const token = (name: string) => css.match(new RegExp(`--color-${name}:\\s*(#[0-9A-Fa-f]{6})`))?.[1]?.toUpperCase() ?? "";
const lum = (hex: string) => {
  const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
};
const contrast = (a: string, b: string) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };

describe("the dark theme", () => {
  it("is dark with a white accent, and red only as the error colour", () => {
    expect(token("paper")).toBe("#0F0E0B");
    expect(token("stamp")).toBe("#FFFFFF");
    expect(token("error")).toBe("#E0584A");
    expect(css).toContain("color-scheme: dark");
  });
  it("keeps every text colour readable on the page, raised and terminal backgrounds", () => {
    for (const fg of ["ink", "muted", "faint", "stamp", "success", "needs"]) {
      for (const bg of ["paper", "raised", "term"]) {
        expect(contrast(token(fg), token(bg)), `${fg} on ${bg}`).toBeGreaterThanOrEqual(4.5);
      }
    }
    expect(contrast(token("error"), token("error-soft"))).toBeGreaterThanOrEqual(4.5);
    expect(contrast(token("needs"), token("needs-soft"))).toBeGreaterThanOrEqual(4.5);
  });
  it("leaves no hard-coded light-page colours behind in the components", () => {
    const files = ["src/components/ui/dialog.tsx", "src/components/site/status-board.tsx", "src/components/site/subscribed-notice.tsx"];
    for (const f of files) {
      const s = readFileSync(new URL(`../${f}`, import.meta.url), "utf8");
      expect(s, f).not.toMatch(/#EAF1EA|#FBF1DD|#DCEBDD|#ffffff|#16140f/i);
    }
  });
});
