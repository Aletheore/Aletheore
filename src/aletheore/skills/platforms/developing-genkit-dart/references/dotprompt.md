# Dotprompt (.prompt files) — Genkit Dart

## What it is

`.prompt` files combine YAML frontmatter (model, config, schemas, tools,
middleware) with a Handlebars template. They keep prompt logic out of Dart code
and make variants and iteration easy.

## Where files live

By default Genkit loads `.prompt` files from `./prompts`. Configure with the
`promptDir` parameter on `Genkit(...)` (set to `null` to disable auto-loading):

```dart
import 'package:genkit/genkit.dart';
import 'package:genkit_google_genai/genkit_google_genai.dart';

final ai = Genkit(
  plugins: [googleAI()],
  promptDir: './prompts', // default
);
```

## File format

`prompts/greeting.prompt`:
```
---
model: googleai/gemini-flash-latest
input:
  schema:
    name: string
    style: string
---
{{role "system"}}
You are a {{style}} greeter.

{{role "user"}}
Greet {{name}}.
```

Schema fields use Picoschema (the compact form above) or you can reference a
named schema registered with `defineSchema`. The `type, description` form is
supported too, e.g. `name: string, the person to greet`.

## Loading and calling a prompt

`ai.prompt(name, {variant})` returns a `Future<ExecutablePrompt>`. The resolved
`ExecutablePrompt` is a **callable object** — invoke it like a function. The
input is a **positional** argument (there is no `input:` named parameter).

```dart
// Non-streaming
final greetingPrompt = await ai.prompt('greeting');
final response = await greetingPrompt({
  'name': 'World',
  'style': 'cheerful',
});
print(response.text);
```

### Streaming

```dart
final storyPrompt = await ai.prompt('story');
final stream = storyPrompt.stream({'subject': 'a robot'});
await for (final chunk in stream) {
  print(chunk.text);
}
```

### Render without generating

Returns `GenerateActionOptions` (messages, model, config) without calling the
model — useful for building `ai.generate` calls or evals:

```dart
final greetingPrompt = await ai.prompt('greeting');
final rendered = await greetingPrompt.render({'name': 'World', 'style': 'casual'});
rendered.model;    // resolved model
rendered.messages; // rendered messages
```

## Registering named schemas

Reference a schema by name in `.prompt` frontmatter (`input.schema` /
`output.schema`) after registering it with `defineSchema` (a JSON Schema map):

```dart
ai.defineSchema('Recipe', {
  'type': 'object',
  'properties': {
    'title': {'type': 'string'},
    'steps': {'type': 'array', 'items': {'type': 'string'}},
  },
  'required': ['title', 'steps'],
});
```

Then in `recipe.prompt`:
```
---
model: googleai/gemini-flash-latest
input:
  schema:
    food: string
output:
  schema: Recipe
  format: json
---
Generate a recipe for {{food}}.
```

For code-defined prompts (`ai.definePrompt`) you typically pass a
[schemantic](schemantic.md)-generated schema (e.g. `JokeInput.schema`) as
`inputSchema`.

## Variants

Name the file `<name>.<variant>.prompt` — e.g. `greeting.formal.prompt`. Load
with the `variant` argument:

```dart
final formalPrompt = await ai.prompt('greeting', variant: 'formal');
final response = await formalPrompt({'name': 'Dr. Smith', 'title': 'Professor'});
```

## Partials

Reusable template fragments. Name a partial file `_<name>.prompt` and include it
with `{{> name param=value}}`.

`prompts/_signature.prompt` referenced from another template:
```
Write a short email to {{recipient}} about {{subject}}.

{{> signature}}
```

## Tools, tool-loop control, and middleware

`.prompt` frontmatter can configure tool calling and attach middleware, so an
agent-style prompt is fully described in the file. This is based on
`prompts/tripPlanner.prompt` from the agents testapp (with
`returnToolRequests` shown for completeness):

```
---
model: googleai/gemini-flash-latest
input:
  schema:
    tone: string
tools:
  - getAttractions
  - getFlightInfo
maxTurns: 20              # max tool-call loop iterations
returnToolRequests: false # return tool requests instead of running them
use:
  - name: retry           # bare string also works: `- retry`
    config:
      maxRetries: 4
---
{{role "system"}}
You are a friendly trip planning assistant. Help users plan trips by suggesting
attractions and looking up flight information. Use the available tools to provide
accurate, up-to-date information. Keep your tone {{tone}}.

{{history}}
```

- `tools`: list of registered tool names.
- `toolChoice`, `maxTurns`, `returnToolRequests`: same semantics as the
  equivalent `ai.generate` options.
- `use`: list of middleware refs. Each entry is a bare string (middleware name)
  or a map with `name` and optional `config`. Names resolve against middleware
  registered on the Genkit instance — register the middleware plugin so the name
  is available:

```dart
final ai = Genkit(
  plugins: [
    googleAI(),
    RetryPlugin(), // registers the `retry` middleware
  ],
  promptDir: './prompts',
);
```

See [genkit_middleware](genkit_middleware.md) for the middleware package.

## Backing an agent with a .prompt file

`definePromptAgent` wires an agent directly to a `.prompt` file by name. The
frontmatter (`tools`, `maxTurns`, `use`, ...) applies to the agent, and
`promptInput` supplies template variables:

```dart
final tripPlannerAgent = ai.definePromptAgent(
  promptName: 'tripPlanner',            // -> prompts/tripPlanner.prompt
  promptInput: {'tone': 'enthusiastic'}, // fills {{tone}}
  store: InMemorySessionStore(),
);
```

See [Agents](agents.md) for defining and serving agents.
