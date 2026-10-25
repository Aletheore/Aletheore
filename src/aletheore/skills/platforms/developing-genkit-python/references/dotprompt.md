# Dotprompt — Genkit Python

## What it is

`.prompt` files combine YAML frontmatter (model config, schemas) with Handlebars
templates. Keeps prompt logic out of Python code and makes variants easy.

## File format

```yaml
---
model: googleai/gemini-flash-latest
input:
  schema:
    food: string
    tone?: string                 # optional scalar — just `field?: type`
    ingredients?(array): string   # optional array/object needs the parenthetical
output:
  schema: Recipe    # references a schema registered with ai.define_schema()
  format: json
---
{{role "system"}}
You are a chef. Keep recipes practical.

{{role "user"}}
Generate a recipe for {{food}}.
{{#if ingredients}}
Prefer these ingredients: {{ingredients}}.
{{/if}}
```

Use `{{role "system"}}` / `{{role "user"}}` (and friends) so the model sees a
real system instruction. A flat template body becomes a single **user** message.

Place `.prompt` files in a `prompts/` directory and point `prompt_dir` at it.

**Note:** `input.schema` describes the contract for humans and tooling. Today it
is **not** validated locally before the model call — missing required fields can
still render and hit the API. Validate in your flow if you need hard failures.

## Python setup

```python
from pathlib import Path
from pydantic import BaseModel
from genkit import Genkit
from genkit_google_genai import GoogleAI

ai = Genkit(
    plugins=[GoogleAI()],
    model='googleai/gemini-flash-latest',
    prompt_dir=Path(__file__).resolve().parent.parent / 'prompts',
)

class Recipe(BaseModel):
    title: str
    steps: list[str]

ai.define_schema('Recipe', Recipe)
```

## Calling a prompt

There is **no** `prompt.execute()`. Non-streaming uses the callable
(`ExecutablePrompt.__call__`):

```python
# Non-streaming — await the prompt itself (double-call: ai.prompt('name') then (...))
prompt = ai.prompt('recipe')
response = await prompt(input={'food': 'banana bread'})
# same as: await ai.prompt('recipe')(input={'food': 'banana bread'})
result = Recipe.model_validate(response.output)

# Variant (recipe.robot.prompt file)
response = await ai.prompt('recipe', variant='robot')(input={'food': 'banana bread'})
```

## Streaming from a prompt

`.stream(...)` is **not** awaited; then consume `.stream` / `.response`:

```python
from genkit import ActionRunContext

@ai.flow()
async def tell_story(subject: str, ctx: ActionRunContext) -> str:
    result = ai.prompt('story').stream(input={'subject': subject})
    full = ''
    async for chunk in result.stream:
        if chunk.text:
            ctx.send_chunk(chunk.text)
            full += chunk.text
    final = await result.response  # completes even if you stop reading chunks early
    return final.text or full
```

## Render without generating (for LLM-judge evals)

```python
rendered = await ai.prompt('my_prompt').render(input={'key': 'value'})
# Inspect roles/messages before spending tokens:
# print(rendered.messages)
response = await ai.generate(model='googleai/gemini-flash-latest', messages=rendered.messages)
```

## Helpers

Handlebars helpers receive template args in a packed form. Prefer simple
string/list formatting in the template, or unpack carefully:

```python
def list_helper(data: object, *args, **kwargs) -> str:
    # Positional template args arrive packed in the first parameter.
    items = data[0] if isinstance(data, (list, tuple)) and data else data
    if not isinstance(items, list):
        return ''
    return '\n'.join(f'- {item}' for item in items)

ai.define_helper('list', list_helper)
```

Then `{{list ingredients}}` in the `.prompt`. If output looks like a Python
`repr` of a list, the helper unpacked wrong — fix the helper, not the model.

## Variants

Name the file `<name>.<variant>.prompt` — e.g. `recipe.robot.prompt`.
Call with `ai.prompt('recipe', variant='robot')`.

## Partials

Use `{{>partial_name param=value}}` in templates. Partial files are named
`_partial_name.prompt`.

## Prompts + tools + structured output

Naming a tool in the template is not enough — pass tool objects at call time
(or list names in frontmatter `tools:` that match registered tools):

```python
response = await ai.prompt('insurance_quote')(
    input={'age': 35, 'zip': '94105', 'coverage_tier': 'plus'},
    tools=[lookup_rate],
)
quote = QuoteResult.model_validate(response.output)
```

Register output schemas with `ai.define_schema('QuoteResult', QuoteResult)`
when frontmatter references them by name.

### Partials example

```
prompts/_greeting.prompt   # partial body only
prompts/support_reply.prompt
```

In `support_reply.prompt`:
```
{{>greeting}}
... main template ...
{{>disclaimer}}
```

Call `await ai.prompt('support_reply')(input={...})`. Parent input is visible
inside partials unless you override with `{{>greeting name=name}}`.
