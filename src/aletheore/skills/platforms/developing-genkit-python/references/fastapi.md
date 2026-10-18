# FastAPI — Genkit Python

## Install

```bash
uv add genkit-fastapi fastapi uvicorn
```

Import from `genkit_fastapi`:

- `serve_agent` / `serve_flow` — mount an agent or flow as a router
- `handle_genkit_request` — escape hatch for a custom `@app.post` that needs its own
  `Depends` / auth wiring while still speaking the Genkit wire format

For agents, see also [Agent HTTP](agents-http.md).

---

## Serve an agent or flow

```python
import uvicorn
from fastapi import FastAPI
from genkit import Genkit
from genkit.agent import InMemorySessionStore
from genkit_fastapi import serve_agent, serve_flow
from genkit_google_genai import GoogleAI

ai = Genkit(plugins=[GoogleAI()], model='googleai/gemini-flash-latest')

agent = ai.define_agent(
    name='weatherAgent',
    system='Weather assistant. Be concise.',
    store=InMemorySessionStore(),
)

@ai.flow()
async def hello(name: str) -> str:
    return f'Hello, {name}'

app = FastAPI()
# Routes use the action name as defined: agent `weatherAgent` → /api/weatherAgent;
# flow `hello` → /api/hello. Override with base_path=... if needed.
app.include_router(serve_agent(agent), prefix='/api')
app.include_router(serve_flow(hello), prefix='/api')

# Under genkit start, drive uvicorn from ai.run_main (don't nest uvicorn.run inside it).
async def main() -> None:
    import os
    config = uvicorn.Config(app, host='0.0.0.0', port=int(os.environ.get('PORT', '8080')))
    await uvicorn.Server(config).serve()

if __name__ == '__main__':
    ai.run_main(main())
```

---

## Streaming

`serve_agent`, `serve_flow`, and `handle_genkit_request` all stream when the client
sends `Accept: text/event-stream` (same wire path). Otherwise they return a one-shot
`{"result": ...}` JSON body.

**Wire format (SSE):**
```
data: {"message": "<chunk text>"}   ← one per ctx.send_chunk() call
data: {"message": "<chunk text>"}
data: {"result": <final output>}    ← sent once when the action completes
```

**Frontend:**
```js
const res = await fetch('/api/chat', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json', 'Accept': 'text/event-stream' },
  body: JSON.stringify({ data: { topic: 'quantum computing' } }),
});
const reader = res.body.getReader();
// decode and parse each `data: {...}` line
```

**curl test:**
```bash
curl -N -X POST http://localhost:8080/api/chat \
  -H 'Content-Type: application/json' \
  -H 'Accept: text/event-stream' \
  -d '{"data": {"topic": "quantum computing"}}'
```

---

## Minimal streaming FastAPI app

```python
import uvicorn
from pydantic import BaseModel
from fastapi import FastAPI
from genkit import Genkit, ActionRunContext
from genkit_fastapi import serve_flow
from genkit_google_genai import GoogleAI

ai = Genkit(plugins=[GoogleAI()], model='googleai/gemini-flash-latest')


class ChatInput(BaseModel):
    topic: str


@ai.flow()
async def chat(input: ChatInput, ctx: ActionRunContext) -> str:
    sr = ai.generate_stream(prompt=f'Tell me about {input.topic}.')
    full = ''
    async for chunk in sr.stream:
        if chunk.text:
            ctx.send_chunk(chunk.text)   # each chunk → SSE event on the wire
            full += chunk.text
    return full


app = FastAPI()
app.include_router(serve_flow(chat), prefix='/api')  # POST /api/chat

async def main() -> None:
    import os
    config = uvicorn.Config(app, host='0.0.0.0', port=int(os.environ.get('PORT', '8080')))
    await uvicorn.Server(config).serve()

if __name__ == '__main__':
    ai.run_main(main())
```

**Key:** the flow must accept `ctx: ActionRunContext` and call `ctx.send_chunk(text)`
to emit SSE chunks. Without `ctx.send_chunk`, the flow runs but streams nothing — the
client waits for the final result.

---

## Advanced use cases

### Nested flow streaming

Chain flows so a child's chunks surface on the parent's HTTP stream. Call the
child with `.run(..., on_chunk=ctx.send_chunk)` — do **not** pass `ctx` as a
second positional argument to `await child(input, ctx)` (that raises `TypeError`).

```python
class ResearchInput(BaseModel):
    topic: str

@ai.flow()
async def research(input: ResearchInput, ctx: ActionRunContext) -> str:
    sr = ai.generate_stream(prompt=f'Explain {input.topic} in depth.')
    full = ''
    async for chunk in sr.stream:
        if chunk.text:
            ctx.send_chunk(chunk.text)
            full += chunk.text
    return full


class HeadlineInput(BaseModel):
    text: str

@ai.flow()
async def make_headline(input: HeadlineInput) -> str:
    response = await ai.generate(prompt=f'One-line headline for: {input.text}')
    return response.text.strip()


class ReportInput(BaseModel):
    topic: str

@ai.flow()
async def report(input: ReportInput, ctx: ActionRunContext) -> str:
    headline = await make_headline(HeadlineInput(text=input.topic))
    ctx.send_chunk(f'# {headline}\n\n')

    body = (
        await research.run(
            ResearchInput(topic=input.topic),
            on_chunk=ctx.send_chunk,
        )
    ).response

    return f'# {headline}\n\n{body}'


app.include_router(serve_flow(report), prefix='/api')  # POST /api/report
```

