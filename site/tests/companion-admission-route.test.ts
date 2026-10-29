import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { POST } from "@/app/api/companion/route";
import { acquirePermit, LimitError } from "@/lib/companion/limits";
import { answerQuestion } from "@/lib/companion/service";
vi.mock("@/lib/companion/limits", async () => ({ ...await vi.importActual("@/lib/companion/limits"), acquirePermit: vi.fn() }));
vi.mock("@/lib/companion/service", () => ({ answerQuestion: vi.fn() }));
const permit = { settle: vi.fn(), unknown: vi.fn() };
const question = () => new Request("http://localhost:3101/api/companion", { method: "POST", headers: { origin: "http://localhost:3101", "Content-Type": "application/json" }, body: '{"question":"Install Aletheore"}' });
beforeEach(() => {
  vi.resetAllMocks(); vi.stubEnv("NODE_ENV", "development"); vi.stubEnv("COMPANION_LIVE_ENABLED", "1"); vi.stubEnv("INDIEROUTER_API_KEY", "synthetic-test-key");
  vi.mocked(acquirePermit).mockResolvedValue(permit);
});
afterEach(() => { vi.unstubAllEnvs(); vi.useRealTimers(); });
it.each([429,503])("never calls the model when admission returns %s", async (status) => {
  vi.mocked(acquirePermit).mockRejectedValue(new LimitError(status,60));
  const response = await POST(question());
  expect(response.status).toBe(status); expect(response.headers.get("retry-after")).toBe("60"); expect(answerQuestion).not.toHaveBeenCalled();
});
it("does not refund ambiguous failures or retry the model automatically", async () => {
  vi.mocked(answerQuestion).mockRejectedValue(new Error("timeout"));
  expect((await POST(question())).status).toBe(503);
  expect(answerQuestion).toHaveBeenCalledTimes(1); expect(permit.unknown).toHaveBeenCalledTimes(1); expect(permit.settle).not.toHaveBeenCalled();
});
it("refuses to return paid output if usage settlement is unavailable", async () => {
  vi.mocked(answerQuestion).mockResolvedValue({ answer: { text: "public docs", sources: [] }, usage: { prompt_tokens: 100, completion_tokens: 1, total_tokens: 101 }, usageReliable: true, usageViolation: false, corpus: "test" });
  permit.settle.mockRejectedValue(new Error("storage offline"));
  const response = await POST(question());
  expect(response.status).toBe(503); expect(await response.text()).not.toContain("public docs"); expect(permit.unknown).toHaveBeenCalledTimes(1);
});
it("ignores request fields that try to select a free bucket or reservation", async () => {
  const request = new Request("http://localhost:3101/api/companion", { method: "POST", headers: { origin: "http://localhost:3101", "Content-Type": "application/json" }, body: '{"question":"Install Aletheore","reservation":"reuse-this","usageReliable":true,"clientId":"new-user"}' });
  expect((await POST(request)).status).toBe(400); expect(acquirePermit).not.toHaveBeenCalled(); expect(answerQuestion).not.toHaveBeenCalled();
});
it("bounds time spent waiting for a stalled request body before model admission", async () => {
  vi.useFakeTimers();
  const body = new ReadableStream({ start(controller) { controller.enqueue(new TextEncoder().encode('{"question":')); } });
  const request = new Request("http://localhost:3101/api/companion", { method: "POST", headers: { origin: "http://localhost:3101", "Content-Type": "application/json" }, body, duplex: "half" } as RequestInit);
  const pending = POST(request);
  await vi.advanceTimersByTimeAsync(3001);
  expect((await pending).status).toBe(408); expect(acquirePermit).not.toHaveBeenCalled(); expect(answerQuestion).not.toHaveBeenCalled();
});
