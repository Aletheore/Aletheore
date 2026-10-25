# Genkit Google GenAI Plugin (`genkit_google_genai`)

The Google AI plugin provides an interface against the official Google AI Gemini API.

## Usage

```dart
import 'package:genkit/genkit.dart';
import 'package:genkit_google_genai/genkit_google_genai.dart';

void main() async {
  // Initialize Genkit with the Google AI plugin. RetryPlugin() (core genkit)
  // registers the `retry` middleware; transient "high demand" errors are common.
  final ai = Genkit(plugins: [googleAI(), RetryPlugin()]);

  // Generate text
  final response = await ai.generate(
    model: googleAI.gemini('gemini-flash-latest'),
    prompt: 'Tell me a joke about a developer.',
    use: [retry()], // recommended for reliable runs
  );

  print(response.text);
}
```

## Model refs and Gemma

`googleAI.gemini('<name>')` builds a ref for any Gemini model. Gemma models the
Gemini API serves have a dedicated `googleAI.gemma('<name>')` alias (it reads
correctly at call sites), and `GoogleAiModels` exposes typed refs for the curated
Gemini and Gemma entries:

```dart
final response = await ai.generate(
  model: googleAI.gemma('gemma-4-31b-it'), // or GoogleAiModels.gemma431b
  prompt: 'Tell me a joke about a developer.',
);
```

## Embeddings

```dart
final embeddings = await ai.embedMany(
  embedder: googleAI.textEmbedding('gemini-embedding-001'),
  documents: [
    DocumentData(content: [TextPart(text: 'Hello world')]),
  ],
);
```

## Image Generation

The plugin also supports image generation models such as `gemini-3.1-flash-image`.

### Example (Nano Banana)

```dart
// Define an image generation flow
ai.defineFlow(
  name: 'imageGenerator',
  inputSchema: .string(defaultValue: 'A banana riding a bike'),
  outputSchema: Media.$schema,
  fn: (input, context) async {
    final response = await ai.generate(
      model: googleAI.gemini('gemini-3.1-flash-image'),
      prompt: input,
    );
    if (response.media == null) {
      throw Exception('No media generated');
    }
    return response.media!;
  },
);
```

The media (url field) contain base64 encoded data uri. You can decode it and save it as a file.

## Text-to-Speech (TTS)

You can use text-to-speech models to generate audio from text. The generated `Media` object will contain base64 encoded PCM audio in its data URI.

```dart
// Define a TTS flow
ai.defineFlow(
  name: 'textToSpeech',
  inputSchema: .string(defaultValue: 'Genkit is an amazing AI framework!'),
  outputSchema: Media.$schema,
  fn: (prompt, _) async {
    final response = await ai.generate(
      model: googleAI.gemini('gemini-3.1-flash-tts-preview'),
      prompt: prompt,
      config: GeminiTtsOptions(
        responseModalities: ['AUDIO'],
        speechConfig: SpeechConfig(
          voiceConfig: VoiceConfig(
            prebuiltVoiceConfig: PrebuiltVoiceConfig(voiceName: 'Puck'),
          ),
        ),
      ),
    );
    
    if (response.media != null) {
      return response.media!;
    }
    throw Exception('No audio generated');
  },
);
```

Google AI also supports multi-speaker TTS by configuring a `MultiSpeakerVoiceConfig` inside `SpeechConfig`.
