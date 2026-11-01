import { afterEach, expect, it, vi } from "vitest";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { evaluateCompanion, evaluationDestination } from "../scripts/companion-evaluation";
const directories: string[] = [];
function output() { const dir = mkdtempSync(join(tmpdir(), "companion-eval-test-")); directories.push(dir); return join(dir, "report.json"); }
afterEach(() => { for (const dir of directories.splice(0)) rmSync(dir, { recursive: true, force: true }); vi.unstubAllEnvs(); });
const cases = [1,2].map((id) => ({ id: String(id), question: "Install Aletheore", status: "answer" }));
function result(reliable = true) { return { answer: { status: "answer" as const, text: "public docs", sources: [] }, usage: { prompt_tokens: 400000, completion_tokens: 1, total_tokens: 400001 }, usageReliable: reliable, usageViolation: false, corpus: "test" }; }
it("passes a refusal that is one of the accepted statuses, and fails an answer", async () => {
  const refusal = (status: "off_topic" | "unverified" | "answer") => ({ ...result(), answer: { status, text: "x", sources: [] }, usage: { prompt_tokens: 10, completion_tokens: 1, total_tokens: 11 } });
  const test = [{ id: "poisoned", question: "q", status: "off_topic", alsoAccept: ["unverified"] }];
  for (const [status, passes] of [["off_topic", true], ["unverified", true], ["answer", false]] as const) {
    const destination = output();
    await evaluateCompanion({ cases: test, repeats: 1, suite: "test", destination, answer: vi.fn().mockResolvedValue(refusal(status)), log: () => {} });
    expect(JSON.parse(readFileSync(destination, "utf8")).results[0].pass, status).toBe(passes);
  }
});
it("uses the platform temp directory by default", () => {
  vi.stubEnv("COMPANION_EVAL_OUTPUT", undefined);
  expect(evaluationDestination()).toBe(join(tmpdir(), "aletheore-companion-live-eval.json"));
});
it("persists completed results when the token cap stops later requests", async () => {
  const destination = output(); const answer = vi.fn().mockResolvedValue(result());
  const summary = await evaluateCompanion({ cases, repeats: 1, suite: "test", destination, answer, log: () => {} });
  expect(summary.stopped).toContain("token cap"); expect(answer).toHaveBeenCalledTimes(1);
  const report = JSON.parse(readFileSync(destination, "utf8")); expect(report.cases).toBe(1); expect(report.results[0].pass).toBe(true); expect(report.stopped).toContain("token cap");
});
it.each([false, true])("preserves evidence and stops on unknown usage/failure: %s", async (throws) => {
  const destination = output(); const answer = throws ? vi.fn().mockRejectedValue(new Error("request_failed")) : vi.fn().mockResolvedValue(result(false));
  const summary = await evaluateCompanion({ cases, repeats: 2, suite: "test", destination, answer, log: () => {} });
  expect(summary.stopped).toBeDefined(); expect(answer).toHaveBeenCalledTimes(1);
  expect(JSON.parse(readFileSync(destination, "utf8")).cases).toBe(1);
});
it("fails before paid work when the output path is not writable", async () => {
  const destination = join(output(), "nonexistent-parent", "report.json"); const answer = vi.fn();
  await expect(evaluateCompanion({ cases, repeats: 1, suite: "test", destination, answer })).rejects.toThrow(); expect(answer).not.toHaveBeenCalled();
});
