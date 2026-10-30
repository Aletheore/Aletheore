// The website states things that are true of the CLI and the README; fail when they drift apart.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { expect, it } from "vitest";
import { languageSupport, mcpSetup } from "@/data/developers";

const repoFile = (path: string) => readFileSync(fileURLToPath(new URL(`../../${path}`, import.meta.url)), "utf8");

it("lists exactly the MCP clients that `aletheore mcp-install --target` accepts", () => {
  const cli = repoFile("src/aletheore/cli.py");
  const block = cli.match(/_MCP_CLIENT_CONFIGS[^=]*=\s*\{([\s\S]*?)\n\}/)?.[1] ?? "";
  const targets = [...block.matchAll(/^\s+"([a-z-]+)":/gm)].map((m) => m[1]);
  // The CLI adds two file-format-specific clients next to the table; read them from its own list, not from this test.
  const extra = [...(cli.match(/all_targets = \[\*_MCP_CLIENT_CONFIGS\.keys\(\), ([^\]]+)\]/)?.[1] ?? "").matchAll(/"([a-z-]+)"/g)].map((m) => m[1]);
  expect(extra, "could not read the extra targets from cli.py; update this test's parser").toEqual(expect.arrayContaining(["codex-cli", "claude-desktop"]));
  expect(targets.length, "could not read the target list from cli.py; update this test's parser").toBeGreaterThanOrEqual(6);
  const names: Record<string, string> = { "claude-code": "Claude Code", cursor: "Cursor", vscode: "VS Code", kiro: "Kiro", opencode: "Opencode", antigravity: "Antigravity", "codex-cli": "Codex CLI", "claude-desktop": "Claude Desktop" };
  for (const target of [...targets, ...extra]) {
    expect(names[target], `no display name for new MCP target ${target}: add it here and to the site copy`).toBeDefined();
    expect(mcpSetup.body, target).toContain(names[target]);
  }
  expect(mcpSetup.code).toMatch(/^aletheore mcp-install \. --target (cursor)$/);
});

it("states the same language support as the README", () => {
  const readme = repoFile("README.md").replace(/\*\*/g, "").replace(/\s+/g, " ");
  const sentence = languageSupport.body.replace(/\s+/g, " ");
  expect(readme, "the README's language-support paragraph changed; update site/src/data/developers.ts to match").toContain(sentence);
});
