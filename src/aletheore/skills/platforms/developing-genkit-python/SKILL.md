---
name: developing-genkit-python
description: Develop AI-powered applications using Genkit in Python. Use when the user asks about Genkit, AI agents, flows, or tools in Python, or when encountering Genkit errors, import issues, or API problems.
metadata:
  version: 1.0.0
  category: AiAndMachineLearning
skill_id: platforms/developing-genkit-python
title: Developing Genkit Python
source_url: https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/developing-genkit-python
license: Apache-2.0
shelf: build
aletheore: {"id": "platforms/developing-genkit-python", "shelf": "build", "category": "platforms", "origin": "https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/developing-genkit-python", "license": "Apache-2.0", "license_source": "LICENSE", "upstream_commit": "e86adb9e2b80220a593c2be9a68627171fd17afb", "quality": 4, "curation_note": "official Google Cloud / Genkit / Gemini", "needs_tool": null, "vet": {"injection_pattern_hits": ["pipe-to-shell"], "portability_flags": [], "companion_files": 16, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {"pipe-to-shell": 1}, "status": "ready", "mentions_agents": []}
---

# Genkit Python

Build AI features in Python — generate, stream, tools, flows, and multi-turn
agents — with one SDK.

## Prerequisites

- Python **3.10+** and **`uv`** ([install](https://docs.astral.sh/uv/getting-started/installation/))
- Genkit CLI: `npm install -g genkit-cli` if `genkit --version` is missing

New app? [Setup](references/setup.md). Patterns? [Examples](references/examples.md).

## Hello World

```python
from genkit import Genkit
from genkit_google_genai import GoogleAI

ai = Genkit(
    plugins=[GoogleAI()],
    model='googleai/gemini-flash-latest',
)

async def main():
    response = await ai.generate(prompt='Tell me a joke about Python.')
    print(response.text)

if __name__ == '__main__':
    ai.run_main(main())
```

## Agents (Beta)

Multi-turn chats with history, typed state, human approval, branching, and
background work. Start here: [Agents](references/agents.md).

```python
chat = agent.chat()
res = await chat.send('Hello')           # AgentResponse
turn = chat.send_stream('Hello')         # AgentTurn — .stream / .response
```

More: [sessions](references/agents-sessions.md) ·
[HITL](references/agents-human-in-the-loop.md) ·
[branching](references/agents-branching.md) ·
[background](references/agents-background.md) ·
[state](references/agents-state.md) ·
[artifacts](references/agents-artifacts.md) ·
[custom](references/agents-custom.md) ·
[HTTP](references/agents-http.md)

## Imports

- Google AI: `from genkit_google_genai import GoogleAI`
- Agents: `from genkit.agent import InMemorySessionStore, ...`
- Middleware: `from genkit_middleware import Middleware, ToolApproval, ...`
- FastAPI: `from genkit_fastapi import serve_agent, serve_flow`
- Evals: `from genkit_evaluators import register_genkit_evaluators`

## Workflow

1. **Agent or flow?** If the task is conversational, multi-turn, or described as
   "an agent", "assistant", or "chatbot", build it with `ai.define_agent` (see
   [Agents](references/agents.md)) rather than hand-rolling a `generate` + tools
   loop inside a flow. Reach for a plain flow only for single-shot, stateless
   generation.
2. Set **`GEMINI_API_KEY`**. Use prefixed model ids (`googleai/gemini-flash-latest`).
3. Enter via **`ai.run_main(main())`** for Genkit apps (especially under
   `genkit start`). See [Common Errors](references/common-errors.md).
4. Run with [Dev Workflow](references/dev-workflow.md) (`genkit start` + Dev UI).
5. Verify with traces, not a blind run. Running the app directly (`uv run`)
   does **not** capture dev traces. See [Genkit CLI](#genkit-cli-recommended)
   for how to run your app and capture traces.
6. Stuck? [Common Errors](references/common-errors.md) first.

## Genkit CLI (recommended)

`genkit start` unintrusively wraps any Python program that uses the Genkit library, running it unchanged while capturing traces from every Genkit action so you can prove tools were actually called and inspect model I/O from the terminal, even for headless checks. It forwards stdio, so interactive CLI tools that rely on stdin/stdout work without issues. Running the app directly (`uv run`) skips trace capture, so you're debugging blind.

**Primary pattern (default):** prefix `genkit start --` to your normal run command. This collects telemetry from any Genkit code your program runs, whether triggered from the dev UI, your own web server/web UI, or a plain script:
```bash
genkit start -- uv run src/main.py
genkit start --noui -- uv run src/main.py   # same, without the Dev UI (still a persistent server)
```
`genkit start` runs until you stop it with Ctrl+C. That is expected and correct for the common cases: a server your web/mobile app calls, or an interactive CLI you exit yourself. `--noui` only drops the Dev UI; it is **not** a one-shot command and will not exit on its own. Do **not** use `genkit start` as a blocking step in automated/non-interactive contexts; use `flow:run` (below) for that.

**Non-interactive use (agents/CI):** add the global `--non-interactive` flag before `--` so the CLI uses defaults and never blocks on a prompt (e.g. the first-run analytics notice): `genkit start --non-interactive -- uv run src/main.py` (works with `flow:run` too).

**Run a flow (`flow:run`):** invoke a specific flow by name from the CLI. Append your run command after `--` to spin up the runtime just for this run (the command runs as-is to register your flows):
```bash
genkit flow:run myFlow '{"data": "input"}' -- uv run src/main.py
```
This is **self-terminating**: it runs the flow once, prints a `Trace ID`, then exits, so it's the right choice for a quick, non-interactive check (unlike `genkit start`). Note: `flow:run` runs **flows** (`@ai.flow()`), not agents; you can't `flow:run` an agent (`ai.define_agent`) directly. To exercise an agent from the CLI, wrap one turn in a throwaway flow and run that (see [Agents](references/agents.md)).

**Debugging with traces:** the fastest way to see prompts, model inputs/outputs, tool calls, latencies, and errors. Inspect from the terminal after any run under `genkit start`:
```bash
genkit trace:list                        # find recent trace IDs
genkit trace:get <traceId>               # full trace details (inputs, outputs, tool calls, errors)
genkit trace:get <traceId> --format json # machine-readable JSON, safe to pipe into jq or other parsers
```

For machine-readable output, pass `--format json` to get clean JSON you can pipe into `jq` or other parsers. The **default** output is human-oriented (banner/log lines, possible truncation on large traces), so don't pipe that form directly; use `--format json`, grep, or the Dev UI trace viewer.


See [Dev Workflow](references/dev-workflow.md) for the full checklist and Dev UI walkthrough.

## References

- [Examples](references/examples.md): Structured output, streaming, flows, tools, embeddings.
- [Setup](references/setup.md): New project bootstrap and plugins.
- [Common Errors](references/common-errors.md): Read first when something breaks.
- [FastAPI](references/fastapi.md): HTTP, `genkit_fastapi_handler`, parallel flows.
- [Dotprompt](references/dotprompt.md): `.prompt` files and helpers.
- [Evals](references/evals.md): Evaluators and datasets.
- [Dev Workflow](references/dev-workflow.md): `genkit start`, Dev UI, checklist.
- [Agents (Beta)](references/agents.md): Multi-turn API.
