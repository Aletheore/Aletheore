"""Real-process smoke test of the installed `aletheore` CLI on the current OS.

Runs the console script exactly as a user would (piped stdio, redirected
HOME/APPDATA, no network) and prints PASS/FAIL per check. Exits 1 if any fail.
Usage: python cli_smoke.py
"""

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

WIN = sys.platform == "win32"
# A FAIL's detail is the CLI's own output, which can hold characters the
# runner's console codepage (cp1252 on Windows) can't encode, e.g. Rich's box
# drawing. Escape them rather than crash before the failure is printed.
sys.stdout.reconfigure(errors="backslashreplace")
EXE = shutil.which("aletheore")
ROOT = Path(tempfile.mkdtemp(prefix="aletheore-smoke-"))
HOME = ROOT / "home"
HOME.mkdir()
RESULTS: list[tuple[str, bool, str]] = []
NO_NET = {"HTTP_PROXY": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9", "NO_PROXY": ""}


def env(**extra):
    e = dict(os.environ)
    e.update(
        HOME=str(HOME), USERPROFILE=str(HOME), APPDATA=str(HOME / "AppData" / "Roaming"),
        LOCALAPPDATA=str(HOME / "AppData" / "Local"), ALETHEORE_NO_UPDATE_CHECK="1",
        PYTHONIOENCODING="", PYTHONUTF8="",
    )
    e.pop("XDG_CONFIG_HOME", None)
    e.update(extra)
    return e


def run(args, cwd=None, timeout=300, stdin="", **envx):
    p = subprocess.run(
        [EXE, *args], cwd=cwd, env=env(**envx), input=stdin, capture_output=True,
        timeout=timeout, text=True, encoding="utf-8", errors="replace",
    )
    return p.returncode, p.stdout + p.stderr


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n      {str(detail)[-600:]}"), flush=True)


def clean_error(name, args, code=1, **kw):
    rc, out = run(args, **kw)
    check(name, rc == code and "Traceback" not in out, f"rc={rc}\n{out}")
    return out


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def spawn(args, cwd=None, **envx):
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if WIN else 0
    return subprocess.Popen(
        [EXE, *args], cwd=cwd, env=env(**envx), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", creationflags=flags,
    )


def stop(p):
    try:
        p.send_signal(signal.CTRL_BREAK_EVENT if WIN else signal.SIGINT)
        p.wait(10)
    except Exception:
        p.kill()
    try:
        return p.communicate(timeout=5)[0] or ""
    except Exception:
        return ""


def wait_for(pred, secs):
    end = time.time() + secs
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.5)
    return False


# ---------------------------------------------------------------- fixture repo
repo = ROOT / "repo with spaces"
(repo / "src").mkdir(parents=True)
(repo / "src" / "a.py").write_text("import os\n\n\ndef f():\n    return os.getcwd()\n", encoding="utf-8")
(repo / "src" / "ünï_日本.py").write_text("def g():\n    return 'é'\n", encoding="utf-8")
(repo / "node_modules" / "pkg").mkdir(parents=True)
(repo / "node_modules" / "pkg" / "i.js").write_text("module.exports = 1\n")
for g in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"], ["add", "-A"], ["commit", "-qm", "i"]):
    subprocess.run(["git", *g], cwd=repo, check=True, capture_output=True)
SCAN = ["--no-check-vulnerabilities", "--no-check-licenses", "--no-scan-git-history", "--no-check-static-analysis"]

# ---------------------------------------------------------------- basics
rc, out = run(["--version"])
check("--version", rc == 0 and "aletheore" in out, out)
rc, out = run([])
check("bare banner (non-tty, unicode)", rc == 0 and "ALETHEORE" in out, out)
rc, out = run(["query"])
check("query lists kinds", rc == 0 and "secrets" in out, out)
out = run(["query", "secret"])[1]
check("query typo suggestion", "Did you mean" in out and "secrets" in out, out)

# ---------------------------------------------------------------- path validation
clean_error("scan missing path", ["scan", str(ROOT / "nope"), *SCAN])
clean_error("scan file as path", ["scan", str(repo / "src" / "a.py"), *SCAN])
for cmd in ("init", "watch", "mcp-install", "index", "dashboard"):
    clean_error(f"{cmd} missing path", [cmd, str(ROOT / "nope")])
clean_error("diff dir as file", ["diff", str(repo), str(repo)])
clean_error("verify missing report", ["verify", str(ROOT / "nope.md")])
clean_error("healthcheck without --base-url", ["healthcheck", str(repo)])
check("--k 0 rejected", run(["query", "secrets", "--k", "0"])[0] == 2)
check("--debounce -1 rejected", run(["watch", str(repo), "--debounce", "-1"])[0] == 2)

