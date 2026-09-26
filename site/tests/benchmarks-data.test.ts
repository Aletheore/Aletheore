import { describe, expect, it } from "vitest";
import * as B from "@/data/benchmarks";
import { asRecords, T } from "@/data/benchmarks";
import claims from "@/data/benchmarks-claims.json";

describe("benchmarks data", () => {
  it("has rectangular tables with a source for every one", () => {
    for (const [id, t] of Object.entries(T)) {
      expect(t.sources.length, id).toBeGreaterThan(0);
      for (const r of t.rows) expect(r.length, `${id}: ${r[0]}`).toBe(t.columns.length);
    }
  });
  it("keeps the Experiment 8 rows the pricing page quotes", () => {
    const flash = T.prRecall.rows.find((r) => r[0] === "Aletheore Flash, shared context")!;
    expect(flash[2]).toBe("53.4% [39-68]");
    expect(flash[3]).toBe("92.6% [86-96]");
  });
  it("shows results only for vendors whose terms allow it", () => {
    const rows = JSON.stringify([T.prRecall, T.swePr, T.graphify, T.detVsLlm]);
    for (const banned of ["CodeRabbit", "Greptile", "Bugbot", "Cursor"]) expect(rows).not.toContain(banned);
  });
  it("names the three excluded vendors only in the not-shown note", () => {
    const prose = JSON.stringify({ ...B, T: undefined });
    const outside = prose.replace(B.prReview.notShown, "");
    for (const banned of ["CodeRabbit", "Greptile", "Bugbot"]) expect(outside).not.toContain(banned);
  });
  it("never claims to beat the field on bugs caught", () => {
    const prose = JSON.stringify({ ...B, T: undefined }).toLowerCase();
    for (const bad of ["beats the field", "more bugs than", "outperforms", "best-in-class"]) expect(prose).not.toContain(bad);
  });
  it("carries no em or en dashes in prose", () => {
    const prose = JSON.stringify({ ...B, T: undefined });
    expect(prose).not.toMatch(/[—]/);
    expect(prose).not.toMatch(/[–]/);
  });
  it("carries no dashes as connectors in the tables either", () => {
    for (const [id, tb] of Object.entries(T)) expect(JSON.stringify(tb), id).not.toMatch(/[–—]/);
  });
  it("has verifiable claim entries pointing at real files", () => {
    expect(claims.length).toBeGreaterThan(20);
    for (const c of claims) expect(c.file).toMatch(/README\.md$/);
  });
  it("turns tables into renderable records", () => {
    const { columns, rows } = asRecords(T.graphify);
    expect(columns).toHaveLength(3);
    expect(rows[1].c0).toBe("+ Aletheore");
  });
});