**Rules:**
- Streaming children accept `ctx: ActionRunContext` and call `ctx.send_chunk`
- Parents forward chunks with `child.run(input, on_chunk=ctx.send_chunk)`
- Non-streaming children: `await child(input)` is fine

### Executing flows in parallel

Use `asyncio.gather` to run multiple flows concurrently. Only makes sense when children don't need to stream.

```python
import asyncio

class AnalysisInput(BaseModel):
    text: str

class CheckResult(BaseModel):
    issues: list[str]

class CombinedAnalysis(BaseModel):
    issues: list[str]

@ai.flow()
async def check_security(input: AnalysisInput) -> CheckResult:
    r = await ai.generate(
        prompt=f'List security concerns as a short comma-separated line (or "none"): {input.text[:2000]}',
    )
    raw = (r.text or '').strip()
    issues = [s.strip() for s in raw.split(',') if s.strip() and s.strip().lower() != 'none']
    return CheckResult(issues=issues)

@ai.flow()
async def check_bugs(input: AnalysisInput) -> CheckResult:
    r = await ai.generate(
        prompt=f'List likely bugs or correctness issues as a short comma-separated line (or "none"): {input.text[:2000]}',
    )
    raw = (r.text or '').strip()
    issues = [s.strip() for s in raw.split(',') if s.strip() and s.strip().lower() != 'none']
    return CheckResult(issues=issues)

@ai.flow()
async def check_style(input: AnalysisInput) -> CheckResult:
    r = await ai.generate(
        prompt=f'List style or clarity issues as a short comma-separated line (or "none"): {input.text[:2000]}',
    )
    raw = (r.text or '').strip()
    issues = [s.strip() for s in raw.split(',') if s.strip() and s.strip().lower() != 'none']
    return CheckResult(issues=issues)

@ai.flow()
async def analyze(input: AnalysisInput) -> CombinedAnalysis:
    security, bugs, style = await asyncio.gather(
        check_security(input),
        check_bugs(input),
        check_style(input),
    )
    return CombinedAnalysis(issues=security.issues + bugs.issues + style.issues)


app.include_router(serve_flow(analyze), prefix='/api')  # POST /api/analyze
```

---

## Structured output endpoint (non-streaming)

```python
class SentimentResult(BaseModel):
    sentiment: str        # positive / negative / neutral
    confidence: float     # 0.0–1.0
    key_phrases: list[str]

@ai.flow()
async def sentiment(input: AnalysisInput) -> SentimentResult:
    response = await ai.generate(
        prompt=f'Analyze sentiment: {input.text}',
        output_format='json',
        output_schema=SentimentResult,
    )
    return response.output


app.include_router(serve_flow(sentiment), prefix='/api')  # POST /api/sentiment
```

Client calls this without `Accept: text/event-stream` — gets `{"result": {...}}` back.

---

## Custom route with `handle_genkit_request`

When you need your own FastAPI dependencies (auth, tenancy, etc.) and still want
the Genkit wire format, call `handle_genkit_request` from a hand-written route:

```python
from fastapi import Depends, Request
from genkit_fastapi import handle_genkit_request


async def current_user(request: Request) -> dict:
    # Resolve auth however you like.
    return {'uid': request.headers.get('x-user-id', 'anon')}


@app.post('/api/secure-chat')
async def secure_chat(
    request: Request,
    user: dict = Depends(current_user),
):
    return await handle_genkit_request(
        request,
        action=chat,
        context={'auth': user},
    )
```

Prefer `serve_flow` / `serve_agent` with `context_dependency=` when a single
`Depends` is enough — use `handle_genkit_request` when the route shape itself
needs to be custom.

---

## Run with Dev UI

```bash
GEMINI_API_KEY=your-key genkit start -- uv run src/main.py
```

Leave the process running until the CLI prints something like:

```
Genkit Developer UI: http://localhost:4000
```

Open that URL. Port may differ if 4000 is busy.


## Python HTTP client (mounted flows)

```python
import httpx, asyncio

async with httpx.AsyncClient(base_url='http://127.0.0.1:18524') as client:
    async def call(path, data):
        r = await client.post(path, json={'data': data})
        r.raise_for_status()
        return r.json()['result']
    a, b = await asyncio.gather(
        call('/api/summarize', {'text': '...'}),
        call('/api/sentiment', {'text': '...'}),
    )
```

Wire shape is `{"data": ...}` in, `{"result": ...}` out (not raw flow args).
