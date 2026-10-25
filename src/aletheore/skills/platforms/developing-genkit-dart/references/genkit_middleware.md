# Genkit Middleware (`genkit_middleware`)

A collection of useful middleware for Genkit Dart to enhance your agent's capabilities. Register plugins when initializing Genkit:

```dart
import 'package:genkit/genkit.dart';
import 'package:genkit_middleware/genkit_middleware.dart';

void main() {
  final ai = Genkit(
    plugins: [
      FilesystemPlugin(),
      SkillsPlugin(),
      ToolApprovalPlugin(),
    ],
  );
}
```

## Filesystem Middleware
Allows the agent to list, read, write, and search/replace files within a restricted root directory.

```dart
final response = await ai.generate(
  prompt: 'Check the logs in the current directory.',
  use: [
    filesystem(rootDirectory: '/path/to/secure/workspace'),
  ],
);
```

**Tools Provided:**
- `list_files`, `read_file`, `write_file`, `search_and_replace`

## Skills Middleware
Injects specialized instructions (skills) into the system prompt from `SKILL.md` files located in specified directories.

```dart
final response = await ai.generate(
  prompt: 'Help me debug this issue.',
  use: [
    skills(skillPaths: ['/path/to/skills']),
  ],
);
```

**Tools Provided:**
- `use_skill`: Retrieve the full content of a skill by name.

## Tool Approval Middleware
Intercepts tool execution for specified tools and requires explicit approval. Returns `FinishReason.interrupted`.

A tool is allowed through only if it is in the `approved` list or its request's
`resumed` payload carries `{ 'tool-approved': true }`. To approve on resume,
re-issue the paused `ToolRequestPart` with `.restart({'tool-approved': true})` —
the builder nests the payload under `metadata.resumed`, exactly what the
middleware reads.

```dart
final response = await ai.generate(
  prompt: 'Delete the database.',
  use: [
    // Require approval for all tools EXCEPT those below
    toolApproval(approved: ['read_file', 'list_files']),
  ],
);

if (response.finishReason == FinishReason.interrupted) {
  // `response.interrupts` is a List<ToolRequestPart>.
  final interrupt = response.interrupts.first;

  // Ask user for approval
  final isApproved = await askUser();

  if (isApproved) {
    final resumeResponse = await ai.generate(
      messages: response.messages, // Pass history
      toolChoice: ToolChoice.none, // Prevent immediate re-call
      interruptRestart: [
        // `.restart(...)` nests the payload under `metadata.resumed`.
        interrupt.restart({'tool-approved': true}),
      ],
    );
  }
}
```

> **Agent-side resume.** When resuming an agent chat rather than a raw
> `ai.generate` call, the interrupts are `AgentInterrupt`s; pass the same
> `.restart(...)` builder directly to `chat.resume`:
> `chat.resume(restart: [interrupt.restart({'tool-approved': true})])`.
> See [human-in-the-loop](agents-human-in-the-loop.md).
