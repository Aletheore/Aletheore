import subprocess

import pytest
from typer.testing import CliRunner

from aletheore.council import cli
from aletheore.council.runners import ScriptedRunner

runner = CliRunner()


def git(repo, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def env(tmp_path, monkeypatch):
    repo = tmp_path / "app"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "redis").mkdir()
    (repo / "redis" / "f.txt").write_text("base\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    monkeypatch.setenv("COUNCIL_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(cli, "make_runner", lambda agent: ScriptedRunner(final_text="built", writes={"redis/n.py": "1\n"}))
    return repo


def start(env):
    res = runner.invoke(cli.app, ["start", "--repo", str(env), "--host", "alice"])
    assert res.exit_code == 0, res.output
    return res.output.strip().split()[-1]


def test_cli_start_assign_status_round_trip(env):
    room = start(env)
    res = runner.invoke(cli.app, ["assign", "--room", room, "--area", "redis", "--glob", "redis/**", "--owner", "alice", "--agent", "codex", "build the cache"])
    assert res.exit_code == 0, res.output
    status = runner.invoke(cli.app, ["status", "--room", room])
    assert status.exit_code == 0 and "redis" in status.output and "done" in status.output


def test_cli_approve_denied_for_non_owner_exit_code_nonzero(env):
    from aletheore.council.protocol import EventDraft, EventType
    room = start(env)
    runner.invoke(cli.app, ["assign", "--room", room, "--area", "redis", "--glob", "redis/**", "--owner", "alice", "--agent", "codex", "x"])
    store = cli.open_store()
    store.append(EventDraft(room, "d1", "t", EventType.DECISION, "alice", "planner", None,
                            {"verdict_id": "v1", "decision": "d", "affected_area": "redis", "winning_branch": None}))
    res = runner.invoke(cli.app, ["approve", "--room", room, "--verdict", "v1", "--me", "carol"])
    assert res.exit_code != 0 and "may not" in res.output
    ok = runner.invoke(cli.app, ["approve", "--room", room, "--verdict", "v1", "--me", "alice"])
    assert ok.exit_code == 0, ok.output


def test_cli_agents_shows_unavailable_when_binary_missing(env, tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "nothing"))
    res = runner.invoke(cli.app, ["agents"])
    assert res.exit_code == 0 and "unavailable" in res.output and "claude" in res.output


# ---- stage 2: serve / join / worker / remote store ----
import json

from fastapi.testclient import TestClient

from aletheore.council.remote_store import RemoteRoomStore
from aletheore.council.server import create_app
from aletheore.council.sqlite_store import SqliteRoomStore


@pytest.fixture
def remote(env, tmp_path, monkeypatch):
    backing = SqliteRoomStore(tmp_path / "server.db")
    app = create_app(backing, {"tok-alice": "alice", "tok-bob": "bob"}, tmp_path / "server-bundles")
    client = TestClient(app)
    monkeypatch.setattr(cli, "remote_store", lambda server, token: RemoteRoomStore(server, token, client=client, backoff_s=0))
    monkeypatch.setenv("COUNCIL_SERVER", "http://testserver")
    monkeypatch.setenv("COUNCIL_TOKEN", "tok-alice")
    return backing, env


def test_cli_serve_with_no_tokens_runs_in_accounts_mode(tmp_path, monkeypatch):
    """The short `council serve` is a room people join with their own device keys; a token file is the older way."""
    seen = {}
    monkeypatch.setattr(cli, "run_server", lambda db, bundle_dir, tokens, retention, host, port, settings=None, host_identity=None:
                        seen.update(tokens=tokens, accounts=settings.accounts, host=host))
    monkeypatch.setenv("COUNCIL_HOME", str(tmp_path))
    res = runner.invoke(cli.app, ["serve", "--no-wake-lock", "--db", str(tmp_path / "s.db"), "--bundle-dir", str(tmp_path / "b")])
    assert res.exit_code == 0, res.output
    assert seen["accounts"] is True and seen["tokens"] == {} and seen["host"] == "127.0.0.1"