# ---------------------------------------------------------------- scan / query / diff / verify
rc, out = run(["init", str(repo)])
check("init writes config", rc == 0 and (repo / ".aletheore.json").exists(), out)
check("init refuses overwrite", run(["init", str(repo)])[0] == 1)
rc, out = run(["scan", str(repo), *SCAN])
air = repo / ".aletheore" / "air.json"
check("scan (piped, unicode paths)", rc == 0 and air.exists(), out)
rc, out = run(["scan", str(repo), *SCAN])
snaps = sorted((repo / ".aletheore" / "history").glob("*.json"))
check("two valid snapshot filenames", rc == 0 and len(snaps) >= 2, [s.name for s in snaps])
evidence = json.loads(air.read_text(encoding="utf-8"))
mods = [m if isinstance(m, str) else m.get("path", "") for m in evidence["repository"].get("modules", [])]
check("unicode module present in evidence", any("ünï_日本" in m for m in mods), mods)
check("node_modules not scanned", not any("node_modules" in m for m in mods), mods)
rc, out = run(["query", "symbols", "src/ünï_日本.py", "--path", str(repo)])
check("query symbols unicode (piped)", rc == 0 and "g" in out, out)
rc, out = run(["query", "symbol-source", "src/a.py", "f", "--path", str(repo)])
check("query symbol-source", rc == 0 and "getcwd" in out, out)
check("query unknown module is clean", run(["query", "imports", "../../etc/passwd", "--path", str(repo)])[0] == 1)
rc, out = run(["query", "changes", "--path", str(repo)])
check("query changes", rc == 0, out)
rc, out = run(["diff", str(snaps[0]), str(snaps[-1])])
check("diff json", rc == 0 and json.loads(out), out)
rc, out = run(["diff", str(snaps[0]), str(snaps[-1]), "--format", "sarif"])
check("diff sarif", rc == 0 and '"version": "2.1.0"' in out, out)
(ROOT / "report.md").write_text("see `src/a.py:2` and `src/a.py:9999`\n", encoding="utf-8")
rc, out = run(["verify", str(ROOT / "report.md"), "--path", str(repo)])
check("verify flags the bad citation", rc == 1 and "9999" in out and "Traceback" not in out, out)

# ---------------------------------------------------------------- mcp-install
cfg_home = HOME
cursor = repo / ".cursor"
cursor.mkdir()
(cursor / "mcp.json").write_bytes(b'\xef\xbb\xbf{\n // keep me\n "mcpServers": {"other": {"command": "x",},},\n}')
(repo / ".vscode").mkdir()
(repo / ".vscode" / "mcp.json").write_text('{\n/* c */ "servers": {}\n}', encoding="utf-8")
if WIN:
    (HOME / "AppData" / "Roaming" / "Claude").mkdir(parents=True)
elif sys.platform == "darwin":
    (HOME / "Library" / "Application Support" / "Claude").mkdir(parents=True)
