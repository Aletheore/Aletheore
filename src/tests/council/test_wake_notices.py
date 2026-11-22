"""A mention that cannot be answered says so in the room. Before this, a mention from someone who may not wake the agent, or one older than the
age limit, was dropped without a word, so the sender waited for an answer that was never coming."""
import time

from aletheore.council.protocol import EventDraft, EventType
from tests.council.test_wake import events, say
from tests.council.world_fixture import daemon, world  # noqa: F401


def skips(world, reason=None):
    out = [e for e in events(world) if e.type is EventType.SYSTEM and e.body.get("subtype") == "wake_skipped"]
    return [e for e in out if reason is None or e.body.get("reason") == reason]


def test_a_sender_who_may_not_wake_the_agent_is_told(world):
    say(world, "bob", "@codex run something")
    d, runner = daemon(world, "alice")
    d.run_once()
    assert runner.calls == []
    got = skips(world, "not_allowed")
    assert len(got) == 1 and got[0].sender_participant == "alice" and got[0].sender_agent == "codex"
    assert "--wake-from" in got[0].body["note"] and "bob" in got[0].body["note"]


def test_the_notice_never_contains_a_mention_so_it_cannot_wake_anyone(world):
    say(world, "bob", "@codex @claude run something")
    d, _ = daemon(world, "alice")
    d.run_once()
    assert all("@" not in e.body["note"] for e in skips(world))


def test_repeated_mentions_from_one_sender_get_one_notice_per_window(world):
    for i in range(5):
        say(world, "bob", f"@codex try {i}")
    d, _ = daemon(world, "alice")
    d.run_once()
    d.run_once()
    assert len(skips(world, "not_allowed")) == 1                  # a guest cannot make the owner's worker post a flood


def test_a_restart_does_not_repeat_the_notice(world):
    say(world, "bob", "@codex run something")
    d1, _ = daemon(world, "alice")
    d1.run_once()
    d2, _ = daemon(world, "alice")
    d2.run_once()
    assert len(skips(world, "not_allowed")) == 1


def test_an_allowed_sender_gets_an_answer_not_a_notice(world):
    say(world, "bob", "@codex run something")
    d, runner = daemon(world, "alice", wake_from={"bob"})
    d.run_once()
    assert len(runner.calls) == 1 and skips(world) == []


def test_a_mention_too_old_to_answer_is_reported_once(world):
    say(world, "alice", "@claude do it")
    d, runner = daemon(world, "bob", clock=lambda: time.time() + 5000)
    d.run_once()
    d.run_once()
    assert runner.calls == []
    got = skips(world, "too_old")
    assert len(got) == 1 and got[0].sender_agent == "claude"


def test_history_from_long_ago_is_not_reported(world):
    say(world, "alice", "@claude do it")
    d, _ = daemon(world, "bob", clock=lambda: time.time() + 24 * 3600)
    d.run_once()
    assert skips(world) == []                                      # a worker replaying an old log stays quiet


def test_a_restart_does_not_repeat_the_too_old_notice(world):
    say(world, "alice", "@claude do it")
    d1, _ = daemon(world, "bob", clock=lambda: time.time() + 5000)
    d1.run_once()
    d2, _ = daemon(world, "bob", clock=lambda: time.time() + 5000)
    d2.run_once()
    assert len(skips(world, "too_old")) == 1


def test_a_chain_stopped_by_the_depth_limit_stays_silent(world):
    say(world, "alice", "@claude deep", wake_depth=4)  # the default limit
    d, runner = daemon(world, "bob")
    d.run_once()
    assert runner.calls == [] and skips(world) == []               # the loop guard must not itself make noise


def test_a_mention_of_someone_elses_agent_is_not_my_business(world):
    say(world, "carol", "@codex hi")
    d, _ = daemon(world, "bob")
    d.run_once()
    assert skips(world) == []


def test_the_sender_is_woken_by_the_notice_when_waiting(world):
    from aletheore.council.waiting import wait_for
    say(world, "bob", "@codex run something")
    d, _ = daemon(world, "alice")
    d.run_once()
    got = wait_for(world["store"], world["alice"].room_id, "bob", since=0, timeout_s=0.3, poll_s=0.02)
    assert [e.body.get("subtype") for e in got] == ["wake_skipped"]


def test_a_failed_wake_reaches_the_person_who_mentioned_the_agent(world):
    from aletheore.council.runners import ScriptedRunner
    from aletheore.council.waiting import wait_for
    say(world, "alice", "@claude do it")
    d, _ = daemon(world, "bob", runner=ScriptedRunner(ok=False, final_text="claude crashed"))
    d.run_once()
    got = wait_for(world["store"], world["alice"].room_id, "alice", since=0, timeout_s=0.3, poll_s=0.02)
    assert [e.body.get("subtype") for e in got] == ["wake_failed"] and got[0].body["to"] == "alice"
