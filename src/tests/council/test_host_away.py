"""When the host is away: workers stop starting work, finish what they have, hold results, and leases pause instead of expiring."""

from aletheore.council.connection import ConnectionMonitor
from aletheore.council.protocol import EventDraft, EventType
from aletheore.council.state import load_state
from tests.council.world_fixture import daemon, world  # noqa: F401


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def test_workers_stop_claiming_when_the_host_has_been_away_over_five_minutes_and_resume_on_return(world):
    clock = Clock()
    monitor = ConnectionMonitor(clock)
    task = world["alice"].request_task("redis", "build cache")
    d, runner = daemon(world, "alice", monitor=monitor, clock=clock)
    monitor.failed("host")
    clock.t += 200
    assert monitor.state().kind == "unreachable"
    clock.t += 200  # now 400 s: away
    assert monitor.state().kind == "away"
    assert d.run_once() == 0 and runner.calls == []  # nothing new is started while the host is away
    assert load_state(world["store"], world["alice"].room_id).tasks[task].status == "assigned"
    monitor.ok()
    assert d.run_once() >= 1 and len(runner.calls) == 1  # back: the pending request is picked up
    assert load_state(world["store"], world["alice"].room_id).tasks[task].status == "done"


def test_a_short_absence_does_not_stop_new_work(world):
    clock = Clock()
    monitor = ConnectionMonitor(clock)
    monitor.failed("host")
    clock.t += 100  # unreachable, not away
    world["alice"].request_task("redis", "build cache")
    d, runner = daemon(world, "alice", monitor=monitor, clock=clock)
    assert d.run_once() >= 1 and len(runner.calls) == 1


def test_a_lease_pauses_while_the_host_is_away_instead_of_expiring(world):
    clock = Clock(100.0)
    monitor = ConnectionMonitor(clock)
    task = world["alice"].request_task("redis", "build cache")
    room_id = world["alice"].room_id
    world["store"].append(EventDraft(room_id, f"claim:{task}", task, EventType.TASK, "alice", "codex", None,
                                     {"op": "claimed", "task_id": task, "instance": "other-instance", "at": 100.0, "down": 0.0}))
    monitor.failed("host")
    clock.t = 100.0 + 3500
    monitor.ok()  # 3,500 s of the 3,600 s since the claim were spent unable to reach the host
    clock.t += 200  # 3,700 s since the claim, but only 200 s of them with the host reachable
    d, runner = daemon(world, "alice", claim_timeout_s=3600, monitor=monitor, clock=clock)
    assert d.run_once() == 0 and runner.calls == []  # not stale: the absence does not count against the claim
    clock.t += 4000  # now genuinely stale
    assert d.run_once() >= 1 and len(runner.calls) == 1


def test_finished_work_is_held_while_away_and_uploaded_once_when_the_host_answers(world):
    world["alice"].request_task("schema", "design table")
    room_id = world["alice"].room_id
    d, runner = daemon(world, "bob")
    real_put = world["bundles"].put_bundle
    state = {"down": True}

    def flaky_put(*a, **k):
        if state["down"]:
            raise ConnectionError("host unreachable")
        return real_put(*a, **k)

    world["bundles"].put_bundle = flaky_put
    d.run_once()  # the task runs; the upload cannot happen
    events = world["store"].read_after(room_id, 0)
    assert not [e for e in events if e.body.get("subtype") in ("branch_published", "publish_failed")]  # not a failure, just waiting
    assert len(d._held) == 1 and len(runner.calls) == 1
    d.run_once()
    assert len(d._held) == 1  # still away: kept
    state["down"] = False
    d.run_once()
    pub = [e for e in world["store"].read_after(room_id, 0) if e.body.get("subtype") == "branch_published"]
    assert len(pub) == 1 and d._held == [] and len(runner.calls) == 1  # uploaded once, and the work was not run again
