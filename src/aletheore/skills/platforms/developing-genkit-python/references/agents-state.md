# Agent State (Beta)

> See [agents.md](agents.md) · [sessions](agents-sessions.md).

A session carries three layers: messages, custom state (`state_schema`), and
artifacts. With a schema, `chat.state` and streamed `chunk.custom` come back as
that model.

## Two modes

**With a store** — Genkit owns history. Resume by `snapshot_id` or
`session_id`. You cannot seed with `chat(state=...)`.

**Without a store** — your app owns history. Seed and round-trip yourself:

```python
chat = agent.chat(state=Profile(name='Ada', tier='pro'))
await chat.send('Hello')
resumed = agent.chat(
    messages=chat.messages, state=chat.state, artifacts=chat.artifacts
)
```

Custom state is for your product (routing, UI). It is not injected into the
model unless you put it in the system prompt or messages.

`state_schema` alone does not fill `chat.state`. Something in the turn must call
`update_custom`. Prefer tools on a normal agent so you keep middleware.

## Client-managed typed state

```python
from pydantic import BaseModel

from genkit import Genkit
from genkit_google_genai import GoogleAI

ai = Genkit(plugins=[GoogleAI()], model='googleai/gemini-flash-latest')


class Profile(BaseModel):
    name: str
    tier: str = 'free'


agent = ai.define_agent(
    name='profileAgent',
    system='Greet the user by name when you know it.',
    state_schema=Profile,
)

chat = agent.chat(state=Profile(name='Ada', tier='pro'))
await chat.send('Hello')
print(chat.state.name)
```

## Store + middleware

Update custom state from tools with `ai.current_session()`. Mutators are
async. Coerce to your model if the callback receives a dict:

```python
from pydantic import BaseModel, Field

from genkit import Genkit
from genkit.agent import InMemorySessionStore
from genkit_google_genai import GoogleAI
from genkit_middleware import Middleware, ToolApproval


class CaseState(BaseModel):
    case_id: str = ''
    status: str = 'open'


class OpenCaseInput(BaseModel):
    case_id: str = Field(description='Support case id')


ai = Genkit(plugins=[GoogleAI(), Middleware()], model='googleai/gemini-flash-latest')


@ai.tool(name='openCase', description='Open or update the support case id.')
async def open_case(input: OpenCaseInput) -> dict:
    sess = ai.current_session()
    if sess is None:
        return {'ok': False, 'error': 'no session'}

    async def mutate(c: object) -> CaseState:
        base = c if isinstance(c, CaseState) else CaseState.model_validate(c or {})
        return CaseState(case_id=input.case_id, status=base.status)

    await sess.update_custom(mutate)
    return {'ok': True, 'case_id': input.case_id}


agent = ai.define_agent(
    name='supportOps',
    system='Support ops. Call openCase when asked. Be brief.',
    tools=[open_case],
    state_schema=CaseState,
    use=[ToolApproval(allowed_tools=['openCase'])],
    store=InMemorySessionStore(),
)
```

## Live patches

In a [custom agent](agents-custom.md), `update_custom` streams to
`chunk.custom`:

```python
async def bump(c):
    return {'turns': (c or {}).get('turns', 0) + 1}

await sess.update_custom(bump)
```

## Redacting on the way out

`state_transform` and `chunk_transform` shape what clients see. They do not
change what the store keeps. Return a full `SessionState` from
`state_transform`. Returning `None` from `chunk_transform` drops that chunk.

```python
from genkit.agent import SessionState

def redact(state: SessionState) -> SessionState:
    custom = dict(state.custom or {})
    if 'api_key' in custom:
        custom['api_key'] = 'REDACTED'
    return SessionState(messages=state.messages, custom=custom, artifacts=state.artifacts)

agent = ai.define_agent(..., state_schema=SecretState, state_transform=redact, store=...)
```
