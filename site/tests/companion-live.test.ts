// Opt in explicitly; ordinary test runs never spend money or read local credentials.
import { writeFileSync } from "node:fs";
import { expect, it } from "vitest";
import { CASES } from "./companion-eval-cases";
import { INJECTION_CASES } from "./companion-injection-cases";
import { answerQuestion, MODEL } from "@/lib/companion/service";

it.skipIf(process.env.RUN_COMPANION_LIVE !== "1")("evaluates the live model against the product and adversarial cases", async () => {
  const key = process.env.INDIEROUTER_API_KEY;
  if (!key) throw new Error("An IndieRouter key must be supplied through the environment.");
  type EvalCase = { id: string; question: string; history?: readonly string[]; status: string; contains?: string; command?: string };
  const cases: readonly EvalCase[] = process.env.COMPANION_EVAL_SUITE === "injection" ? INJECTION_CASES : CASES;
  const limit = Math.min(cases.length, Math.max(1, Math.floor(Number(process.env.COMPANION_EVAL_LIMIT) || cases.length)));
  const repeats = Math.min(3, Math.max(1, Math.floor(Number(process.env.COMPANION_EVAL_REPEATS) || 1)));
  const rows: unknown[] = [];
  let tokens = 0;
  let input = 0;
  let output = 0;
  let failures = 0;
  for (let round = 0; round < repeats; round++) {
    for (const test of cases.slice(0, limit)) {
      if (tokens > 400000) throw new Error("Evaluation token cap reached.");
      try {
        const start = Date.now();
        const result = await answerQuestion(test.question, { apiKey: key, history: test.history ? [...test.history] : [] });
        tokens += result.usage.total_tokens;
        input += result.usage.prompt_tokens;
        output += result.usage.completion_tokens;
        const pass = result.answer.status === test.status && (!test.contains || result.answer.text.includes(test.contains)) && (!test.command || result.answer.command?.includes(test.command));
        if (!pass) failures++;
        rows.push({ id: test.id, round, pass, expected: test.status, actual: result.answer.status, answer: result.answer, usage: result.usage, selection: "selection" in result ? result.selection : undefined, ms: Date.now() - start });
        console.info(`${pass ? "PASS" : "FAIL"} ${test.id}: ${result.answer.status}`);
      } catch (error) {
        failures++;
        rows.push({ id: test.id, round, pass: false, error: error instanceof Error ? error.message : "request_failed" });
        console.info(`FAIL ${test.id}: request failed`);
      }
    }
  }
  const summary = { model: MODEL, suite: process.env.COMPANION_EVAL_SUITE ?? "product", testedAt: new Date().toISOString(), cases: rows.length, failures, inputTokens: input, outputTokens: output, estimatedINRExGST: input * 6.73 / 1e6 + output * 22.43 / 1e6, note: "List-rate estimate without cached-input discount. Synthetic prompts only. Not proof of zero hallucinations.", results: rows };
  writeFileSync(process.env.COMPANION_EVAL_OUTPUT ?? "/private/tmp/aletheore-companion-live-eval.json", JSON.stringify(summary, null, 2));
  expect(failures, "See evaluation report for individual failures").toBe(0);
}, 1800000);
