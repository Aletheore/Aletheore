"""The wake path over a real HTTP room server: a mention posted by one machine is answered by another, once, even across a server restart."""
import subprocess
import time

import pytest

from aletheore.council.daemon import WorkerDaemon
from aletheore.council.protocol import EventDraft, EventType
from aletheore.council.remote_store import RemoteRoomStore
from aletheore.council.room import Room
from aletheore.council.runners import ScriptedRunner
from aletheore.council.state import Area
from tests.council.chaos.live import LiveServer


def git(repo, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def live(tmp_path):
    repo = tmp_path / "a"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "x").mkdir()
    (repo / "x" / "f.txt").write_text("base\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    clone = tmp_path / "b"
    git(tmp_path, "clone", "-q", str(repo), str(clone))
    server = LiveServer(tmp_path).start()
    alice = RemoteRoomStore(server.url, "tok-a", timeout_s=2, backoff_s=0.01)
    room = Room.create(alice, repo, "alice", tmp_path / "state_a")
    alice.add_member(room.room_id, "bob")
    room.define_area(Area("x", ["x/**"], "bob", "claude"))
    bob = RemoteRoomStore(server.url, "tok-b", timeout_s=2, backoff_s=0.01)
    bob_room = Room.open_remote(bob, room.room_id, clone, tmp_path / "state_b")
    yield dict(server=server, alice=alice, bob=bob, room=room, bob_room=bob_room)
    room.close_brokers()
    bob_room.close_brokers()
    server.stop()


def chat(live, text):
    return live["alice"].append(EventDraft(live["room"].room_id, f"m:{time.monotonic_ns()}", "room", EventType.QUESTION, "alice", None, None,
                                           {"text": text, "kind": "chat"}))


def answers(live):
    return [e for e in live["alice"].read_after(live["room"].room_id, 0) if e.body.get("wake_for")]


def test_a_mention_over_http_is_answered_by_the_other_machine(live):
    runner = ScriptedRunner(final_text="done from bob's machine")
    d = WorkerDaemon(live["bob"], live["bob_room"], "bob", {"claude": runner})
    chat(live, "@claude hello over the network")
    d.run_once()
    (a,) = answers(live)
    assert a.sender_participant == "bob" and a.sender_agent == "claude" and a.body["text"] == "done from bob's machine"
    assert runner.read_only_calls == [True]


def test_a_server_restart_neither_loses_nor_repeats_a_wake(live):
    runner = ScriptedRunner(final_text="ok")
    d = WorkerDaemon(live["bob"], live["bob_room"], "bob", {"claude": runner})
    chat(live, "@claude before the restart")
    d.run_once()
    live["server"].restart()
    chat(live, "@claude after the restart")
    for _ in range(3):
        try:
            d.run_once()
        except ConnectionError:
            time.sleep(0.2)
    assert len(runner.calls) == 2 and len(answers(live)) == 2
    d2 = WorkerDaemon(live["bob"], live["bob_room"], "bob", {"claude": runner})     # a fresh worker after the outage
    d2.run_once()
    assert len(runner.calls) == 2


def test_a_member_who_is_not_on_the_allow_list_cannot_wake_over_http(live):
    runner = ScriptedRunner(final_text="ok")
    live["alice"].add_member(live["room"].room_id, "carol")
    carol = RemoteRoomStore(live["server"].url, "tok-c", timeout_s=2, backoff_s=0.01)
    carol.append(EventDraft(live["room"].room_id, "c1", "room", EventType.QUESTION, "carol", None, None, {"text": "@claude run it", "kind": "chat"}))
    d = WorkerDaemon(live["bob"], live["bob_room"], "bob", {"claude": runner})
    d.run_once()
    assert runner.calls == []
