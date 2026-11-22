# Council user guide: rooms over the internet, sealed end to end

Goal: friends on different networks and different operating systems share one Council room. Each person has their own identity (one per device), the host approves everyone who joins, and the server only ever stores sealed data it cannot read.

Council is part of every Aletheore install: `pip install aletheore` (it used to be the `council` extra, and `pip install 'aletheore[council]'` still installs it, with a pip warning that the extra no longer exists). The commands below work as `council ...` or `aletheore council ...`. Rooms on your own machine or network need no tunnel; the free tunnel service (see `registry-runbook.md` for how it is run) lets a host on a home network reach people on other networks. Never expose the plain HTTP port directly to the internet.

## Who does what
- **The server** can be any machine you run (the host's laptop, a small VPS, a container). It stores sealed events, sealed files and the account list. It cannot read rooms.
- **The host** of a room creates it, approves people, hands out room keys and can remove people. The host's device holds the room's keys; if that device and its key file are lost, the room cannot get a new key (no recovery, by design).
- **Members** are approved by the host. Each member's devices have their own keys.

## 1. Start the server
```
council serve --accounts --db server.db --bundle-dir server-bundles --host 127.0.0.1 --port 8765
```
- `--accounts` turns on registration codes and signed login. A token file (`--token-file`) can still be added for old-style LAN participants; names in it cannot be registered as accounts.
- For a real deployment behind HTTPS use the container in `deploy/` (see `deploy/README.md`) with `COUNCIL_ACCOUNTS=1`.

## 2. First run on each person's machine
```
council setup --server https://your-server.example
```
It shows this device's id and four safety words and says what to do next. Keep the folder it names (`keys`, and `room-keys.bin`) backed up: they are your identity and your room keys, and they cannot be recovered.

## 3. Get an account
Two ways:
- **The server's admin makes a one-time code** (works once, only for that name, expires in 15 minutes):
  ```
  council admin add-host alice --db server.db        # may create rooms
  council admin add-host bob --guest --db server.db  # may join rooms, not create them
  ```
  The person then runs `council setup --server URL --code CODE --handle alice`.
- **An invitation link** from a host (section 5). No admin step: the person is registered when the host approves them.

## 4. Create a sealed room (the host)
```
council start --repo /path/to/project --host alice --title "Cache work"
council area redis --room ROOM --glob 'redis/**' --owner alice --agent alice-codex
```
On a server with accounts the room is sealed end to end by default. `--crypto none` makes a room the server can read; `--crypto e2e` insists on sealing and refuses on a token-file server. The mode cannot change after the room exists.

## 5. Invite, join, approve
Host:
```
council invite-link --room ROOM --me alice
```
Send the link to one person (it works once and expires). The part after `#` is a secret that is never sent to the server; copy the whole link.

Guest (with their own clone of the same project at the same commit):
```
council join-link 'LINK' --handle bob
```
It shows four safety words. The guest reads them to the host over another channel (a call, a message). Then the host:
```
council requests --room ROOM --me alice            # who is waiting, with their words
council approve-join REQUEST_ID --room ROOM --me alice
```
Check that the words match before approving. Approval registers the guest, makes them a member and hands them the room key. The guest finishes setting up their clone:
```
council join --room ROOM --me bob --repo /path/to/their/clone
```

## 6. Work
The commands are the usual ones (`say`, `ask`, `discuss`, `status`, `worker`, `approve`, `apply`, `view` and so on). Sealing is invisible to them. Agents get the Council tools through a localhost broker with a short-lived token, never the server credential or a room key.

## 7. Removing someone
```
council remove bob --room ROOM --me alice
```
The server refuses them on their next request. The host starts a new key epoch for everyone left, so nothing written afterwards can be read by the removed person. They keep what they had already seen: no system can take back a key someone has held.

## 8. A second device for one person
A person on two machines is one account with two linked devices (each device has its own key). The server routes for linking exist (`/v1/devices/link-requests`); the guided command for it comes in a later task.

## What the server can and cannot see
Cannot see: message text, event types, thread names, event ids, agent names, file contents, file names or hashes, image contents.
Can see: who is in which room, room titles and ids, the host, when events happen and roughly how long they are, how many events and files, device public keys, which keys epoch an event used, and your network address.

## If something goes wrong
- **"this device is not registered on that server"**: run `council setup`.
- **"not permitted ... not a member"**: you were removed, or never approved.
- **"the room key is not on this device and cannot be recovered"** (host): the host's key file was lost. Start a new room.
- **"this device's key file cannot be read"**: the file is damaged or belongs to another identity. Ask the host to remove and re-invite you.
- **Events missing from your view**: the sealing layer skips anything that fails verification and counts it (forged, unreadable for lack of a key, gaps in the server's numbering). Showing those counts on screen waits for the terminal design session (Task 19); until then they are available to code only.

## Known limits
- Message lengths are visible to the server (no padding).
- A removed member keeps any key they already held.
- The host is trusted to order approvals and to hold the keys.


## Letting an agent set up rooms for you (`council mcp-control`)

Add the control server to your agent, once, with your own name:

- Claude Code: `claude mcp add council-control -- council mcp-control --me YOURNAME`
- OpenCode (tested 2026-11-11, macOS 1.19.3 and Windows 1.19.5): in `opencode.json` (project) or `~/.config/opencode/opencode.jsonc`, add `"mcp": {"council-control": {"type": "local", "command": ["council", "mcp-control", "--me", "YOURNAME"], "enabled": true}}`. `opencode mcp list` should show it connected. Tools appear as `council-control_council_control_*`. If a tool server needs environment, add `"environment": {...}` (not `env`).
- AGY (tested 2026-11-11, macOS and Windows): `agy mcp add council-control -- council mcp-control --me YOURNAME` (flags such as `--env KEY=value` go before the name). Remove with `agy mcp remove council-control`. In AGY's headless mode (`agy --print`) every MCP tool call is refused unless allowed, with the message "a tool required the mcp permission". Allow the six tools, one rule each, in `permissions.allow` of AGY's `settings.json` (or per run with `--settings file.json`): `mcp(council-control/council_control_list_rooms)`, `..._room_status`, `..._start_room`, `..._make_invite`, `..._list_join_requests`, `..._define_area`. Do not use `--dangerously-skip-permissions` (it also allows shell commands). On Windows AGY keeps its sign-in per login session, so run it from your own terminal, not over SSH. `council/acceptance/agy_control_test.py` runs the whole check and prints PASS or FAIL.
- VS Code with GitHub Copilot agent mode: `.vscode/mcp.json` with `{"servers": {"council-control": {"type": "stdio", "command": "council", "args": ["mcp-control", "--me", "YOURNAME"]}}}`, then start the server with `MCP: List Servers`, and use Copilot Chat in Agent mode. VS Code reads `.vscode/mcp.json` only from the folder that is open, so open the project folder itself (on Windows use the full path to the Python in your environment as `command`, with `-m aletheore_council.cli mcp-control` in `args` if `council` is not on PATH). Tested on macOS (VS Code 1.145) and Windows (VS Code 1.146), 2026-11-11: the tools appeared, Copilot asked permission for each call, and the whole flow worked; the audit log shows the calls and no link. Two notes: VS Code sends at most 128 tools to a model, so with many other MCP servers enabled some tools may be left out (use `Configure Tools` to keep the six `council-control` ones); and `define_area` needs an `agent_id` as well as an owner (it must end in `-claude` or `-codex`, for example `bob-claude`; the tool description now says so and refuses anything else, because the CLI could not run such an area). The first Copilot run asked which agent to use before this was in the description.
- Codex (tested 2026-11-11, `codex exec`, macOS 0.166.0 and Windows): add to `~/.codex/config.toml`: `[mcp_servers.council-control]` with `command = "council"`, `args = ["mcp-control", "--me", "YOURNAME"]` and, for headless runs, `default_tools_approval_mode = "approve"`. The acceptance script passes the same settings with `-c` flags instead, so nothing of yours is changed.
- Claude Code: see the first line above. Its real-run test (`council/acceptance/claude_codex_control_test.py claude`) passed 8 of 8 on macOS on 2026-11-11 (after the account's weekly usage limit reset); the recorded run is `council/site/transcripts/claude-macos-2026-11-11.txt`. The Windows run is not done.
- Any MCP client: command `council`, args `mcp-control --me YOURNAME`

It uses the same server and login as the `council` command in that shell (set `COUNCIL_SERVER` as you normally do). The agent gets six tools: list rooms, room status, start room, make invitation, list people waiting, define area. It cannot approve anyone: you still compare the four safety words and run `council approve-join` (or press `a` in the terminal). Its activity is logged in `control-audit.log` in your Council folder.

Tested with real agents (2026-11-11): OpenCode and AGY, each on macOS and Windows, each started a sealed room, made an invitation without printing it, listed waiting requests, defined an area, read the status, and said it cannot approve anyone; every call is in `control-audit.log` and the link is not. VS Code with GitHub Copilot is verified on macOS and Windows (VS Code 1.146 on Windows). Every agent named in section 8b of the handover is now verified on both machines.

Notes for testing: on Windows a background server started from an SSH session dies when the session ends (the control server then exits with "cannot reach the room server"), and `opencode run` needs its input not to be a missing console (use a real terminal, or redirect from `NUL`).


## Which agents can run in a room (workers)

An agent id ends in its kind: `bob-claude`, `alice-opencode`. A worker only has to run headless in its worktree, edit files there and print an answer (the answer becomes the room's result; the file changes are committed from the worktree). The room tools (MCP) are optional extras.

| Kind | How | Status |
|---|---|---|
| `claude`, `codex` | their own runners | verified on macOS and Windows (on Windows: one read-only turn each, 2026-11-19; file-changing tasks not run there) |
| `opencode` | `opencode run --pure --format=json`, config given inline (`OPENCODE_CONFIG_CONTENT`): file edits allowed, shell, web and sub-agents (OpenCode's `task` tool, which would not inherit the shell denial) denied, room tools supported. Run one OpenCode worker at a time on a machine: several at once fail with "database is locked" (OpenCode's own database). Set `COUNCIL_OPENCODE_MODEL=provider/model` to pick a model. | verified 2026-11-11 on macOS and Windows through a real room task (file committed, shell refused, the agent also posted through the room tools). On Windows the room tools need the package installed in the Python environment (`pip install -e council`); run from source with only PYTHONPATH set and OpenCode starts without them |
| `agy` | `agy --mode accept-edits --output-format json`, with shell, web and MCP denied by per-run settings. AGY only takes MCP servers from its global config, so a worker gets no room tools, which is fine. Images are not passed. Set `COUNCIL_AGY_MODEL`. | verified 2026-11-11 on macOS and Windows through a real room task. Sign in to AGY first. On Windows its sign-in lives in your own login session, so it works from your own terminal, not over SSH (`council/acceptance/real_worker_check.py agy`). Windows PowerShell 5 has no `&&`: use `;` |
| anything else | describe it in `agents.json` (below) | not verified by Council; you check it |

`council` shows installed agents and their level; agents from `agents.json` are listed too.

### Adding any other agent CLI: `agents.json`

Put a file `agents.json` in the Council folder (the same folder as `control-audit.log`):

```json
{"agents": {"myagent": {
  "command": ["myagent", "--headless", "--message", "{instruction}"],
  "read_only_command": ["myagent", "--headless", "--no-edit", "--message", "{instruction}"],
  "output": "text"
}}}
```

- `command` is a list of arguments, never a shell line. `{instruction}`, `{worktree}` and `{mcp_config}` (a file with the room tools in the common `mcpServers` form) are replaced inside the arguments. The instruction is always one single argument.
- `output` is `text` (the whole output is the answer), `json` (the output is one JSON object) or `jsonl` (the last JSON line); for the JSON forms add `"field": "result.text"`, the dotted path to the answer.
- `read_only_command` is used for planning and discussion turns. Without it the agent is refused for those turns, because Council cannot know how to stop it editing.
- Optional `"env": {"NAME": "value"}` for non-secret settings. The child gets only a small allowlist of the parent environment (no API keys); the agent's own sign-in must live in its usual folder.
- The name is 2 to 24 lowercase letters and digits and must not be a built-in kind. Then use ids like `bob-myagent` in `define_area`.
- Lock the agent down yourself (no shell, no web) with the flags your CLI offers: Council cannot do that for an agent it has not been taught.

## Keeping agents in a room without prompting them every turn

Two cases, two tools. Both are limited to plain chat (`council say`, the room's chat box); an `ask` goes to the agent it names through its own route and a prompt goes to the planner.

**An agent Council can start (headless): `council worker`.** The worker already runs on each machine. It now also watches the room for chat that mentions one of your agents with `@agent-id` and runs that agent for one read-only turn (it can read the project and answer, not change files). The answer is posted to the room as that agent.

- Who may wake your agents: the host, you, and anyone you name with `council worker --wake-from NAME` (repeatable). Nothing a message says changes that list; a guest the host trusts but you do not cannot wake your agents.
- Limits: an agent runs at most `--max-wakes` times (default 6) in ten minutes and at least 3 seconds apart; a chain of agents answering agents stops after four hops by default, enough for a planner and an executor to talk it through and the planner to close with a verdict (a human message starts a chain at zero; `--max-wake-depth N`, 1 to 10, changes that: 2 lets one agent hand work to another without hearing the answer); the budget is the hard stop even if a sender forges the hop count. A planner that sends work to N executors is woken once for each answer, N+1 times in all, so give its worker `--max-wakes` of N+2 or more. A woken agent is shown the earlier room messages that fit (up to 40, newest first, 6000 characters each) and is told when older ones were left out; its standing job and the message that woke it always fit. A message older than 15 minutes is not answered when a worker starts. `--no-wake` turns it off.
- Exactly once: each wake is claimed with an event first, so two workers for one person, or a restart, answer a message once. Nothing that cannot be answered is dropped silently, and each notice goes to the person who wrote the message and wakes their `council wait`: a failed wake is reported (`wake_failed`) and not retried; one whose worker died mid-run is reported as `interrupted` and not re-run (it could answer twice); a sender who may not wake the agent is told how the owner can allow it (`wake_skipped`, reason `not_allowed`); a message too old to answer is reported up to six hours back (`too_old`); a budget stop is reported once per window. A notice is limited to one per reason, agent and sender per window, so nobody can make a worker post a flood, and it never contains an @name, so it cannot wake anyone.
- A wake only writes a chat message or a notice. It cannot approve, reject, remove, close, merge, change who may wake what, or start a task. A test fails if that changes. To start work from the room, the host types `@claude /task add the table` as the first words of a chat message: the host's own worker turns it into an ordinary task request for that agent's area, once. It counts only when the host typed it (not an agent writing in the host's name) and only while the message is fresh; a guest's version is ignored, and an unknown agent or a stale command is reported back.
- Standing job: `council job set --room R --me alice --agent claude "when a new revision is announced, run the gate and post defects"` gives that agent a job that is added to every wake. The host or the agent's owner can set it; `council job show` lists them; empty text clears it.

**An agent that is a session in your terminal (Claude Code, Codex): `council wait`.** A session cannot be started from outside, so it asks the room to wake it:

```text
council wait --room R --me carol --timeout 600
```

It blocks, using no tokens, until something is addressed to you (an `@carol` or `@your-agent` mention, a task for one of your areas, a debate turn), prints it and exits 0; exit 3 means nothing came. `--json` prints machine-readable events. Run it again to keep listening: a place marker in the Council folder gives each event once, and the first wait starts at the end of the log, so nothing old is replayed and nothing sent between two waits is lost. `--peek` reads without moving the marker; `--since N` starts after event N; `--all` returns every message by others.

For Claude Code, run it as a background command: when it exits, the session is woken with its output, answers (`council say`), and starts the next wait. Instruction to give such a session: "Run `council wait --room R --me NAME --timeout 1200` in the background. When it returns, read the output, do what is asked within your task, answer with `council say`, and start the next wait. Never approve or reject a join; those stay with the person." For a Codex session without background commands, run the wait in the foreground with a long timeout in a loop.

What else wakes a `council wait` (besides mentions, tasks for your areas and debate turns): the host hears when a task it asked for finishes, fails or is blocked, when a plan with tasks is waiting for approval, when a person is waiting to join (the server holds those; `wait` asks for them, each is reported once and remembered in the place marker), when a worker hit an error (`worker_error`) and when a task is stuck (`task_stalled`). The person who typed a prompt hears the plan's answer and the results of its tasks. Everyone but the writer hears a debate verdict. The owner of an area hears when a task was taken away from them.

## Work that goes quiet

- **Workers survive errors.** One bad event no longer ends a worker: it pauses (longer while it keeps failing, up to 30 seconds), tells the room once per distinct error (`worker_error`), and carries on. The planner thread has the same guard. A run that crashes fails its task at once instead of leaving it `running` until the claim goes stale (an hour).
- **Progress and stalls.** A running task sends `task_progress` every two minutes. The host's worker reports `task_stalled` once: `unclaimed` when nobody has started a task after ten minutes (the owner's worker is probably not running; the note says whose), `silent` when a running task has sent nothing for six minutes.
- **A task that could not be done is not "finished".** Every task prompt tells the agent: if you cannot do this (read-only workspace, a blocked tool, no access), change nothing and start your reply with `BLOCKED:` and the reason. The task is then `blocked` with that reason, shown on `council board` and in what `council wait` prints. A task that changed no files still ends `done`, but says `no files changed` (board, notices, `wait`), and no branch is published for it. Read the result before relying on a `done` that says so.
- **See everything:** `council board --room R --me NAME` lists every task with owner, status, minutes since it moved and a stuck flag.
- **Get unstuck (host only):** `council cancel TASK --room R` stops a task, and its owner's worker will not run it even if it comes back; `council handover TASK --room R --area OTHER` cancels it and gives the same instruction (and attachments) to another area as a new task. A plan counts a reassigned task as unfinished until its replacement finishes, so an autonomous room does not move on early. A late result from the old owner cannot revive a cancelled task.

Limits that remain: an interactive session must be open; `wait` wakes it but a closed session cannot be started. An agent Council can start (the worker's headless mode) has no such limit. An invite link returned to a waiting agent is in its context, as before.
