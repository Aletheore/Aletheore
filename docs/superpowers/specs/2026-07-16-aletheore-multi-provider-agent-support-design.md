# Aletheore Multi-Provider Agent Support Design

**Status:** Draft, pending review
**Date:** 2026-07-16

## Problem

`aletheore audit` today only ever works with one coding-agent CLI: Claude Code
(`ClaudeCodeAdapter`, the only entry in `KNOWN_ADAPTERS`). Not everyone can afford Claude Code —
many people already have free or cheaper access to OpenAI, Google Gemini, Mistral, xAI's Grok,
or a locally-run Llama model (via Ollama), or use OpenCode as their coding-agent CLI instead.
For Aletheore to be a genuinely usable, enterprise-credible tool rather than something only
useful to people who already have Claude Code installed, `audit` needs to support choosing from
a real set of providers — with explicit, honest consent whenever evidence leaves the machine to
reach a third-party API, since that is a real exception to Aletheore's "100% local, nothing
leaves this machine" positioning that today's single-adapter, CLI-only design has never had to
address.

## Goals

- Support Claude Code (existing), OpenCode, OpenAI, Google Gemini, Mistral, xAI Grok, and Ollama
  (local Llama or any other locally-hosted model) as audit providers.
- Every interactive `audit` run always shows a provider-selection menu, even when only one
  provider is available — transparency over silent convenience, consistent with an enterprise
  tool that might itself be audited for exactly this kind of behavior.
- Every run using an API-based provider (not Ollama, not the CLI-based ones) shows a fresh,
  specific consent prompt naming the exact provider, every single time, before any data leaves
  the machine — no remembered/standing consent.
- API keys are checked from the provider's standard environment variable first; if absent, the
  user is prompted once for that run, then explicitly offered a choice to save it locally for
  future runs or use it once and discard it.
- Keep the existing `AgentAdapter` interface (`is_available() -> bool`,
  `invoke(instruction, cwd) -> str`) completely unchanged. Every new provider is just a new
  implementation of that same interface — `report.py`'s `select_adapter`/`run_reasoning_phase`
  need zero changes.

## Non-Goals

- **No raw source-file access for API-based providers.** The tool-calling adapters (see below)
  are only ever given a `read_evidence_section` tool backed by `evidence.toon` — the same
  deterministic facts already in the evidence file. There is no tool that reads arbitrary
  repository source files. This is what makes the consent prompt's wording honest: it can say
  "this sends your repo's *evidence*, not your source code" and mean it as an architectural
  guarantee, not a promise that happens to be true today.
- **No remembered consent.** Every API-based run asks again, even for the same provider used
  moments ago. Deliberately more repetitive than convenient, given this is exactly the kind of
  behavior a security-conscious org would want to see audited.
- **No silent auto-pick in CI.** Non-interactive runs (no TTY) must pass `--agent` explicitly.
  If they don't, the run fails clearly rather than silently picking whichever provider happens
  to be available — auto-picking is exactly the kind of implicit behavior that shouldn't govern
  whether data reaches a third-party API.
- **Exact system-prompt content for the tool-calling adapters is deliberately not specified in
  this document.** The architecture (which tools exist, what data backs them, how the loop
  terminates) is specified below, but the actual prompt wording/structure sent to the model is
  being worked out in a separate, focused discussion before that part of the implementation
  plan is finalized — flagged explicitly here rather than silently decided.
- **Full tool-calling harnesses built independently per provider.** OpenAI, Mistral, xAI Grok,
  and Ollama all speak an OpenAI-compatible chat-completions + tool-calling API surface — this
  is one shared adapter implementation, parameterized by base URL / API key / model name, not
  four separate ones.

## Architecture: Three Adapter Archetypes

### 1. CLI-subprocess adapters (existing pattern)

