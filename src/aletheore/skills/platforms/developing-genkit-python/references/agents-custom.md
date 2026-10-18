# Advanced Custom Agents — `define_custom_agent` (Beta)

> **Beta / preview API.** See [agents.md](agents.md).

`define_agent` runs a fixed prompt + tool loop. For your own orchestration
(multi-step, custom streaming, manual messages/state/artifacts), use
`ai.define_custom_agent`. Callers still use `chat.send` / `send_stream`.

## Anatomy

Register one async `fn`. Inside it, define `handle_turn`, hand it to
`sess.run`, return `sess.result()`:

```python
async def fn(sess: SessionRunner, ctx: ActionRunContext) -> AgentResult:
    async def handle_turn(inp: AgentInput, turn_ctx: TurnContext) -> TurnResult | None:
        # user message already in sess.get_messages()
        # model / tools / logic…
        # ctx.send_chunk(AgentStreamChunk(...)) to stream
        # sess.add_messages(...) to persist replies
        return TurnResult(finish_reason=AgentFinishReason.STOP)

    await sess.run(handle_turn)
    return await sess.result()


agent = ai.define_custom_agent(name='customCoder', fn=fn, store=store)
```

`handle_turn` runs once per user turn. Before it runs, the runtime appends
`inp.message` to history. Return a `TurnResult` (or `None`); common finish
reasons: `STOP`, `INTERRUPTED`, `FAILED`. Raising ends the invocation as
`FAILED`.

`turn_ctx` (store only): `snapshot_id` (name external worktrees/dirs under
this), `parent_snapshot_id`, `turn_index`. Without a store, `snapshot_id` is
`None`.

From `sess`: `get_messages` / `add_messages` / `set_messages`,
`get_custom` / `update_custom`, `get_artifacts` / `add_artifacts`.
`add_messages` and `add_artifacts` each take a **list**. Stream with
`ctx.send_chunk(AgentStreamChunk(...))`.

## Example

```python
from genkit import ActionRunContext, FinishReason, Genkit, Message
from genkit.agent import (
    AgentFinishReason,
    AgentInput,
    AgentResult,
    AgentStreamChunk,
    InMemorySessionStore,
    SessionRunner,
    TurnContext,
    TurnResult,
)
from genkit_google_genai import GoogleAI

ai = Genkit(plugins=[GoogleAI()])
store = InMemorySessionStore()


async def custom_coder_fn(sess: SessionRunner, ctx: ActionRunContext) -> AgentResult:
    async def handle_turn(inp: AgentInput, turn_ctx: TurnContext) -> TurnResult | None:
        history = await sess.get_messages()
        messages = [Message(m) for m in history] if history else None

        stream_resp = ai.generate_stream(
            model='googleai/gemini-flash-latest',
            system='Concise coding assistant.',
            messages=messages,
        )
        async for chunk in stream_resp.stream:
            ctx.send_chunk(AgentStreamChunk(model_chunk=chunk))

        res = await stream_resp.response
        if res.message:
            await sess.add_messages([res.message])

        fr = (
            AgentFinishReason.STOP
            if res.finish_reason == FinishReason.STOP
            else AgentFinishReason.UNKNOWN
        )
        return TurnResult(finish_reason=fr)

    await sess.run(handle_turn)
    return await sess.result()


agent = ai.define_custom_agent(name='customCoder', fn=custom_coder_fn, store=store)

chat = agent.chat()
await chat.send('What is a Python list comprehension?')
```

Persist model replies with `sess.add_messages` or the next turn won't see them.
Custom state → [state](agents-state.md). Artifacts → [artifacts](agents-artifacts.md).


## Non-streaming `handle_turn` with tools

```python
async def handle_turn(inp: AgentInput, ctx: TurnContext) -> TurnResult | None:
    history = [Message(m) for m in await sess.get_messages()]
    # ai.generate runs the tool loop for you
    res = await ai.generate(
        messages=history + list(inp.messages or []),
        tools=[get_time],
        system='You are helpful.',
    )
    return TurnResult(messages=res.messages, finish_reason=AgentFinishReason.STOP)
```

Tool inputs: [agents.md](agents.md#tool-inputs).
