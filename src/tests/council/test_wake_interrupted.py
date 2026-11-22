"""A wake whose worker died mid-run is reported, once, to the person who asked. It is not re-run: that could answer twice and cost twice."""
import time

from aletheore.council.protocol import EventDraft, EventType
from tests.council.test_wake import events, say
from tests.council.world_fixture import daemon, world  # noqa: F401


def claim(world, msg, instance="dead-worker", who="bob", agent="claude"):
    return world["store"].append(EventDraft(world["alice"].room_id, f"wakeclaim:{msg.event_id}:{agent}", msg.thread_id, EventType.SYSTEM, who, agent,
                                            msg.event_id, {"subtype": "wake_claimed", "instance": instance, "for": msg.event_id, "to": msg.sender_participant}))


def failures(world):
    return [e for e in events(world) if e.body.get("subtype") == "wake_failed"]


def test_a_claim_with_no_answer_long_after_is_reported_as_interrupted(world):
    msg = say(world, "alice", "@claude do it")
    claim(world, msg)
    d, runner = daemon(world, "bob", clock=lambda: time.time() + 4000)
    d.run_once()
    got = failures(world)
    assert len(got) == 1 and got[0].body["reason"] == "interrupted" and got[0].body["to"] == "alice" and got[0].sender_agent == "claude"
    assert runner.calls == []                                    # reported, never re-run


def test_not_before_the_run_could_still_be_going(world):
    msg = say(world, "alice", "@claude do it")
    claim(world, msg)
    d, _ = daemon(world, "bob", clock=lambda: time.time() + 30)
    d.run_once()
    assert failures(world) == []


def test_an_answered_wake_is_not_reported(world):
    msg = say(world, "alice", "@claude do it")
    d, runner = daemon(world, "bob")
    d.run_once()
    assert len(runner.calls) == 1
    d2, _ = daemon(world, "bob", clock=lambda: time.time() + 9000)
    d2.run_once()
    assert failures(world) == []


def test_a_wake_still_running_in_this_worker_is_not_reported(world):
    msg = say(world, "alice", "@claude do it")
    d, _ = daemon(world, "bob", clock=lambda: time.time() + 9000)
    claim(world, msg, instance=d.instance)
    d.run_once()
    assert failures(world) == []


def test_the_report_is_made_once_across_polls_and_restarts(world):
    msg = say(world, "alice", "@claude do it")
    claim(world, msg)
    d1, _ = daemon(world, "bob", clock=lambda: time.time() + 4000)
    d1.run_once(); d1.run_once()
    d2, _ = daemon(world, "bob", clock=lambda: time.time() + 4500)
    d2.run_once()
    assert len(failures(world)) == 1


def test_someone_elses_claim_is_none_of_my_business(world):
    msg = say(world, "bob", "@codex hi")
    claim(world, msg, who="alice", agent="codex")
    d, _ = daemon(world, "bob", clock=lambda: time.time() + 4000)
    d.run_once()
    assert failures(world) == []