`ClaudeCodeAdapter` (already shipped) and a new `OpenCodeAdapter`, following the exact same
shape: `is_available()` checks the binary is on `PATH` (`shutil.which`), `invoke()` shells out
with the instruction text and trusts the CLI's own file-reading/writing tools to read the
manual + `evidence.toon` and write `audit-report.md` itself (already-tested fallback in
`run_reasoning_phase` writes the returned text if the file wasn't written directly). OpenCode's
exact invocation syntax (flags, how it's told to run non-interactively with a single prompt)
needs verifying against the real CLI before being implemented — the same discipline every
previous CLI/language integration in this project has used, not assumed from documentation
alone.

### 2. One shared OpenAI-compatible tool-calling adapter

A single adapter class, parameterized per-provider by base URL, API key (or none, for Ollama),
and model name, reused for:

- **OpenAI** (`api.openai.com`)
- **Mistral** (their OpenAI-compatible endpoint)
- **xAI Grok** (their OpenAI-compatible endpoint)
- **Ollama** (`http://localhost:11434` by default, no API key — matches "calling from a
  localhost" directly)

Internally, `invoke(instruction, cwd)`:

1. Reads every `manual/*.md` file itself (small, fixed-size, embedded directly in the system
   prompt — no tool needed for these, since they're the operating instructions, not evidence
   data).
2. Parses `cwd/.aletheore/evidence.toon` (via the existing `toon.decode`) into a Python object,
   which backs a `read_evidence_section(path)` tool — the model requests specific dot-paths
   (`repository.modules`, `security.secrets`, etc.) on demand rather than the whole evidence
   blob being forced into context up front. This is a real, not cosmetic, difference from the
   CLI-based adapters: a large repo's full evidence could exceed some providers' context limits
   if embedded whole; on-demand section reads avoid that regardless of repo size.
3. Also exposes a `write_report_section(name, content)` tool, mapping directly onto the manual's
   own section-based output contract (Summary, Repository Intelligence, Git Intelligence,
   Architecture, Security, AI Usage, Perspectives, Roadmap, Evidence Gaps) — each section
   written via its own tool call, rather than hoping one long single-shot completion maintains
   the manual's strict per-finding citation/confidence rules all the way through. (Long
   single-shot generations degrading in instruction-following partway through is a known
   failure mode this design avoids by construction, not by hope.)
4. Loops (bounded — see below) until the model signals it's done, then assembles the
   accumulated sections into the final report text and **returns it as `invoke()`'s return
   value** — it does not need to write `audit-report.md` itself; the existing, already-tested
   fallback in `run_reasoning_phase` (write the returned text if the file wasn't written
   directly) handles that with zero changes to `report.py`.
5. Bounded by both a maximum tool-call round count and an overall wall-clock timeout (exact
   numbers are an implementation-time detail, not a design fork — mirroring the existing
   `INVOCATION_TIMEOUT_SECONDS = 600` precedent for Claude Code's subprocess).

### 3. Gemini

Google's OpenAI-compatibility layer's tool-calling maturity is not something to assume from
documentation — it gets verified empirically against a real key during implementation. If it
supports the same tool-calling shape as the shared adapter above, it can reuse that same
adapter with Gemini's compatibility base URL. If not, it gets its own small adapter using
Google's native SDK's tool-calling interface instead, with the same two tools
(`read_evidence_section`, `write_report_section`) and the same bounded-loop behavior.

## Provider Selection and Consent Flow

- **Interactive runs** (a human at a terminal, `sys.stdin.isatty()`) always show a
  provider-selection menu listing every *available* provider (CLI-based ones found on `PATH`;
  API-based ones with a usable key — from env var or willingness to enter one; Ollama if its
  local server responds) — even when exactly one is available. This is a deliberate change from
  today's `select_adapter`, which currently auto-picks silently when there's only one.
- **Non-interactive runs** (no TTY — CI, scripts) must pass `--agent` explicitly, or the run
  fails clearly with the same style of error `select_adapter` already raises for the ambiguous
  case today. No silent auto-pick, even with only one provider available.
- **Consent**: immediately after a provider is selected, if it's an API-based one (OpenAI,
  Mistral, Grok, Gemini — not Ollama, which is local, and not the CLI-based ones, which never
  send evidence through Aletheore's own network code), a specific prompt names the exact
  provider and states plainly that repository evidence (not source code) is about to be sent to
  it, requiring an explicit yes before continuing. Declining exits cleanly, the same way a
  missing adapter does today (evidence is still available locally for manual use).

## API Key Handling

For each API-based provider, in order:

1. Check the provider's standard environment variable (e.g. `OPENAI_API_KEY`, `MISTRAL_API_KEY`,
   `XAI_API_KEY`, `GEMINI_API_KEY` — exact names confirmed against each provider's own SDK/docs
   at implementation time).
2. If absent, prompt once, interactively, for that run.
3. After entry, explicitly ask whether to save it locally (e.g.
   `~/.config/aletheore/credentials.json`, restrictive file permissions) for future runs, or use
   it once and discard it. Both are legitimate choices Aletheore offers rather than picks for
   the user — a locally-saved key never leaves the machine either, so both options are
   consistent with the "nothing leaves this machine" story; only the actual API call to the
   provider is the exception, which is what the separate consent step is for.

## Testing Strategy

Same discipline as every other integration this project has built: real verification, not
fixtures alone.

- **CLI-subprocess (OpenCode)**: install the real CLI, confirm its actual invocation syntax via
  a live run, mirroring exactly how `ClaudeCodeAdapter` was originally verified.
- **Shared OpenAI-compatible tool-calling adapter**: verified against a real Ollama instance
  first (free, local, no API key needed, fastest iteration loop), then against at least one real
  paid API (a small, cheap real request) to confirm the shared adapter genuinely works
  cross-provider, not just against the one it was developed against.
- **Gemini**: a real API call to confirm whether its OpenAI-compatibility layer's tool-calling
  actually works as assumed, before deciding whether it needs its own adapter.
- **Consent/selection flow**: tested via mocked `is_available()`/input, the same pattern
  `select_adapter`'s existing ambiguous-multiple-adapters test already uses.

## Success Criteria

1. `aletheore audit` run interactively with two or more available providers shows a selection
   menu every time, including when exactly one is available.
2. Selecting an API-based provider always shows a fresh consent prompt naming that exact
   provider before any network call is made; declining exits cleanly without sending anything.
3. A missing API key is prompted for once, with an explicit, honest choice to save or discard it
   afterward — never silently persisted, never silently re-requested if declined.
4. Running non-interactively without `--agent` and with more than zero providers available still
   fails clearly rather than silently picking one.
5. The shared OpenAI-compatible adapter, verified against both a real local Ollama model and at
   least one real paid API, produces a real, grounded audit report citing actual evidence fields
   — not a demonstration against mocked responses only.
6. `report.py`'s `select_adapter`/`run_reasoning_phase` require zero code changes — every new
   provider is purely additive, a new `AgentAdapter` implementation registered in
   `KNOWN_ADAPTERS`.
