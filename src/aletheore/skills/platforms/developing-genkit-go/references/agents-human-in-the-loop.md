# Agent Human-in-the-Loop / Interrupts (Experimental)

> **Experimental / preview API.** Read [agents.md](agents.md) first.

An **interrupt** pauses an agent mid-turn and hands control back to your code (or
a human) — e.g. to approve a sensitive action, collect missing input, or confirm
a plan. Internally it's a **tool call used as control flow**: the interrupting
tool does not produce a normal result; it pauses the turn. You then **resume**
from the exact point it paused.

Interrupts are **orthogonal to persistence** — they work the same whether the
agent uses a [session store](agents-sessions.md) or
[client-managed state](agents.md#client-managed-vs-server-managed-state). The
paused turn just needs to be carried back into the resume: with a store the
session/snapshot ID does it; without one you round-trip the state blob.

Flow: send a turn → the response finishes with
`aix.AgentFinishReasonInterrupted` and carries interrupt parts → collect human
input → send an `aix.ToolResume` payload to continue.

## Define an interruptible tool

`genkitx.DefineInterruptibleTool[In, Out, Resume]` defines a tool that can pause
by calling `tool.Interrupt(data)`. The `Resume` type is what the caller sends
back to continue; it arrives as the non-nil `res *Resume` parameter on
re-execution.

```go
import (
	"github.com/genkit-ai/genkit/go/ai"
	"github.com/genkit-ai/genkit/go/ai/exp/tool"
	"github.com/genkit-ai/genkit/go/core/status" // status.Errorf / status.ErrInvalidArgument
)

type ApprovalAsk struct {
	Action  string `json:"action"`
	Details string `json:"details"`
}
type ApprovalReply struct {
	Approved bool   `json:"approved"`
	Feedback string `json:"feedback,omitempty"`
}
type TransferInput struct {
	Amount    float64 `json:"amount"`
	ToAccount string  `json:"toAccount"`
}
type TransferOutput struct {
	Success       bool   `json:"success"`
	TransactionID string `json:"transactionId"`
}

// transferMoney pauses for approval on the first call, then completes once a
// resume payload (res) is supplied.
transferMoney := genkitx.DefineInterruptibleTool(g, "transferMoney",
	"Transfer money to a specified account, pausing for user approval.",
	func(ctx context.Context, in TransferInput, res *ApprovalReply) (TransferOutput, error) {
		if res == nil {
			// First call: pause and surface the request to the caller.
			return TransferOutput{}, tool.Interrupt(ApprovalAsk{
				Action:  "transferMoney",
				Details: fmt.Sprintf("Transfer $%.2f to %s", in.Amount, in.ToAccount),
			})
		}
		if !res.Approved {
			return TransferOutput{Success: false}, nil
		}
		return TransferOutput{Success: true, TransactionID: fmt.Sprintf("txn-%d", time.Now().Unix())}, nil
	},
)

bankingAgent := genkitx.DefineAgent(g, "bankingAgent",
	aix.InlinePrompt{
		ai.WithModelName("googleai/gemini-flash-latest"),
		ai.WithSystem("You are a banking assistant. Use transferMoney to move funds; it will pause for user approval."),
		ai.WithTools(transferMoney),
	},
	aix.WithSessionStore(localstore.NewInMemorySessionStore[any]()),
)
```

`tool.Interrupt(data)` requires `data` to serialize to a JSON object (a struct or
map), since it rides as structured metadata on the interrupted tool request.

Returning an ordinary Go `error` from the tool is different: it **fails the turn**
rather than pausing it. The runtime wraps the cause as `ai.ErrToolFailed` with the
original preserved, so you can validate inputs and still branch on the classified
status server-side, e.g.:

```go
if in.Amount <= 0 {
	return TransferOutput{}, status.Errorf(status.ErrInvalidArgument,
		"transfer amount must be positive, got $%.2f", in.Amount)
}
```

## Detect and resume

When a turn pauses, its output has `FinishReason == aix.AgentFinishReasonInterrupted`.
The interrupt parts are on the last model message; read the typed payload with
`tool.InterruptAs[T]`. Build the resume payload from the **same** interrupt part
(so it validates against history) with `InterruptibleTool.Respond` (supply the
output directly) or `InterruptibleTool.Resume` (re-run the tool with typed resume
data), and continue with `aix.ToolResume`.

```go
conn, _ := bankingAgent.Connect(ctx)

_ = conn.SendText("Transfer $500 to my savings account.")

var interruptPart *ai.Part
for chunk, err := range conn.Receive() {
	if err != nil {
		log.Fatal(err)
	}
	if chunk.ModelChunk != nil {
		for _, p := range chunk.ModelChunk.Content {
			if p.IsToolRequest() {
				interruptPart = p
			}
		}
	}
	if chunk.TurnEnd != nil {
		break
	}
}

if interruptPart != nil {
	if ask, ok := tool.InterruptAs[ApprovalAsk](interruptPart); ok {
		fmt.Println(ask.Action, ask.Details) // show this to the human
	}

	// Collect the human decision, then build a respond part for the SAME tool.
	respond, err := transferMoney.Respond(interruptPart, TransferOutput{
		Success:       true,
		TransactionID: "txn-approved",
	})
	if err != nil {
		log.Fatal(err)
	}

	// Resume the same connection with the tool response.
	_ = conn.SendResume(&aix.ToolResume{Respond: []*ai.Part{respond}})
	for chunk, err := range conn.Receive() {
		if err != nil {
			log.Fatal(err)
		}
		if chunk.ModelChunk != nil {
			fmt.Print(chunk.ModelChunk.Text())
		}
		if chunk.TurnEnd != nil {
			break
		}
	}
}

out, _ := conn.Output()
```

### Respond vs. Restart

- `tool.Respond` / `InterruptibleTool.Respond(part, output)` — supply the tool's
  output **without** re-running it. Goes on `aix.ToolResume{Respond: [...]}`.
- `tool.Resume` / `InterruptibleTool.Resume(part, resumeData)` — **re-run** the
  interrupted tool, delivering `resumeData` as its `res *Resume` parameter. Goes
  on `aix.ToolResume{Restart: [...]}`.

```go
// Re-run the tool with the human's decision instead of injecting the result:
restart, _ := transferMoney.Resume(interruptPart, ApprovalReply{Approved: true, Feedback: "ok"})
_ = conn.SendResume(&aix.ToolResume{Restart: []*ai.Part{restart}})
```

You can resume several interrupts at once and mix the two lists:

```go
_ = conn.SendResume(&aix.ToolResume{
	Respond: []*ai.Part{a},
	Restart: []*ai.Part{b},
})
```

## Single-turn resume with `Run`

You don't need a persistent connection. Detect the interrupt on one `Run`, then
resume with another `Run` carrying the `Resume` payload (plus the session source
so the paused turn is in scope).

```go
out, _ := bankingAgent.RunText(ctx, "Transfer $500 to savings.")
if out.FinishReason == aix.AgentFinishReasonInterrupted {
	// Find the interrupt part: don't assume it's the last content part. The
	// model may emit text alongside the tool request, or several tool calls
	// where only one interrupted. tool.InterruptAs matches the right one.
	var part *ai.Part
	for _, p := range out.Message.Content {
		if _, ok := tool.InterruptAs[ApprovalAsk](p); ok {
			part = p
			break
		}
	}
	respond, _ := transferMoney.Respond(part, TransferOutput{Success: true, TransactionID: "txn-1"})

	out, _ = bankingAgent.Run(ctx,
		&aix.AgentInput{Resume: &aix.ToolResume{Respond: []*ai.Part{respond}}},
		aix.WithSessionID(out.SessionID), // server-managed: carry the paused turn back
	)
}
fmt.Println(out.Message.Text())
```

For a client-managed agent, pass `aix.WithState(out.State)` instead of
`WithSessionID`.

## Notes & gotchas

- **No store required.** Interrupts work with a [session store](agents-sessions.md)
  or [client-managed state](agents.md#client-managed-vs-server-managed-state).
  Just carry the paused turn back into the resume (session/snapshot ID, or the
  state blob).
- **Build resume parts from the interrupt part.** `Respond`/`Resume` derive the
  name/ref from the original request. The framework validates every entry against
  conversation history (`aix.ValidateResumeAgainstHistory` runs automatically for
  prompt-backed agents): name/ref must match, and a `Restart` input must be
  unchanged. Hand-rolled parts are rejected.
- **Only resumable snapshots resume.** A failed/aborted/pending snapshot is kept
  for inspection but can't be resumed.
- **Re-pausing.** After resuming, the new turn may interrupt again; loop until
  the finish reason is no longer `aix.AgentFinishReasonInterrupted`.
- **`ToolApproval` middleware.** For gating arbitrary tools (rather than writing
  an interruptible tool by hand), `plugins/middleware`'s `ToolApproval` turns any
  tool call outside an allow list into an interrupt. See [middleware](middleware.md).
