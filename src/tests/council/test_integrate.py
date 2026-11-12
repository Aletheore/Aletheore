from aletheore.council.runners import ScriptedRunner
from aletheore.council.worktrees import integration_branch
from tests.council.world_fixture import daemon, git, world  # noqa: F401


def files_on_integration(w):
    return git(w["a"], "ls-tree", "-r", "--name-only", integration_branch(w["alice"].room_id)).split()


def test_host_integrates_its_own_finished_branch(world):
    world["alice"].request_task("redis", "write cache")
    d, _ = daemon(world, "alice")
    d.run_once()
    res = world["alice"].integrate("redis", world["bundles"])
    assert res.ok and "redis/n.py" in files_on_integration(world)
    assert world["alice"].integrate("redis", world["bundles"]).ok  # merging again changes nothing and does not fail


def test_host_integrates_a_branch_that_lives_on_another_machine_through_its_bundle(world):
    world["alice"].request_task("schema", "design table")
    d, _ = daemon(world, "bob")
    d.run_once()
    assert "schema/n.sql" not in files_on_integration(world)
    res = world["alice"].integrate("schema", world["bundles"])
    assert res.ok and "schema/n.sql" in files_on_integration(world)
    assert git(world["a"], "branch", "--show-current") == "main"  # the host's own branch is untouched


def test_files_outside_the_area_are_not_integrated(world):
    world["alice"].request_task("redis", "sneaky")
    d, _ = daemon(world, "alice", ScriptedRunner(final_text="ok", writes={"redis/ok.py": "1\n", "schema/oops.sql": "2\n"}))
    d.run_once()
    res = world["alice"].integrate("redis", world["bundles"])
    assert not res.ok and "outside" in res.message and "schema/oops.sql" in res.message
    assert "redis/ok.py" not in files_on_integration(world)


def test_a_branch_that_was_never_published_is_reported_not_crashed(world):
    import pytest
    with pytest.raises(ValueError, match="never published"):
        world["alice"].integrate("schema", world["bundles"])


# ---- bringing the merged work to another agent before its rework --------------------------------------------------
def test_remote_agent_gets_the_hosts_integration_branch_before_a_rework(world):
    rid = world["alice"].room_id
    world["alice"].request_task("redis", "write cache")
    d, _ = daemon(world, "alice")
    d.run_once()
    assert world["alice"].integrate("redis", world["bundles"]).ok  # also publishes the integration branch
    assert world["bob"].sync_integration("claude", "bob", world["bundles"]) == ""
    assert "redis/n.py" in git(world["b"], "ls-tree", "-r", "--name-only", f"council/{rid}/claude").split()
    assert git(world["b"], "branch", "--show-current") == "main"  # bob's own checkout is untouched


def test_hosts_own_agent_syncs_from_the_local_integration_branch(world):
    rid = world["alice"].room_id
    world["alice"].request_task("redis", "write cache")
    d, _ = daemon(world, "alice")
    d.run_once()
    world["alice"].integrate("redis", world["bundles"])
    # a second area owned by alice's machine: its branch gets the merged redis work without any bundle
    from aletheore.council.state import Area
    world["alice"].define_area(Area("docs", ["docs/**"], "alice", "alice-claude"))
    assert world["alice"].sync_integration("alice-claude", "alice", None) == ""
    assert "redis/n.py" in git(world["a"], "ls-tree", "-r", "--name-only", f"council/{rid}/alice-claude").split()


def test_sync_reports_a_problem_instead_of_failing_when_nothing_was_published(world):
    note = world["bob"].sync_integration("claude", "bob", world["bundles"])
    assert "integration" in note and note != ""


def test_daemon_runs_a_rework_request_on_top_of_the_integrated_work(world):
    rid = world["alice"].room_id
    world["alice"].request_task("redis", "write cache")
    d_alice, _ = daemon(world, "alice")
    d_alice.run_once()
    world["alice"].integrate("redis", world["bundles"])
    seen = {}

    class Peek:
        name = "peek"

        def run(self, instruction, worktree, mcp_config, identity_env, timeout_s, images=(), read_only=False):
            seen["has_redis_work"] = (worktree / "redis" / "n.py").exists()
            seen["prompt"] = instruction
            (worktree / "schema").mkdir(exist_ok=True)
            (worktree / "schema" / "rework.sql").write_text("1\n")
            from aletheore.council.runners import RunResult
            return RunResult(True, "reworked", 0, [])

    world["alice"].request_task("schema", "bring schema in line", task_id="rework-x", sync_integration=True)
    from aletheore.council.daemon import WorkerDaemon
    WorkerDaemon(world["store"], world["bob"], "bob", {"claude": Peek()}, bundles=world["bundles"]).run_once()
    assert seen["has_redis_work"] is True and "bring schema in line" in seen["prompt"]


def _rework_world(world, writes):
    """Host merged redis work; bob's agent is then asked to rework schema, writing `writes`. Returns bob's task status."""
    from aletheore.council.daemon import WorkerDaemon
    from aletheore.council.state import load_state
    world["alice"].request_task("redis", "write cache")
    d_alice, _ = daemon(world, "alice")
    d_alice.run_once()
    assert world["alice"].integrate("redis", world["bundles"]).ok
    world["alice"].request_task("schema", "bring schema in line", task_id="rework-y", sync_integration=True)
    bob = WorkerDaemon(world["store"], world["bob"], "bob", {"claude": ScriptedRunner(final_text="reworked", writes=writes)}, bundles=world["bundles"])
    bob.run_once()
    return load_state(world["store"], world["alice"].room_id).tasks["rework-y"]


def test_a_reworking_remote_agent_is_not_blamed_for_the_work_it_was_given(world):
    task = _rework_world(world, {"schema/rework.sql": "1\n"})
    assert task.status == "done" and task.blocked_files == []  # the host's merged redis files are not this agent's changes


def test_a_reworking_remote_agent_that_really_edits_outside_its_area_is_still_blocked(world):
    task = _rework_world(world, {"schema/ok.sql": "1\n", "redis/oops.py": "2\n"})
    assert task.status == "blocked" and task.blocked_files == ["redis/oops.py"]  # only its own stray edit, not the merged files
