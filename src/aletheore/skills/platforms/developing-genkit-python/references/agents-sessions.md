# Sessions & Persistence (Beta)

> See [agents.md](agents.md).

With a store, the server owns history. Each turn writes an immutable snapshot —
the backbone for branching and [background work](agents-background.md).
Approvals work with or without a store ([HITL](agents-human-in-the-loop.md)).

## Choose a store

```python
from genkit.agent import InMemorySessionStore, FileSessionStore

mem_store = InMemorySessionStore()              # gone on restart
file_store = FileSessionStore('./.snapshots')   # one JSON file per snapshot

# Optional: prune long chains; refuse ambiguous session_id after forks
pruning = FileSessionStore(
    './.snapshots',
    max_persisted_chain_length=3,
    reject_ambiguous_session=True,
)
```

```python
from genkit import Genkit
from genkit_google_genai import GoogleAI

ai = Genkit(plugins=[GoogleAI()], model='googleai/gemini-flash-latest')

agent = ai.define_agent(
    name='logbookAgent',
    system='You are a personal logbook assistant.',
    store=file_store,
)

chat = agent.chat()
res = await chat.send('Log this: I started studying Genkit today.')
print(res.snapshot_id)

resumed = await agent.load_chat(snapshot_id=res.snapshot_id)
await resumed.send('Add another note.')
```

## Typed state

Pass `state_schema`. Seed with `chat(state=...)` only when there is no store.
With a store, update fields from tools via `ai.current_session()` —
[state](agents-state.md).

## What a store unlocks

Without: multi-turn on one chat, interrupts.

With: durable ids, `load_chat`, branching, detach / background tasks, server
abort.

## Many users, one agent

Each `agent.chat()` gets its own session on a shared store. Resume with the
right `session_id` or `snapshot_id` per user.
