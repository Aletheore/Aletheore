# Shield spike: Claude Code hook contract and evidence refresh timing

Date: 2026-10-28. Claude Code 2.1.310, run headless (`claude -p`) against a scratch git repo with a
logging hook registered in `.claude/settings.local.json`. Three runs, about $0.31 in total. Statements
marked **Verified** were observed in those runs. Anything else is marked "documented, not verified"
(taken from the existing hook code and tests or from Claude Code's hook documentation, not observed live)
or "not tested, documented, not verified" (not tested and not covered by anything I read).
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
(the existing `evidence_hook.py` and `test_evidence_hook.py` already encode `tool_input.file_path` for them,
which is the only thing taken from them).
`cwd` in the logs was the directory `claude` was launched in.

## Session id availability

**Verified.** `session_id` and `cwd` are present on PreToolUse, PostToolUse, Stop and SessionEnd. In
every run the `session_id` was identical across all four events (and equal to the `session_id` in
`claude -p --output-format json` output). Run 2 had two Stop events (one blocked, one after) with the
same id. `prompt_id` is also present on all four and identifies one user prompt; it is constant across
the events of a single-prompt run.

Not tested, documented, not verified: whether the id changes on `/clear`, `--resume` or `--continue`;
behaviour with subagents; interactive sessions; SessionEnd firing on Ctrl-C or crash.

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
`stop_hook_active` to avoid loops. Whether Stop honours `hookSpecificOutput.additionalContext` was not tested, documented, not verified;
the only agent-visible Stop channel observed is the block reason.

Side observation: a `Stop hook error occurred` notification appeared in the stream after a block. It
is a user-facing notice, and the agent still received the reason. How it renders in the interactive UI is
not tested, documented, not verified.

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

Only the `src/` copy (8438 files) is a large case. The second repo (976 files) is smaller; it was the
biggest real source tree I found under `~/Documents/GitHub`, and `~/dev` does not exist. `rebuild` does a
full re-parse (no incremental scan in core), so cost scales with file count. Even the 976-file repo
misses 2 s.

## Decisions

Which spec fallbacks apply (spec sections "Hook triggers and session identity" and "Evidence freshness"):

1. **Session id: the primary path holds.** `session_id` is on all four events and constant. Spec
   fallback (b) (receipt "since the previous receipt", checkpoints grouped by a 30-minute idle gap)
   applies only when `session_id` is empty or invalid (validate it the way `_session_state_path` does).
2. **Agent-visible output: spec fallback (a) is not needed for mid-session text; it is used at turn end.**
   Per situation, v1 Guard uses:
   - Reach the agent mid-session: PostToolUse or PreToolUse, stdout, exit 0,
     `{"hookSpecificOutput":{"hookEventName":"PostToolUse"|"PreToolUse","additionalContext":"<text>"}}`.
   - Turn end (Stop): user-visible only, `{"systemMessage":"<text>"}`. No `decision: block` in v1
     (controller ruling). If `stop_hook_active` is true, emit nothing.
   - Outside Claude Code: print at pre-commit, as the spec says.
3. **Stop hook discipline.** No block in v1 (a block costs an extra model turn and risks loops); skip any
   Stop output when `stop_hook_active` is true.
4. **Evidence freshness: the spec default applies.** `rebuild` is a full re-parse: about 2.5 s on a
   976-file repo and about 9 s on the 8438-file case, both over the 2 s agent-hook hard stop. Task 7 will
   NOT run it inline in a hook. Guard spawns a detached background refresh (as the existing watcher does)
   and reports evidence staleness, i.e. states when evidence is older than the changed files, as the spec
   says.
5. **Index not covered.** If an index exists, `rebuild` additionally embeds chunks; that only makes it
   slower, which reinforces decision 4.
