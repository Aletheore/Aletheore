"""Behavior every RoomStore implementation must satisfy.

Subclass `StoreContract` in a test module named test_*.py and provide a
`store` fixture returning a fresh store whose rooms "r1" and "r2" can be created.
"""

import threading

import pytest

from aletheore.council.protocol import EventDraft, EventType


def draft(room="r1", cid="c1", text="hi", thread="t1", sender="alice"):
    return EventDraft(
        room_id=room,
        client_event_id=cid,
        thread_id=thread,
        type=EventType.CLAIM,
        sender_participant=sender,
        sender_agent="claude",
        reply_to=None,
        body={"text": text},
    )


def q(room, cid, sender="alice", text="hi"):
    return EventDraft(room, cid, "room", EventType.QUESTION, sender, None, None, {"text": text, "kind": "chat"})


class StoreContract:
    def _rooms(self, store):
        store.create_room("r1", "alice")
        store.create_room("r2", "alice")

    def test_seq_is_gap_free_and_ordered(self, store):
        self._rooms(store)
        events = [store.append(draft(cid=f"c{i}")) for i in range(5)]
        assert [e.seq for e in events] == [1, 2, 3, 4, 5]
        assert [e.seq for e in store.read_after("r1", 0)] == [1, 2, 3, 4, 5]

    def test_duplicate_client_event_id_stores_one(self, store):
        self._rooms(store)
        first = store.append(draft(cid="same"))
        second = store.append(draft(cid="same", text="different retry"))
        assert second == first
        assert store.last_seq("r1") == 1

    def test_read_after_resumes_exactly(self, store):
        self._rooms(store)
        for i in range(3):
            store.append(draft(cid=f"a{i}"))
        for i in range(2):
            store.append(draft(cid=f"b{i}"))
        assert [e.seq for e in store.read_after("r1", 3)] == [4, 5]

    def test_rooms_are_isolated(self, store):
        self._rooms(store)
        a = store.append(draft(room="r1", cid="same"))
        b = store.append(draft(room="r2", cid="same"))
        assert a.seq == 1 and b.seq == 1
        assert a.event_id != b.event_id
        assert store.read_after("r2", 0) == [b]

    def test_concurrent_appends_from_two_threads_have_unique_seq(self, store):
        self._rooms(store)

        def work(prefix):
            for i in range(50):
                store.append(draft(cid=f"{prefix}{i}"))

        threads = [threading.Thread(target=work, args=(p,)) for p in ("x", "y")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        seqs = [e.seq for e in store.read_after("r1", 0, limit=1000)]
        assert seqs == list(range(1, 101))

    def test_event_roundtrips_fields(self, store):
        self._rooms(store)
        e = store.append(draft(text="hello"))
        got = store.read_after("r1", 0)[0]
        assert got == e
        assert got.type is EventType.CLAIM
        assert got.body == {"text": "hello"}
        assert got.created_at.endswith("Z") or "+00:00" in got.created_at


class RoomFeaturesContract:
    """Titles, modes, members, invites-by-host, closing and listing: every store must behave the same."""

    def test_room_has_title_mode_status_and_host_is_the_first_member(self, store):
        s = store
        s.create_room("r1", "alice", title="Signup flow", mode="autonomous")
        info = s.room_info("r1")
        assert (info.host, info.title, info.mode, info.status, info.members) == ("alice", "Signup flow", "autonomous", "open", ["alice"])
        s.create_room("r2", "alice")
        assert s.room_info("r2").mode == "interactive" and s.room_info("r2").title == ""

    def test_crypto_mode_is_fixed_at_creation_and_envelopes_may_be_bigger_than_plain_events(self, store):
        from aletheore.council.protocol import EventDraft, EventType, MAX_BODY_BYTES, ProtocolError

        s = store
        s.create_room("plain", "alice")
        s.create_room("sealed", "alice", crypto="e2e-v1")
        assert (s.room_info("plain").crypto, s.room_info("sealed").crypto) == ("none", "e2e-v1")
        s.create_room("sealed", "alice", crypto="none")  # a second create does not change it
        assert s.room_info("sealed").crypto == "e2e-v1" and "sealed" in {r.room_id for r in s.list_rooms("alice")}
        assert {r.room_id: r.crypto for r in s.list_rooms("alice")} == {"plain": "none", "sealed": "e2e-v1"}
        with pytest.raises(ProtocolError):
            s.create_room("bad", "alice", crypto="rot13")
        big = {"text": "x" * (MAX_BODY_BYTES + 10)}
        with pytest.raises(ProtocolError):
            s.append(EventDraft("sealed", "big1", "t", EventType.CLAIM, "alice", None, None, big))
        stored = s.append(EventDraft("sealed", "big2", "a" * 32, EventType.ENVELOPE, "alice", None, None, big))
        assert stored.type is EventType.ENVELOPE and len(stored.body["text"]) == MAX_BODY_BYTES + 10

    def test_removing_a_member(self, store):
        from aletheore.council.protocol import ProtocolError

        s = store
        s.create_room("r1", "alice")
        s.add_member("r1", "bob")
        s.add_member("r1", "carol")
        s.remove_member("r1", "bob")
        assert s.members_of("r1") == ["alice", "carol"] and [r.room_id for r in s.list_rooms("bob")] == []
        with pytest.raises(KeyError):
            s.remove_member("r1", "bob")  # not a member any more
        with pytest.raises(KeyError):
            s.remove_member("nope", "bob")
        with pytest.raises(ProtocolError):
            s.remove_member("r1", "alice")  # the host stays
        s.add_member("r1", "bob")  # they can be invited again
        assert s.members_of("r1") == ["alice", "carol", "bob"]

    def test_invite_close_and_list(self, store):
        s = store
        s.create_room("r1", "alice", title="one")
        s.create_room("r2", "bob", title="two")
        s.add_member("r1", "bob")
        s.add_member("r1", "bob")  # inviting twice is harmless
        assert s.members_of("r1") == ["alice", "bob"]
        s.append(q("r1", "c1"))
        mine = {r.room_id: r for r in s.list_rooms("bob")}
        assert set(mine) == {"r1", "r2"} and mine["r1"].last_seq == 1 and mine["r1"].last_activity
        assert {r.room_id for r in s.list_rooms("carol")} == set()
        s.close_room("r1")
        assert s.room_info("r1").status == "closed"
        with pytest.raises(ValueError, match="closed"):
            s.append(q("r1", "c2"))
        assert len(s.read_after("r1", 0)) == 1  # history stays readable

