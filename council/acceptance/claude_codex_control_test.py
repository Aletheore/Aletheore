"""Test the Council control tools through the real Claude Code or Codex CLI, headless, on this machine.

    python claude_codex_control_test.py claude      (or: codex)

Starts a throwaway local Council server and registers a throwaway user, lets the agent use ONLY the six council-control tools, checks the
audit log, prints PASS or FAIL for each check, and saves what the agent did (tool calls and answers, with the invitation link removed) to
./control-transcript-<kind>.txt. Nothing of yours is changed: the tools are given to this one run on the command line. It uses your own
Claude Code or Codex sign-in, so it counts against your usage. Set COUNCIL_SRC to the checkout's `src` folder if it is not found.
"""
import json, os, re, shutil, subprocess, sys, tempfile, time
from pathlib import Path

KIND = sys.argv[1] if len(sys.argv) > 1 else ""
if KIND not in ("claude", "codex"):
    sys.exit("usage: claude_codex_control_test.py claude|codex")
WIN = os.name == "nt"
SRC = Path(os.environ.get("COUNCIL_SRC") or Path(__file__).resolve().parents[2] / "src")
PORT = int(os.environ.get("TEST_PORT", "8765"))
SERVER = f"http://127.0.0.1:{PORT}"
TOOLS = ["list_rooms", "room_status", "start_room", "make_invite", "list_join_requests", "define_area"]
PROMPT = ('Using only the council-control tools: start a new interactive room titled agent-test in this repo, make a one-time '
          'invitation for it, list who is waiting to join, define an area named docs with globs ["docs/**"] owned by tester with '
          f'agent tester-{KIND}, then show room status. Never print the invitation link. Tell me one line per tool what it answered, '
          'and whether you can approve a joiner. Do not run any shell commands or read files; use only the council-control tools.')
LINK = re.compile(r"https?://\S*/j/\S+")

results = []
def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS  " if ok else "FAIL  ") + name + (f"   [{detail}]" if detail and not ok else ""), flush=True)

def find_binary(name):
    """The real executable, never a Windows .cmd shim (the prompt carries quotes that cmd.exe would mangle): Council's own resolver."""
    sys.path.insert(0, str(SRC))
    from aletheore.council.runners import AgentLaunchError, _resolve
    try:
        return _resolve(name)
    except AgentLaunchError as exc:
        sys.exit(str(exc))

binary = find_binary(KIND)
base = Path(tempfile.mkdtemp(prefix=f"{KIND}-council-test-"))
home, repo = base / "home", base / "repo"
home.mkdir(); repo.mkdir()
env = {**os.environ, "COUNCIL_HOME": str(home), "COUNCIL_SERVER": SERVER, "PYTHONPATH": str(SRC)}
cli = [sys.executable, "-m", "aletheore.council.cli"]
subprocess.run("git init -q && git -c user.email=t@t -c user.name=t commit -q --allow-empty -m init", shell=True, cwd=repo)
mcp_entry = {"command": sys.executable, "args": ["-m", "aletheore.council.cli", "mcp-control", "--me", "tester"],
             "env": {"COUNCIL_SERVER": SERVER, "COUNCIL_HOME": str(home), "PYTHONPATH": str(SRC)}}

def toml_env(d):
    return "{" + ",".join(f"{k}={json.dumps(v)}" for k, v in d.items()) + "}"

if KIND == "claude":
    cfg = base / "mcp.json"
    cfg.write_text(json.dumps({"mcpServers": {"council-control": mcp_entry}}))
    argv = [binary, "-p", PROMPT, "--output-format", "stream-json", "--verbose", "--strict-mcp-config", "--mcp-config", str(cfg),
            "--allowedTools", *[f"mcp__council-control__council_control_{t}" for t in TOOLS]]
else:
    argv = [binary, "exec", "--json", "--sandbox", "read-only", "-C", str(repo),
            "-c", f"mcp_servers.council-control.command={json.dumps(mcp_entry['command'])}",
            "-c", f"mcp_servers.council-control.args={json.dumps(mcp_entry['args'])}",
            "-c", f"mcp_servers.council-control.env={toml_env(mcp_entry['env'])}",
            "-c", 'mcp_servers.council-control.default_tools_approval_mode="approve"', PROMPT]

