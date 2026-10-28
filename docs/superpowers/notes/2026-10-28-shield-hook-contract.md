# Shield spike: Claude Code hook contract and evidence refresh timing

Date: 2026-10-28. Claude Code 2.1.310, run headless (`claude -p`) against a scratch git repo with a
logging hook registered in `.claude/settings.local.json`. Three runs, about $0.31 in total. All
statements below marked **Verified** were observed in those runs; none are taken from documentation.
Sessions ran under `permission_mode: "auto"` with `--allowedTools Write`.

## Event names and payloads

Every event is delivered as one JSON object on stdin. Common fields on all four events:
`session_id`, `transcript_path`, `cwd`, `hook_event_name`, `prompt_id`, `scratchpad_dir`.

| Event | Extra fields (Verified) | Notes |
|---|---|---|
| `PreToolUse` (matcher `Edit\|MultiEdit\|Write`) | `tool_name`, `tool_input`, `tool_use_id`, `permission_mode`, `effort` | `Write` input keys: `file_path`, `content` |
| `PostToolUse` | same as Pre, plus `tool_response`, `duration_ms` | `Write` response: `type` ("create"), `filePath`, `content`, `structuredPatch`, `originalFile`, `userModified`. Pre and Post share the same `tool_use_id`. |
| `Stop` | `last_assistant_message`, `stop_hook_active`, `permission_mode`, `effort`, `background_tasks`, `session_crons` | `stop_hook_active` is `false` on the first Stop and `true` on the Stop that follows a block. |
| `SessionEnd` | `reason` (saw `"other"` for a headless run) | No `permission_mode`, no `effort`. |

Only the `Write` tool was exercised; `Edit` and `MultiEdit` payload shapes are documented, not verified
(the existing `evidence_hook.py` and `test_evidence_hook.py` already encode `tool_input.file_path` for them).
`cwd` in the logs was the directory `claude` was launched in.

## Session id availability

**Verified.** `session_id` and `cwd` are present on PreToolUse, PostToolUse, Stop and SessionEnd. In
every run the `session_id` was identical across all four events (and equal to the `session_id` in
`claude -p --output-format json` output). Run 2 had two Stop events (one blocked, one after) with the
same id. `prompt_id` is also present on all four and identifies one user prompt; it is constant across
the events of a single-prompt run.

Not tested: `/clear`, `--resume`, `--continue` (whether the id changes), subagents, interactive sessions,
and SessionEnd firing on Ctrl-C or crash.

## Agent-visible output shape

**Verified** in the session transcript (`api-request-blob` and `hook_additional_context` attachments)
and by asking the model to report what it saw.

| Output | Reaches the agent? | Exact shape that worked |
|---|---|---|
| PostToolUse `additionalContext` | Yes. Injected as a system-reminder `PostToolUse:Write hook additional context: ...` | stdout, exit 0: `{"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":"<text>"}}` |
| PreToolUse `additionalContext` | Yes (same mechanism, `PreToolUse:Write hook additional context: ...`) | `{"hookSpecificOutput":{"hookEventName":"PreToolUse","additionalContext":"<text>"}}` |
| Stop block + reason | Yes. Arrives as a synthetic user message `Stop hook feedback:\n<reason>`; the agent continues the turn and acts on it. A second Stop then fires with `stop_hook_active: true`. | stdout, exit 0: `{"decision":"block","reason":"<text>"}` |
| `systemMessage` (PostToolUse, Stop) | No, user-only. The model answered NONE when asked for the code word. Recorded as a `hook_system_message` attachment. | `{"systemMessage":"<text>"}` |
| stderr with exit 0 | No (agent did not see it). Logged only in the stream. | n/a |

Consequences: Shield can speak to the agent mid-session (PostToolUse) and at turn end (Stop block). A
Stop block makes the agent do another turn, so it must be used sparingly and must honour
`stop_hook_active` to avoid loops. Stop does not accept `hookSpecificOutput.additionalContext`; the
only agent-visible Stop channel observed is the block reason (that combination was not tested and is
documented, not verified).

Side observation: a `Stop hook error occurred` notification appeared in the stream after a block. It
is a user-facing notice, and the agent still received the reason. Whether it shows in the interactive UI
as an error was not checked.

## Evidence refresh timing

Method: `git ls-files` copy of each repo into a scratch dir (never touched the originals), `git init`
commit, one `aletheore scan <copy> --yes --no-check-vulnerabilities --no-check-licenses
--no-scan-git-history --no-check-static-analysis`, then edit one `.py` file (append a comment) and time
`aletheore.watch.rebuild(repo, report)` three times, editing before each. macOS, Python venv from the
worktree. No `.aletheore/index.lancedb` existed, so the index-rebuild half of `rebuild` was NOT
measured (it returns right after writing evidence when no index exists).

| Repo (tracked files only) | Files | Scan (s) | `rebuild` (s), 3 runs | Inside 2 s? |
|---|---|---|---|---|
| This repo's `src/` copy | 8438 (mostly fixtures and tests) | 31.4 | 9.8, 9.0, 8.8 | No |
| `~/Documents/GitHub/Veridion` (976 files; 356 py, 94 ts/tsx) | 976 | 6.7 | 3.0, 2.5, 2.5 | No |

This is the largest repo I could find under `~/Documents/GitHub` that had a real source tree; `~/dev`
does not exist. `rebuild` does a full re-parse (no incremental scan in core), so cost scales with file
count. Even the mid-size repo misses 2 s.

## Decisions

Which spec fallbacks apply (spec sections "Hook triggers and session identity" and "Evidence freshness"):

1. **Session id: the primary path holds.** `session_id` is on all four events and constant. Fallback (b)
   (receipt "since the previous receipt", idle-gap grouping of 30 minutes) is not needed for
   Claude Code. Keep it only as a degraded mode for a missing or empty id, validating the id the way
   `_session_state_path` does.
2. **Agent-visible output: fallback (a) is not needed.** PostToolUse `additionalContext` and Stop
   `decision: block` both reach the agent. Guard may speak to the agent at turn end via a block. Use
   `systemMessage` for user-only text. The printing-at-pre-commit path still stands as the path outside
   Claude Code.
3. **Stop hook discipline.** Block at most once per turn: if `stop_hook_active` is true, exit 0 with no
   output. A block costs an extra model turn.
4. **Evidence freshness: the spec default applies.** `rebuild` takes about 2.5 s on a ~1k-file repo and
   about 9 s on a large one, over the 2 s agent-hook hard stop. Do NOT call `rebuild` inline in a hook.
   Guard reads evidence on disk and states when it is older than the changed files, as the spec says.
   Refreshing evidence is out of scope for v1 hooks; if ever wanted, run it detached (as the existing
   background watcher does), never in the hook's 2 s budget.
5. **Index not covered.** If an index exists, `rebuild` additionally embeds chunks; that only makes it
   slower, which reinforces decision 4.
