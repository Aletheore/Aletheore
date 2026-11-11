"""ACCEPTANCE TEST, HOST SIDE (Mac). Real `council serve` behind the real free tunnel and registry, a sealed room, a real invitation, and a scripted
conversation with a guest on another network. Prints PASS/FAIL for every check; cleans up and releases the slot at the end."""
import hashlib, os, random, re, signal, subprocess, sys, tempfile, threading, time
from pathlib import Path

IS_WIN = sys.platform.startswith("win")
SRC = Path(os.environ.get("ACC_SRC") or Path(__file__).resolve().parents[2] / "src")  # the `src` folder of a checkout
PY = sys.executable
CRYPTO = os.environ.get("ACC_CRYPTO", "e2e")          # e2e (sealed room) or none (plain room)
SCENARIO = os.environ.get("ACC_SCENARIO", "tunnel")    # tunnel | recycled (the link's address has been given to someone else) | local (no tunnel, this machine only)
import psutil
HOME = Path(tempfile.mkdtemp(prefix="acc-host-")); (HOME / "consent.json").write_text('{"internet": true}')
if os.environ.get("ACC_CLOUDFLARED"):  # a host machine that does not have cloudflared on its PATH: put a copy where Council looks for it
    import shutil; (HOME / "bin").mkdir(); shutil.copy(os.environ["ACC_CLOUDFLARED"], HOME / "bin" / Path(os.environ["ACC_CLOUDFLARED"]).name)
ENV = {**os.environ, "COUNCIL_HOME": str(HOME), "COUNCIL_FILE_CAP_MB_PER_DAY": "6", "PYTHONPATH": str(SRC)}
if SCENARIO == "local": (HOME / "consent.json").write_text('{"internet": false}')
sys.path.insert(0, str(SRC)); os.environ.update(COUNCIL_HOME=str(HOME), COUNCIL_FILE_CAP_MB_PER_DAY="6")
GUEST = "winguest"
UNI = "Grüße 名前 café ✓ — «quotes» \"and\" 'more' \\ backslash"
LONG = ("The quick brown fox jumps over the lazy dog. " * 175).rstrip()   # 7,875 characters: under the 8,000 limit, so it must arrive exactly
OVER = "A" * 9000   # over the limit: it must arrive cut with a visible marker, never silently
OVER_EXPECTED = OVER[:8000] + " ...[truncated]"
HOST_MSGS = ["short one", UNI, LONG, "line one\nline two\n\ttabbed", '{"looks": "like json", "n": [1,2,3]}', OVER]
EXPECTED = HOST_MSGS[:5] + [OVER_EXPECTED]
def blob(tag, size=1_300_000): return random.Random(tag).randbytes(size)
RESULTS = []; t0 = time.time()
def say_(m): print(f"[{time.time()-t0:5.0f}s] {m}", flush=True)
def check(phase, name, ok, detail=""):
    RESULTS.append((phase, name, bool(ok)))
    say_(f"[{'PASS' if ok else 'FAIL'}] {phase}: {name}" + (f"  ({detail})" if detail and not ok else ""))
CONTROL = os.environ.get("ACC_CONTROL") == "1"   # an agent sets the room up through the control tools; the human steps stay on the command line
def control(tool, args=None):
    """A real MCP client against `council mcp-control` over stdio, the way an agent host would use it."""
    import asyncio, json
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    async def go():
        params = StdioServerParameters(command=PY, args=["-m", "aletheore.council.cli", "mcp-control", "--me", "hosty"], env=ENV)
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                if tool == "__tools__": return sorted(t.name for t in (await s.list_tools()).tools)
                return json.loads((await s.call_tool(tool, args or {})).content[0].text)
    return asyncio.run(go())
def cli_run(*args, extra=None, timeout=180):
    return subprocess.run([PY, "-m", "aletheore.council.cli", *args], env={**ENV, **(extra or {})}, capture_output=True, text=True, timeout=timeout)

