// Opt in explicitly; ordinary test runs never spend money or read local credentials.
import { evaluateCompanion, type EvalCase } from "../scripts/companion-evaluation";
import { expect, it } from "vitest";
import { CASES } from "./companion-eval-cases";
import { INJECTION_CASES } from "./companion-injection-cases";
import { answerQuestion } from "@/lib/companion/service";

it.skipIf(process.env.RUN_COMPANION_LIVE !== "1")("evaluates the live model against the product and adversarial cases", async () => {
  const key = process.env.INDIEROUTER_API_KEY;
  if (!key) throw new Error("An IndieRouter key must be supplied through the environment.");
  const cases: readonly EvalCase[] = process.env.COMPANION_EVAL_SUITE === "injection" ? INJECTION_CASES : CASES;
  const limit = Math.min(cases.length, Math.max(1, Math.floor(Number(process.env.COMPANION_EVAL_LIMIT) || cases.length)));
  const repeats = Math.min(3, Math.max(1, Math.floor(Number(process.env.COMPANION_EVAL_REPEATS) || 1)));
  const { failures, stopped } = await evaluateCompanion({ cases: cases.slice(0, limit), repeats, suite: process.env.COMPANION_EVAL_SUITE ?? "product", answer: (question, history) => answerQuestion(question, { apiKey: key, history }) });
  expect(stopped, "See checkpoint report for early-stop reason").toBeUndefined();
  expect(failures, "See evaluation report for individual failures").toBe(0);
}, 1800000);
