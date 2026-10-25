# Agent HTTP (Beta)

> See [agents.md](agents.md).

Serve an agent over FastAPI. Talk to it from another process with
`remote_agent`. Flows use `serve_flow` — [FastAPI](fastapi.md).

## Serve

```python
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from genkit import Genkit
from genkit.agent import InMemorySessionStore
from genkit_fastapi import serve_agent
from genkit_google_genai import GoogleAI

ai = Genkit(plugins=[GoogleAI()], model='googleai/gemini-flash-latest')

agent = ai.define_agent(
    name='weatherAgent',
    system='Weather assistant. Be concise.',
    store=InMemorySessionStore(),
)

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_methods=['*'],
    allow_headers=['*'],
    expose_headers=['X-Genkit-Stream-Id'],
)

app.include_router(serve_agent(agent), prefix='/api')

if __name__ == '__main__':
    uvicorn.run(app, host='0.0.0.0', port=8080)
```

Routes land at `/{agent.name}` by default. Override with `base_path=...`.

```bash
genkit start -- uv run server.py
```

## Remote client

```python
from genkit.agent import remote_agent

client = remote_agent(
    url='http://127.0.0.1:8080/api/weatherAgent',
    state_management='server',  # 'client' if the server has no store
)
chat = client.chat()
res = await chat.send('Weather in Tokyo?')
print(res.text, res.snapshot_id)

turn = chat.send_stream('And humidity?')
async for chunk in turn.stream:
    if chunk.text:
        print(chunk.text, end='', flush=True)
final = await turn.response

resumed = await client.load_chat(snapshot_id=final.snapshot_id)
await resumed.send('What city was that?')
```

Keep one chat for multi-turn. Match `state_management` to the server. Skip the
trailing slash on the URL.

## Auth

Resolve identity on the **server**. Mount the agent with a FastAPI dependency
that returns a plain dict. Tools read it with `Genkit.current_context()` for
the whole turn.

```python
from fastapi import FastAPI, Header, HTTPException
from genkit_fastapi import serve_agent

async def resolve_user(x_user: str | None = Header(default=None)) -> dict:
    if not x_user:
        raise HTTPException(status_code=401, detail='X-User header required')
    return {'sub': x_user, 'role': 'engineer'}

app = FastAPI()
app.include_router(
    serve_agent(agent, context_dependency=resolve_user),
    prefix='/api',
)

@ai.tool()
async def who_am_i() -> str:
    ctx = Genkit.current_context() or {}
    return f"user={ctx.get('sub')} role={ctx.get('role')}"
```

The remote client only forwards credentials:

```python
client = remote_agent(
    url='http://127.0.0.1:8080/api/supportAgent',
    state_management='server',
    headers={'X-User': 'alice'},
)
```

Return a `dict` from the dependency — Pydantic models are dropped. Custom
routes can pass context through `handle_genkit_request` ([FastAPI](fastapi.md)).
One-shot calls outside chat can use `ai.generate(..., context={...})`.