server = log = None
try:
    log = open(base / "serve.log", "w")
    server = subprocess.Popen(cli + ["serve", "--local", "--no-wake-lock", "--port", str(PORT), "--db", str(home / "server.db"),
                                     "--bundle-dir", str(home / "bundles")], env=env, stdout=log, stderr=subprocess.STDOUT)
    for _ in range(60):
        time.sleep(1)
        if "Council room server on" in (base / "serve.log").read_text(errors="ignore"):
            break
    else:
        sys.exit("the test server did not start:\n" + (base / "serve.log").read_text(errors="ignore")[-800:])
    code = re.search(r"cr_[0-9a-f]+", subprocess.run(cli + ["admin", "add-host", "tester", "--db", str(home / "server.db")], env=env, capture_output=True, text=True).stdout).group(0)
    reg = subprocess.run(cli + ["register", "--code", code, "--handle", "tester", "--server", SERVER], env=env, capture_output=True, text=True)
    check("test server up and test user registered", reg.returncode == 0, reg.stdout + reg.stderr)

    print(f"running {KIND} (up to 5 minutes)...", flush=True)
    run = subprocess.run(argv, cwd=repo, env={k: v for k, v in os.environ.items()}, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
    out = run.stdout or ""
    events = []
    for line in out.splitlines():
        if line.strip().startswith("{"):
            try: events.append(json.loads(line))
            except ValueError: pass

    # what the agent did, for the transcript (the link is removed) and for the final answer
    lines, answer = [], ""
    if KIND == "claude":
        for e in events:
            if e.get("type") == "assistant":
                for part in e.get("message", {}).get("content", []):
                    if part.get("type") == "tool_use":
                        lines.append(f"> {part['name'].replace('mcp__council-control__', '')} {json.dumps(part.get('input', {}))}")
                    elif part.get("type") == "text" and part["text"].strip():
                        lines.append(part["text"].strip())
            elif e.get("type") == "user":
                for part in e.get("message", {}).get("content", []) if isinstance(e.get("message", {}).get("content"), list) else []:
                    if part.get("type") == "tool_result":
                        body = part.get("content")
                        body = body if isinstance(body, str) else " ".join(b.get("text", "") for b in body or [])
                        lines.append("< " + LINK.sub("[link removed]", body))
            elif e.get("type") == "result":
                answer = str(e.get("result", ""))
    else:
        for e in events:
            item = e.get("item", {})
            if e.get("type") == "item.completed":
                if item.get("type") == "mcp_tool_call":
                    lines.append(f"> {item.get('tool')} {json.dumps(item.get('arguments', {}))}")
                    lines.append("< " + LINK.sub("[link removed]", json.dumps(item.get("result", item.get("error", "")))[:600]))
                elif item.get("type") == "agent_message":
                    answer = item.get("text", "")
                    lines.append(answer)
    transcript = LINK.sub("[link removed]", "\n".join(lines))
    Path(f"control-transcript-{KIND}.txt").write_text(transcript + "\n", encoding="utf-8")

    audit_file = home / "control-audit.log"
    audit = audit_file.read_text() if audit_file.exists() else ""
    called = {json.loads(l)["tool"] for l in audit.splitlines() if l.strip()}
    for t in ("start_room", "make_invite", "list_join_requests", "define_area"):
        check(f"{KIND} called {t}", t in called)
    check("the invitation link is not in the audit log", "#" not in audit and "/j/" not in audit)
    check(f"{KIND} did not print the invitation link in its answer", not LINK.search(answer))
    check(f"{KIND} says it cannot approve a joiner", "approve" in answer.lower() and bool(re.search(r"cannot|can't|can not|unable|not able|only the", answer, re.I)), answer[-300:])
    if run.returncode != 0:
        print("exit code", run.returncode, (run.stderr or "")[-600:])
    print("\n--- final answer ---\n" + answer.strip()[-1500:])
    print(f"\ntranscript saved to control-transcript-{KIND}.txt")
finally:
    if server:
        server.terminate()
        try: server.wait(10)
        except Exception: server.kill()
    if log: log.close()
    shutil.rmtree(base, ignore_errors=True)

print(f"\n{sum(results)} of {len(results)} checks passed")
sys.exit(0 if results and all(results) else 1)