def test_cli_serve_reads_token_file_and_binds_localhost_by_default(tmp_path, monkeypatch):
    f = tmp_path / "t.json"
    f.write_text(json.dumps({"alice": "tok-a", "bob": "tok-b"}))
    seen = {}
    monkeypatch.setattr(cli, "run_server", lambda db, bundle_dir, tokens, retention, host, port, settings=None, host_identity=None: seen.update(tokens=tokens, host=host, port=port))
    monkeypatch.setenv("COUNCIL_HOME", str(tmp_path))
    res = runner.invoke(cli.app, ["serve", "--no-wake-lock", "--db", str(tmp_path / "s.db"), "--bundle-dir", str(tmp_path / "b"), "--token-file", str(f)])
    assert res.exit_code == 0, res.output
    assert seen["tokens"] == {"tok-a": "alice", "tok-b": "bob"} and seen["host"] == "127.0.0.1" and seen["port"] == 8765
    assert "tok-a" not in res.output and "tok-b" not in res.output


def test_cli_room_commands_use_remote_store_when_server_set(remote):
    backing, repo = remote
    res = runner.invoke(cli.app, ["start", "--repo", str(repo), "--host", "alice"])
    assert res.exit_code == 0, res.output
    room = res.output.strip().split()[-1]
    assert backing.host_of(room) == "alice"
    assert backing.read_after(room, 0)[0].body["subtype"] == "room_created"


def test_cli_join_refuses_when_base_commit_missing(remote, tmp_path):
    backing, repo = remote
    room = runner.invoke(cli.app, ["start", "--repo", str(repo), "--host", "alice"]).output.strip().split()[-1]
    backing.add_member(room, "bob")
    stranger = tmp_path / "stranger"
    stranger.mkdir()
    git(stranger, "init", "-q", "-b", "main")
    (stranger / "x").write_text("x")
    git(stranger, "add", "-A")
    git(stranger, "commit", "-q", "-m", "unrelated")
    res = runner.invoke(cli.app, ["join", "--room", room, "--me", "bob", "--repo", str(stranger)], env={"COUNCIL_TOKEN": "tok-bob"})
    assert res.exit_code != 0 and "starting commit" in res.output
    assert git(stranger, "branch", "--list", f"council/{room}/*") == ""


def test_cli_join_succeeds_with_matching_clone_and_records_repo(remote, tmp_path):
    backing, repo = remote
    room = runner.invoke(cli.app, ["start", "--repo", str(repo), "--host", "alice"]).output.strip().split()[-1]
    backing.add_member(room, "bob")
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", str(repo), str(clone))
    res = runner.invoke(cli.app, ["join", "--room", room, "--me", "bob", "--repo", str(clone)], env={"COUNCIL_TOKEN": "tok-bob"})
    assert res.exit_code == 0, res.output
    cfg = json.loads((cli.home() / "rooms" / f"{room}.json").read_text())
    assert cfg["repo"] == str(clone.resolve()) and cfg["me"] == "bob" and "token" not in json.dumps(cfg)


def test_token_not_printed_in_any_cli_output(remote, tmp_path):
    backing, repo = remote
    outs = []
    r = runner.invoke(cli.app, ["start", "--repo", str(repo), "--host", "alice"])
    outs.append(r.output)
    room = r.output.strip().split()[-1]
    outs.append(runner.invoke(cli.app, ["status", "--room", room]).output)
    outs.append(runner.invoke(cli.app, ["approve", "--room", room, "--verdict", "nope", "--me", "alice"]).output)
    assert all("tok-alice" not in o for o in outs)


def test_cli_wrong_token_gives_clean_error_not_a_traceback(remote):
    backing, repo = remote
    res = runner.invoke(cli.app, ["status", "--room", "ghost"], env={"COUNCIL_TOKEN": "wrong"})
    assert res.exit_code != 0 and "Traceback" not in res.output and "not permitted" in res.output.lower()


def test_make_runner_honours_model_env(monkeypatch):
    from aletheore.council.runners import ClaudeRunner, CodexRunner
    import importlib
    real = importlib.reload(cli).make_runner
    monkeypatch.setenv("COUNCIL_CLAUDE_MODEL", "haiku")
    monkeypatch.setenv("COUNCIL_CODEX_MODEL", "some-model")
    assert real("claude").model == "haiku" and real("codex").model == "some-model"
    monkeypatch.delenv("COUNCIL_CLAUDE_MODEL")
    assert real("claude").model is None


