import { describe, expect, it } from "vitest";
import tools from "@/data/mcp-tools.json";
import * as dev from "@/data/developers";

describe("developers data", () => {
  it("derives the MCP tool count from the generated package data, not from copy", () => {
    expect(dev.mcpTools.defaults).toEqual(tools.default);
    expect(dev.mcpToolNote).toContain(`${tools.default.length} tools`);
    expect(dev.mcpToolNote).toContain(tools.version);
  });
  it("never lists a tool twice", () => {
    const all = [...tools.default, ...tools.optional];
    expect(new Set(all).size).toBe(all.length);
  });
  it("keeps the opt-in tools out of the default list", () => {
    for (const o of dev.mcpTools.optional) expect(tools.default).not.toContain(o.name);
    expect(dev.mcpTools.optional.map((o) => o.name).sort()).toEqual([...tools.optional].sort());
  });
  it("names only real tools in the feature cards", () => {
    const real = new Set([...tools.default, ...tools.optional]);
    for (const f of dev.mcpHead.features) expect(real.has(f.name.replace(/\(.*$/, ""))).toBe(true);
  });
  it("groups the query kinds the CLI reports", () => {
    expect(dev.queryKindCount).toBe(Object.values(tools.queryGroups).flat().length);
    expect(dev.queryKindCount).toBeGreaterThan(20);
  });
  it("carries no em or en dashes", () => {
    expect(JSON.stringify(dev)).not.toMatch(/[–—]/);
  });
});
