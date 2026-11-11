import subprocess

import pytest

from aletheore.council.protocol import EventType
from aletheore.council.room import Room
from aletheore.council.runners import ScriptedRunner
from aletheore.council.sqlite_store import SqliteRoomStore
from aletheore.council.state import Area, load_state


def git(repo, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def room(tmp_path):
    repo = tmp_path / "app"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    for d in ("redis", "schema"):
        (repo / d).mkdir()
        (repo / d / "f.txt").write_text("base\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    store = SqliteRoomStore(tmp_path / "state" / "council.db")
    r = Room.create(store, repo, "alice", tmp_path / "state")
    r.define_area(Area("redis", ["redis/**"], "alice", "codex"))
    r.define_area(Area("schema", ["schema/**"], "bob", "claude"))
    return r


def test_assign_creates_worktree_runs_worker_posts_result(room):
    runner = ScriptedRunner(final_text="redis layer built", writes={"redis/cache.py": "x = 1\n"})
    task_id = room.assign("redis", "build the cache", runner)
    state = load_state(room.store, room.room_id)
    t = state.tasks[task_id]
    assert t.status == "done" and t.branch == f"council/{room.room_id}/codex"
    assert "redis" in runner.calls[0] and "build the cache" in runner.calls[0] and "redis/**" in runner.calls[0]
    results = [e for e in room.store.read_after(room.room_id, 0) if e.type is EventType.RESULT]
    assert results and results[0].body["text"] == "redis layer built" and results[0].sender_agent == "codex"
    assert "redis/cache.py" in git(room.repo, "ls-tree", "-r", "--name-only", t.branch)


def test_out_of_area_change_marks_task_blocked(room):
    runner = ScriptedRunner(writes={"redis/ok.py": "1\n", "schema/oops.sql": "2\n"})
    task_id = room.assign("redis", "work", runner)
    t = load_state(room.store, room.room_id).tasks[task_id]
    assert t.status == "blocked" and t.blocked_files == ["schema/oops.sql"] and not t.acknowledged
    room.acknowledge_out_of_area(task_id, "alice")
    assert load_state(room.store, room.room_id).tasks[task_id].acknowledged


def test_acknowledge_requires_owner_or_host(room):
    task_id = room.assign("redis", "work", ScriptedRunner(writes={"schema/oops.sql": "2\n"}))
    with pytest.raises(PermissionError):
        room.acknowledge_out_of_area(task_id, "carol")


def test_failed_worker_marks_task_failed(room):
    task_id = room.assign("redis", "work", ScriptedRunner(final_text="crashed", ok=False, exit_code=1))
    assert load_state(room.store, room.room_id).tasks[task_id].status == "failed"


def test_conflict_runs_debate_and_creates_pending_verdict(room):
    room.assign("redis", "build", ScriptedRunner(writes={"redis/a.py": "1\n"}))
    room.assign("schema", "design", ScriptedRunner(writes={"schema/a.sql": "1\n"}))
    runners = {"codex": ScriptedRunner(texts=["redis wants int ids", "still int", "int"]),
               "claude": ScriptedRunner(texts=["schema uses uuid", "uuid", "uuid!"]),
               "planner": ScriptedRunner(final_text="WINNER: claude\nuuid is the source of truth")}
    outcome = room.raise_conflict("t-ids", "id type mismatch", "redis", "schema", runners)
    state = load_state(room.store, room.room_id)
    v = state.verdicts["verdict-t-ids"]
    assert v.status == "pending" and v.winning_branch == f"council/{room.room_id}/claude"
    assert v.affected_area == "redis" and outcome.verdict_event.body["winner"] == "claude"  # loser's area changes


def test_conflict_with_autonomy_applies_without_waiting(room):
    from aletheore.council.approval import set_autonomy
    room.assign("redis", "build", ScriptedRunner(writes={"redis/a.py": "1\n"}))
    room.assign("schema", "design", ScriptedRunner(writes={"schema/a.sql": "1\n"}))
    set_autonomy(room.store, room.room_id, "alice", True)
    runners = {"codex": ScriptedRunner(final_text="x"), "claude": ScriptedRunner(final_text="AGREE: yes"),
               "planner": ScriptedRunner(final_text="WINNER: claude\nok")}
    room.raise_conflict("t1", "s", "redis", "schema", runners)
    state = load_state(room.store, room.room_id)
    assert state.verdicts["verdict-t1"].status == "applied"
    assert "schema/a.sql" in git(room.repo, "ls-tree", "-r", "--name-only", f"council/{room.room_id}/main")


def test_room_can_be_reopened_from_store(room):
    reopened = Room.open(room.store, room.room_id, room.state_dir)
    assert reopened.repo == room.repo and reopened.host == "alice"


def test_missing_agent_binary_marks_task_failed_not_stuck(room, monkeypatch, tmp_path):
    from aletheore.council.runners import ClaudeRunner
    import shutil
    real_which = shutil.which
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: None if name in ("claude", "codex") else real_which(name, *a, **k))
    task_id = room.assign("schema", "work", ClaudeRunner())
    assert load_state(room.store, room.room_id).tasks[task_id].status == "failed"


def test_long_agent_text_is_clipped_in_result_event(room):
    from aletheore.council.protocol import MAX_AGENT_TEXT
    room.assign("redis", "work", ScriptedRunner(final_text="y" * 300000, writes={"redis/a.py": "1\n"}))
    res = [e for e in room.store.read_after(room.room_id, 0) if e.type is EventType.RESULT][0]
    assert len(res.body["text"]) <= MAX_AGENT_TEXT + 50


def test_rework_goes_to_the_loser_and_reusing_a_thread_is_refused(room):
    from aletheore.council.approval import set_autonomy
    room.assign("redis", "build", ScriptedRunner(writes={"redis/a.py": "1\n"}))
    room.assign("schema", "design", ScriptedRunner(writes={"schema/a.sql": "1\n"}))
    set_autonomy(room.store, room.room_id, "alice", True)
    runners = lambda: {"codex": ScriptedRunner(final_text="x"), "claude": ScriptedRunner(final_text="AGREE: y"),
                       "planner": ScriptedRunner(final_text="WINNER: claude\nok")}
    room.raise_conflict("tx", "s", "redis", "schema", runners())
    tasks = load_state(room.store, room.room_id).tasks
    assert tasks["rework-verdict-tx"].area == "redis"
    with pytest.raises(ValueError):
        room.raise_conflict("tx", "s", "redis", "schema", runners())


def test_remote_rooms_give_agents_the_council_tools_through_a_localhost_broker(tmp_path):
    """Found in the first real networked run: the agent's council_post tool wrote to a local database instead of the room
    server. Stage 3 fixes it properly: the tool talks to a broker on this machine that holds the store, so the agent gets
    the tools but never the server token and never a database path."""
    import json
    from fastapi.testclient import TestClient
    from aletheore.council.remote_store import RemoteRoomStore
    from aletheore.council.server import create_app

    repo = tmp_path / "app"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "redis").mkdir()
    (repo / "redis" / "f.txt").write_text("base\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    app = create_app(SqliteRoomStore(tmp_path / "srv.db"), {"tok": "alice"}, tmp_path / "bundles")
    store = RemoteRoomStore("http://testserver", "tok", client=TestClient(app), backoff_s=0)
    room = Room.create(store, repo, "alice", tmp_path / "state")
    try:
        room.define_area(Area("redis", ["redis/**"], "alice", "codex"))
        runner = ScriptedRunner(writes={"redis/a.py": "1\n"})
        room.assign("redis", "build", runner)
        assert "council_post" in runner.calls[0]
        cfg = json.loads(room._mcp_config("alice", "codex").read_text())
        env = cfg["mcpServers"]["council"]["env"]
        assert env["COUNCIL_BROKER_URL"].startswith("http://127.0.0.1:") and env["COUNCIL_BROKER_TOKEN"]
        assert "COUNCIL_STORE" not in env and "tok" not in {v for k, v in env.items() if k != "COUNCIL_BROKER_TOKEN"}
    finally:
        room.close_brokers()
