# Agent Human-in-the-Loop / Interrupts (Beta)

> **Beta / preview API.** See [agents.md](agents.md).

An **interrupt** pauses mid-turn so your code (or a human) can decide, then
**resume**. Works with or without a [store](agents-sessions.md).

Flow: `send` → `finish_reason == INTERRUPTED` + `res.interrupts` → human input
→ `resume`.

## ToolApproval

Tools in `allowed_tools` run freely; everything else pauses. Empty list ⇒ every
tool needs approval. When you also mount `Filesystem` / `Artifacts`, remember
to allow-list (or deliberately interrupt on) `write_file` / `edit_file` /
`write_artifact` — the coding-agent example often auto-approves **reads only**.

```python
from uuid import uuid4

from pydantic import BaseModel, Field
from genkit_google_genai import GoogleAI
from genkit_middleware import Middleware, ToolApproval

from genkit import Genkit, ToolRequestPart
from genkit.agent import AgentFinishReason, InMemorySessionStore

ai = Genkit(plugins=[GoogleAI(), Middleware()])
tool_approval = ToolApproval(allowed_tools=[])


class TransferInput(BaseModel):
    amount: float
    to_account: str = Field(alias='toAccount')


class TransferOutput(BaseModel):
    success: bool
    transaction_id: str = Field(alias='transactionId')


@ai.tool(name='transferMoney', description='Transfer money between accounts.')
async def transfer_money(input: TransferInput) -> TransferOutput:
    return TransferOutput(success=True, transactionId=f'txn-{uuid4().hex[:12]}')


agent = ai.define_agent(
    name='bankingAgent',
    model='googleai/gemini-flash-latest',
    system='Banking assistant. Call transferMoney when the user asks to transfer money.',
    tools=[transfer_money],
    use=[tool_approval],
    store=InMemorySessionStore(),
)
```

## Detect and resume

```python
chat = agent.chat()

out1 = await chat.send('Transfer $500 to account 12345 for rent.')
# finish_reason == INTERRUPTED; transferMoney has NOT executed yet.

restart_parts: list[ToolRequestPart] = [
    intr.restart(resumed_metadata={'tool_approved': True}) for intr in out1.interrupts
]
out2 = await chat.resume(restart=restart_parts)
```

Resume builders (return parts; you still call `chat.resume`):

- `interrupt.restart(...)` — re-issue the tool request (ToolApproval after OK).
  `resumed_metadata={'tool_approved': True}` (camelCase `toolApproved` also ok)
- `interrupt.respond(output)` — supply a tool response without running the tool
  (use this to **deny**: e.g. `respond({'ok': False, 'error': 'denied by policy'})`)

```python
# Approve (runs the tool):
await chat.resume(
    restart=[intr.restart(resumed_metadata={'tool_approved': True}) for intr in out1.interrupts]
)

# Deny (model sees the respond payload; tool does not execute):
await chat.resume(
    respond=[intr.respond({'ok': False, 'error': 'export denied'}) for intr in out1.interrupts]
)

await chat.resume(
    respond=[a.respond({'approved': True})],
    restart=[b.restart(resumed_metadata={'tool_approved': True})],
)

turn = chat.resume_stream(restart=restart_parts)
async for chunk in turn.stream:
    if chunk.text:
        print(chunk.text, end='', flush=True)
final = await turn.response
```

Without a store: keep the same `chat`, or round-trip messages/state/artifacts
([client-managed state](agents.md#client-managed-state-no-store)).

Build resume parts from `res.interrupts` only. Loop until finish reason is no
longer `INTERRUPTED`. Don't treat an interrupted turn as the final reply.

For approval UIs, each interrupt exposes `.name`, `.ref`, and `.input` (the
pending tool call). Use those to render the prompt; then `.restart(...)` or
`.respond(...)`.
