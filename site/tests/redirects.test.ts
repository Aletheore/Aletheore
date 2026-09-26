import { describe, expect, it } from "vitest";
import nextConfig from "../next.config";

describe("legacy .html redirects", () => {
  it("redirects every old page permanently to its clean URL", async () => {
    const rules = await nextConfig.redirects!();
    const home = rules.find((r) => r.source === "/index.html");
    expect(home).toMatchObject({ destination: "/", permanent: true });
    const generic = rules.find((r) => r.source.includes("pricing|developers"));
    expect(generic).toMatchObject({ destination: "/:slug", permanent: true });
    for (const page of ["pricing", "developers", "benchmarks", "dogfooding", "status", "privacy", "terms", "refund", "security"]) {
      expect(generic!.source).toContain(page);
    }
  });
});

describe("cutover compatibility", () => {
  it("keeps the public logo URL the static site published (og:image links point at it)", async () => {
    const { existsSync } = await import("node:fs");
    expect(existsSync("public/assets/logo-mark.png")).toBe(true);
  });
  it("sends baseline security headers on every route", async () => {
    const rules = await nextConfig.headers!();
    const all = rules.find((r) => r.source === "/:path*")!;
    const keys = all.headers.map((h) => h.key);
    expect(keys).toEqual(expect.arrayContaining(["X-Content-Type-Options", "Referrer-Policy", "X-Frame-Options"]));
  });
});
