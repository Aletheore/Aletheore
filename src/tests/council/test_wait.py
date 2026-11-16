"""`council wait`: block until something addressed to this participant or its agents arrives, without replaying or losing events."""
import threading
import time

import pytest

from aletheore.council.protocol import EventDraft, EventType
from aletheore.council.waiting import Cursor, addressed, wait_for
from tests.council.world_fixture import world  # noqa: F401


def post(world, who, text, kind="chat", agent=None):
    return world["store"].append(EventDraft(world["alice"].room_id, f"m:{time.monotonic_ns()}", "room", EventType.QUESTION, who, agent, None,
                                            {"text": text, "kind": kind}))


def wait(world, me, **kw):
    kw.setdefault("timeout_s", 0.3)
    kw.setdefault("poll_s", 0.02)
    return wait_for(world["store"], world["alice"].room_id, me, **kw)


def test_a_mention_of_my_handle_or_agent_arrives(world):
    post(world, "alice", "@bob please look")
    post(world, "alice", "@claude and this")
    got = wait(world, "bob", since=0)
    assert [e.body["text"] for e in got] == ["@bob please look", "@claude and this"]


def test_other_peoples_agents_and_plain_chat_do_not(world):
    post(world, "alice", "@codex that is alice's agent")
    post(world, "alice", "just chat")
    assert wait(world, "bob", since=0) == []


def test_my_own_messages_never_wake_me(world):
    post(world, "bob", "@bob @claude talking to myself")
    assert wait(world, "bob", since=0) == []


def test_tasks_and_debate_turns_for_me_arrive(world):
    t = world["alice"].request_task("schema", "design the table")
    world["store"].append(EventDraft(world["alice"].room_id, "turn:x", "th-1", EventType.TASK, "alice", None, None,
                                     {"op": "debate_turn", "turn_id": "x", "participant": "bob", "agent": "claude", "instruction": "argue"}))
    got = wait(world, "bob", since=0)
    assert {e.body["op"] for e in got} == {"request", "debate_turn"}


def test_a_task_for_someone_elses_area_does_not_arrive(world):
    world["alice"].request_task("redis", "build cache")
    assert [e for e in wait(world, "bob", since=0) if e.type is EventType.TASK] == []


def test_include_all_returns_everything_by_others(world):
    post(world, "alice", "plain chat")
    post(world, "bob", "mine")
    assert [e.body["text"] for e in wait(world, "bob", since=0, include_all=True) if e.type is EventType.QUESTION] == ["plain chat"]


def test_waiting_blocks_until_a_message_arrives(world):
    out = []
    t = threading.Thread(target=lambda: out.extend(wait(world, "bob", since=None, timeout_s=5)))
    t.start()
    time.sleep(0.3)
    assert t.is_alive()                       # nothing yet: still waiting
    post(world, "alice", "@claude now")
    t.join(3)
    assert [e.body["text"] for e in out] == ["@claude now"]


def test_first_wait_starts_at_the_end_of_the_log(world):
    post(world, "alice", "@claude old news")
    assert wait(world, "bob", since=None) == []


def test_cursor_gives_each_event_once_and_survives_restarts(world, tmp_path):
    c = tmp_path / "cursor.json"
    post(world, "alice", "@claude one")
    first = wait(world, "bob", since=0, cursor=Cursor(c))
    assert [e.body["text"] for e in first] == ["@claude one"]
    assert wait(world, "bob", cursor=Cursor(c)) == []                      # a new process, same file: nothing again
    post(world, "alice", "@claude two")
    assert [e.body["text"] for e in wait(world, "bob", cursor=Cursor(c))] == ["@claude two"]


def test_peek_does_not_move_the_cursor(world, tmp_path):
    c = tmp_path / "cursor.json"
    post(world, "alice", "@claude one")
    assert len(wait(world, "bob", since=0, cursor=Cursor(c), peek=True)) == 1
    assert len(wait(world, "bob", since=0, cursor=Cursor(c))) == 1
    assert wait(world, "bob", cursor=Cursor(c)) == []


def test_an_unread_cursor_file_never_loses_events(world, tmp_path):
    c = tmp_path / "cursor.json"
    c.write_text("not json")
    post(world, "alice", "@claude after a damaged file")
    assert len(wait(world, "bob", since=0, cursor=Cursor(c))) == 1          # a damaged cursor means start from where `since` says


def test_a_negative_or_huge_since_is_handled(world):
    post(world, "alice", "@claude x")
    assert len(wait(world, "bob", since=-5)) == 1
    assert wait(world, "bob", since=10 ** 9) == []


def test_addressed_rules_directly(world):
    hello = post(world, "alice", "@Claude, hello")
    assert addressed(hello, "bob", {"bob", "claude"}, {"schema"}) is True
    assert addressed(post(world, "alice", "@claudette"), "bob", {"bob", "claude"}, {"schema"}) is False
    assert addressed(post(world, "alice", "see `@claude`"), "bob", {"bob", "claude"}, {"schema"}) is False
    assert addressed(post(world, "bob", "@claude"), "bob", {"bob", "claude"}, {"schema"}) is False


def test_a_message_between_two_waits_is_not_lost(world, tmp_path):
    c = tmp_path / "cursor.json"
    assert wait(world, "bob", cursor=Cursor(c)) == []                       # first wait: marks the end of the log, times out
    post(world, "alice", "@claude sent between the waits")
    assert [e.body["text"] for e in wait(world, "bob", cursor=Cursor(c))] == ["@claude sent between the waits"]
