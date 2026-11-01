import { companionEnabled } from "@/lib/companion/enabled";
import { acquirePermit, LimitError } from "@/lib/companion/limits";
import { answerQuestion } from "@/lib/companion/service";

export const runtime = "nodejs";
export const maxDuration = 35;
const headers = { "Cache-Control": "no-store" };
function error(message: string, status: number, retryAfter?: number) { return Response.json({ error: message }, { status, headers: { ...headers, ...(retryAfter ? { "Retry-After": String(retryAfter) } : {}) } }); }
// Enabled only by COMPANION_LIVE_ENABLED=1 plus a provider key (see lib/companion/enabled.ts). Before turning it on:
// deployed ledger, provider spend cap and Vercel proxy verification. No process-local or fail-open fallback exists.
export async function POST(request: Request) {
  const apiKey = process.env.INDIEROUTER_API_KEY;
  if (!companionEnabled() || !apiKey) return error("Chat is not enabled. Please use the developer guide.", 503);
  const origin = request.headers.get("origin");
  if (!origin || origin !== new URL(request.url).origin) return error("Request origin is not allowed.", 403);
  if (request.headers.get("content-type")?.split(";")[0].trim().toLowerCase() !== "application/json") return error("Expected JSON.", 415);
  let raw = "";
  const reader = request.body?.getReader();
  if (!reader) return error("A question is required.", 400);
  let bytes = 0;
  let timedOut = false;
  const cancelRead = () => { void reader.cancel().catch(() => {}); };
  const bodyDeadline = setTimeout(() => { timedOut = true; cancelRead(); }, 3000);
  request.signal.addEventListener("abort", cancelRead, { once: true });
  if (request.signal.aborted) cancelRead();
  try {
    const decoder = new TextDecoder("utf-8", { fatal: true });
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      bytes += value.byteLength;
      if (bytes > 10000) { await reader.cancel(); return error("Question is too large.", 413); }
      raw += decoder.decode(value, { stream: true });
    }
    raw += decoder.decode();
    if (timedOut || request.signal.aborted) return error("Question upload timed out.", 408);
  } catch { return timedOut || request.signal.aborted ? error("Question upload timed out.", 408) : error("Couldn't read the question.", 400); }
  finally { clearTimeout(bodyDeadline); request.signal.removeEventListener("abort", cancelRead); }
  let body: unknown;
  try { body = JSON.parse(raw); } catch { return error("Invalid JSON.", 400); }
  if (!body || typeof body !== "object" || Array.isArray(body)) return error("Invalid request.", 400);
  const data = body as Record<string, unknown>;
  if (Object.keys(data).some((key) => !["question", "history"].includes(key)) || typeof data.question !== "string" || !data.question.trim() || data.question.length > 2000 || (data.history !== undefined && (!Array.isArray(data.history) || data.history.length > 2 || data.history.some((q) => typeof q !== "string" || q.length > 2000)))) return error("Invalid question or history.", 400);
  let permit: Awaited<ReturnType<typeof acquirePermit>>;
  try { permit = await acquirePermit(request); }
  catch (cause) {
    const limited = cause instanceof LimitError && cause.status === 429;
    return error(limited ? "The guide has reached its usage limit. Try later or use the developer guide." : "The guide's usage protection is unavailable. Please use the developer guide.", limited ? 429 : 503, cause instanceof LimitError ? cause.retryAfter : 60);
  }
  try {
    request.signal.throwIfAborted();
    const result = await answerQuestion(data.question.trim(), { apiKey, history: data.history as string[] | undefined, signal: request.signal });
    await permit.settle(result.usage, result.usageReliable, result.usageViolation);
    return Response.json(result.answer, { headers });
  } catch {
    // No refund when we cannot prove the provider's usage. Aborts and ambiguous retries consume the hold.
    try { await permit.unknown(); } catch { /* The original durable reservation remains charged. */ }
    return error("Couldn't reach the guide. Please retry later or open the developer guide.", 503, 60);
  }
}
