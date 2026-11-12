"""The outbox: order, idempotency across a restart, refusals kept with their reason, nothing duplicated after the host returns."""

import os
import stat

import pytest

from aletheore.council.outbox import Outbox, OutboxStore
from aletheore.council.protocol import EventDraft, EventType
from aletheore.council.sqlite_store import SqliteRoomStore


class Flaky:
    """A store that can be switched off, like a host that went to sleep, and that can lose the answer to a send that went through."""

    def __init__(self, inner):
        self.inner, self.down, self.drop_answer_once = inner, False, False
        self.supports_agent_tools = True

    def append(self, draft):
        if self.down:
            raise ConnectionError("host unreachable")
        event = self.inner.append(draft)
        if self.drop_answer_once:
            self.drop_answer_once = False
            raise ConnectionError("answer lost")  # the host stored it; we never heard
        return event

    def __getattr__(self, name):
        return getattr(self.inner, name)


def draft(cid, text="x", room="r1"):
    return EventDraft(room, cid, "t", EventType.CLAIM, "bob", None, None, {"text": text})


@pytest.fixture
def world(tmp_path):
    inner = SqliteRoomStore(tmp_path / "r.db")
    inner.create_room("r1", "alice")
    inner.add_member("r1", "bob")
    flaky = Flaky(inner)
    return inner, flaky, tmp_path


def texts(inner):
    return [e.body["text"] for e in inner.read_after("r1", 0) if e.type is EventType.CLAIM]


def test_messages_queue_while_the_host_is_away_and_drain_in_order_when_it_returns(world):
    inner, flaky, tmp = world
    store = OutboxStore(flaky, tmp / "outbox.json")
    store.append(draft("c0", "before"))
    flaky.down = True
    first = store.append(draft("c1", "one"))
    store.append(draft("c2", "two"))
    assert first.seq == 0 and first.event_id.startswith("queued-")  # a provisional event, clearly not a real one
    assert [d.body["text"] for d in store.queued("r1")] == ["one", "two"] and texts(inner) == ["before"]
    store.append(draft("c3", "three"))  # still away: behind the others
    flaky.down = False
    assert store.flush() == (3, 0)
    assert texts(inner) == ["before", "one", "two", "three"] and store.queued() == []


def test_a_new_message_after_return_goes_behind_the_waiting_ones(world):
    inner, flaky, tmp = world
    store = OutboxStore(flaky, tmp / "o.json")
    flaky.down = True
    store.append(draft("c1", "old"))
    flaky.down = False
    store.append(draft("c2", "new"))  # the queue drains first, in order
    assert texts(inner) == ["old", "new"]


def test_the_queue_survives_a_restart_and_a_lost_answer_does_not_duplicate(world):
    inner, flaky, tmp = world
    path = tmp / "o.json"
    store = OutboxStore(flaky, path)
    flaky.down = True
    store.append(draft("c1", "waits"))
    flaky.down = False
    flaky.drop_answer_once = True  # the send goes through but the answer is lost: the item stays queued
    assert store.flush() == (0, 0) and texts(inner) == ["waits"]
    again = OutboxStore(flaky, path)  # the app was closed and reopened
    assert [d.body["text"] for d in again.queued()] == ["waits"]
    assert again.flush() == (1, 0)
    assert texts(inner) == ["waits"] and again.queued() == []  # one message, not two


def test_the_same_message_queued_twice_is_one_message(world):
    inner, flaky, tmp = world
    store = OutboxStore(flaky, tmp / "o.json")
    flaky.down = True
    store.append(draft("c1", "same"))
    store.append(draft("c1", "same"))
    assert len(store.queued()) == 1


def test_a_refusal_on_return_is_kept_with_its_reason_and_the_queue_moves_on(world):
    inner, flaky, tmp = world
    store = OutboxStore(flaky, tmp / "o.json")
    flaky.down = True
    store.append(draft("c1", "to a room that will close"))
    store.append(draft("c2", "to the open room"))
    flaky.down = False
    real = flaky.inner.append

    def picky(d):
        if d.client_event_id == "c1":
            raise PermissionError("the room was closed")
        return real(d)

    flaky.inner.append = picky
    assert store.flush() == (1, 1)
    assert texts(inner) == ["to the open room"]
    assert store.outbox.rejected and store.outbox.rejected[0]["reason"] == "the room was closed"  # never silently dropped


def test_rejections_from_a_direct_send_are_not_queued(world):
    inner, flaky, tmp = world
    store = OutboxStore(flaky, tmp / "o.json")

    def refuse(d):
        raise PermissionError("not a member")

    flaky.inner.append = refuse  # the host answers, and says no: that is not an outage
    with pytest.raises(PermissionError):
        store.append(draft("x", "not a member"))
    assert store.queued() == []


def test_other_calls_pass_straight_through_and_the_file_is_private(world):
    inner, flaky, tmp = world
    store = OutboxStore(flaky, tmp / "o.json")
    assert store.host_of("r1") == "alice" and store.supports_agent_tools is True
    flaky.down = True
    store.append(draft("c1"))
    if os.name != "nt":
        assert stat.S_IMODE((tmp / "o.json").stat().st_mode) == 0o600
    (tmp / "o.json").write_text("{damaged")
    assert Outbox(tmp / "o.json").pending() == []  # a damaged file is forgotten, not a crash