class Server:
    def __init__(self): self.proc = None; self.lines = []
    def args(self):
        base = [PY, "-m", "aletheore.council.cli", "serve", "--no-wake-lock", "--db", str(HOME / "server.db"), "--bundle-dir", str(HOME / "bundles")]
        return base + (["--local"] if SCENARIO == "local" else [])
    def start(self):
        self.lines = []
        kw = {"creationflags": 0x00000200} if IS_WIN else {"start_new_session": True}
        self.proc = subprocess.Popen(self.args(), env=ENV, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, **kw)
        threading.Thread(target=lambda: [self.lines.append(l) for l in self.proc.stdout], daemon=True).start()
        end = time.time() + 120
        while time.time() < end:
            text = "".join(self.lines)
            if SCENARIO == "local":
                if "Council room server on" in text: time.sleep(2); return "http://127.0.0.1:8765"
            else:
                m = re.search(r"https://slot-\d+\.aletheorecouncil\.com", text)
                if m: return m.group(0)
            time.sleep(1)
        raise RuntimeError("serve did not come up:\n" + "".join(self.lines))
    def tree(self):
        try: return psutil.Process(self.proc.pid).children(recursive=True)
        except psutil.NoSuchProcess: return []
    def tunnel_kids(self): return [p for p in self.tree() if "cloudflared" in (p.name() or "").lower()]
    def _all(self):  # python processes only (the server and its launcher): other children, like the console host, cannot be suspended and need not be
        try: tree = [psutil.Process(self.proc.pid)] + psutil.Process(self.proc.pid).children(recursive=True)
        except psutil.NoSuchProcess: return []
        return [p for p in tree if "python" in (p.name() or "").lower()]
    def freeze(self):  # the whole tree: on Windows the venv python.exe is only a launcher, the real server is its child
        self._frozen = self._all()
        for p in self._frozen:
            try: p.suspend()
            except (psutil.NoSuchProcess, psutil.AccessDenied, PermissionError): pass
    def thaw(self):
        for p in getattr(self, "_frozen", []) or self._all():
            try: p.resume()
            except (psutil.NoSuchProcess, psutil.AccessDenied, PermissionError): pass
    def crash(self):
        kids = self.tree()
        for p in [psutil.Process(self.proc.pid)] + kids:
            try: p.kill()
            except psutil.NoSuchProcess: pass
        self.proc.wait()
    def stop(self):
        if self.proc and self.proc.poll() is None:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGINT) if not IS_WIN else self.proc.send_signal(signal.CTRL_BREAK_EVENT)
                time.sleep(6)
            except Exception: pass
            if self.proc.poll() is None: self.crash()
