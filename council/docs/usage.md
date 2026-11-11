# Council user guide: rooms over the internet, sealed end to end

Goal: friends on different networks and different operating systems share one Council room. Each person has their own identity (one per device), the host approves everyone who joins, and the server only ever stores sealed data it cannot read.

Install with `pip install 'aletheore[council]'`. The commands below work as `council ...` or `aletheore council ...`. Rooms on your own machine or network need no tunnel; the free tunnel service (see `registry-runbook.md` for how it is run) lets a host on a home network reach people on other networks. Never expose the plain HTTP port directly to the internet.

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
| `claude`, `codex` | their own runners | verified on macOS; Windows not tested |
| `opencode` | `opencode run --pure --format=json`, config given inline (`OPENCODE_CONFIG_CONTENT`): file edits allowed, shell and web denied, room tools supported. Set `COUNCIL_OPENCODE_MODEL=provider/model` to pick a model. | verified 2026-11-11 on macOS and Windows through a real room task (file committed, shell refused, the agent also posted through the room tools). On Windows the room tools need the package installed in the Python environment (`pip install -e council`); run from source with only PYTHONPATH set and OpenCode starts without them |
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
