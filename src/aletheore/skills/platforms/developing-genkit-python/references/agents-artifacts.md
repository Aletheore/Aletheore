# Artifacts (Beta)

> See [agents.md](agents.md).

Named deliverables on the session — reports, files, code — available as
`chat.artifacts` and `res.artifacts`.

## Middleware

`Artifacts()` gives the model `write_artifact` and `read_artifact`. Writing the
same name replaces the previous version.

Store-backed agents cannot seed with `chat(artifacts=...)`. Have the model
write on the first turn, or call `add_artifacts` from a custom agent.

```python
from genkit_google_genai import GoogleAI
from genkit_middleware import Artifacts, Middleware

from genkit import Genkit
from genkit.agent import InMemorySessionStore

ai = Genkit(plugins=[GoogleAI(), Middleware()])

agent = ai.define_agent(
    name='workspaceAgent',
    model='googleai/gemini-flash-latest',
    system='Use write_artifact for files. Use read_artifact to review them.',
    use=[Artifacts()],
    store=InMemorySessionStore(),
)

chat = agent.chat()
await chat.send('Write poem.txt with a short poem about Python agents.')
print([a.name for a in chat.artifacts])
```

## From a custom agent

Pass a list:

```python
from genkit import Part, TextPart
from genkit.agent import Artifact

await sess.add_artifacts(
    [Artifact(name='report.md', parts=[Part(TextPart(text=body))])]
)
```

## Reading text

Parts use a RootModel — read text from `.root` when it is a `TextPart`:

```python
from genkit import TextPart

def artifact_text(artifact) -> str:
    return ''.join(
        p.root.text
        for p in (artifact.parts or [])
        if isinstance(p.root, TextPart)
    )
```