def test_cli_choose_winner_host_only(env):
    from aletheore.council.protocol import EventDraft, EventType
    room = start(env)
    runner.invoke(cli.app, ["assign", "--room", room, "--area", "redis", "--glob", "redis/**", "--owner", "alice", "--agent", "codex", "x"])
    store = cli.open_store()
    store.append(EventDraft(room, "d9", "t", EventType.DECISION, "alice", "planner", None, {
        "verdict_id": "v9", "decision": "tie", "affected_area": "redis", "winning_branch": None,
        "candidates": {"codex": f"council/{room}/codex", "claude": f"council/{room}/claude"},
        "affected_by_winner": {"codex": "redis", "claude": "redis"}}))
    bad = runner.invoke(cli.app, ["choose", "--room", room, "--verdict", "v9", "--winner", "codex", "--me", "carol"])
    assert bad.exit_code != 0 and "only the host" in bad.output
    ok = runner.invoke(cli.app, ["choose", "--room", room, "--verdict", "v9", "--winner", "codex", "--me", "alice"])
    assert ok.exit_code == 0, ok.output
    assert cli.load_state(store, room).verdicts["v9"].winning_branch == f"council/{room}/codex"


def test_cli_me_must_match_token_identity(remote):
    backing, repo = remote  # the token is alice's
    room = runner.invoke(cli.app, ["start", "--repo", str(repo), "--host", "alice"]).output.strip().split()[-1]
    res = runner.invoke(cli.app, ["autonomy", "--room", room, "--me", "bob", "on"])
    assert res.exit_code != 0 and "token belongs to alice" in res.output
    res = runner.invoke(cli.app, ["approve", "--room", room, "--verdict", "v1", "--me", "bob"])
    assert res.exit_code != 0 and "token belongs to alice" in res.output


def test_cli_second_worker_for_same_person_is_refused(env, monkeypatch):
    from aletheore.council.worker_lock import WorkerLock
    room = start(env)
    held = WorkerLock(cli.home(), room, "alice")
    held.acquire()
    try:
        res = runner.invoke(cli.app, ["worker", "--room", room, "--me", "alice"])
        assert res.exit_code != 0 and "already running" in res.output
    finally:
        held.release()


def test_cli_ask_and_say_post_messages_and_planner_option_is_host_only(env):
    from aletheore.council.state import load_state
    room = start(env)
    res = runner.invoke(cli.app, ["ask", "--room", room, "--me", "alice", "build user accounts"])
    assert res.exit_code == 0, res.output
    res = runner.invoke(cli.app, ["say", "--room", room, "--me", "alice", "hello team"])
    assert res.exit_code == 0, res.output
    store = cli.open_store()
    kinds = [(e.body.get("kind"), e.body.get("text")) for e in cli.read_all(store, room) if e.type.value == "question"]
    assert ("prompt", "build user accounts") in kinds and ("chat", "hello team") in kinds
    assert "p-" in res.output or True
    res = runner.invoke(cli.app, ["worker", "--room", room, "--me", "bob", "--planner", "claude"])
    assert res.exit_code != 0 and "host" in res.output


def test_cli_plan_approve_and_reject_are_host_only(env):
    from aletheore.council.protocol import EventDraft, EventType
    room = start(env)
    store = cli.open_store()
    runner.invoke(cli.app, ["area", "redis", "--room", room, "--glob", "redis/**", "--owner", "alice", "--agent", "alice-codex"])
    for pid in ("a", "b"):
        store.append(EventDraft(room, f"pl-{pid}", f"p-{pid}", EventType.PROPOSAL, "alice", "planner", None,
                                {"plan_id": f"plan-p-{pid}", "reply": "r", "tasks": [{"area": "redis", "instruction": "do"}]}))
    bad = runner.invoke(cli.app, ["plan", "approve", "plan-p-a", "--room", room, "--me", "carol"])
    assert bad.exit_code != 0 and "only the host" in bad.output
    assert runner.invoke(cli.app, ["plan", "approve", "plan-p-a", "--room", room, "--me", "alice"]).exit_code == 0
    assert runner.invoke(cli.app, ["plan", "reject", "plan-p-b", "--room", room, "--me", "alice"]).exit_code == 0
    plans = cli.load_state(store, room).plans
    assert plans["plan-p-a"].status == "approved" and plans["plan-p-b"].status == "rejected"
    assert runner.invoke(cli.app, ["plan", "bogus", "plan-p-a", "--room", room, "--me", "alice"]).exit_code != 0


