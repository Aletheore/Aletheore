"""A planner and an executor can talk it through: the depth limit that stops two agents looping is a setting (`council worker --max-wake-depth`), because the
default of two lets one agent hand work to another but never hear the answer. The budget (wakes per agent per ten minutes) stays the hard stop at any depth."""
import pytest
from typer.testing import CliRunner

from aletheore.council import cli
from aletheore.council.runners import ScriptedRunner
from tests.council.test_wake import events, say
from tests.council.test_wait_cli import room  # noqa: F401
from tests.council.world_fixture import daemon, world  # noqa: F401

runner = CliRunner()


def planner_and_executor(world, depth):
    # bob's claude plans, alice's codex executes: planner asks, executor reports back, planner closes with a verdict that mentions nobody
    planner = ScriptedRunner(texts=["@codex check line 3 for an off-by-one", "VERDICT: one bug, off-by-one at line 3"])
    executor = ScriptedRunner(texts=["I found an off-by-one at line 3. @claude your call."])
    b, rb = daemon(world, "bob", runner=planner, wake_from={"alice"}, max_wake_depth=depth)
    a, ra = daemon(world, "alice", runner=executor, wake_from={"bob"}, max_wake_depth=depth)
    return a, ra, b, rb


def run(a, b, rounds=12):
    for _ in range(rounds):
        a.run_once(); b.run_once()


def test_with_enough_depth_the_planner_hears_the_executor_and_closes_the_loop(world):
    a, ra, b, rb = planner_and_executor(world, 4)
    say(world, "alice", "@claude plan a review of parser.py")
    run(a, b)
    assert len(rb.calls) == 2 and len(ra.calls) == 1                         # planner, executor, planner
    assert "off-by-one at line 3" in rb.calls[1]                             # the planner's second turn was started by the executor's findings
    last = [e for e in events(world) if e.body.get("wake_for")][-1]
    assert last.body["text"].startswith("VERDICT") and last.body["wake_depth"] == 3


def test_the_default_depth_hands_work_over_but_never_hears_back(world):
    a, ra, b, rb = planner_and_executor(world, 2)
    say(world, "alice", "@claude plan a review of parser.py")
    run(a, b)
    assert len(rb.calls) == 1 and len(ra.calls) == 1                         # the executor's findings wake nobody


def test_the_wake_budget_still_stops_a_chain_however_deep_it_is_allowed(world):
    a, ra = daemon(world, "alice", runner=ScriptedRunner(final_text="@claude again"), wake_from={"bob"}, max_wake_depth=50, max_wakes=3)
    b, rb = daemon(world, "bob", runner=ScriptedRunner(final_text="@codex again"), wake_from={"alice"}, max_wake_depth=50, max_wakes=3)
    say(world, "alice", "@claude start")
    run(a, b, 30)
    assert len(ra.calls) <= 3 and len(rb.calls) <= 3                         # at most `max_wakes` each, then silence


def test_the_worker_command_has_the_flag_and_passes_it_on(room, monkeypatch):
    seen = {}

    class FakeDaemon:
        def __init__(self, *a, **kw):
            seen.update(kw)

        def run_forever(self, stop):
            stop.set()

        def close(self):
            pass

    monkeypatch.setattr(cli, "WorkerDaemon", FakeDaemon)
    res = runner.invoke(cli.app, ["worker", "--room", room, "--me", "alice", "--max-wake-depth", "4"])
    assert res.exit_code == 0, res.output
    assert seen["max_wake_depth"] == 4


def test_the_default_is_unchanged(room, monkeypatch):
    seen = {}

    class FakeDaemon:
        def __init__(self, *a, **kw):
            seen.update(kw)

        def run_forever(self, stop):
            stop.set()

        def close(self):
            pass

    monkeypatch.setattr(cli, "WorkerDaemon", FakeDaemon)
    assert runner.invoke(cli.app, ["worker", "--room", room, "--me", "alice"]).exit_code == 0
    assert seen["max_wake_depth"] == 2


@pytest.mark.parametrize("bad", ["0", "-1", "11", "abc"])
def test_a_depth_that_makes_no_sense_is_refused(room, bad):
    res = runner.invoke(cli.app, ["worker", "--room", room, "--me", "alice", "--max-wake-depth", bad])
    assert res.exit_code != 0


def test_help_explains_the_flag():
    out = runner.invoke(cli.app, ["worker", "--help"]).output
    assert "--max-wake-depth" in out
