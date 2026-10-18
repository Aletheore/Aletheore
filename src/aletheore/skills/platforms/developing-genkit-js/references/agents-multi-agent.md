# Multi-Agent Orchestration / Sub-Agents (Beta)

> **Beta / preview API.** Sub-agent delegation uses the `agents()` middleware
> from `@genkit-ai/middleware`. Read [agents.md](agents.md) first.

A common pattern is an **orchestrator** agent that delegates tasks to
specialized **sub-agents** (e.g. a `researcher` and a `coder`). The `agents()`
middleware injects one delegation tool per sub-agent (`delegate_to_<name>`),
appends a `<sub-agents>` block to the orchestrator's system prompt, and — when
the model calls a delegation tool — runs the sub-agent and returns its response
as the tool result.

## 1. Define the sub-agents

Give each sub-agent a `description` — it's auto-discovered and shown to the
orchestrator so the model knows when to delegate.

```ts
import { artifacts, retry } from '@genkit-ai/middleware';
import { ai, defaultModel } from './genkit.js';

export const researcher = ai.defineAgent({
  name: 'researcher',
  description:
    'A thorough research assistant that searches the web and provides ' +
    'well-sourced answers.',
  model: defaultModel,
  system:
    'You are a thorough research assistant. Save findings with write_artifact.',
  maxTurns: 10,
  use: [retry(), artifacts()],
});

export const coder = ai.defineAgent({
  name: 'coder',
  description: 'An expert programmer that writes clean, well-commented code.',
  system: 'You are an expert programmer. Save code with write_artifact.',
  maxTurns: 10,
  use: [artifacts(), retry()],
});
```

## 2. Wire up the orchestrator

Add `agents()` to the orchestrator's `use: [...]`. Each entry is a name string
(description auto-discovered) or `{ name, description }` to override.

```ts
import { agents, artifacts, retry } from '@genkit-ai/middleware';
import { ai } from './genkit.js';

export const orchestratorAgent = ai.defineAgent({
  name: 'orchestratorAgent',
  system: `You are a helpful project assistant. Analyze the request and
delegate to the appropriate sub-agent. If it needs research AND code, call them
sequentially, then synthesize a final answer.`,
  use: [
    agents({
      agents: [
        'researcher', // auto-discovered description
        {
          name: 'coder',
          description: 'Writes, debugs, and explains code. Use for programming.',
        },
      ],
      maxDelegations: 5, // guard rail against runaway delegation loops
      historyLength: 4, // forward the last N user/model messages as context
      artifactStrategy: 'session', // see "Sharing artifacts" below
    }),
    artifacts({ readonly: true }), // read sub-agent artifacts via read_artifact
    retry(),
  ],
});
```

Run it like any other agent:

```ts
const chat = orchestratorAgent.chat();
const turn = chat.sendStream(
  'Research the best sorting algorithms, then write a TypeScript quicksort.'
);
for await (const chunk of turn.stream) process.stdout.write(chunk.text ?? '');
const res = await turn.response;
```

## `agents()` options

- `agents` (required): array of sub-agent refs. A `string` (name, description
  auto-discovered from the registry) or `{ name, description? }` (explicit
  override).
- `toolPrefix`: prefix for generated tool names. Default `"delegate_to"` →
  `delegate_to_<agent>`. Empty string uses bare agent names.
- `maxDelegations`: max delegations per `generate` call. Prevents runaway loops.
- `historyLength`: number of recent user/model messages forwarded to sub-agents
  as context. `0`/omitted sends only the task description. (Only client-managed
  sub-agents — no `store` — accept ad-hoc seeded history; server-managed
  sub-agents skip it and just receive the task.)
- `artifactStrategy`: `'inline'` (default) or `'session'` — see below.

## Sharing artifacts between agents

Sub-agents can produce [artifacts](agents-artifacts.md). `artifactStrategy`
controls how they reach the orchestrator:

- `'inline'` (default): artifact content is included in the delegation tool
  result (so the model sees it directly) **and** merged into the parent session.
- `'session'`: artifacts are merged into the parent session only; the tool
  result lists artifact names, not content. Pair with the `artifacts()`
  middleware so the orchestrator can `read_artifact` on demand (the pattern
  shown above). Merged artifacts are namespaced by an invocation ID
  (`<agentName>_<rand>/<name>`).

## Other middleware

`@genkit-ai/middleware` also exports `retry`, `fallback`, `filesystem`,
`skills`, and `toolApproval`. They attach the same way via `use: [...]` on an
agent (or on `ai.generate`) — see [using middleware](middleware.md). `retry()`
is commonly paired with delegation:

```ts
import { retry } from '@genkit-ai/middleware';

retry({
  maxRetries: 3, // default 3
  initialDelayMs: 1000, // default 1000
  maxDelayMs: 60000, // default 60000
  backoffFactor: 2, // default 2 (exponential backoff)
  // statuses defaults to UNAVAILABLE, DEADLINE_EXCEEDED, RESOURCE_EXHAUSTED,
  // ABORTED, INTERNAL
});
```

> Note: if a sub-agent triggers an [interrupt](agents-human-in-the-loop.md), it
> is reported back to the orchestrator as a normal tool response (not propagated
> as a resumable interrupt). Interactive, stateful sub-agent interrupts are a
> future feature — delegate self-contained tasks.
