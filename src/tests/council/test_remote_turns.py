import threading
import time
from pathlib import Path

import pytest

from aletheore.council.approval import approve
from aletheore.council.bundle_transport import TransportError, create_bundle
from aletheore.council.protocol import EventDraft, EventType
from aletheore.council.remote_turns import RemoteTurnRunner
from aletheore.council.runners import ScriptedRunner
from aletheore.council.state import load_state
from tests.council.world_fixture import daemon, git, world  # noqa: F401


def start_daemon(d):
    stop = threading.Event()
    t = threading.Thread(target=d.run_forever, args=(stop,), daemon=True)
    t.start()
    return stop, t


def verdict(w, branch, area="redis", vid="v1"):
    w["store"].append(EventDraft(w["alice"].room_id, f"dec-{vid}", "t1", EventType.DECISION, "alice", "planner", None,
                                 {"verdict_id": vid, "decision": "schema wins", "affected_area": area, "winning_branch": branch}))


def test_remote_turn_roundtrip_with_daemon(world):
    d, _ = daemon(world, "bob", ScriptedRunner(final_text="UUIDs are the source of truth"), poll_interval_s=0.02)
    stop, t = start_daemon(d)
    try:
        runner = RemoteTurnRunner(world["store"], world["alice"].room_id, "alice", "bob", "claude", "t1", timeout_s=10, poll_interval_s=0.02)
        res = runner.run("argue your case", Path("."), Path("."), {}, 10)
    finally:
        stop.set()
        t.join(5)
    assert res.ok and res.final_text == "UUIDs are the source of truth"
    claims = [e for e in world["store"].read_after(world["alice"].room_id, 0) if e.type is EventType.CLAIM]
    assert claims and claims[0].sender_participant == "bob" and claims[0].sender_agent == "claude"


def test_remote_turn_timeout_returns_failed_result(world):
    runner = RemoteTurnRunner(world["store"], world["alice"].room_id, "alice", "bob", "claude", "t1", timeout_s=0.3, poll_interval_s=0.02)
    res = runner.run("argue", Path("."), Path("."), {}, 0.3)
    assert not res.ok and "no reply from bob" in res.final_text


def test_remote_turn_failure_reply_is_reported(world):
    d, _ = daemon(world, "bob", ScriptedRunner(final_text="crashed", ok=False), poll_interval_s=0.02)
    stop, t = start_daemon(d)
    try:
        runner = RemoteTurnRunner(world["store"], world["alice"].room_id, "alice", "bob", "claude", "t1", timeout_s=10, poll_interval_s=0.02)
        res = runner.run("argue", Path("."), Path("."), {}, 10)
    finally:
        stop.set()
        t.join(5)
    assert not res.ok and "crashed" in res.final_text


def test_conflict_with_missing_remote_side_still_posts_a_verdict(world):
    room = world["alice"]
    runners = {"codex": ScriptedRunner(final_text="int ids"), "planner": ScriptedRunner(final_text="WINNER: none\nneeds a human")}
    out = room.raise_conflict("t1", "id types", "redis", "schema", runners, max_rounds=1, remote_timeout_s=0.3)
    events = world["store"].read_after(room.room_id, 0)
    assert out.verdict_event.type is EventType.DECISION
    failed = [e for e in events if e.body.get("subtype") == "debate_run_failed"]
    assert failed and failed[0].sender_participant == "alice"  # posted by the planner, not forged as bob


def test_conflict_roundtrip_with_remote_daemon(world):
    d, _ = daemon(world, "bob", ScriptedRunner(final_text="AGREE: ints are fine"), poll_interval_s=0.02)
    stop, t = start_daemon(d)
    try:
        runners = {"codex": ScriptedRunner(final_text="ints"), "planner": ScriptedRunner(final_text="WINNER: codex\nok")}
        out = world["alice"].raise_conflict("t1", "id types", "redis", "schema", runners, max_rounds=2, remote_timeout_s=10)
    finally:
        stop.set()
        t.join(5)
    assert out.converged and out.verdict_event.body["winner"] == "codex"
    bob_claims = [e for e in world["store"].read_after(world["alice"].room_id, 0) if e.sender_participant == "bob" and e.type is EventType.CLAIM]
    assert len(bob_claims) == 1


def published_world(world, extra_writes=None):
    world["alice"].request_task("schema", "design table")
    writes = {"schema/n.sql": "1\n", **(extra_writes or {})}
    d, _ = daemon(world, "bob", ScriptedRunner(final_text="ok", writes=writes))
    d.run_once()
    return f"council/{world['alice'].room_id}/claude"