SERVER = Server()
registry_cleanup = []
try:
    LOCAL = "http://127.0.0.1:8765"  # the host's own commands talk to its own server; only invitations and health checks use the public address
    addr = SERVER.start(); os.environ["COUNCIL_SERVER"] = ENV["COUNCIL_SERVER"] = LOCAL
    say_(f"host is up at {addr}  [config: room={'sealed' if CRYPTO == 'e2e' else 'plain'}, mode={SCENARIO}, os={'windows' if IS_WIN else 'mac'}]")
    import httpx
    from aletheore.council import cli
    up = False
    for _ in range(40):
        try: up = httpx.get(addr + "/healthz", timeout=10).status_code == 200
        except Exception: up = False
        if up: break
        time.sleep(3)
    check("setup", "the public address answers over the internet", up)
    code = re.search(r"cr_[0-9a-f]+", cli_run("admin", "add-host", "hosty", "--db", str(HOME / "server.db")).stdout).group(0)
    reg = cli_run("register", "--code", code, "--handle", "hosty", "--server", LOCAL)
    check("setup", "the host registered its device through the tunnel", reg.returncode == 0, reg.stdout + reg.stderr)
    repo = HOME / "repo"; repo.mkdir(); subprocess.run("git init -q && echo hi > a.txt && git add . && git -c user.email=a@b -c user.name=a commit -qm init", shell=True, cwd=repo)
    if CONTROL:
        names = control("__tools__")
        check("control", "the control server offers exactly the six tools and none that approves or rejects", len(names) == 6 and not any(x in n for n in names for x in ("approve", "reject", "remove", "close")), str(names))
        started = control("council_control_start_room", {"repo": str(repo), "title": "Acceptance via agent"})
        room = started.get("room_id", "")
        check("control", "an agent started a room, sealed as the server allows", bool(room) and started.get("sealed") is True, str(started))
        made_i = control("council_control_make_invite", {"room_id": room}); link = made_i.get("link", "")
        check("control", "the agent made an invitation that names the public address", link.startswith(addr + "/j/") and "#" in link, link[:60])
        check("control", "the agent is refused, with a reason, for a room that is not there", "no such room" in control("council_control_make_invite", {"room_id": "nope"}).get("error", ""))
    else:
        made = cli_run("start", "--repo", str(repo), "--host", "hosty", "--title", "Acceptance", "--crypto", CRYPTO)
        room = re.search(r"Created room (\S+)", made.stdout).group(1)
        check("setup", "a " + ("sealed (end to end encrypted)" if CRYPTO == "e2e" else "plain") + " room was created", made.returncode == 0, made.stdout + made.stderr)
        link = next(w for w in cli_run("invite-link", "--room", room, "--me", "hosty").stdout.split() if w.startswith("http"))
    if SCENARIO == "recycled":
        # the link names slot-001; now the host gives that slot up, a stranger takes it, and the host comes back on a different slot
        from aletheore.council.cli import DEFAULT_REGISTRY_URL, DEFAULT_REGISTRY_KEY
        from aletheore.council.identity import Keystore
        from aletheore.council.registry_client import RegistryClient
        SERVER.crash()
        hostc = RegistryClient(httpx.Client(base_url=DEFAULT_REGISTRY_URL, timeout=20), Keystore(HOME / "keys").load_or_create("device"), bytes.fromhex(DEFAULT_REGISTRY_KEY))
        hostc.release()
        stranger = RegistryClient(httpx.Client(base_url=DEFAULT_REGISTRY_URL, timeout=20), Keystore(HOME / "stranger").load_or_create("device"), bytes.fromhex(DEFAULT_REGISTRY_KEY))
        taken = stranger.claim(); registry_cleanup.append(stranger)
        check("recycled", "a stranger now holds the slot the invitation names", addr.startswith("https://" + str(taken.get("slot")) + "."), str(taken.get("slot")))
        addr2 = SERVER.start()
        check("recycled", "the host came back on a different slot", addr2 != addr, f"{addr} -> {addr2}")
        for _ in range(40):
            try:
                if httpx.get(addr2 + "/healthz", timeout=10).status_code == 200: break
            except Exception: time.sleep(3)
        addr = addr2; store_reset = True
    print("\n" + "=" * 70 + "\nGUEST: on the other network run\n  python acceptance_guest.py \"" + link + "\"\n" + "=" * 70 + "\n", flush=True)
    (HOME / "LINK.txt").write_text(link); Path("acc_link.txt").write_text(link)
    from aletheore.council.dispatcher import send_message
    from aletheore.council.protocol import EventType
    store = cli.open_store()
    cursor = {"seq": 0}; seen = []
    def poll():
        while True:
            page = store.read_after(room, cursor["seq"], 500)
            if not page: return
            seen.extend(page); cursor["seq"] = page[-1].seq
    def say(t): send_message(store, room, "hosty", t, chat=True)
    def wait_for(prefix, timeout=900):
        end = time.time() + timeout
        while time.time() < end:
            try: poll()
            except ConnectionError: pass
            for e in seen:
                if isinstance(e.body.get("text"), str) and e.body["text"].startswith(prefix) and e.sender_participant == GUEST: return e.body["text"]
            time.sleep(1.5)
        return None
    # ---- the join: the host compares the safety words and approves
    end = time.time() + float(os.environ.get('ACC_JOIN_TIMEOUT', 1800)); rid = None
    while time.time() < end and rid is None:
        out = cli_run("requests", "--room", room, "--me", "hosty").stdout
        m = re.search(r"(\S+)\s+" + GUEST + r"\s+words:\s+(\S+)", out)
        if m: rid, words = m.group(1), m.group(2)
        else: time.sleep(3)
    if CONTROL and rid:
        seen_by_agent = [r for r in control("council_control_list_join_requests", {"room_id": room}).get("requests", []) if r["handle"] == GUEST]
        check("control", "the agent sees the same waiting request and the same four safety words as the human's screen", len(seen_by_agent) == 1 and seen_by_agent[0]["safety_words"] == words and seen_by_agent[0]["request_id"] == rid, str(seen_by_agent))
        st = control("council_control_room_status", {"room_id": room})
        check("control", "the room is still host-only while the request waits (the agent cannot let anyone in)", st.get("members") == ["hosty"] and st.get("pending_join_requests") == 1, str(st))
    check("join", "a join request arrived from the other network", rid is not None)
    if rid is None: raise SystemExit("no join request: stopping")
    say_(f"SAFETY WORDS on the host: {words}  (the guest's screen must show the same four words)")
    ap = cli_run("approve-join", rid, "--room", room, "--me", "hosty")
    check("join", "the host approved the guest and handed over the room key", ap.returncode == 0, ap.stdout + ap.stderr)
    if CONTROL:
        st2 = control("council_control_room_status", {"room_id": room})
        check("control", "after the human approved, the agent's status shows the guest as a member", GUEST in st2.get("members", []), str(st2))
        audit = (HOME / "control-audit.log").read_text(encoding="utf-8") if (HOME / "control-audit.log").exists() else ""
        check("control", "every agent action is in the local audit log and the link and secrets are not", all(t in audit for t in ("start_room", "make_invite", "list_join_requests")) and link.split("#")[1] not in audit and "claim" not in audit)
    check("join", "the guest announced itself in the sealed room", wait_for("G:ready", 300) is not None)
    # ---- P1
    say("H:P1")
    for t in HOST_MSGS: say(t)
    check("P1", "the guest confirmed phase 1", wait_for("G:P1 ok", 300) is not None)
    poll(); gmsgs = [e.body["text"] for e in seen if e.sender_participant == GUEST and re.match(r"G1-\d\|", e.body.get("text", ""))]
    check("P1", "the host received all five guest messages with their exact content", gmsgs == [f"G1-{i}|" + ("x" * (i * 700)) for i in range(5)], str(len(gmsgs)))
    # ---- P2
    bundles = cli.bundle_store(store); fh = blob("host-1"); sha_h = hashlib.sha256(fh).hexdigest(); (HOME / "fh.bin").write_bytes(fh)
    bundles.put_bundle(room, sha_h, HOME / "fh.bin"); say(f"H:P2 up {sha_h}")
    msg = wait_for("G:P2 up", 300); sha_g = msg.split()[-1] if msg else ""
    try:
        bundles.get_bundle(room, sha_g, HOME / "dlg.bin")
        check("P2", "the host downloaded the guest's file and it matches byte for byte", hashlib.sha256((HOME / "dlg.bin").read_bytes()).hexdigest() == sha_g == hashlib.sha256(blob("guest-1")).hexdigest())
    except Exception as exc: check("P2", "the host downloaded the guest's file", False, repr(exc))
    say("H:P2 dl ok")
    check("P2", "the guest reported the file allowance stopped its extra upload", wait_for("G:P2 capped", 300) is not None)
    # ---- P3 outage: freeze the server (a sleeping laptop) and kill its tunnel program
    say("H:P3 cutting"); time.sleep(8)
    for k in SERVER.tunnel_kids():
        try: k.kill()
        except psutil.NoSuchProcess: pass
    SERVER.freeze()
    time.sleep(6)
    try: down = httpx.get(addr + "/healthz", timeout=10).status_code != 200
    except Exception: down = True
    check("P3", "while the host is away its address does not answer", down)
    say_("host is away for 55 seconds (server frozen" + ("" if SCENARIO == "local" else ", tunnel killed") + ")"); time.sleep(55)
    SERVER.thaw()
    end = time.time() + 120; back = False
    while time.time() < end and not back:
        try: back = httpx.get(addr + "/healthz", timeout=10).status_code == 200
        except Exception: time.sleep(3)
    check("P3", "the host woke up and its address answers again (same address)", back)
    check("P3", "the guest reported all queued messages delivered", wait_for("G:P3 done", 400) is not None)
    poll(); g3 = [e.body["text"].split()[0] for e in seen if e.sender_participant == GUEST and e.body.get("text", "").startswith("G3-")]
    check("P3", "the host holds each of the guest's five outage messages exactly once, in order", g3 == [f"G3-{i}" for i in range(5)], str(g3))
    # ---- P4 hard crash of the server, then a fresh start
    SERVER.crash()
    say_("server killed with no warning; starting it again over the same database"); time.sleep(3)
    addr2 = SERVER.start()
    check("P4", "after the crash the host's address is the same as before (the slot was kept)", addr2 == addr, f"{addr} -> {addr2}")
    end = time.time() + 90
    while time.time() < end:
        try:
            if httpx.get(addr + "/healthz", timeout=10).status_code == 200: break
        except Exception: pass
        time.sleep(3)
    say("H:P4 back")
    check("P4", "the guest reported it carried on after the crash", wait_for("G:P4 ok", 400) is not None)
    # ---- P5 remove the guest
    rm = cli_run("remove", GUEST, "--room", room, "--me", "hosty")
    check("P5", "the host removed the guest (the room moves to a new key)", rm.returncode == 0, rm.stdout + rm.stderr)
    say("SECRET-AFTER-REMOVAL the removed guest must never read this")
    time.sleep(45)
