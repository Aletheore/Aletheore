"""ACCEPTANCE TEST, GUEST SIDE. Run on the guest's machine, on a different network from the host:
    python acceptance_guest.py "<invitation link>"
It joins with the real `council join-link`, then takes part in a scripted conversation with the host and prints PASS or FAIL for every check."""
import hashlib, os, random, subprocess, sys, tempfile, time
from pathlib import Path

LINK = sys.argv[1]
SRC = os.environ.get("COUNCIL_SRC") or str(Path(__file__).resolve().parents[2] / "src")  # the `src` folder of a checkout
sys.path.insert(0, SRC)
HOME = Path(os.environ.get("COUNCIL_HOME") or tempfile.mkdtemp(prefix="acc-guest-"))
os.environ["COUNCIL_HOME"] = str(HOME)
ME = "winguest"
UNI = "Grüße 名前 café ✓ — «quotes» \"and\" 'more' \\ backslash"
LONG = ("The quick brown fox jumps over the lazy dog. " * 175).rstrip()   # 7,875 characters: under the 8,000 limit, so it must arrive exactly
OVER = "A" * 9000   # over the limit: it must arrive cut with a visible marker, never silently
OVER_EXPECTED = OVER[:8000] + " ...[truncated]"
HOST_MSGS = ["short one", UNI, LONG, "line one\nline two\n\ttabbed", '{"looks": "like json", "n": [1,2,3]}', OVER]
EXPECTED = HOST_MSGS[:5] + [OVER_EXPECTED]
def blob(tag, size=1_300_000): return random.Random(tag).randbytes(size)
RESULTS = []
def check(phase, name, ok, detail=""):
    RESULTS.append((phase, name, bool(ok)))
    print(f"[{'PASS' if ok else 'FAIL'}] {phase}: {name}" + (f"  ({detail})" if detail and not ok else ""), flush=True)

from aletheore.council import cli, invites
from aletheore.council.dispatcher import send_message
from aletheore.council.outbox import OutboxStore
from aletheore.council.protocol import EventType
from aletheore.council.remote_store import TransferBudgetError

parsed = invites.parse_link(LINK)
print(f"joining {parsed.server} from this machine; safety words will be shown for the host to compare", flush=True)
proc = subprocess.run([sys.executable, "-m", "aletheore.council.cli", "join-link", LINK, "--handle", ME, "--timeout-minutes", "30"], capture_output=True, text=True, env={**os.environ, "PYTHONPATH": SRC})
print(proc.stdout.strip(), flush=True)
check("join", "the host approved this device and join-link finished", proc.returncode == 0, proc.stdout[-200:] + proc.stderr[-200:])
if proc.returncode != 0:
    sys.exit(1)
import json
joined_server = json.loads(cli._account_file().read_text(encoding="utf-8"))["server"]  # where the join really happened (the registry may have moved us off the link's address)
if joined_server.rstrip("/") != parsed.server.rstrip("/"):
    print(f"the link's address was out of date; the registry sent us to {joined_server}", flush=True)
check("join", "the guest used the registry when the link's address was out of date" if os.environ.get("ACC_EXPECT_MOVED") else "joined at the link's own address", (joined_server.rstrip("/") != parsed.server.rstrip("/")) == bool(os.environ.get("ACC_EXPECT_MOVED")))
os.environ["COUNCIL_SERVER"] = joined_server
store = cli.open_store()
room = next((r.room_id for r in store.list_rooms(ME)), None)
check("join", "this guest sees the room it was invited to", room is not None)
if os.environ.get("ACC_CONTROL") == "1":
    import asyncio
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    async def guest_control():
        params = StdioServerParameters(command=sys.executable, args=["-m", "aletheore.council.cli", "mcp-control", "--me", ME], env={**os.environ, "PYTHONPATH": SRC})
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                out = {}
                for key, tool, args in (("rooms", "council_control_list_rooms", {}), ("invite", "council_control_make_invite", {"room_id": room}),
                                        ("waiting", "council_control_list_join_requests", {"room_id": room}), ("area", "council_control_define_area", {"room_id": room, "name": "x", "globs": ["a/**"], "owner": ME, "agent_id": "g-codex"})):
                    out[key] = json.loads((await s.call_tool(tool, args)).content[0].text)
                return out
    got = asyncio.run(guest_control())
    mine = [r for r in got["rooms"].get("rooms", []) if r["room_id"] == room]
    check("control", "a guest's control tools list the room and say the guest is not its host", len(mine) == 1 and mine[0]["you_are_host"] is False, str(got["rooms"])[:150])
    check("control", "a guest's control tools refuse to make invitations, see the waiting list or define areas (host only)",
          all("only the host" in got[k].get("error", "") for k in ("invite", "waiting", "area")), str({k: got[k] for k in ("invite", "waiting", "area")})[:200])
box = OutboxStore(store, HOME / "outbox.json")
cursor = {"seq": 0}
def poll(): 
    out = []
    while True:
        page = store.read_after(room, cursor["seq"], 500)
        if not page: return out
        out += page; cursor["seq"] = page[-1].seq