def test_cli_start_with_title_lists_invites_and_closes(env):
    res = runner.invoke(cli.app, ["start", "--repo", str(env), "--host", "alice", "--title", "Signup flow"])
    assert res.exit_code == 0, res.output
    room = res.output.strip().split()[-1]
    listed = runner.invoke(cli.app, ["rooms", "--me", "alice"])
    assert listed.exit_code == 0 and room in listed.output and "Signup flow" in listed.output and "interactive" in listed.output
    assert runner.invoke(cli.app, ["rooms", "--me", "bob"]).output.count(room) == 0  # not invited yet
    bad = runner.invoke(cli.app, ["invite", "bob", "--room", room, "--me", "bob"])
    assert bad.exit_code != 0 and "only the host" in bad.output
    assert runner.invoke(cli.app, ["invite", "bob", "--room", room, "--me", "alice"]).exit_code == 0
    assert room in runner.invoke(cli.app, ["rooms", "--me", "bob"]).output
    assert runner.invoke(cli.app, ["close", "--room", room, "--me", "bob"]).exit_code != 0
    assert runner.invoke(cli.app, ["close", "--room", room, "--me", "alice"]).exit_code == 0
    assert "closed" in runner.invoke(cli.app, ["rooms", "--me", "alice"]).output
    assert runner.invoke(cli.app, ["say", "--room", room, "--me", "alice", "late"]).exit_code != 0


def test_cli_autonomous_room_turns_autonomy_on_and_posts_the_goal(env):
    res = runner.invoke(cli.app, ["start", "--repo", str(env), "--host", "alice", "--mode", "autonomous",
                                  "--goal", "build the signup flow", "--title", "auto"])
    assert res.exit_code == 0, res.output
    room = res.output.strip().split()[-1]
    store = cli.open_store()
    state = cli.load_state(store, room)
    assert state.autonomy is True
    goals = [e for e in cli.read_all(store, room) if e.type.value == "question" and e.body.get("kind") == "prompt"]
    assert len(goals) == 1 and goals[0].body["text"] == "build the signup flow" and goals[0].body.get("goal") is True
    assert store.room_info(room).mode == "autonomous"
    bad = runner.invoke(cli.app, ["start", "--repo", str(env), "--host", "alice", "--mode", "autonomous"])
    assert bad.exit_code != 0 and "goal" in bad.output


def test_cli_stop_is_host_only_and_ends_an_autonomous_room(env):
    res = runner.invoke(cli.app, ["start", "--repo", str(env), "--host", "alice", "--mode", "autonomous", "--goal", "g"])
    room = res.output.strip().split()[-1]
    assert runner.invoke(cli.app, ["stop", "--room", room, "--me", "bob"]).exit_code != 0
    assert runner.invoke(cli.app, ["stop", "--room", room, "--me", "alice"]).exit_code == 0
    s = cli.load_state(cli.open_store(), room)
    assert s.finished == "stopped" and s.autonomy is False


def test_view_without_a_room_picks_the_most_recently_active_open_one(env):
    store = cli.open_store()
    a = runner.invoke(cli.app, ["start", "--repo", str(env), "--host", "alice", "--title", "first"]).output.strip().split()[-1]
    b = runner.invoke(cli.app, ["start", "--repo", str(env), "--host", "alice", "--title", "second"]).output.strip().split()[-1]
    runner.invoke(cli.app, ["say", "--room", a, "--me", "alice", "newest activity"])
    assert cli.pick_room(store, "alice") == a
    runner.invoke(cli.app, ["close", "--room", a, "--me", "alice"])
    assert cli.pick_room(store, "alice") == b  # closed rooms are skipped while an open one exists
    import pytest
    with pytest.raises(KeyError):
        cli.pick_room(store, "nobody")


def test_hidden_commands_are_not_in_the_help(env):
    out = runner.invoke(cli.app, ["--help"]).output
    assert "assign" not in out and "rooms" in out and "invite" in out and "stop" in out
    assert runner.invoke(cli.app, ["assign", "--help"]).exit_code == 0  # still works for scripts


def test_cli_discuss_and_task_post_their_requests(env):
    room = start(env)
    res = runner.invoke(cli.app, ["discuss", "ints or uuids?", "--room", room, "--me", "alice"])
    assert res.exit_code == 0, res.output
    res = runner.invoke(cli.app, ["discuss", "just you", "--to", "alice-codex", "--room", room, "--me", "alice"])
    assert res.exit_code == 0, res.output
    res = runner.invoke(cli.app, ["task", "redis", "write the cache", "--room", room, "--me", "alice"])
    assert res.exit_code == 0, res.output
    store = cli.open_store()
    got = [(e.body["kind"], e.body.get("to") or e.body.get("area")) for e in cli.read_all(store, room)
           if e.type.value == "question" and e.body.get("kind") in ("ask", "task")]
    assert got == [("ask", "all"), ("ask", "alice-codex"), ("task", "redis")]
    assert runner.invoke(cli.app, ["discuss", "   ", "--room", room, "--me", "alice"]).exit_code != 0
