"""Drive the REAL Claude Code and Codex CLIs through Council's wake path, in one throwaway room on this machine.

    PYTHONPATH=<council folder> python real_wake_check.py

alice (the host) owns area `redis` with agent alice-codex; bob owns area `schema` with agent bob-claude. Both workers run in this process over one
local store, the way two machines would over a server. It checks, with the real agents:
  1. an @mention wakes the real Claude agent and its answer lands in the room, as that agent
  2. an @mention wakes the real Codex agent likewise
  3. a wake is read-only: asking an agent to create a file during a wake must not create one
  4. `@bob-claude /task ...` typed by the host becomes a task, Claude does it for real, the file is committed on its branch
  5. the same with Codex
  6. the host's `council wait` hears both results; each sender's wait hears a mention
Uses your own Claude Code and Codex sign-ins, so it counts against your usage. Prints PASS or FAIL per check and the agents' words.
"""
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from aletheore.council.bundle_store import LocalBundleStore
from aletheore.council.daemon import WorkerDaemon
from aletheore.council.protocol import EventDraft, EventType
from aletheore.council.room import Room
from aletheore.council.runners import ClaudeRunner, CodexRunner
from aletheore.council.sqlite_store import SqliteRoomStore
from aletheore.council.state import Area, load_state
from aletheore.council.store import read_all
from aletheore.council.waiting import wait_for

TURN_TIMEOUT = 240
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + name + (f"   [{detail}]" if detail and not ok else ""), flush=True)


def git(repo, *a):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


base = Path(tempfile.mkdtemp(prefix="real-wake-"))
repo, clone = base / "alice-repo", base / "bob-repo"
repo.mkdir()
git(repo, "init", "-q", "-b", "main")
for d in ("redis", "schema"):
    (repo / d).mkdir()
    (repo / d / "README.txt").write_text(f"the {d} area\n")
git(repo, "add", "-A")
git(repo, "commit", "-q", "-m", "init")
git(base, "clone", "-q", str(repo), str(clone))

store = SqliteRoomStore(base / "room.db")
alice_room = Room.create(store, repo, "alice", base / "state_alice")
alice_room.define_area(Area("redis", ["redis/**"], "alice", "alice-codex"))
alice_room.define_area(Area("schema", ["schema/**"], "bob", "bob-claude"))
bob_room = Room.open_remote(store, alice_room.room_id, clone, base / "state_bob")
RID = alice_room.room_id
bundles = LocalBundleStore(base / "bundles")

claude, codex = ClaudeRunner(), CodexRunner()
alice = WorkerDaemon(store, alice_room, "alice", {"alice-codex": codex}, bundles=bundles, task_timeout_s=TURN_TIMEOUT, claim_timeout_s=TURN_TIMEOUT + 600,
                     wake_cooldown_s=0.0)
bob = WorkerDaemon(store, bob_room, "bob", {"bob-claude": claude}, bundles=bundles, task_timeout_s=TURN_TIMEOUT, claim_timeout_s=TURN_TIMEOUT + 600,
                   wake_cooldown_s=0.0)


def say(text):
    return store.append(EventDraft(RID, f"say:{time.monotonic_ns()}", "room", EventType.QUESTION, "alice", None, None, {"text": text, "kind": "chat"}))


def pump(until, seconds=TURN_TIMEOUT * 2):
    """Run both workers until `until()` is true or time runs out."""
    end = time.time() + seconds
    while time.time() < end:
        alice.run_once()
        bob.run_once()
        if until():
            return True
        time.sleep(0.5)
    return False


def events():
    return read_all(store, RID)


def answer_from(agent, after_seq):
    return next((e for e in events() if e.seq > after_seq and e.sender_agent == agent and e.body.get("wake_for")), None)


def failure_from(agent, after_seq):
    return next((e for e in events() if e.seq > after_seq and e.sender_agent == agent and e.body.get("subtype") in ("wake_failed", "wake_skipped")), None)


def wake_test(agent, label, question):
    mark = events()[-1].seq
    say(f"@{agent} {question}")
    pump(lambda: answer_from(agent, mark) or failure_from(agent, mark))
    a, f = answer_from(agent, mark), failure_from(agent, mark)
    print(f"--- {label} said: {(a.body['text'] if a else (f.body.get('note') if f else '(nothing)'))[:400]!r}", flush=True)
    return a, f


