import subprocess

import pytest

from aletheore.council.approval import (
    apply_verdict,
    approve,
    can_approve,
    maybe_auto_apply,
    reject,
    required_approver,
    set_autonomy,
)
from aletheore.council.protocol import EventDraft, EventType
from aletheore.council.sqlite_store import SqliteRoomStore
from aletheore.council.state import load_state
from aletheore.council.worktrees import commit_all, create_worktree


def git(repo, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


def post(store, etype, body, cid, sender="alice", thread="t1"):
    return store.append(EventDraft("r1", cid, thread, etype, sender, None, None, body))


@pytest.fixture
def env(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    for d in ("redis", "schema"):
        (repo / d).mkdir()
        (repo / d / "f.txt").write_text("base\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    store = SqliteRoomStore(tmp_path / "r.db")
    store.create_room("r1", "alice")
    post(store, EventType.SYSTEM, {"subtype": "room_created", "host": "alice"}, "s0")
    post(store, EventType.SYSTEM, {"subtype": "area_defined", "name": "redis", "globs": ["redis/**"], "owner": "alice", "agent_id": "codex"}, "s1")
    post(store, EventType.SYSTEM, {"subtype": "area_defined", "name": "schema", "globs": ["schema/**"], "owner": "bob", "agent_id": "claude"}, "s2")
    wt, branch = create_worktree(repo, "r1", "claude", tmp_path / "wt")
    (wt / "schema" / "new.sql").write_text("create table x;\n")
    commit_all(wt, "claude work")
    post(store, EventType.DECISION, {"verdict_id": "v1", "decision": "schema wins", "affected_area": "schema", "winning_branch": branch}, "d1")
    return store, repo, branch, tmp_path


def test_owner_can_approve(env):
    store, *_ = env
    approve(store, "r1", "v1", "bob")
    assert load_state(store, "r1").verdicts["v1"].status == "approved"


def test_non_owner_cannot_approve(env):
    store, *_ = env
    assert not can_approve(load_state(store, "r1"), "v1", "carol")
    with pytest.raises(PermissionError):
        approve(store, "r1", "v1", "carol")
    with pytest.raises(PermissionError):
        reject(store, "r1", "v1", "carol")
    assert load_state(store, "r1").verdicts["v1"].status == "pending"


def test_host_can_override(env):
    store, *_ = env
    assert required_approver(load_state(store, "r1"), "v1") == "bob"
    approve(store, "r1", "v1", "alice")  # alice is host, bob owns schema
    assert load_state(store, "r1").verdicts["v1"].status == "approved"


def test_unowned_area_falls_back_to_host(env):
    store, *_ = env
    post(store, EventType.DECISION, {"verdict_id": "v2", "decision": "x", "affected_area": "nowhere"}, "d2")
    assert required_approver(load_state(store, "r1"), "v2") == "alice"


def test_apply_requires_approval(env):
    store, repo, branch, _ = env
    with pytest.raises(PermissionError):
        apply_verdict(store, "r1", repo, "v1", branch, "redis")


def test_apply_merges_and_creates_rework_task(env):
    store, repo, branch, _ = env
    approve(store, "r1", "v1", "bob")
    res = apply_verdict(store, "r1", repo, "v1", branch, "redis")
    assert res.ok
    state = load_state(store, "r1")
    assert state.verdicts["v1"].status == "applied"
    assert "rework-v1" in state.tasks and state.tasks["rework-v1"].area == "redis"
    assert "schema/new.sql" in git(repo, "ls-tree", "-r", "--name-only", "council/r1/main")


def test_apply_is_idempotent_on_events(env):
    store, repo, branch, _ = env
    approve(store, "r1", "v1", "bob")
    approve(store, "r1", "v1", "bob")
    apply_verdict(store, "r1", repo, "v1", branch, "redis")
    n = store.last_seq("r1")
    apply_verdict(store, "r1", repo, "v1", branch, "redis")  # merge of already merged branch is a no-op merge
    assert sum(1 for e in store.read_after("r1", 0) if e.body.get("subtype") == "verdict_applied") == 1
    assert store.last_seq("r1") == n


def test_out_of_area_files_block_apply_until_acknowledged(env):
    store, repo, branch, _ = env
    post(store, EventType.TASK, {"op": "assign", "task_id": "t1", "area": "schema"}, "k1")
    post(store, EventType.TASK, {"op": "started", "task_id": "t1", "branch": branch}, "k2", sender="bob")
    post(store, EventType.TASK, {"op": "blocked", "task_id": "t1", "files": ["redis/f.txt"]}, "k3", sender="bob")
    approve(store, "r1", "v1", "bob")
    res = apply_verdict(store, "r1", repo, "v1", branch, "redis")
    assert not res.ok and "redis/f.txt" in res.message
    assert "schema/new.sql" not in git(repo, "ls-tree", "-r", "--name-only", "council/r1/main")
    post(store, EventType.SYSTEM, {"subtype": "out_of_area_acknowledged", "task_id": "t1"}, "k4")
    assert apply_verdict(store, "r1", repo, "v1", branch, "redis").ok


def test_merge_conflict_blocks_and_reports(env):
    store, repo, branch, tmp = env
    post(store, EventType.SYSTEM, {"subtype": "area_defined", "name": "redis", "globs": ["redis/**", "schema/**"], "owner": "alice", "agent_id": "codex"}, "s9")
    wt2, b2 = create_worktree(repo, "r1", "codex", tmp / "wt")
    (wt2 / "schema" / "new.sql").write_text("conflicting\n")
    commit_all(wt2, "codex work")
    post(store, EventType.DECISION, {"verdict_id": "v3", "decision": "codex", "affected_area": "schema", "winning_branch": b2}, "d3")
    approve(store, "r1", "v1", "bob")
    assert apply_verdict(store, "r1", repo, "v1", branch, "redis").ok
    approve(store, "r1", "v3", "bob")
    res = apply_verdict(store, "r1", repo, "v3", b2, "redis")
    assert not res.ok and res.conflicts == ["schema/new.sql"]
    assert load_state(store, "r1").verdicts["v3"].status == "approved"  # not applied
    assert any(e.body.get("subtype") == "apply_blocked" for e in store.read_after("r1", 0))


def test_verdict_without_winning_branch_cannot_be_applied(env):
    store, repo, _, _ = env
    post(store, EventType.DECISION, {"verdict_id": "v4", "decision": "human", "affected_area": "schema", "winning_branch": None}, "d4")
    approve(store, "r1", "v4", "bob")
    with pytest.raises(ValueError):
        apply_verdict(store, "r1", repo, "v4", None, "redis")


def test_autonomy_only_host_can_enable(env):
    store, *_ = env
    with pytest.raises(PermissionError):
        set_autonomy(store, "r1", "bob", True)
    set_autonomy(store, "r1", "alice", True)
    assert load_state(store, "r1").autonomy is True


def test_auto_apply_off_does_nothing_on_does_apply(env):
    store, repo, branch, _ = env
    assert maybe_auto_apply(store, "r1", repo, "v1", "redis") is None
    set_autonomy(store, "r1", "alice", True)
    res = maybe_auto_apply(store, "r1", repo, "v1", "redis")
    assert res is not None and res.ok
    assert load_state(store, "r1").verdicts["v1"].status == "applied"


def test_autonomy_still_blocks_on_out_of_area_files(env):
    store, repo, branch, _ = env
    post(store, EventType.TASK, {"op": "assign", "task_id": "t1", "area": "schema"}, "k1")
    post(store, EventType.TASK, {"op": "started", "task_id": "t1", "branch": branch}, "k2", sender="bob")
    post(store, EventType.TASK, {"op": "blocked", "task_id": "t1", "files": ["redis/f.txt"]}, "k3", sender="bob")
    set_autonomy(store, "r1", "alice", True)
    res = maybe_auto_apply(store, "r1", repo, "v1", "redis")
    assert res is not None and not res.ok
    assert load_state(store, "r1").verdicts["v1"].status != "applied"


def test_apply_rechecks_branch_contents_when_no_task_was_marked_blocked(env):
    store, repo, branch, tmp = env
    wt = tmp / "wt" / "r1" / "claude"
    (wt / "redis" / "rogue.txt").write_text("rogue\n")  # outside claude's schema area, never recorded as blocked
    commit_all(wt, "rogue")
    approve(store, "r1", "v1", "bob")
    res = apply_verdict(store, "r1", repo, "v1", branch, "redis")
    assert not res.ok and "redis/rogue.txt" in res.message
    assert "schema/new.sql" not in git(repo, "ls-tree", "-r", "--name-only", "council/r1/main")


# -- human picks the winner when the planner said "none" -------------------------------------------
from aletheore.council.approval import choose_winner


@pytest.fixture
def undecided(env):
    store, repo, branch, tmp = env
    post(store, EventType.DECISION, {
        "verdict_id": "v2", "decision": "cannot tell", "affected_area": "schema", "winning_branch": None, "winner": None,
        "candidates": {"codex": "council/r1/codex", "claude": branch}, "affected_by_winner": {"codex": "schema", "claude": "redis"},
    }, "d2")
    return store, repo, branch


def test_host_chooses_winner_and_affected_area_follows_loser(undecided):
    store, repo, branch = undecided
    choose_winner(store, "r1", "v2", "alice", "claude")
    v = load_state(store, "r1").verdicts["v2"]
    assert v.winning_branch == branch and v.affected_area == "redis"


def test_non_host_cannot_choose_winner(undecided):
    store, *_ = undecided
    with pytest.raises(PermissionError):
        choose_winner(store, "r1", "v2", "bob", "claude")
    # and a forged event from bob does not count either
    post(store, EventType.SYSTEM, {"subtype": "winner_chosen", "verdict_id": "v2", "winner": "claude"}, "forge", sender="bob")
    assert load_state(store, "r1").verdicts["v2"].winning_branch is None


def test_winner_must_be_a_candidate(undecided):
    store, *_ = undecided
    with pytest.raises(ValueError):
        choose_winner(store, "r1", "v2", "alice", "someone-else")


def test_cannot_override_a_planner_verdict_or_choose_twice(env, undecided):
    store, repo, branch = undecided
    with pytest.raises(ValueError):
        choose_winner(store, "r1", "v1", "alice", "claude")  # v1 already has a winner
    choose_winner(store, "r1", "v2", "alice", "claude")
    with pytest.raises(ValueError):
        choose_winner(store, "r1", "v2", "alice", "codex")


def test_chosen_winner_applies_after_approval(undecided):
    store, repo, branch = undecided
    choose_winner(store, "r1", "v2", "alice", "claude")
    approve(store, "r1", "v2", "alice")
    v = load_state(store, "r1").verdicts["v2"]
    res = apply_verdict(store, "r1", repo, "v2", v.winning_branch, v.affected_area)
    assert res.ok


def test_rework_is_a_real_request_for_the_loser_with_the_decision_and_an_integration_sync(env):
    store, repo, branch, tmp = env
    approve(store, "r1", "v1", "bob")
    apply_verdict(store, "r1", repo, "v1", branch, "redis")
    req = next(e for e in store.read_after("r1", 0) if e.type is EventType.TASK and e.body.get("task_id") == "rework-v1")
    assert req.body["op"] == "request" and req.body["area"] == "redis" and req.body["sync_integration"] is True
    assert "schema wins" in req.body["instruction"] and "schema" in req.body["instruction"] and "inside your area" in req.body["instruction"]
    assert req.sender_participant == "alice"  # host-issued, so the owner's worker will run it
