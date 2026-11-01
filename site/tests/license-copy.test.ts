// The site's license claim follows the actual license. It went stale once (PolyForm Noncommercial -> AGPL-3.0 on
// 2026-10-25 left "Source-available" on the hero); this fails if the file and the copy disagree again, in either direction.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { expect, it } from "vitest";
import ld from "@/data/jsonld.json";
import legal from "@/data/legal.json";

const repoFile = (path: string) => readFileSync(fileURLToPath(new URL(`../../${path}`, import.meta.url)), "utf8");
const siteFile = (path: string) => readFileSync(fileURLToPath(new URL(`../${path}`, import.meta.url)), "utf8");

it("the repository is AGPL-3.0, which is open source", () => {
  expect(repoFile("LICENSE")).toContain("GNU AFFERO GENERAL PUBLIC LICENSE");
  expect(repoFile("src/pyproject.toml")).toMatch(/^license = "AGPL-3\.0-only"$/m);
});

it("the site says open source, never source-available, while the license is AGPL", () => {
  for (const file of ["src/components/scenes/hero.tsx", "src/content/home.ts", "public/llms.txt"]) {
    expect(siteFile(file), file).not.toMatch(/source[- ]available/i);
  }
  expect(siteFile("src/components/scenes/hero.tsx")).toContain("<span>Open source</span>");
  expect(siteFile("src/content/home.ts")).toContain("Open source (AGPL-3.0) and local-first.");
  expect(siteFile("public/llms.txt")).toContain("open source under AGPL-3.0");
  expect(legal.terms.html).toContain("open source, local-first CLI under the");
  expect(legal.terms.html).toContain("GNU Affero General Public License v3.0");
});

it("the structured data declares the license for search engines", () => {
  expect((ld as { license?: string }).license).toBe("https://github.com/Aletheore/Aletheore/blob/master/LICENSE");
});
