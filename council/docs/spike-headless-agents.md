# Spike: driving Claude Code and Codex headlessly (2026-11-09, macOS, observed)

Scratch repo per agent, a stub stdio MCP server (`ping` tool returning `pong from $COUNCIL_AGENT`).
Always pass `< /dev/null` for stdin (otherwise Claude waits 3s and warns).

## Claude Code (claude CLI on this Mac)
Command that worked (haiku model, trivial task):
`claude -p "<task>" --output-format stream-json --verbose --allowedTools "Write" "Edit" --model haiku < /dev/null`
- Wrote the file in cwd headless: yes (needs `--allowedTools`; `--verbose` is required for stream-json).
- Output: one JSON object per line. Types seen: `system` (hook_started, hook_response, init, thinking_tokens), `assistant`, `user`, `rate_limit_event`, `result`.
- Final text: the last line, `type == "result"`, field `result`; `is_error` false; `subtype == "success"`; also `total_cost_usd` (reported 0.006 for the trivial task).
- Exit code 0 on success. Failure exit code not yet exercised.
- MCP: `--mcp-config <file> --allowedTools "mcp__stub__ping"` made the tool call; the identity came from the `env` block of the config ("pong from agent-x"). Tool name pattern: `mcp__<server>__<tool>`.
- WARNING: without `--strict-mcp-config` the headless run also loaded all of the user's other MCP servers and plugins (init showed ~10). The runner must pass `--strict-mcp-config` (not yet tested here) so a worker only sees the Council server.
- User hooks also run in headless mode (hook_started events). Not blocking, but note for isolation.
- Billing: init event shows `apiKeySource: "none"` (login/OAuth, no API key). Consistent with subscription use, but billing itself NOT confirmed.

## Codex (codex-cli 0.166.0)
Command that worked:
`codex exec --json --sandbox workspace-write -C <dir> -c 'mcp_servers.stub.command="<python>"' -c 'mcp_servers.stub.args=["<script>"]' -c 'mcp_servers.stub.env={COUNCIL_AGENT="agent-y"}' -c 'mcp_servers.stub.default_tools_approval_mode="approve"' "<task>" < /dev/null`
- Wrote the file in the workspace headless: yes.
- Output: JSONL. Events: `thread.started`, `turn.started`, `item.started`/`item.completed` (item types: `agent_message` with `text`, `command_execution`, `mcp_tool_call`), `turn.completed`.
- Final text: the last `item.completed` with `item.type == "agent_message"`.
- Exit code 0 on success. Failure exit code not yet exercised.
- MCP: a tool call is BLOCKED in exec mode by default ("MCP tool call requires approval, but approval policy is never"). Setting `mcp_servers.<name>.default_tools_approval_mode="approve"` (also `mcp_servers.<name>.tools.<tool>.approval_mode="approve"`) allowed it ("pong from agent-y"). Identity via the `env` table works.
- Auth: `codex login status` says "Logged in using ChatGPT" (subscription login). Billing itself NOT confirmed.

## Not tested
Windows (either agent); nonzero-exit behavior; Gemini CLI (not installed); `--strict-mcp-config`; long-running tasks and timeout handling; billing against usage pages.
