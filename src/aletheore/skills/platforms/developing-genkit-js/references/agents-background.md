# Background Agents / Detaching (Beta)

> **Beta / preview API.** Detaching **requires a [session store](agents-sessions.md)** —
> the server needs somewhere to write the result when background work finishes.
> Read [agents.md](agents.md) first.

Detaching runs a turn in the background: the server saves a `pending` snapshot
and returns a `snapshotId` **immediately**, keeps processing, then updates the
snapshot to a terminal status (`completed` / `failed` / `aborted` / `expired`).
The client polls for completion and can abort.

## Define the agent (store required)

```ts
import { InMemorySessionStore } from 'genkit/beta';
import { ai } from './genkit.js';

export const backgroundAgent = ai.defineAgent({
  name: 'backgroundAgent',
  system:
    'You are a senior research analyst. Produce a comprehensive markdown report.',
  store: new InMemorySessionStore(), // REQUIRED for detach
});
```

Expose its companion actions so the client can poll/abort (see
[agents.md](agents.md#serve-an-agent-over-http)):

```ts
app.post('/api/backgroundAgent', expressHandler(backgroundAgent));
app.post(
  '/api/backgroundAgent/getSnapshot',
  expressHandler(backgroundAgent.getSnapshotDataAction)
);
app.post(
  '/api/backgroundAgent/abort',
  expressHandler(backgroundAgent.abortAgentAction)
);
```

## Server-side: detach + wait

`chat.detach(message)` returns a `DetachedTask` carrying the `snapshotId`.
`task.wait()` polls the store until a terminal state and resolves with the final
snapshot.

```ts
const chat = backgroundAgent.chat();
const task = await chat.detach('Write a report on renewable energy trends');
console.log(task.snapshotId); // available immediately

const snapshot = await task.wait({ intervalMs: 2000 });
console.log(snapshot?.status); // 'completed' | 'failed' | 'aborted' | 'expired'
```

## Client-side: detach + poll + abort

On the client (`genkit/beta/client`), `task.poll()` yields snapshots until a
terminal status; `task.abort()` cancels the background work.

```ts
import { remoteAgent, type DetachedTask } from 'genkit/beta/client';
import type { MessageData, Part } from 'genkit/beta';

const agent = remoteAgent({ url: '/api/backgroundAgent' });

// Submit — resolves immediately with a handle.
const task: DetachedTask = await agent.chat().detach('Quantum computing impact');
console.log(task.snapshotId);

// Poll until a terminal status.
for await (const snap of task.poll({ intervalMs: 2000 })) {
  if (snap.status === 'completed') {
    const messages: MessageData[] = snap.state?.messages ?? [];
    const lastModel = messages.filter((m) => m.role === 'model').at(-1);
    const text = (lastModel?.content ?? [])
      .filter((p: Part) => p.text)
      .map((p: Part) => p.text)
      .join('');
    console.log(text);
    break;
  } else if (snap.status === 'failed') {
    throw new Error('Background task failed on the server.');
  } else if (snap.status === 'aborted') {
    break;
  } else if (snap.status === 'expired') {
    // Worker stopped sending heartbeats (e.g. the server restarted) so the
    // task can never complete — treat as terminal.
    break;
  }
  // 'pending' → keep polling
}

// Abort an in-flight task at any time:
await task.abort();
```

## Status values

- `pending` — still processing.
- `completed` — completed successfully (read the result from `snapshot.state.messages`).
- `failed` — error during processing.
- `aborted` — cancelled by the client via `abort()`.
- `expired` — the background worker stopped responding (e.g. server restart);
  terminal, the task can never complete.

> Equivalent low-level wire protocol: the client sends `{ detach: true }` with
> the message; `poll`/`wait` hit the agent's `getSnapshot` action, and `abort`
> hits the `abort` action. `remoteAgent` wraps all of this for you.