def test_apply_imports_peer_bundle_then_merges(world):
    branch = published_world(world)
    verdict(world, branch, area="redis")
    approve(world["store"], world["alice"].room_id, "v1", "alice")
    res = world["alice"].apply("v1", world["bundles"])
    assert res.ok
    rid = world["alice"].room_id
    assert "schema/n.sql" in git(world["a"], "ls-tree", "-r", "--name-only", f"council/{rid}/main")
    assert git(world["a"], "for-each-ref", f"refs/council/peers/bob/{branch}")
    assert git(world["a"], "branch", "--list", branch) == ""  # never became a local head


def test_apply_refuses_when_branch_was_never_published(world):
    rid = world["alice"].room_id
    verdict(world, f"council/{rid}/claude")
    approve(world["store"], rid, "v1", "alice")
    with pytest.raises(ValueError, match="never published"):
        world["alice"].apply("v1", world["bundles"])


def test_peer_bundle_with_out_of_area_files_blocked_after_import(world):
    branch = published_world(world, extra_writes={"redis/evil.txt": "x\n"})
    rid = world["alice"].room_id
    verdict(world, branch, area="redis")
    approve(world["store"], rid, "v1", "alice")
    res = world["alice"].apply("v1", world["bundles"])
    assert not res.ok and "redis/evil.txt" in res.message
    assert "schema/n.sql" not in git(world["a"], "ls-tree", "-r", "--name-only", f"council/{rid}/main")


def test_out_of_area_is_caught_from_bundle_contents_even_without_a_blocked_task(world):
    # bob publishes a branch by hand: no task events at all, an out-of-area file inside
    rid = world["alice"].room_id
    branch = f"council/{rid}/claude"
    b = world["b"]
    git(b, "checkout", "-q", "-b", branch, world["alice"].base_sha)
    (b / "schema").mkdir(exist_ok=True)
    (b / "schema" / "ok.sql").write_text("1\n")
    (b / "redis" / "sneaky.txt").write_text("x\n")
    git(b, "add", "-A")
    git(b, "commit", "-q", "-m", "by hand")
    info = create_bundle(b, branch, world["alice"].base_sha, world["tmp"] / "hand.bundle")
    world["bundles"].put_bundle(rid, info.sha256, world["tmp"] / "hand.bundle")
    world["store"].append(EventDraft(rid, "pub-hand", "task-h", EventType.SYSTEM, "bob", "claude", None,
                                     {"subtype": "branch_published", "task_id": "task-h", "participant": "bob", "branch": branch,
                                      "sha256": info.sha256, "size": info.size, "tip_sha": info.tip_sha, "base_sha": info.base_sha}))
    verdict(world, branch, area="redis")
    approve(world["store"], rid, "v1", "alice")
    res = world["alice"].apply("v1", world["bundles"])
    assert not res.ok and "redis/sneaky.txt" in res.message


def test_hostile_or_forged_branch_published_events_are_refused(world):
    rid = world["alice"].room_id
    branch = f"council/{rid}/claude"
    # a hostile branch name never matches the verdict's branch, so it is ignored
    world["store"].append(EventDraft(rid, "pub-evil", "task-e", EventType.SYSTEM, "bob", "claude", None,
                                     {"subtype": "branch_published", "task_id": "task-e", "participant": "bob", "branch": "../../evil",
                                      "sha256": "0" * 64, "size": 1, "tip_sha": "a" * 40, "base_sha": "b" * 40}))
    verdict(world, branch)
    approve(world["store"], rid, "v1", "alice")
    with pytest.raises(ValueError, match="never published"):
        world["alice"].apply("v1", world["bundles"])


def test_tampered_bundle_bytes_refused(world):
    branch = published_world(world)
    rid = world["alice"].room_id
    for f in (world["tmp"] / "bundles" / rid).glob("*.bundle"):
        f.write_bytes(f.read_bytes() + b"tamper")
    verdict(world, branch)
    approve(world["store"], rid, "v1", "alice")
    with pytest.raises(TransportError):
        world["alice"].apply("v1", world["bundles"])
    assert git(world["a"], "for-each-ref", "refs/council/") == ""


def test_bundle_published_by_someone_who_does_not_own_the_area_is_refused(world):
    rid = world["alice"].room_id
    branch = f"council/{rid}/claude"  # claude's area belongs to bob
    world["store"].append(EventDraft(rid, "pub-forged", "task-f", EventType.SYSTEM, "alice", "claude", None,
                                     {"subtype": "branch_published", "task_id": "task-f", "participant": "bob", "branch": branch,
                                      "sha256": "0" * 64, "size": 1, "tip_sha": "a" * 40, "base_sha": "b" * 40}))
    verdict(world, branch)
    approve(world["store"], rid, "v1", "alice")
    with pytest.raises(ValueError, match="never published|not the owner"):
        world["alice"].apply("v1", world["bundles"])
