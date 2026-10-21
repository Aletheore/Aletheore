# Cross-agent adoption smoke tests (2026-10-21)

Question: does the agent call Hallbook (`aletheore_find_skill` and friends) under each delivery channel?
Tasks: t01, t03, t07, t13, t14, t22 from `tasks_usage.json` (commit message, Postgres migration, CI setup, Stripe webhook, Dockerfile, verify-a-fix). Counts are runs that used Hallbook / runs.

| Agent | Nothing said | One-line snippet (AGENTS.md / CLAUDE.md) | "Use the Aletheore skills" |
|---|---|---|---|
| Antigravity CLI 1.1.28 (`agy`, logged tool calls) | 0/6 | 5/6 | 6/6 |
| Codex CLI 0.163.0 (`codex exec`, logged tool calls, two replicates) | 0/12 | 3/12 | 2/12 |
| Claude Haiku subagents (self-reported tool lists) | 0/6 | 5/6 | 5/6 |

Misses: Claude snippet t01; Claude ask t22.

## Notes and caveats
- Small samples; direction only. Relevance of loaded skills was not judged.
- Antigravity and Codex rows come from logged event streams (`results_smoke/`, `results_codex*/`). The Claude row is from subagents reporting their own tool use, so it is less exact.
- Claude subagents inherit the parent session's MCP tools; Aletheore's tools are deferred, so an agent must search for them (ToolSearch). Codex also hides MCP tools until searched and often did not look.
- Codex: a project `.codex/config.toml` only loads in a trusted project (untrusted: 0 tool calls; trusted or global: works). `mcp-install` now says so.
- Antigravity: `.agents/mcp_config.json` per project works; `agy mcp list` shows only globally registered servers.
- Not tested: the Claude Code prompt hook (headless `claude -p` account was at its weekly limit), whether the hook reaches subagents, Gemini CLI (replaced by Antigravity for unpaid and Google One users on 2026-06-18).
- Claude snippet condition was supplied in the prompt as the CLAUDE.md text, not read from a file.
