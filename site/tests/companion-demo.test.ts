import { describe, expect, it } from "vitest";
import { companionDemoAnswer, COMPANION_STARTERS } from "@/lib/companion-demo";

describe("development companion fixtures", () => {
  it.each(COMPANION_STARTERS)("answers the %s starter with public source links", (question) => {
    const answer = companionDemoAnswer(question);
    expect(answer.text.length).toBeGreaterThan(40);
    expect(answer.sources.length).toBeGreaterThan(0);
    for (const source of answer.sources) expect(source.href).toMatch(/^\/(?!\/)/);
  });
  it("offers a copyable install command", () => {
    expect(companionDemoAnswer("Install the CLI").command).toBe("pip install aletheore");
  });
  it("does not invent answers outside the fixture set", () => {
    const answer = companionDemoAnswer("Tell me the price of a private enterprise contract");
    expect(answer.text).toContain("can't verify");
    expect(answer.command).toBeUndefined();
  });
  it("does not turn input into source links or executable markup", () => {
    const answer = companionDemoAnswer('<script>fetch("https://evil.example")</script>');
    expect(JSON.stringify(answer)).not.toContain("evil.example");
  });
});
