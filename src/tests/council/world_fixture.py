"""Shared two-machine fixture: a "Mac" repo, a "Windows" clone of it, one room store, one bundle store."""
import subprocess

import pytest

from aletheore.council.bundle_store import LocalBundleStore
from aletheore.council.daemon import WorkerDaemon
from aletheore.council.room import Room
from aletheore.council.runners import ScriptedRunner
from aletheore.council.sqlite_store import SqliteRoomStore
from aletheore.council.state import Area

def git(repo, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def world(tmp_path):
    a = tmp_path / "mac repo"
    a.mkdir()
    git(a, "init", "-q", "-b", "main")
    for d in ("redis", "schema"):
        (a / d).mkdir()
        (a / d / "f.txt").write_text("base\n")
    git(a, "add", "-A")
    git(a, "commit", "-q", "-m", "init")
    b = tmp_path / "win repo"
    git(tmp_path, "clone", "-q", str(a), str(b))
    store = SqliteRoomStore(tmp_path / "room.db")
    alice_room = Room.create(store, a, "alice", tmp_path / "state_alice")
    alice_room.define_area(Area("redis", ["redis/**"], "alice", "codex"))
    alice_room.define_area(Area("schema", ["schema/**"], "bob", "claude"))
    bob_room = Room.open_remote(store, alice_room.room_id, b, tmp_path / "state_bob")
    bundles = LocalBundleStore(tmp_path / "bundles")
    return dict(store=store, a=a, b=b, alice=alice_room, bob=bob_room, bundles=bundles, tmp=tmp_path)


def daemon(w, who, runner=None, **kw):
    room = w[who]
    agent = "codex" if who == "alice" else "claude"
    runner = runner or ScriptedRunner(final_text="ok", writes={("redis/n.py" if who == "alice" else "schema/n.sql"): "1\n"})
    kw.setdefault("bundles", w["bundles"])
    return WorkerDaemon(w["store"], room, who, {agent: runner}, **kw), runner


