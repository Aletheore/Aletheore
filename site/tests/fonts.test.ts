import { describe, expect, it } from "vitest";
import { refreshAfterFonts } from "@/lib/fonts";

describe("refreshAfterFonts", () => {
  it("refreshes once, after fonts are ready", async () => {
    const order: string[] = [];
    const ready = Promise.resolve().then(() => order.push("ready"));
    await refreshAfterFonts({ ready }, () => order.push("refresh"));
    expect(order).toEqual(["ready", "refresh"]);
  });
  it("refreshes immediately when the Font Loading API is missing", async () => {
    let n = 0;
    await refreshAfterFonts(undefined, () => { n++; });
    expect(n).toBe(1);
  });
  it("still refreshes when fonts fail to load", async () => {
    let n = 0;
    await refreshAfterFonts({ ready: Promise.reject(new Error("x")) }, () => { n++; }).catch(() => {});
    expect(n).toBe(1);
  });
});
