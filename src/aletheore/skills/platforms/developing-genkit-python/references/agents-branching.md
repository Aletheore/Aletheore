# Agent Branching (Beta)

> **Beta / preview API.** Needs a [session store](agents-sessions.md).
> See [agents.md](agents.md).

A `snapshot_id` is an immutable checkpoint. Load it with
`load_chat(snapshot_id=...)` and send again — each turn creates a new leaf;
the original stays put.

```python
from genkit.agent import InMemorySessionStore

# After a fork, session_id is ambiguous — raise instead of guessing.
store = InMemorySessionStore(reject_ambiguous_session=True)

agent = ai.define_agent(
    name='designer',
    model='googleai/gemini-flash-latest',
    system='You help design a product landing page. Reply briefly.',
    store=store,
)

root = agent.chat()
await root.send('Plan a landing page for a note-taking app.')
checkpoint = root.snapshot_id

minimal = await agent.load_chat(snapshot_id=checkpoint)
await minimal.send('Direction: minimal.')

bold = await agent.load_chat(snapshot_id=checkpoint)
await bold.send('Direction: bold.')

resumed = await agent.load_chat(snapshot_id=bold.snapshot_id)
await resumed.send('Add a pricing section.')
```

Same pattern for time travel: reopen an earlier snapshot and send a different
follow-up. After forks, resume by `snapshot_id`, not `session_id`:

```python
# Ambiguous when reject_ambiguous_session=True — pick a leaf instead:
resumed = await agent.load_chat(snapshot_id=bold.snapshot_id)
```