finally:
    say_("cleaning up")
    try: SERVER.stop()
    except Exception: pass
    if SCENARIO != "local":
        try:
            from aletheore.council.cli import DEFAULT_REGISTRY_URL, DEFAULT_REGISTRY_KEY
            from aletheore.council.identity import Keystore
            from aletheore.council.registry_client import RegistryClient
            import httpx
            for ident_dir in (HOME / "keys", HOME / "stranger"):
                if ident_dir.exists():
                    rc = RegistryClient(httpx.Client(base_url=DEFAULT_REGISTRY_URL, timeout=20), Keystore(ident_dir).load_or_create("device"), bytes.fromhex(DEFAULT_REGISTRY_KEY))
                    try: rc.release()
                    except Exception: pass
            say_(f"slots released; registry now {httpx.get(DEFAULT_REGISTRY_URL + '/v1/status').json()['in_use']} / 150")
        except Exception as exc: say_(f"RELEASE FAILED: {exc}")
    for p in psutil.process_iter(["name"]):
        if "cloudflared" in (p.info["name"] or "").lower() and False: pass
bad = [r for r in RESULTS if not r[2]]
say_(f"=== HOST RESULT: {len(RESULTS) - len(bad)} of {len(RESULTS)} checks passed ===")
for r in bad: say_(f"FAILED: {r[0]}: {r[1]}")
