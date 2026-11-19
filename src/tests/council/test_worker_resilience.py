"""A worker must outlive a bad moment. Before this, `run_forever` caught only a lost connection: any other error ended the loop, and the
CLI's planner thread died silently while the worker looked alive. Now the loop keeps going, backs off, and tells the room once."""
import threading
import time

from aletheore.council.daemon import run_guarded
from aletheore.council.protocol import EventType
from aletheore.council.store import read_all
from aletheore.council.waiting import wait_for
from tests.council.world_fixture import daemon, world  # noqa: F401


def run_in_thread(target, stop, seconds=1.0):
    t = threading.Thread(target=target, daemon=True)
    t.start()
    time.sleep(seconds)
    stop.set()
    t.join(3)
    assert not t.is_alive()


def notices(world):
    return [e for e in read_all(world["store"], world["alice"].room_id) if e.type is EventType.SYSTEM and e.body.get("subtype") == "worker_error"]


def test_the_loop_survives_an_ordinary_error_and_keeps_working(world):
    d, _ = daemon(world, "alice", poll_interval_s=0.01)
    calls = []
    real = d.run_once

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise ValueError("one bad event")
        return real()

    d.run_once = flaky
    stop = threading.Event()
    run_in_thread(lambda: d.run_forever(stop), stop)
    assert len(calls) > 3                                  # it went on after the error


def test_the_room_is_told_once_however_often_it_fails(world):
    d, _ = daemon(world, "alice", poll_interval_s=0.01)

    def always():
        raise OSError("disk went away")

    d.run_once = always
    stop = threading.Event()
    run_in_thread(lambda: d.run_forever(stop), stop)
    got = notices(world)
    assert len(got) == 1
    assert "disk went away" in got[0].body["note"] and got[0].sender_participant == "alice"


def test_a_different_error_is_told_again(world):
    d, _ = daemon(world, "alice", poll_interval_s=0.01)
    errs = iter([ValueError("first"), ValueError("first"), KeyError("second")] + [ValueError("first")] * 50)

    def step():
        raise next(errs)

    d.run_once = step
    stop = threading.Event()
    run_in_thread(lambda: d.run_forever(stop), stop)
    assert {e.body["note"].split(":")[-1].strip() for e in notices(world)} >= {"first", "'second'"}


def test_it_backs_off_while_failing_and_recovers_when_it_works(world):
    d, _ = daemon(world, "alice", poll_interval_s=0.05)
    times: list[float] = []
    state = {"fail": True}

    def step():
        times.append(time.monotonic())
        if state["fail"]:
            raise ValueError("boom")
        return 0

    d.run_once = step
    stop = threading.Event()
    t = threading.Thread(target=lambda: d.run_forever(stop), daemon=True)
    t.start()
    time.sleep(1.0)
    failing = len(times)
    state["fail"] = False
    deadline = time.monotonic() + 15.0                      # the longest backoff pause passes, then it runs at the normal rate again;
    while len(times) - failing <= 15 and time.monotonic() < deadline:   # wait for that rather than a fixed sleep, so a slow runner cannot flake
        time.sleep(0.05)
    stop.set()
    t.join(3)
    assert failing < 1.0 / 0.05 * 0.6                       # slower than the normal rate while failing
    assert len(times) - failing > 15                        # and back to normal after


def test_a_failure_to_post_the_notice_does_not_stop_the_loop(world):
    d, _ = daemon(world, "alice", poll_interval_s=0.01)
    calls = []

    def step():
        calls.append(1)
        raise ValueError("boom")

    d.run_once = step
    d.store = type("Broken", (), {"append": lambda self, draft: (_ for _ in ()).throw(ConnectionError("server away"))})()
    stop = threading.Event()
    run_in_thread(lambda: d.run_forever(stop), stop)
    assert len(calls) > 2


def test_a_lost_connection_still_goes_to_the_monitor_not_the_notice(world):
    class Monitor:
        def __init__(self):
            self.failed_calls = 0

        def ok(self):
            pass

        def failed(self, who):
            self.failed_calls += 1

    m = Monitor()
    d, _ = daemon(world, "alice", poll_interval_s=0.01, monitor=m)
    d.run_once = lambda: (_ for _ in ()).throw(ConnectionError("away"))
    stop = threading.Event()
    run_in_thread(lambda: d.run_forever(stop), stop)
    assert m.failed_calls > 1 and notices(world) == []


def test_the_host_is_woken_by_a_workers_error_notice(world):
    d, _ = daemon(world, "bob", poll_interval_s=0.01)
    d.run_once = lambda: (_ for _ in ()).throw(ValueError("bad"))
    stop = threading.Event()
    run_in_thread(lambda: d.run_forever(stop), stop, 0.4)
    got = wait_for(world["store"], world["alice"].room_id, "alice", since=0, timeout_s=0.3, poll_s=0.02)
    assert [e.body.get("subtype") for e in got] == ["worker_error"]


# -- the planner thread uses the same guard ---------------------------------------------------------------------------------------
def test_run_guarded_keeps_a_helper_loop_alive():
    calls, errors = [], []

    def step():
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("planner hiccup")

    stop = threading.Event()
    run_in_thread(lambda: run_guarded(step, stop, 0.01, on_error=errors.append), stop, 0.6)
    assert len(calls) > 4
    assert [str(e) for e in errors] == ["planner hiccup", "planner hiccup"]


def test_run_guarded_ignores_a_lost_connection_quietly():
    errors, calls = [], []

    def step():
        calls.append(1)
        raise ConnectionError("away")

    stop = threading.Event()
    run_in_thread(lambda: run_guarded(step, stop, 0.01, on_error=errors.append), stop, 0.3)
    assert len(calls) > 2 and errors == []


def test_run_guarded_a_failing_error_handler_cannot_kill_it():
    calls = []

    def step():
        calls.append(1)
        raise RuntimeError("x")

    def bad_handler(exc):
        raise RuntimeError("handler broke")

    stop = threading.Event()
    run_in_thread(lambda: run_guarded(step, stop, 0.01, on_error=bad_handler), stop, 0.3)
    assert len(calls) > 2