rc, out = run(["mcp-install", str(repo)])
check("mcp-install all targets", rc == 0, out)
c = json.loads((cursor / "mcp.json").read_text(encoding="utf-8"))
check("mcp-install merged BOM+JSONC cursor config", "other" in c["mcpServers"] and "aletheore" in c["mcpServers"], c)
check("mcp-install merged JSONC vscode config", "aletheore" in json.loads((repo / ".vscode" / "mcp.json").read_text(encoding="utf-8"))["servers"])
cmd = json.loads((repo / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["aletheore"]["command"]
check("written command exists on disk", Path(cmd).exists(), cmd)
check("command is the .exe on Windows", (not WIN) or cmd.lower().endswith(".exe"), cmd)
check("warns about machine-specific paths", "not portable" in out, out)
rc, out = run(["mcp-install", str(repo)])
check("mcp-install idempotent", rc == 0 and "updated" in out, out)
if True:
    victim = ROOT / "victim.json"
    victim.write_text("{}")
    shutil.rmtree(repo / ".kiro", ignore_errors=True)
    link = repo / ".kiro" / "settings"
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(ROOT, link, target_is_directory=True)
        out = run(["mcp-install", str(repo), "--target", "kiro"])[1]
        check("symlinked config dir refused", "escapes the repo" in out and not (ROOT / "mcp.json").exists(), out)
    except OSError as exc:
        print(f"SKIP  symlinks unavailable: {exc}")
if WIN or sys.platform == "darwin":
    desktop = [p for p in HOME.rglob("claude_desktop_config.json")]
    check("claude desktop config written under redirected profile", len(desktop) == 1, desktop)

# ---------------------------------------------------------------- dashboard
for bad in ("0", "70000", "-1"):
    out = run(["dashboard", str(repo), "--port", bad])[1]
    check(f"dashboard --port {bad} rejected cleanly", "Traceback" not in out and "port" in out.lower(), out)
port = free_port()
d = spawn(["dashboard", str(repo), "--port", str(port)])


def _bindable(port):
    with socket.socket() as s:
        if not WIN:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # ignore TIME_WAIT
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def http_ok(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


check("dashboard serves 200", wait_for(lambda: http_ok(port), 90))
rc, out = run(["dashboard", str(repo), "--port", str(port)])
check("second dashboard on same port refused (Windows exclusive bind)", rc == 1 and "already in use" in out, out)
out = stop(d)
check("dashboard exits on interrupt without traceback", "Traceback" not in out, out)
check("dashboard port freed", wait_for(lambda: _bindable(port), 10))

# ---------------------------------------------------------------- watch
w = spawn(["watch", str(repo), "--debounce", "1"])
lines: list[str] = []
import threading

threading.Thread(target=lambda: [lines.append(l) for l in w.stdout], daemon=True).start()
check("watch starts", wait_for(lambda: any("watching" in l for l in lines), 60), lines)
time.sleep(5)
(repo / "node_modules" / "pkg" / "i.js").write_text("module.exports = 2\n")
time.sleep(4)
check("watch ignores node_modules edits", not any("changed" in l for l in lines), lines)
(repo / "src" / "a.py").write_text("def f():\n    return 2\n", encoding="utf-8")
check("watch rebuilds on source edit", wait_for(lambda: any("file(s) changed" in l for l in lines), 90), lines)
(repo / "newtop").mkdir()
time.sleep(3)
(repo / "newtop" / "n.py").write_text("def n():\n    return 1\n", encoding="utf-8")
check("watch sees a top-level dir created after startup", wait_for(lambda: any("n.py" in l for l in lines), 90), lines)
w.send_signal(signal.CTRL_BREAK_EVENT if WIN else signal.SIGINT)
try:
    w.wait(15)
except Exception:
    w.kill()
check("watch stops cleanly", "Traceback" not in "".join(lines), lines)

# ---------------------------------------------------------------- credentials (isolated profile)
CRED = r"""
import json, os, stat, subprocess, sys, multiprocessing as mp
from pathlib import Path
from aletheore import credentials as c
p = c.DEFAULT_CREDENTIALS_PATH
assert str(p).startswith(os.environ["HOME"]), p
if __name__ == "__main__":
    child = "from aletheore import credentials as c; import sys; c.save_api_token(sys.argv[1], 'v-' + sys.argv[1])"
    ps = [subprocess.Popen([sys.executable, "-c", child, f"k{i}"]) for i in range(6)]
    assert all(x.wait() == 0 for x in ps)
    data = json.loads(p.read_text(encoding="utf-8"))
    assert sorted(data) == [f"k{i}" for i in range(6)], data
    assert c.clear_api_key("k0", p) and not c.clear_api_key("k0", p)
    assert c.has_api_key("", "k1", p)
    p.write_text("{broken")
    c.save_api_token("z", "1")
    assert p.with_suffix(".json.bak").read_text() == "{broken"
    if sys.platform == "win32":
        out = subprocess.run(["icacls", str(p)], capture_output=True, text=True).stdout
        print(out)
        assert "BUILTIN\\Users" not in out and "Everyone" not in out and "Authenticated Users" not in out, out
    else:
        assert stat.S_IMODE(p.stat().st_mode) == 0o600, oct(p.stat().st_mode)
    print("ok")
"""
p = subprocess.run([sys.executable, "-c", CRED], env=env(), capture_output=True, text=True)
check("credentials: concurrent saves, backup, permissions", p.returncode == 0 and "ok" in p.stdout, p.stdout + p.stderr)

# ---------------------------------------------------------------- status / login / managed audit offline
rc, out = run(["status"], **NO_NET)
check("status offline", rc == 0 and "Aletheore v" in out and "Traceback" not in out, out)
out = clean_error("login offline", ["login"], **NO_NET)
(HOME / ".config" / "aletheore").mkdir(parents=True, exist_ok=True)
out = clean_error("audit --managed offline", ["audit", str(repo), "--managed", "--token", "x", *SCAN], **NO_NET)
rc, out = run(["audit", str(repo), "--agent", "openai", *SCAN], stdin="", OPENAI_API_KEY="sk-x", **NO_NET)
check("audit consent prompt on closed stdin", "Traceback" not in out and rc in (0, 1), f"rc={rc}\n{out}")

# ---------------------------------------------------------------- MCP server over stdio
m = subprocess.Popen([EXE, "mcp", str(repo), "--no-watch"], env=env(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "smoke", "version": "0"}}}
try:
    m.stdin.write((json.dumps(init) + "\n").encode())
    m.stdin.flush()
    line = m.stdout.readline()
    check("mcp server answers initialize over stdio", b'"result"' in line, line)
    m.stdin.write((json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n").encode())
    m.stdin.write((json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}) + "\n").encode())
    m.stdin.flush()
    line = m.stdout.readline()
    check("mcp server lists tools", b"aletheore_scan" in line or b"aletheore_overview" in line, line[:300])
finally:
    m.kill()

# ---------------------------------------------------------------- summary
failed = [n for n, ok, _ in RESULTS if not ok]
print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed on {sys.platform}")
shutil.rmtree(ROOT, ignore_errors=True)
sys.exit(1 if failed else 0)
