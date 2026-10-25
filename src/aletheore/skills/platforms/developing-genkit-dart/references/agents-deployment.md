# Deploying / Serving Agents over HTTP

> Uses `shelfHandler` from `package:genkit_shelf` and the agent's companion
> actions. Read [agents.md](agents.md) first. For the general `genkit_shelf`
> reference, see [genkit_shelf.md](genkit_shelf.md).

An agent is served as HTTP endpoints. Each `Agent` exposes three actions:

- `agent.action` → `POST /api/<name>` — the main turn action.
- `agent.getSnapshotDataAction` → `POST /api/<name>/getSnapshot` — read a
  snapshot's state. Needed for [snapshot restore](agents-branching.md) and
  [background](agents-background.md) polling.
- `agent.abortAgentAction` → `POST /api/<name>/abort` — cancel a
  [background](agents-background.md) turn.

These paths match the `remoteAgent` client defaults (`${url}/getSnapshot`,
`${url}/abort`), so a client only needs the base `url`.

## A reusable `mountAgent` helper

When serving several agents, a small helper keeps registration consistent.

```dart
import 'package:genkit/genkit.dart';
import 'package:genkit/experimental.dart'; // Agent type
import 'package:genkit_shelf/genkit_shelf.dart';
import 'package:shelf_router/shelf_router.dart';

import 'package:agents_sample/weather_agent.dart';
import 'package:agents_sample/background_agent.dart';

/// Mounts an agent's turn action plus its `/getSnapshot` and `/abort` actions.
void mountAgent(Router router, String path, Agent agent) {
  router.post('/api/$path', shelfHandler(agent.action));
  router.post('/api/$path/getSnapshot', shelfHandler(agent.getSnapshotDataAction));
  router.post('/api/$path/abort', shelfHandler(agent.abortAgentAction));
}

final router = Router();
mountAgent(router, 'weatherAgent', weatherAgent);
mountAgent(router, 'backgroundAgent', backgroundAgent);

// A client-managed (stateless) agent only needs the turn action:
router.post(
  '/api/weatherAgentStateless',
  shelfHandler(weatherAgentStateless.action),
);
```

Which companions to enable:

| Agent capability | `getSnapshot` | `abort` |
| --- | --- | --- |
| Plain chat (client- or server-state) | – | – |
| Snapshot restore / [branching](agents-branching.md) | ✓ | – |
| [Background](agents-background.md) / detach | ✓ | ✓ |

## CORS for browser clients

A browser `remoteAgent` calling a different origin (e.g. a Jaspr/Flutter web dev
server on another port) needs CORS. **Streaming requires the
`X-Genkit-Stream-Id` header** to be allowed. Use `shelf_cors_headers`.

```dart
import 'dart:io';

import 'package:shelf/shelf.dart';
import 'package:shelf/shelf_io.dart' as io;
import 'package:shelf_cors_headers/shelf_cors_headers.dart';

final handler = const Pipeline()
    .addMiddleware(logRequests())
    .addMiddleware(
      corsHeaders(
        headers: {
          'Access-Control-Allow-Origin': '*',
          'Access-Control-Allow-Headers':
              'Content-Type, Accept, X-Genkit-Stream-Id',
          'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
        },
      ),
    )
    .addHandler(router.call);

final port = int.tryParse(Platform.environment['PORT'] ?? '') ?? 8080;
final server = await io.serve(handler, InternetAddress.anyIPv4, port);
print('Agents API server running on http://localhost:${server.port}');
```

## Exposing plain flows

Agents are just actions, so you can serve ordinary flows the same way — e.g.
helper endpoints used by a UI:

```dart
router.post('/api/workspace/files', shelfHandler(listWorkspaceFiles));
router.post('/api/workspace/file', shelfHandler(readWorkspaceFile));
```

## Registering agents/flows

Agents register with Genkit when their defining top-level `final` is evaluated.
Importing the module (e.g. in your server's `bin/server.dart`) and referencing
the agent — as `mountAgent(...)` does — ensures the `defineAgent` call runs.

## Notes

- The wire body matches the Genkit client: `{ "data": <input>, "init": <init> }`.
- For persistence across restarts, use `FileSessionStore`
  (`package:genkit/experimental_io.dart`) or `FirestoreSessionStore`
  (`package:genkit_google_cloud`) instead of `InMemorySessionStore`. See
  [sessions](agents-sessions.md).
- Consuming these endpoints from Dart/Flutter/web uses `remoteAgent` from
  `package:genkit/experimental_client.dart` (alongside
  `package:genkit/client.dart`) — see
  [agents.md](agents.md#consume-an-agent-from-a-client-remoteagent).
