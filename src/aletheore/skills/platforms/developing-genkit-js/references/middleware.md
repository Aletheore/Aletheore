# Using Middleware

Middleware wraps generation to add cross-cutting behavior — retries, fallback,
extra tools, request/response transforms, etc. You attach middleware with the
`use: [...]` array, which is supported on `ai.generate` / `ai.generateStream`,
executable prompts (`definePrompt`), and agents (`defineAgent`).

```ts
import { retry } from '@genkit-ai/middleware';

const res = await ai.generate({
  model: googleAI.model('gemini-flash-latest'),
  prompt: 'Say hello',
  use: [retry({ maxRetries: 2 })],
});
```

The same array works on prompts and agents:

```ts
const myPrompt = ai.definePrompt({ name: 'p', prompt: '...', use: [retry()] });

const myAgent = ai.defineAgent({ name: 'a', system: '...', use: [retry()] });
```

Middleware is a configurable factory like `retry()` / `artifacts()` that returns
a reference for `use: [...]`. The `@genkit-ai/middleware` package ships a set of
ready-made ones (covered below); to write your own see
[building custom middleware](middleware-custom.md).

## Register middleware as a plugin

Register each middleware with Genkit via its `.plugin()` method. This is the
recommended setup — middleware still works in `use: [...]` without registering,
but **unregistered middleware is not visible to the Genkit Dev UI**.

```ts
import { genkit } from 'genkit';
import { googleAI } from '@genkit-ai/google-genai';
import { retry, artifacts } from '@genkit-ai/middleware';

export const ai = genkit({
  plugins: [googleAI(), retry.plugin(), artifacts.plugin()],
});

// Then reference the factory in `use: [...]`:
await ai.generate({
  model: googleAI.model('gemini-flash-latest'),
  prompt: 'Say hello',
  use: [retry({ maxRetries: 2 })],
});
```

## The `@genkit-ai/middleware` package

```bash
npm i @genkit-ai/middleware
```

Exports seven middleware factories. Register each via `.plugin()` (above), then
call `name(options)` in `use: [...]`.

### `retry(options?)`

Retries on transient errors with exponential backoff.

```ts
import { retry } from '@genkit-ai/middleware';

retry({
  maxRetries: 3, // default 3
  initialDelayMs: 1000, // default 1000
  maxDelayMs: 60000, // default 60000
  backoffFactor: 2, // default 2
  noJitter: false, // default false
  // statuses defaults to UNAVAILABLE, DEADLINE_EXCEEDED, RESOURCE_EXHAUSTED,
  // ABORTED, INTERNAL
});
```

### `fallback(options)`

Falls back to other models when the primary fails.

```ts
import { fallback } from '@genkit-ai/middleware';
import { googleAI } from '@genkit-ai/google-genai';

fallback({
  models: [googleAI.model('gemini-flash-latest')], // tried in order
  // statuses defaults to UNAVAILABLE, DEADLINE_EXCEEDED, RESOURCE_EXHAUSTED,
  // ABORTED, INTERNAL, NOT_FOUND, UNIMPLEMENTED
  isolateConfig: false, // default false — fallback inherits the request config
});
```

### `artifacts(options?)`

Adds `write_artifact` / `read_artifact` tools for session artifacts.

```ts
artifacts({ readonly: false }); // readonly true → read_artifact only
```

See [working with artifacts](agents-artifacts.md).

### `agents(options)`

Sub-agent delegation — injects a `delegate_to_<name>` tool per sub-agent.

```ts
agents({ agents: ['researcher', 'coder'], maxDelegations: 5 });
```

See [multi-agent orchestration](agents-multi-agent.md).

### `filesystem(options)`

Grants the model `list_files`, `read_file`, `write_file`, and
`search_and_replace` tools, sandboxed to a root directory.

```ts
filesystem({
  rootDirectory: './workspace', // required; all access is restricted to it
  allowWriteAccess: false, // default false (read-only)
  toolNamePrefix: '', // optional prefix for the injected tool names
});
```

### `skills(options?)`

Scans directories for skill files (frontmatter `name`/`description`), injects a
listing into the system prompt, and provides a `use_skill` tool.

```ts
skills({ skillPaths: ['skills'] }); // default ['skills']
```

### `toolApproval(options)`

Restricts tool execution to an approved list; throws a `ToolInterruptError` for
anything else (resumable via [interrupts](agents-human-in-the-loop.md)).

```ts
toolApproval({ approved: ['getWeather', 'search'] });
```

## Built-in model middleware (in core)

Some middleware ships in the `genkit` core (no extra package) — import from
`genkit/model/middleware`:

- `downloadRequestMedia({ maxBytes? })` — fetch URL media and inline it.
- `validateSupport({ name, ... })` — assert a model supports requested features.
- `simulateSystemPrompt({ preface? })` — emulate a system prompt for models
  without native support.
- `augmentWithContext(options?)` — inject retrieved context documents.
- `simulateConstrainedGeneration(options?)` — emulate constrained/JSON output.

```ts
import { simulateConstrainedGeneration } from 'genkit/model/middleware';

await ai.generate({
  model: someModel,
  prompt: '...',
  use: [simulateConstrainedGeneration()],
});
```

To write your own, see [building custom middleware](middleware-custom.md).
