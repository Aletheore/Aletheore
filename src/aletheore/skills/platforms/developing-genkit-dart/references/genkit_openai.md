# Genkit OpenAI Plugin (`genkit_openai`)

OpenAI-compatible API plugin for Genkit Dart. Supports OpenAI models and other compatible APIs (xAI, DeepSeek, Together AI, Groq, etc.).

## Basic Usage

```dart
import 'dart:io';
import 'package:genkit/genkit.dart';
import 'package:genkit_openai/genkit_openai.dart';

void main() async {
  // RetryPlugin() (core genkit) registers the `retry` middleware.
  final ai = Genkit(plugins: [
    openAI(apiKey: Platform.environment['OPENAI_API_KEY']),
    RetryPlugin(),
  ]);

  final response = await ai.generate(
    model: openAI.model('gpt-5'),
    prompt: 'Tell me a joke.',
    use: [retry()], // recommended for reliable runs
  );
}
```

The plugin does **not** require network access or an API key at startup; a key is
only needed when you actually call a model.

## Typed model and embedder refs

The plugin ships a curated per-model catalog. `openAI.model('<name>')` and
`openAI.embedder('<name>')` accept any id (uncurated ids still resolve via
dated-suffix aliases and generic defaults), and `OpenAIModels` / `OpenAIEmbedders`
expose typed refs for the curated entries:

```dart
final response = await ai.generate(
  model: OpenAIModels.gpt5Mini, // == openAI.model('gpt-5-mini')
  prompt: 'Tell me a joke.',
);

final embeddings = await ai.embedMany(
  embedder: OpenAIEmbedders.textEmbedding3Small, // == openAI.embedder(...)
  documents: [DocumentData(content: [TextPart(text: 'Hello world')])],
);
```

> Capability detection is data-driven (per-model), not name-matching. `-pro`
> tiers are not curated because they are Responses API only and this plugin
> speaks Chat Completions (they still come back from discovery).

## Options

`OpenAIOptions` allows configuring sampling temperature, nucleus sampling, token generation, seed, etc:
`config: OpenAIOptions(temperature: 0.7, maxTokens: 100)`

> **Schemaless JSON output is advisory.** When you request JSON output without a
> schema, the plugin now sends `json_object` (was strict `json_schema`), so
> schema conformance is best-effort rather than guaranteed. Provide an
> `outputSchema` when you need enforced structure.

## Groq API override

Specify custom `baseUrl` and custom models to integrate with third-party providers.

```dart
final ai = Genkit(plugins: [
  openAI(
    apiKey: Platform.environment['GROQ_API_KEY'],
    baseUrl: 'https://api.groq.com/openai/v1',
    models: [
      CustomModelDefinition(
        name: 'llama-3.3-70b-versatile',
        info: ModelInfo(
          label: 'Llama 3.3 70B',
          supports: {'multiturn': true, 'tools': true, 'systemRole': true},
        ),
      ),
    ],
  ),
]);

final response = await ai.generate(
  model: openAI.model('llama-3.3-70b-versatile'),
  prompt: 'Hello!',
);
```
