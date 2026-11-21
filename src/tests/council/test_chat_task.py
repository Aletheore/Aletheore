"""'@agent /task <what to do>' typed by the host in chat becomes a real task for that agent's area. A wake can only answer in text; this is how a
person says 'do this' in the room without leaving it. Only a person who is the host, typing it themselves, can start work this way."""
import time

import pytest

from aletheore.council.protocol import EventDraft, EventType
from aletheore.council.state import load_state
from aletheore.council.store import read_all
from aletheore.council.wake import parse_chat_task
from tests.council.test_wake import events, say
from tests.council.world_fixture import daemon, world  # noqa: F401


def requests(world):
    return [e for e in events(world) if e.type is EventType.TASK and e.body.get("op") == "request"]


def skips(world, reason):
    return [e for e in events(world) if e.body.get("subtype") == "wake_skipped" and e.body.get("reason") == reason]


@pytest.mark.parametrize("text,expected", [
    ("@claude /task add the table", ("claude", "add the table")),
    ("@Claude, /task add the table", ("claude", "add the table")),
    ("  @claude   /task   multi\nline  ", ("claude", "multi\nline")),
    ("@alice-codex /task go", ("alice-codex", "go")),
    ("@claude /task", None),
    ("@claude /task   ", None),
    ("please @claude /task do it", None),            # must start the message
    ("`@claude /task x`", None),                     # code is not a command
    ("```\n@claude /task x\n```", None),
    ("@claude task x", None),
    ("/task @claude x", None),
    ("", None),
])
def test_parse(text, expected):
    assert parse_chat_task(text) == expected


def test_the_hosts_chat_command_becomes_a_task_that_the_owner_runs(world):
    say(world, "alice", "@claude /task add the table")
    a, _ = daemon(world, "alice")
    a.run_once()
    (r,) = requests(world)
    assert r.body["area"] == "schema" and r.body["instruction"] == "add the table" and r.sender_participant == "alice"
    b, runner = daemon(world, "bob")
    b.run_once()
    assert len(runner.calls) == 1 and "add the table" in runner.calls[0]          # the task ran; nobody also answered it as chat
    assert load_state(world["store"], world["alice"].room_id).tasks[r.body["task_id"]].status == "done"


def test_the_command_is_not_also_a_wake(world):
    say(world, "alice", "@claude /task add the table")
    b, runner = daemon(world, "bob")
    b.run_once()
    assert runner.calls == []                                                      # no task yet (the host's worker has not run), and no chat wake either


def test_once_only_across_polls_and_restarts(world):
    say(world, "alice", "@claude /task add the table")
    for _ in range(2):
        a, _ = daemon(world, "alice")
        a.run_once(); a.run_once()
    assert len(requests(world)) == 1


def test_a_restarted_worker_does_not_call_a_task_that_already_ran_too_old(world):
    say(world, "alice", "@claude /task add the table")
    a, _ = daemon(world, "alice")
    a.run_once()
    assert len(requests(world)) == 1
    later, _ = daemon(world, "alice", clock=lambda: time.time() + 5000)       # a restart long after: the whole log is read again
    later.run_once()
    assert len(requests(world)) == 1 and skips(world, "too_old") == []        # the task exists, so there is nothing to report


def test_a_connection_error_while_starting_the_task_does_not_lose_the_command(world):
    say(world, "alice", "@claude /task add the table")
    a, _ = daemon(world, "alice")
    real = a.room.request_task
    a.room.request_task = lambda *args, **kw: (_ for _ in ()).throw(ConnectionError("the room server is unreachable"))
    try:
        with pytest.raises(ConnectionError):
            a.run_once()
    finally:
        a.room.request_task = real
    a.run_once()                                                                   # the server is back: the command is still waiting
    assert len(requests(world)) == 1


def test_a_guest_cannot_start_work_this_way(world):
    say(world, "bob", "@codex /task delete everything")
    a, runner = daemon(world, "alice", wake_from={"bob"})
    a.run_once()
    assert requests(world) == []


def test_an_agent_cannot_start_work_this_way_even_in_the_hosts_name(world):
    say(world, "alice", "@claude /task make more work", agent="codex")           # written by the host's agent, not typed by the host
    a, _ = daemon(world, "alice")
    a.run_once()
    assert requests(world) == []


def test_an_unknown_agent_is_told_to_the_host(world):
    say(world, "alice", "@ghost /task do it")
    a, _ = daemon(world, "alice")
    a.run_once()
    assert requests(world) == []
    got = skips(world, "unknown_agent")
    assert len(got) == 1 and got[0].body["to"] == "alice" and "@" not in got[0].body["note"]


def test_an_old_command_is_not_run_late_and_says_so(world):
    say(world, "alice", "@claude /task add the table")
    a, _ = daemon(world, "alice", clock=lambda: time.time() + 5000)
    a.run_once()
    assert requests(world) == [] and len(skips(world, "too_old")) == 1


def test_only_the_hosts_worker_converts_it(world):
    say(world, "alice", "@claude /task add the table")
    b, _ = daemon(world, "bob")
    b.run_once()
    assert requests(world) == []


def test_a_waiting_owner_is_woken_by_the_task(world):
    from aletheore.council.waiting import wait_for
    say(world, "alice", "@claude /task add the table")
    a, _ = daemon(world, "alice")
    a.run_once()
    got = wait_for(world["store"], world["alice"].room_id, "bob", since=0, timeout_s=0.3, poll_s=0.02)
    assert "request" in [e.body.get("op") for e in got]
