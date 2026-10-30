// Deterministic and free: checks that the passages a visitor's wording should reach are actually handed to the model.
import { describe, expect, it } from "vitest";
import { normalizeProductName, retrieve } from "@/lib/companion/knowledge";

const ids = (question: string, history: string[] = []) => retrieve(question, history).map((fact) => fact.id);

describe("retrieval reaches the right passages for how people really type", () => {
  it.each<[string, string[]]>([
    ["do you give refunds?", ["refund-0"]],
    ["can I get my money back within 14 days of buying?", ["refund-0"]],
    ["can I cancel my subscription anytime?", ["cancel-0"]],
    ["how do I contact support?", ["contact-0"]],
    ["I found a security bug on your website, where do I report it?", ["vuln-1"]],
    ["what permissions does the GitHub app ask for?", ["perms-0"]],
    ["does the github app keep a copy of my repo?", ["security-code-0"]],
    ["who are you?", ["theo-0"]],
    ["what's your name?", ["theo-0"]],
    ["hi", ["theo-0"]],
    ["are you a human or a bot?", ["theo-0"]],
    ["do you store my questions?", ["theo-2"]],
    ["do you save my IP address when I ask you something?", ["theo-3"]],
    ["how much does it cost?", ["price-flash", "price-air", "price-community"]],
    ["pricing", ["price-flash", "price-air", "price-community"]],
    ["is there a free plan?", ["price-community"]],
    ["what plans do you have?", ["price-flash", "price-air", "price-community"]],
    ["what's the difference between Flash and AIR?", ["price-flash", "price-air"]],
    ["how do i instal aletheore", ["quickstart-0"]],
    ["install", ["quickstart-0"]],
    ["wat is aletheor", ["flow-scan"]],
    ["does it work with claude code or cursor?", ["mcp"]],
    ["How much is Flash, how do I install the CLI, and does it send crash reports?", ["price-flash", "quickstart-0", "privacy-cli-2"]],
    ["is aletheore open source?", ["terms-what-0"]],
    ["what license is it under?", ["terms-what-0"]],
    ["can I use it in a closed-source product?", ["terms-what-0"]],
    ["which languages does it support?", ["languages"]],
    ["does it support rust?", ["languages"]],
    ["does it work with Cursor specifically?", ["mcp-setup"]],
    ["which coding agents are supported?", ["mcp-setup"]],
    ["how do I connect claude code?", ["mcp-setup"]],
    ["what is hallbook?", ["hallbook-what"]],
    ["how do I add my own skill?", ["hallbook-custom"]],
    ["does hallbook make my agent better?", ["hallbook-results"]],
    ["how many skills are in hallbook?", ["hallbook-catalog"]],
    ["are hallbook skills permissively licensed?", ["hallbook-vetting-1"]],
    ["how are hallbook skills vetted?", ["hallbook-vetting-0", "hallbook-vetting-2"]],
  ])("%s", (question, expected) => {
    expect(ids(question)).toEqual(expect.arrayContaining(expected));
  });
  it("never lists a passage twice and stays bounded", () => {
    for (const question of ["do you give refunds?", "who are you?", "pricing", "zzzz qqqq", ""]) {
      const found = ids(question);
      expect(new Set(found).size).toBe(found.length);
      expect(found.length).toBeLessThanOrEqual(11);
    }
  });
  it("a question about you ranks Theo's own passages above the CLI's", () => {
    const found = ids("do you store my questions?");
    expect(found.indexOf("theo-2")).toBeLessThan(found.indexOf("privacy-cli-4") === -1 ? 99 : found.indexOf("privacy-cli-4"));
  });
});

describe("misspelled product names are corrected before the model sees them", () => {
  it.each([
    ["wat is aletheor", "wat is Aletheore"],
    ["what is Alethore?", "what is Aletheore?"],
    ["Aletheor pricing", "Aletheore pricing"],
    ["what is Aletheore?", "what is Aletheore?"],
    ["what is a thermometer", "what is a thermometer"],
    ["alethiometer", "alethiometer"],
  ])("%s", (input, expected) => expect(normalizeProductName(input)).toBe(expected));
});
