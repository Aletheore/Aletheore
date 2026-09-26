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
