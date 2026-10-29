// Operator-only evaluation runner. Checkpoints precede spending and follow every case.
import { writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { MODEL, type answerQuestion } from "../src/lib/companion/service";
export type EvalCase = { id: string; question: string; history?: readonly string[]; status: string; contains?: string; command?: string };
export const evaluationDestination = () => process.env.COMPANION_EVAL_OUTPUT ?? join(tmpdir(), "aletheore-companion-live-eval.json");
export async function evaluateCompanion({ cases, repeats, suite, answer, destination = evaluationDestination(), log = console.info }: {
  cases: readonly EvalCase[]; repeats: number; suite: string;
  answer: (question: string, history: string[]) => ReturnType<typeof answerQuestion>;
  destination?: string; log?: (message: string) => void;
}) {
  const rows: unknown[] = [];
  let tokens = 0;
  let input = 0;
  let output = 0;
  let failures = 0;
  let stopped: string | undefined;
  function checkpoint() {
    const summary = { model: MODEL, suite: suite, testedAt: new Date().toISOString(), cases: rows.length, failures, stopped, inputTokens: input, outputTokens: output, estimatedINRExGST: input * 6.73 / 1e6 + output * 22.43 / 1e6, note: "List-rate estimate without cached-input discount. Synthetic prompts only. Not proof of zero hallucinations.", results: rows };
    writeFileSync(destination, JSON.stringify(summary, null, 2), "utf8");
  }
  // Check writability before spending; persist after every case, including early stops.
  checkpoint();
  evaluation: for (let round = 0; round < repeats; round++) {
    for (const test of cases) {
      if (tokens >= 400000) { stopped = "Evaluation token cap reached."; break evaluation; }
      try {
        const start = Date.now();
        const result = await answer(test.question, test.history ? [...test.history] : []);
        if (!result.usageReliable || result.usageViolation) stopped = "Unreliable provider usage; evaluation stopped conservatively.";
        tokens += result.usage.total_tokens;
        input += result.usage.prompt_tokens;
        output += result.usage.completion_tokens;
        if (tokens >= 400000) stopped ??= "Evaluation token cap reached.";
        const pass = result.answer.status === test.status && (!test.contains || result.answer.text.includes(test.contains)) && (!test.command || result.answer.command?.includes(test.command));
        if (!pass) failures++;
        rows.push({ id: test.id, round, pass, expected: test.status, actual: result.answer.status, answer: result.answer, usage: result.usage, selection: "selection" in result ? result.selection : undefined, ms: Date.now() - start });
        log(`${pass ? "PASS" : "FAIL"} ${test.id}: ${result.answer.status}`);
      } catch (error) {
        failures++;
        rows.push({ id: test.id, round, pass: false, error: error instanceof Error ? error.message : "request_failed" });
        log(`FAIL ${test.id}: request failed`);
        stopped = "Unknown request usage; evaluation stopped conservatively.";
      } finally { checkpoint(); }
      if (stopped) break evaluation;
    }
  }
  checkpoint();
  return { failures, stopped, cases: rows.length };
}
