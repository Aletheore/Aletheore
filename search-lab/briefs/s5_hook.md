# Brief S5: a prompt hook that suggests a Hallbook skill automatically
Read `briefs/CONTRACT.md` and `common.py` first. You may edit: `../src/aletheore/skill_hub.py` (add one function plus constants),
`../src/aletheore/cli.py` (add subcommands under the existing `hallbook` command group, next to `snippet`), `../src/tests/test_skill_hub.py`,
`../src/tests/test_cli.py`. Nothing else.

## Background
`skill_hub.find_skill(task)` already returns `confidence` high|low|none and, for high, the whole skill. Agents rarely call it on their own, so we add
a hook for Claude Code's `UserPromptSubmit` event: Claude Code runs a command on every user prompt, passes JSON on stdin (`{"prompt": "...", "session_id": ...,
"cwd": ...}`), and whatever the command prints to stdout with exit code 0 is added to the agent's context. Printing nothing adds nothing. The hook must be
fast (< 300 ms including Python start; the search itself is a few ms), offline, silent on any error (exit 0, no output), and must never write files.

## Build
1. `skill_hub.hook_suggestion(prompt: str, mode: str = "hint") -> str | None`:
   - return None for prompts that are not worth a skill: fewer than 5 words after stripping, slash commands (start with "/"), pure acknowledgements
     ("ok", "thanks", "yes", "continue", "go ahead"...), and prompts longer than 6,000 characters (pasted logs) -- for those use only the first 800 characters.
   - run `find_skill(prompt_text)`; if confidence is not "high" return None.
   - mode "hint": one short block, at most 3 lines: `Hallbook skill that fits this task: <skill_id> (<title>). If it applies, load it with aletheore_get_skill and follow it; adapt it to this project.`
   - mode "inject": a header line `Hallbook skill for this task (<skill_id>), apply it to this project and ignore parts that do not fit:` followed by the skill content (the same cap as find_skill).
   Module constants for the word limits and ack list. Also expose `HOOK_MODES = ("hint", "inject")`.
2. CLI: `aletheore hallbook suggest [--mode hint|inject]` reads the hook JSON from stdin (also accept plain text if the input is not JSON), calls `hook_suggestion`, and prints
   the suggestion as plain text (nothing when None). Catch every exception, print nothing, exit 0.
   `aletheore hallbook hook [--mode hint|inject]` prints (never writes) the JSON settings snippet the user pastes into `.claude/settings.json`:
   `{"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "aletheore hallbook suggest --mode hint"}]}]}}`, plus a one-line comment
   on stderr saying where to paste it. Keep `snippet` as is.
3. Tests: hint for a clear prompt (take 3 high-confidence queries from `data/queries_stage4.jsonl` by running find_skill), None for "thanks", "ok continue", a
   slash command, gibberish and a vague prompt; inject mode contains the skill content; CLI `suggest` reads JSON stdin and prints the hint; CLI exits 0 and prints
   nothing on broken input; `hook` prints valid JSON with the right command. Run both test files; all must pass.
4. Measure: write `measure_hook.py` printing, for 200 random benchmark queries and for 30 hand-written chatty prompts you write ("can you look at why my login test is flaky",
   "thanks that worked", "rename this variable"...), the share that produce a suggestion, the mean characters of hint and inject output, and the mean wall time of
   `aletheore hallbook suggest` run as a subprocess (use `python -m aletheore.cli`, with PYTHONPATH set to `../src`).
Final report: what you built, the exact commands, the measurements.
