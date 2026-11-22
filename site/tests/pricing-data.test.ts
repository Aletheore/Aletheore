import { describe, expect, it } from "vitest";
import ld from "@/data/jsonld.json";
import { credit, footnotes, installNote, plans } from "@/data/pricing";

const flat = JSON.stringify({ plans, credit, footnotes, installNote });

describe("pricing data", () => {
  it("has the four published plans at the published prices", () => {
    expect(plans.map((p) => [p.name, p.price])).toEqual([["Community", "$0"], ["Flash Lite", "$6"], ["Flash", "$15"], ["AIR", "$40"]]);
  });
  it("agrees with the published JSON-LD offers", () => {
    const offers = Object.fromEntries((ld.offers as { name: string; price: string }[]).map((o) => [o.name, o.price]));
    expect(offers["Aletheore Flash Lite"]).toBe("6");
    expect(offers["Aletheore Flash"]).toBe("15");
    expect(offers["Aletheore AIR"]).toBe("40");
  });
  it("states the credit allotments used in the meters", () => {
    expect(credit.meters.map((m) => m.value)).toEqual([3, 8, 24]);
  });
  it("sends Flash Lite to its own checkout tier", () => {
    const lite = plans.find((p) => p.id === "lite");
    expect(lite?.cta.subscribe).toBe("lite");
    expect(lite?.priceNote).toBe("/month");
  });
  it("carries no em or en dashes", () => {
    expect(flat).not.toMatch(/[–—]/);
  });
  it("never names a model behind a plan, on the cards or in the structured offers", () => {
    const models = /haiku|claude|gpt|sol\b|anthropic|openai|sonnet|opus|deepseek|glm|qwen|gemini|mistral|one model/i;
    expect(flat).not.toMatch(models);
    expect(JSON.stringify(ld.offers)).not.toMatch(models);
    expect(JSON.stringify(ld.featureList ?? [])).not.toMatch(/written by/i);   // hosted features are not credited to a named model
  });
  it("says Flash Lite finds more with more noise than Flash, without inventing numbers for it", () => {
    const lite = JSON.stringify(plans.find((p) => p.id === "lite"));
    expect(lite).toMatch(/more noise|noisier/i);
    expect(lite).not.toMatch(/\d+% of human-identified/);
  });
  it("keeps the benchmark numbers on the Flash card in line with Experiment 9", () => {
    expect(flat).toContain("about 55% of human-identified bugs");
    expect(flat).toContain("about 89%");
  });
  it("says in the structured feature list that Flash Lite gets automatic reviews too", () => {
    const features = (ld.featureList as string[]).join(" | ");
    expect(features).toContain("Flash Lite");
    expect(features).not.toMatch(/Flash tier and up/);
  });
  it("keeps the footnote marks the cards refer to", () => {
    expect(footnotes.map((f) => f.mark)).toEqual(["*", "**"]);
  });
  it("names Flash Lite in the AI credit footnote and the install note", () => {
    expect(footnotes[1].body).toContain("Flash Lite includes $3");
    expect(installNote).toContain("Flash Lite");
  });
});
