# Agents (Beta)

> Preview API under `genkit.agent`.

Turn a model + tools into a durable conversation — history, typed state,
approvals, branches, and background work.

Deeper topics: [sessions](agents-sessions.md) ·
[HITL](agents-human-in-the-loop.md) · [branching](agents-branching.md) ·
[background](agents-background.md) · [state](agents-state.md) ·
[artifacts](agents-artifacts.md) · [custom](agents-custom.md) ·
[HTTP](agents-http.md)

## Define an agent

```python
from pydantic import BaseModel

from genkit import Genkit
from genkit_google_genai import GoogleAI
from genkit.agent import InMemorySessionStore

ai = Genkit(plugins=[GoogleAI()])


class WeatherInput(BaseModel):
    location: str


class WeatherOutput(BaseModel):
    weather: str
    temperature: str


@ai.tool(name='getWeather', description='Get weather for a city.')
async def get_weather(input: WeatherInput) -> WeatherOutput:
    return WeatherOutput(weather='Sunny', temperature='21°C')


agent = ai.define_agent(
    name='weatherAgent',
    model='googleai/gemini-flash-latest',
    system='Weather assistant. Use getWeather for weather questions.',
    tools=[get_weather],
    store=InMemorySessionStore(),  # omit to manage history yourself
)
```

Options worth knowing: `use` (middleware), `state_schema`, `max_turns` (tool
loop depth per user message), transforms. Dotprompt agents:
`ai.define_prompt_agent`. Full control: [`define_custom_agent`](agents-custom.md).

Tool parameters should be a small Pydantic model — even for one field. Empty
inputs need an empty subclass, not bare `BaseModel`.

## Middleware

Register `Middleware()` once, then pass instances in `use=[...]`.

- **Filesystem** — `list_files` / `read_file`. Writes stay off until
  `allow_write_access=True`. Paths are relative to `root_dir`.
- **ToolApproval** — tools in `allowed_tools` run; everything else pauses for
  a human. List write/artifact tools if they should run freely.
- **Skills** — load `skills/<name>/SKILL.md` and expose `use_skill`.
- **Retry** — retries flaky model HTTP calls. Tool failures are different:
  return a soft-error payload the model can read.

```python
from genkit_middleware import Middleware, ToolApproval, Filesystem, Skills, Retry
from genkit.agent import FileSessionStore

ai = Genkit(plugins=[GoogleAI(), Middleware()])

coding_agent = ai.define_agent(
    name='codingAgent',
    model='googleai/gemini-flash-latest',
    system='Coding assistant. Use list_files/read_file/write_file/edit_file.',
    use=[
        ToolApproval(allowed_tools=['list_files', 'read_file', 'use_skill']),
        Filesystem(root_dir='./workspace', allow_write_access=True),
        Skills(skill_paths=['./skills']),
        Retry(),
    ],
    store=FileSessionStore('./.snapshots'),
    max_turns=30,
)
```

For typed session fields with middleware, update state from tools via
`ai.current_session()` — [state](agents-state.md). Declaring `state_schema`
alone does not fill `chat.state`.

## Chat

```python
chat = agent.chat()

res = await chat.send('Weather in Tokyo?')
print(res.text, res.snapshot_id)

turn = chat.send_stream('What about Paris?')
async for chunk in turn.stream:
    if chunk.text:
        print(chunk.accumulated_text, end='\r', flush=True)
final = await turn.response
```

Send returns a response. Stream returns a turn with `.stream` and `.response`.
After an interrupt, use `resume` / `resume_stream`.

You can await the response without reading every chunk. One send at a time per
`chat`.

To reopen a stored conversation, use `load_chat` (not `chat(snapshot_id=...)`,
which only attaches a resume handle):

```python
resumed = await agent.load_chat(snapshot_id=res.snapshot_id)
await resumed.send('What city did I ask about?')
```

## Verify an agent from the CLI (`flow:run`)

`genkit flow:run` only runs **flows**, not agents, so you can't `flow:run` an
agent directly. To exercise an agent from the CLI (e.g. a quick, self-terminating
check), wrap one turn in a throwaway flow and run that:

```python
@ai.flow()
async def try_weather_agent(message: str) -> str:
    return (await agent.chat().send(message)).text

# genkit flow:run try_weather_agent '"Weather in Tokyo?"' -- uv run src/main.py
```

## Without a store

Skip `store` when your app owns history. Ids stay `None` — pass messages,
state, and artifacts into the next `chat(...)`:

```python
agent = ai.define_agent(
    name='echoNoStore',
    model='googleai/gemini-flash-latest',
    system='Echo assistant. Answer briefly and remember context.',
)

chat = agent.chat()
await chat.send('My name is Ada. Remember it.')

resumed = agent.chat(
    messages=chat.messages, state=chat.state, artifacts=chat.artifacts
)
await resumed.send('What is my name? One word.')
```

Add a [store](agents-sessions.md) when the server should own history, or when
you need branching and detach. [Interrupts](agents-human-in-the-loop.md) work
either way.

## Auth

Put identity on the server with `serve_agent` and a FastAPI dependency. The
remote client only forwards credentials. See [HTTP](agents-http.md).

## Prompt agents

`define_prompt_agent(name=...)` uses the same name for the agent and the
`.prompt` file stem. Keep preamble inputs stable for the chat — put dynamic
fields in user messages or tools.
