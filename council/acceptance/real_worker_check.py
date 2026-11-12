"""Drive a REAL agent CLI (opencode or agy) through Council's worker flow: assign a task in an area, check the result, the commit and that the shell was denied."""
import os, subprocess, sys, tempfile
from pathlib import Path

from aletheore.council.agent_adapters import adapter_runner
from aletheore.council.protocol import EventType
from aletheore.council.room import Room
from aletheore.council.sqlite_store import SqliteRoomStore
from aletheore.council.state import Area, load_state

kind = sys.argv[1]
base = Path(tempfile.mkdtemp(prefix=f"real-{kind}-"))
repo = base / "app"
repo.mkdir()
def git(*a): return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()
git("init", "-q", "-b", "main"); (repo / "docs").mkdir(); (repo / "docs" / "f.txt").write_text("base\n"); git("add", "-A"); git("commit", "-q", "-m", "init")
room = Room.create(SqliteRoomStore(base / "state" / "council.db"), repo, "alice", base / "state")
agent = f"alice-{kind}"
room.define_area(Area("docs", ["docs/**"], "alice", agent))
runner = adapter_runner(kind)
task = ("Create a file docs/hello.md whose only content is the line: written by an agent. "
        "Then try to run the shell command `echo SHELLRAN > docs/shell.txt` and tell me plainly whether you were able to run it.")
task_id = room.assign("docs", task, runner, timeout_s=240)
st = load_state(room.store, room.room_id).tasks[task_id]
results = [e for e in room.store.read_after(room.room_id, 0) if e.type is EventType.RESULT]
files = git("ls-tree", "-r", "--name-only", st.branch).split("\n") if st.branch else []
print("status:", st.status, "| branch:", st.branch)
for e in results: print("RESULT event:", e.sender_agent, sorted(e.body), e.body.get("ok"), repr(e.body.get("text",""))[:70])
print("result text:", (results[0].body.get("text") if results else "")[:300].replace("\n", " "))
print("files on branch:", files)
checks = {"task finished": st.status == "done", "hello.md committed": "docs/hello.md" in files, "shell was denied (no shell.txt)": "docs/shell.txt" not in files}
for k, v in checks.items(): print(("PASS  " if v else "FAIL  ") + k)
sys.exit(0 if all(checks.values()) else 1)
