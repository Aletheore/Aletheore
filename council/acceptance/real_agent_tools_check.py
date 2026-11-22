"""With the REAL Claude Code and Codex CLIs: can an agent in a task reach the room through the council_post tool and leave a claim in it?

    PYTHONPATH=<council folder> python real_agent_tools_check.py

Runs over a real local room server (agents reach the room through Council's localhost broker, as in a real room). If the council package is not installed
(only on PYTHONPATH), the tool server the agent starts cannot import it, because agents run with a small allowlisted environment; this script passes PYTHONPATH
through the tool server's own environment so the tools can start. A pip-installed council does not need that."""
import os, subprocess, sys, tempfile, time
from pathlib import Path

from aletheore.council.bundle_store import LocalBundleStore
from aletheore.council.daemon import WorkerDaemon
from aletheore.council.protocol import EventType
from aletheore.council.room import Room
from aletheore.council.runners import ClaudeRunner, CodexRunner
from aletheore.council.remote_store import RemoteRoomStore
from tests.council.chaos.live import LiveServer
from aletheore.council.state import Area, load_state
from aletheore.council.store import read_all

_orig = Room._identity
Room._identity = lambda self, participant, agent_id: {**_orig(self, participant, agent_id), "PYTHONPATH": os.environ.get("PYTHONPATH", "")}

def git(repo, *a):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()

base = Path(tempfile.mkdtemp(prefix="real-tools-"))
repo = base / "r"; repo.mkdir(); git(repo, "init", "-q", "-b", "main")
for d in ("redis", "schema"):
    (repo / d).mkdir(); (repo / d / "README.txt").write_text(f"{d}\n")
git(repo, "add", "-A"); git(repo, "commit", "-q", "-m", "init"); git(base, "clone", "-q", str(repo), str(base / "c"))
server = LiveServer(base).start()
store = RemoteRoomStore(server.url, "tok-a", timeout_s=5, backoff_s=0.05)
store_b = RemoteRoomStore(server.url, "tok-b", timeout_s=5, backoff_s=0.05)
ra = Room.create(store, repo, "alice", base / "sa")
store.add_member(ra.room_id, "bob")
ra.define_area(Area("redis", ["redis/**"], "alice", "alice-codex")); ra.define_area(Area("schema", ["schema/**"], "bob", "bob-claude"))
rb = Room.open_remote(store_b, ra.room_id, base / "c", base / "sb")
bundles = LocalBundleStore(base / "b")
alice = WorkerDaemon(store, ra, "alice", {"alice-codex": CodexRunner()}, bundles=bundles, task_timeout_s=240, claim_timeout_s=900)
bob = WorkerDaemon(store_b, rb, "bob", {"bob-claude": ClaudeRunner()}, bundles=bundles, task_timeout_s=240, claim_timeout_s=900)
ok_all = []
for agent, area, ext in (("bob-claude", "schema", "sql"), ("alice-codex", "redis", "txt")):
    ra.request_task(area, f"Create {area}/note.{ext} with one line of text. Then use the council_post tool to post a claim (type claim) saying 'note written'. Tell me plainly whether the tool worked.")
    end = time.time() + 480
    while time.time() < end:
        alice.run_once(); bob.run_once()
        if any(t.area == area and t.status in ("done", "failed", "blocked") for t in load_state(store, ra.room_id).tasks.values()):
            break
        time.sleep(0.5)
    ev = read_all(store, ra.room_id)
    claims = [e for e in ev if e.type is EventType.CLAIM and e.sender_agent == agent]
    res = next((e.body["text"] for e in ev if e.type is EventType.RESULT and e.sender_agent == agent), "")
    print(f"--- {agent} said: {res[:300]!r}")
    print(("PASS  " if claims else "FAIL  ") + f"{agent} left a claim in the room through council_post", [e.body.get("text") for e in claims])
    ok_all.append(bool(claims))
ra.close_brokers(); rb.close_brokers(); server.stop()
sys.exit(0 if all(ok_all) else 1)
