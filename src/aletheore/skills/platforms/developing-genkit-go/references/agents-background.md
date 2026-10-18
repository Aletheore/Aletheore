# Background Agents / Detaching (Experimental)

> **Experimental / preview API.** Detaching **requires a
> [session store](agents-sessions.md)** that also implements
> `aix.SnapshotSubscriber` (both `localstore` stores do) — the server needs
> somewhere to write the result and a way to observe an abort. Read
> [agents.md](agents.md) first.

Detaching runs a turn in the background: the server writes a `pending` snapshot
and returns its `SnapshotID` **immediately**, keeps processing, then rewrites the
snapshot to a terminal status (`completed` / `failed` / `aborted`). A reader polls
`GetSnapshot` for completion and can `Abort` in the meantime.

## Define the agent (store required)

```go
backgroundAgent := genkitx.DefineAgent(g, "backgroundAgent",
	aix.InlinePrompt{
		ai.WithModelName("googleai/gemini-flash-latest"),
		ai.WithSystem("You are a senior research analyst. Produce a comprehensive markdown report."),
	},
	aix.WithSessionStore(localstore.NewInMemorySessionStore[any]()), // REQUIRED for detach
)
```

## Detach a turn

Set `Detach: true` on the input (or call `conn.Detach()` on a connection). The
call returns right away with `FinishReason == aix.AgentFinishReasonDetached` and
the pending snapshot's ID.

```go
out, err := backgroundAgent.Run(ctx, &aix.AgentInput{
	Detach:  true,
	Message: ai.NewUserTextMessage("Write a report on renewable energy trends"),
})
if err != nil {
	log.Fatal(err)
}
fmt.Println(out.FinishReason) // aix.AgentFinishReasonDetached
snapshotID := out.SnapshotID  // pending; poll this
```

## Poll until terminal

Read the snapshot on an interval until its status leaves `pending`.

```go
func waitForResult(ctx context.Context, agent *aix.Agent[any], snapshotID string) (*aix.SessionSnapshot[any], error) {
	ticker := time.NewTicker(2 * time.Second)
	defer ticker.Stop()
	for {
		snap, err := agent.GetSnapshot(ctx, snapshotID)
		if err != nil {
			return nil, err
		}
		switch snap.Status {
		case aix.SnapshotStatusPending:
			// keep polling
		case aix.SnapshotStatusCompleted:
			return snap, nil
		case aix.SnapshotStatusFailed:
			return snap, fmt.Errorf("background task failed: %v", snap.Error)
		case aix.SnapshotStatusAborted:
			return snap, nil
		case aix.SnapshotStatusExpired:
			// The worker stopped sending heartbeats (e.g. the server restarted),
			// so the task can never complete — treat as terminal.
			return snap, nil
		}
		select {
		case <-ctx.Done():
			return nil, ctx.Err()
		case <-ticker.C:
		}
	}
}

snap, err := waitForResult(ctx, backgroundAgent, snapshotID)
if err != nil {
	log.Fatal(err)
}
// Read the result from the finalized snapshot state:
msgs := snap.State.Messages
if len(msgs) > 0 {
	fmt.Println(msgs[len(msgs)-1].Text())
}
```

## Abort an in-flight task

`Agent.Abort` flips a pending snapshot to `aborted`; the runtime observes the
flip and cancels the background work. It is a no-op on a missing or
already-terminal snapshot (returns the existing status).

```go
status, err := backgroundAgent.Abort(ctx, snapshotID)
if err != nil {
	log.Fatal(err)
}
fmt.Println(status) // aix.SnapshotStatusAborted (or the existing terminal status)
```

## Serve detach over HTTP

A remote client drives the same story over HTTP: send `{"detach": true, ...}` as
the turn input, poll the `getSnapshot` companion, and hit the `abort` companion.
Expose both companions next to the agent:

```go
mux.HandleFunc("POST /api/backgroundAgent", genkit.Handler(backgroundAgent))
mux.HandleFunc("POST /api/backgroundAgent/getSnapshot", genkit.Handler(backgroundAgent.GetSnapshotAction()))
mux.HandleFunc("POST /api/backgroundAgent/abort", genkit.Handler(backgroundAgent.AbortAction()))
```

`AbortAction()` is non-nil only when the store implements `aix.SnapshotSubscriber`
(both `localstore` stores do). See [deployment](agents-deployment.md).

## Status values (`aix.SnapshotStatus`)

- `SnapshotStatusPending` — still processing.
- `SnapshotStatusCompleted` — finished successfully; read the result from
  `snap.State.Messages`.
- `SnapshotStatusFailed` — error during processing; details on `snap.Error`.
- `SnapshotStatusAborted` — cancelled via `Abort`.
- `SnapshotStatusExpired` — the background worker stopped responding (its
  heartbeat went stale, e.g. a server restart). Computed on read, never
  persisted; terminal — the task can never complete.
