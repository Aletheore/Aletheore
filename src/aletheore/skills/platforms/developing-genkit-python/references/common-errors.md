# Common Errors

Quick fixes. Longer recipes live in the linked guides.

---

## Wrong Google AI import

```python
from genkit_google_genai import GoogleAI
```

```bash
uv add genkit genkit-google-genai
```

---

## Streaming TypeError

Do not await the stream handle — await the response:

```python
turn = chat.send_stream('hi')
async for chunk in turn.stream: ...
res = await turn.response

sr = ai.generate_stream(prompt='...')
async for chunk in sr.stream: ...
final = await sr.response
```

---

## Tool schema must be an object

Wrap tool params in a Pydantic model. Bare `str` / `float` arguments fail on
Gemini. See [agents.md](agents.md).

```python
class WeatherInput(BaseModel):
    city: str

@ai.tool()
async def get_weather(input: WeatherInput) -> str: ...
```

---

## `define_tool` missing

Use `@ai.tool()`.

---

## Model id without a prefix

Use `googleai/gemini-flash-latest`, not bare `gemini-flash-latest`.

---

## Missing `.json` / `.message` on the response

Plain text → `response.text`. Structured output → `response.output`.

---

## Event loop issues under `genkit start`

Use `ai.run_main(main())` for long-lived apps. See [fastapi.md](fastapi.md)
for serving under Dev UI.

---

## No `snapshot_id` after a turn

Attach a `store`. Without one, pass messages / state / artifacts yourself —
[agents.md](agents.md).

---

## Cannot send state to a server-managed agent

Store-backed chats resume by id. Update fields inside the turn —
[agents-state.md](agents-state.md).

---

## Ambiguous session after a fork

Resume with a leaf `snapshot_id`, not `session_id` —
[agents-branching.md](agents-branching.md).

---

## Approval resume does nothing

Pass approval metadata on the restart part:

```python
await chat.resume(
    restart=[
        i.restart(resumed_metadata={'tool_approved': True})
        for i in out.interrupts
    ]
)
```

Build from `res.interrupts`. Full flow:
[agents-human-in-the-loop.md](agents-human-in-the-loop.md).

---

## Abort seems to do nothing

Await it. Client stop: `turn.abort()` (or a timeout around the stream).
Server cancel: `chat.abort()` / `task.abort()` after you have a snapshot.
Details: [agents-background.md](agents-background.md).

---

## Tool raise becomes `AgentError`

A raised tool ends the turn. Catch `AgentError` on `chat.send`. Prefer
returning a soft-error dict so the model can recover. History stays usable —
the next send continues from the last good snapshot.

---

## Remote client oddities

Match `state_management` to the server. No trailing slash on the URL. Check
server logs. Auth: [agents-http.md](agents-http.md).
