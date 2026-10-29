import { readFileSync } from "node:fs";
import { expect, it } from "vitest";
const evidence = (name: string) => JSON.parse(readFileSync(new URL(`../../docs/audits/companion-evidence/2026-10-29-${name}.json`, import.meta.url), "utf8"));
it("derives the rate evidence totals from every recorded response", () => {
  const report = evidence("ratelimit-http");
  expect(report.accepted).toBe(report.results.filter((row: {status: number}) => row.status === 200).length);
  expect(report.throttled).toBe(report.results.filter((row: {status: number}) => row.status === 429).length);
  expect(report.accepted + report.throttled).toBe(report.results.length);
});
it("retains the six independent HTTP injection checks as a separate artifact", () => {
  const report = evidence("http-injection");
  expect(report).toHaveLength(6); expect(report.every((row: {pass: boolean}) => row.pass)).toBe(true);
});
it.each(["live", "injection"])("keeps historical %s evidence counts consistent", (name) => {
  const report = evidence(name);
  expect(report.cases).toBe(report.results.length);
  expect(report.failures).toBe(report.results.filter((row: {pass: boolean}) => !row.pass).length);
  expect(report.inputTokens).toBe(report.results.reduce((n: number, row: {usage: {prompt_tokens: number}}) => n + row.usage.prompt_tokens, 0));
  expect(report.outputTokens).toBe(report.results.reduce((n: number, row: {usage: {completion_tokens: number}}) => n + row.usage.completion_tokens, 0));
});