def texts(events): return [(e.sender_participant, e.body.get("text", "")) for e in events if e.type is EventType.QUESTION or e.type is EventType.CLAIM]
seen = []
def wait_for(prefix, timeout=900):
    end = time.time() + timeout
    while time.time() < end:
        try: seen.extend(poll())
        except ConnectionError: pass
        for e in seen:
            if isinstance(e.body.get("text"), str) and e.body["text"].startswith(prefix) and e.sender_participant != ME: return e.body["text"]
        time.sleep(1.5)
    return None
def say(t): send_message(box, room, ME, t, chat=True)
def bundle(): return cli.bundle_store(store)

say("G:ready")
# ---- P1 messages: exact text, unicode, long, multi-line, through end-to-end encryption
check("P1", "host announced phase 1", wait_for("H:P1") is not None)
time.sleep(4); seen.extend(poll())
got = [t for s, t in texts(seen) if s != ME and not t.startswith(("H:", "G:"))]
check("P1", "all six host messages arrived exactly as sent (unicode, 7,875 characters, newlines, tabs) and the over-long one arrived cut with its marker", got[:6] == EXPECTED, f"got {len(got)}: " + str([len(t) for t in got[:6]]))
for i in range(5): say(f"G1-{i}|" + ("x" * (i * 700)))
say("G:P1 ok")
# ---- P2 files through the tunnel, end to end encrypted, with the host's daily file allowance
msg = wait_for("H:P2 up")
sha_h = msg.split()[-1] if msg else ""
dest = HOME / "dl1.bin"
try:
    bundle().get_bundle(room, sha_h, dest)
    check("P2", "downloaded the host's 1.3 MB file and it matches byte for byte", hashlib.sha256(dest.read_bytes()).hexdigest() == sha_h)
except Exception as exc:
    check("P2", "downloaded the host's file", False, repr(exc))
g1 = blob("guest-1"); sha_g = hashlib.sha256(g1).hexdigest(); (HOME / "up1.bin").write_bytes(g1)
try:
    bundle().put_bundle(room, sha_g, HOME / "up1.bin"); check("P2", "uploaded a 1.3 MB file of its own", True)
except Exception as exc:
    check("P2", "uploaded a 1.3 MB file of its own", False, repr(exc))
say(f"G:P2 up {sha_g}")
wait_for("H:P2 dl ok", 300)
g2 = blob("guest-2"); (HOME / "up2.bin").write_bytes(g2)
try:
    bundle().put_bundle(room, hashlib.sha256(g2).hexdigest(), HOME / "up2.bin"); check("P2", "the host's daily file allowance stopped the third transfer", False, "it was accepted")
except TransferBudgetError as exc:
    check("P2", "the host's daily file allowance stopped the third transfer, with a plain message", "Messages still work" in str(exc), str(exc)[:120])
say("G:P2 capped")
# ---- P3 the host's machine goes away; this guest keeps writing and nothing is lost or doubled
check("P3", "host announced the outage", wait_for("H:P3 cutting") is not None)
queued_during = 0
for i in range(5):
    box.append(__import__("aletheore.council.protocol", fromlist=["EventDraft"]).EventDraft(room, f"g3-{i}", "chat", EventType.QUESTION, ME, None, None, {"text": f"G3-{i} written while the host was away", "kind": "chat"}))
    time.sleep(3)
queued_during = len(box.queued())
print(f"      (during the outage {queued_during} of 5 messages were waiting in the outbox; the rest waited for the server)", flush=True)
end = time.time() + 240
while time.time() < end and box.queued():
    try: box.flush()
    except Exception: pass
    time.sleep(3)
check("P3", "after the host returned, every queued message was delivered", not box.queued())
say("G:P3 done")
# ---- P4 the host's server is killed and started again; this guest carries on with a fresh sign-in
check("P4", "after the host's server crashed and restarted, the room carried on (automatic sign-in again)", wait_for("H:P4 back", 600) is not None)
notice = [e for e in seen if e.type is EventType.SYSTEM and e.body.get("subtype") == "host_returned"]
check("P4", "the room shows a 'the host's server is back' notice, in a sealed room", len(notice) >= 1)
say("G:P4 ok")
# ---- P5 removed from the room: no more sending, no more reading
end = time.time() + 300; refused = None
while time.time() < end and refused is None:
    try: say("G5 probe")
    except PermissionError as exc: refused = str(exc)
    except Exception: pass
    time.sleep(3)
check("P5", "after the host removed this guest, sending is refused", refused is not None)
try:
    page = store.read_after(room, 0, 500); readable = any("SECRET-AFTER-REMOVAL" in str(e.body) for e in page)
    check("P5", "after removal the new secret message is unreadable", not readable)
except PermissionError:
    check("P5", "after removal the room cannot be read at all", True)
bad = [r for r in RESULTS if not r[2]]
print(f"\n=== GUEST RESULT: {len(RESULTS) - len(bad)} of {len(RESULTS)} checks passed ===", flush=True)
for r in bad: print("FAILED:", r[0], r[1])
(Path.cwd() / "acceptance_guest_report.txt").write_text("\n".join(f"{'PASS' if ok else 'FAIL'} {p}: {n}" for p, n, ok in RESULTS))
