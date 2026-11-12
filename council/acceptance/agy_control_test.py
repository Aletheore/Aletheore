"""Test the Council control tools through AGY (Antigravity CLI), headless, on this machine.

Run it in YOUR OWN terminal (AGY's sign-in lives in your login session):
    python council/acceptance/agy_control_test.py        (use the Python of the environment where `aletheore[council]` is installed)

It starts a throwaway local Council server, registers a throwaway user, lets AGY use ONLY the six council-control tools
(session-only settings; your AGY settings are never changed; shell commands stay blocked), then checks the audit log and
prints PASS or FAIL for each check. It removes everything it added at the end.
"""
import json, os, re, shutil, subprocess, sys, tempfile, time
from pathlib import Path

WIN = os.name == "nt"
SRC = Path(os.environ.get("COUNCIL_SRC") or Path(__file__).resolve().parents[2] / "src")  # the `src` folder of a checkout, where `aletheore` lives
AGY = os.environ.get("AGY") or (str(Path(os.environ.get("LOCALAPPDATA", "")) / "agy" / "bin" / "agy.exe") if WIN else shutil.which("agy"))
PY = sys.executable
PORT = int(os.environ.get("TEST_PORT", "8765"))
SERVER = f"http://127.0.0.1:{PORT}"
TOOLS = ["list_rooms", "room_status", "start_room", "make_invite", "list_join_requests", "define_area"]
PROMPT = ('Using only the council-control tools: start a new interactive room titled agy-test in this repo, make a one-time '
          'invitation for it, list who is waiting to join, define an area named docs with globs [docs/**] owned by tester, '
          'then show room status. Never print the invitation link. Tell me one line per tool what it answered, and whether '
          'you can approve a joiner. Do not run any shell commands or read files; use only the council-control tools.')

results = []
def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS  " if ok else "FAIL  ") + name + (f"   [{detail}]" if detail and not ok else ""), flush=True)

if not AGY or not Path(AGY).exists():
    sys.exit("AGY not found. Set the AGY environment variable to the agy executable.")
if not (SRC / "aletheore" / "council").is_dir():
    sys.exit(f"Council source not found at {SRC}. Set COUNCIL_SRC.")

base = Path(tempfile.mkdtemp(prefix="agy-council-test-"))
home, repo = base / "home", base / "repo"
home.mkdir(); repo.mkdir()
env = {**os.environ, "COUNCIL_HOME": str(home), "COUNCIL_SERVER": SERVER, "PYTHONPATH": str(SRC)}
cli = [PY, "-m", "aletheore.council.cli"]
subprocess.run("git init -q && git -c user.email=t@t -c user.name=t commit -q --allow-empty -m init", shell=True, cwd=repo)
settings = base / "agy-settings.json"
settings.write_text(json.dumps({"permissions": {"allow": [f"mcp(council-control/council_control_{t})" for t in TOOLS]}}))

server = log = None
added = False
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
    code = re.search(r"cr_[0-9a-f]+", subprocess.run(cli + ["admin", "add-host", "tester", "--db", str(home / "server.db")],
                                                      env=env, capture_output=True, text=True).stdout).group(0)
    reg = subprocess.run(cli + ["register", "--code", code, "--handle", "tester", "--server", SERVER], env=env, capture_output=True, text=True)
    check("test server up and test user registered", reg.returncode == 0, reg.stdout + reg.stderr)

    add = subprocess.run([AGY, "mcp", "add", "--env", f"PYTHONPATH={SRC}", "--env", f"COUNCIL_SERVER={SERVER}", "--env", f"COUNCIL_HOME={home}",
                          "council-control", "--", PY, "-m", "aletheore.council.cli", "mcp-control", "--me", "tester"],
                         capture_output=True, text=True, stdin=subprocess.DEVNULL)
    added = add.returncode == 0
    check("AGY accepted the control server", added, add.stdout + add.stderr)

    print("running AGY (up to 4 minutes)...", flush=True)
    run = subprocess.run([AGY, "--settings", str(settings), "--print", PROMPT], cwd=repo, env=env, capture_output=True, text=True,
                         stdin=subprocess.DEVNULL, timeout=240)
    answer = (run.stdout or "") + (run.stderr or "")
    audit_file = home / "control-audit.log"
    audit = audit_file.read_text() if audit_file.exists() else ""
    called = {json.loads(l)["tool"] for l in audit.splitlines() if l.strip()}
    for t in ("start_room", "make_invite", "list_join_requests", "define_area"):
        check(f"AGY called {t}", t in called)
    check("every call is in the audit log and the invitation link is not", "#" not in audit and "/j/" not in audit)
    check("AGY did not print the invitation link", "/j/" not in answer)
    check("AGY says it cannot approve a joiner", bool(re.search(r"cannot|can't|can not|unable|no\b", answer, re.I)) and "approve" in answer.lower(), answer[-300:])
    if "permission" in answer.lower() and "auto-denied" in answer.lower():
        print("\nAGY was blocked by a permission:\n" + answer[-600:])
    print("\n--- AGY said ---\n" + answer.strip()[-1500:])
finally:
    if added:
        print("agy mcp remove:", subprocess.run([AGY, "mcp", "remove", "council-control"], capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout.strip())
    if server:
        server.terminate()
        try: server.wait(10)
        except Exception: server.kill()
    if log: log.close()
    shutil.rmtree(base, ignore_errors=True)

print(f"\n{sum(results)} of {len(results)} checks passed")
sys.exit(0 if results and all(results) else 1)
