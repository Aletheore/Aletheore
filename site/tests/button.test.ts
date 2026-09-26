import { describe, expect, it } from "vitest";
import { buttonVariants } from "@/components/ui/button";

describe("buttonVariants", () => {
  it("primary is ink on paper", () => {
    const c = buttonVariants({ variant: "primary" });
    expect(c).toContain("bg-ink");
    expect(c).toContain("text-paper");
  });
  it("secondary has the strong line border", () => {
    expect(buttonVariants({ variant: "secondary" })).toContain("border-line-strong");
  });
  it("small size tightens padding", () => {
    expect(buttonVariants({ size: "sm" })).toContain("px-3");
  });
  it("defaults to primary md", () => {
    const c = buttonVariants({});
    expect(c).toContain("bg-ink");
    expect(c).toContain("px-4");
  });
});
