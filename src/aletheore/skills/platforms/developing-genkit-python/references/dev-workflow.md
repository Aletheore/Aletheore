# Dev Workflow — Genkit Python

## Agent responsibility

After generating code, always give the developer:
1. The full pre-run checklist with copy-paste commands using absolute paths
2. The `genkit start` command to run in their terminal (foreground — it's expected to block)
3. Step-by-step Dev UI instructions so they can test without guessing

Do not offer to run it for them. Give them the commands and let them run it.

---

## Step 1 — Get a Gemini API key

If the developer doesn't have one:
> Get a free key at https://aistudio.google.com/apikey — click **"Create API key"**, copy it.

---

## Step 2 — Set the API key

Open a terminal and run:
```bash
export GEMINI_API_KEY=your-api-key-here
```

To persist across sessions, add it to your shell profile:
```bash
echo 'export GEMINI_API_KEY=your-api-key-here' >> ~/.zshrc && source ~/.zshrc
```

---

## Step 3 — Install dependencies

Replace `/path/to/your-project` with the actual full path to the project (e.g. `/Users/yourname/projects/my-genkit-app`):

```bash
cd /path/to/your-project
uv add genkit genkit-google-genai
```

(Requires a project with `pyproject.toml` — run `uv init` in an empty directory first if needed.)

---

## Step 4 — Start the Dev UI

Run this in your terminal. **It will block — that's expected.** Leave this terminal open while you use the Dev UI.

```bash
cd /path/to/your-project
GEMINI_API_KEY=your-api-key-here genkit start -- uv run src/main.py
```

You'll see output like:
```
Genkit Tools UI: http://localhost:4000
```

The Dev UI is now running at **http://localhost:4000**

To stop it: press `Ctrl+C` in the terminal.

---

## Step 5 — Test in the Dev UI

1. Open **http://localhost:4000** in your browser
2. Click **"Run"** in the left sidebar
3. Find your flow by name (e.g. `summarize`, `chat`, `joke_generator`)
4. In the input box, paste your input as JSON — e.g:
   ```json
   {"text": "hello world"}
   ```
5. Click the **"Run"** button — the output appears on the right
6. Click **"Traces"** in the left sidebar to inspect every step, model call, token count, and latency

---

## CLI Commands

`genkit start` unintrusively wraps any Python program that uses the Genkit library, running it unchanged while capturing traces from every Genkit action so you can prove tools were actually called and inspect model I/O from the terminal, even for headless checks. It forwards stdio, so interactive CLI tools that rely on stdin/stdout work without issues. Running the app directly (`uv run`) skips trace capture, so you're debugging blind.

**Primary pattern (default):** prefix `genkit start --` to your normal run command. This collects telemetry from any Genkit code your program runs, whether triggered from the dev UI, your own web server/web UI, or a plain script:
```bash
genkit start -- uv run src/main.py
genkit start --noui -- uv run src/main.py   # same, without the Dev UI (still a persistent server)
```
`genkit start` runs until you stop it with Ctrl+C. That is expected and correct for the common cases: a server your web/mobile app calls, or an interactive CLI you exit yourself. `--noui` only drops the Dev UI; it is **not** a one-shot command and will not exit on its own. Do **not** use `genkit start` as a blocking step in automated/non-interactive contexts; use `flow:run` (below) for that.

**Non-interactive use (agents/CI):** add the global `--non-interactive` flag before `--` so the CLI uses defaults and never blocks on a prompt (e.g. the first-run analytics notice): `genkit start --non-interactive -- uv run src/main.py` (works with `flow:run` too).

**Run a flow (`flow:run`):** invoke a specific flow by name from the CLI. Append your run command after `--` to spin up the runtime just for this run (the command runs as-is to register your flows):
```bash
genkit flow:run myFlow '{"data": "input"}' -- uv run src/main.py
```
This is **self-terminating**: it runs the flow once, prints a `Trace ID`, then exits, so it's the right choice for a quick, non-interactive check (unlike `genkit start`). Note: `flow:run` runs **flows** (`@ai.flow()`), not agents; you can't `flow:run` an agent (`ai.define_agent`) directly. To exercise an agent from the CLI, wrap one turn in a throwaway flow and run that (see [Agents](agents.md)). Traces for this run can be inspected using the trace commands below.

**Debugging with traces:** the fastest way to see prompts, model inputs/outputs, tool calls, latencies, and errors. Inspect from the terminal after any run under `genkit start`:
```bash
genkit trace:list                        # find recent trace IDs
genkit trace:get <traceId>               # full trace details (inputs, outputs, tool calls, errors)
genkit trace:get <traceId> --format json # machine-readable JSON, safe to pipe into jq or other parsers
```

For machine-readable output, pass `--format json` to get clean JSON you can pipe into `jq` or other parsers. The **default** output is human-oriented (banner/log lines, possible truncation on large traces), so don't pipe that form directly; use `--format json`, grep, or the Dev UI trace viewer.


**Documentation:**
```bash
genkit docs:search "streaming" python
genkit docs:list python
genkit docs:read python/flows.md
```


## Troubleshooting

- `genkit: command not found` — `npm install -g genkit-cli`
- `GEMINI_API_KEY not set` — `export GEMINI_API_KEY=your-key`
- Port 4000 already in use —
  `genkit start --port 4001 -- uv run src/main.py`
- `uv: command not found` —
  `curl -LsSf https://astral.sh/uv/install.sh -o install.sh && sh install.sh`
- Flow not showing in Dev UI — check `genkit start` output for errors