# ---- 1 and 2: real wakes ----------------------------------------------------------------------------------------------------------
a, f = wake_test("bob-claude", "claude", "In one short sentence, what does your area's README.txt say? Reply in plain text.")
check("1. the real Claude agent was woken and answered in the room", a is not None and "schema" in a.body["text"].lower(), f.body.get("note") if f else "no answer")
check("1b. its answer is posted as that agent by its owner and links the message", a is not None and a.sender_participant == "bob" and a.body.get("wake_depth") == 1)

a, f = wake_test("alice-codex", "codex", "In one short sentence, what does your area's README.txt say? Reply in plain text.")
check("2. the real Codex agent was woken and answered in the room", a is not None and "redis" in a.body["text"].lower(), f.body.get("note") if f else "no answer")
check("2b. its answer is posted as that agent by its owner and links the message", a is not None and a.sender_participant == "alice" and a.body.get("wake_depth") == 1)

# ---- 3: a wake cannot write files -----------------------------------------------------------------------------------------------------
for agent, room_, area in (("bob-claude", bob_room, "schema"), ("alice-codex", alice_room, "redis")):
    mark = events()[-1].seq
    say(f"@{agent} Create a file named {area}/wake-wrote-this.txt containing the word hi, then tell me plainly whether you were able to.")
    pump(lambda: answer_from(agent, mark) or failure_from(agent, mark))
    wt, _ = room_._worktree(agent)
    check(f"3. a wake of {agent} did not create a file", not (wt / area / "wake-wrote-this.txt").exists())

# ---- 4 and 5: '@agent /task' becomes real work ----------------------------------------------------------------------------------------
for agent, area, ext in (("bob-claude", "schema", "sql"), ("alice-codex", "redis", "txt")):
    mark = events()[-1].seq
    line = {"sql": "-- written by a real agent", "txt": "written by a real agent"}[ext]
    say(f"@{agent} /task Create the file {area}/hello.{ext} whose only content is the single line: {line}")

    def done():
        st = load_state(store, RID)
        return any(t.area == area and t.status in ("done", "failed", "blocked") for t in st.tasks.values())

    ok = pump(done)
    st = load_state(store, RID)
    task = next((t for t in st.tasks.values() if t.area == area), None)
    check(f"4/5. '@{agent} /task' became a task and it finished ({agent})", ok and task is not None and task.status == "done",
          f"status={task.status if task else 'no task made'}")
    branch = task.branch if task else None
    files = git(repo if area == "redis" else clone, "ls-tree", "-r", "--name-only", branch).split("\n") if branch else []
    check(f"4/5. {area}/hello.{ext} is committed on {agent}'s branch", f"{area}/hello.{ext}" in files, str(files))
    if branch:
        content = git(repo if area == "redis" else clone, "show", f"{branch}:{area}/hello.{ext}")
        print(f"--- {agent} wrote: {content!r}", flush=True)
        check(f"4/5. its content is the requested line ({agent})", "written by a real agent" in content)

# ---- 6: what a waiting session would have heard ------------------------------------------------------------------------------------------
heard = wait_for(store, RID, "alice", since=0, timeout_s=1, poll_s=0.1)
ops = [e.body.get("op") for e in heard if e.type is EventType.TASK]
check("6. the host's wait hears both finished tasks", ops.count("finished") == 2, str(ops))
bob_heard = wait_for(store, RID, "bob", since=0, timeout_s=1, poll_s=0.1)
check("6b. bob's wait hears the mention of his agent and the task for his area",
      any(e.body.get("kind") == "chat" for e in bob_heard) and any(e.body.get("op") == "request" for e in bob_heard))

# ---- the whole room, for reading ----------------------------------------------------------------------------------------------------------
print("\n--- room log ---")
for e in events():
    who = e.sender_participant + (f"/{e.sender_agent}" if e.sender_agent else "")
    what = e.body.get("text") or e.body.get("note") or e.body.get("op") or e.body.get("subtype") or ""
    print(f"#{e.seq} {who} [{e.type.value}] {str(what)[:110]!r}")

alice_room.close_brokers()
bob_room.close_brokers()
print(f"\n{sum(results)} of {len(results)} checks passed   (scratch folder: {base})")
sys.exit(0 if results and all(results) else 1)
