import { describe, expect, it } from "vitest";
import { home } from "@/content/home";

const flat = JSON.stringify(home);

describe("home content", () => {
  it("has the four surfaces with three bullets each", () => {
    expect(home.surfaces).toHaveLength(4);
    for (const s of home.surfaces) expect(s.bullets).toHaveLength(3);
  });
  it("carries no em or en dashes", () => {
    expect(flat).not.toMatch(/[–—]/);
  });
  it("names no fictional sample data from the old static site", () => {
    expect(flat).not.toContain("/v1/users");
    expect(flat).not.toContain("user.controller");
  });
  it("keeps the proof repos with pinned short commits", () => {
    expect(home.proof.repos.map((r) => r.name)).toEqual(["Django", "Express", "Kubernetes"]);
    for (const r of home.proof.repos) expect(r.commit).toMatch(/^[0-9a-f]{7}$/);
  });
  it("states the Linux stress test numbers exactly", () => {
    const linux = Object.fromEntries(home.proof.linux.stats.map((s) => [s.label, s.value]));
    expect(linux["Files parsed"]).toBe("64,634");
    expect(linux["Commits analyzed"]).toBe("1,463,552");
    expect(linux["Scan time"]).toBe("4m 20s");
  });
});
