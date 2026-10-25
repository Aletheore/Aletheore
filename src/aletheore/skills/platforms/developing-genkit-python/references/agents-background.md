# Background Agents / Detaching (Beta)

> Needs a [session store](agents-sessions.md). See [agents.md](agents.md).

Hand work to the server and keep going. `detach` returns a task handle with a
`snapshot_id` right away. When you need the reply, load that snapshot.

```python
from genkit.agent import InMemorySessionStore

agent = ai.define_agent(
    name='backgroundAgent',
    model='googleai/gemini-flash-latest',
    system='Senior research analyst. Produce a comprehensive markdown report.',
    store=InMemorySessionStore(),
)

chat = agent.chat()
task = await chat.detach('Write a report on renewable energy trends')
print(task.snapshot_id)

snapshot = await task.wait(interval=2.0)
print(snapshot.status)

await task.abort()

done = await agent.load_chat(snapshot_id=task.snapshot_id)
print(done.messages)
```

## Stopping work

Always `await` aborts — otherwise nothing happens.

**Client stop** — `await turn.abort()` (or `asyncio.timeout` around the stream)
stops listening. The server may still finish. Use this mid-stream when you do
not have a snapshot id yet.

**Server cancel** — `await chat.abort()` or `await task.abort()`. Needs a store
and an existing snapshot (after a completed turn or after `detach`).

Aborted snapshots are not resume points. Reload the last good leaf.

On the first turn, a client abort can leave this chat without ids even if the
server later saves. Prefer `detach` + `task.abort()` when you need a
recoverable cancel.

## Parallel research

One `chat` can only host one detach. Fork a leaf from a shared checkpoint for
each branch:

```python
root = agent.chat()
await root.send('Context both researchers should know.')
checkpoint = root.snapshot_id

async def research(topic: str):
    leaf = await agent.load_chat(snapshot_id=checkpoint)
    task = await leaf.detach(f'Research {topic} in depth.')
    await task.wait(interval=2.0)
    done = await agent.load_chat(snapshot_id=task.snapshot_id)
    return topic, done.messages[-1]

import asyncio
results = await asyncio.gather(research('Postgres'), research('SQLite'))
```

Check the loaded messages after wait — status alone is not enough.
