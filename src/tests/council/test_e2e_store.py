"""The encrypting store, against the SQLite store directly and against a real server."""

import dataclasses
import sqlite3

import pytest
from fastapi.testclient import TestClient

from aletheore.council import crypto
from aletheore.council.accounts import AccountService, SqliteAccountBackend
from aletheore.council.e2e_store import E2ERoomStore
from aletheore.council.envelope import Keyring, seal_event
from aletheore.council.identity import Keystore
from aletheore.council.protocol import EventDraft, EventType, ProtocolError
from aletheore.council.remote_store import RemoteRoomStore
from aletheore.council.server import create_app
from aletheore.council.sqlite_store import SqliteRoomStore
from aletheore.council.state import load_state
from aletheore.council.store import read_all

SERVER = "http://council.test"
ROOM = "r1"


def ev(sender, etype=EventType.CLAIM, text="plain words", thread="task-1", agent="meg-claude", cid="c1", body=None):
    return EventDraft(ROOM, cid, thread, etype, sender, agent, None, body if body is not None else {"text": text})


class Net:
    """A server with accounts, two registered people (hosty hosts, meg joins) and an end-to-end room."""

    def __init__(self, tmp_path):
        self.keys = Keystore(tmp_path / "keys")
        self.inner = SqliteRoomStore(tmp_path / "s.db")
        self.service = AccountService(SqliteAccountBackend(tmp_path / "s.db"))
        self.app = create_app(self.inner, {}, tmp_path / "b", accounts=self.service)
        self.client = TestClient(self.app, base_url=SERVER)
        self.db = tmp_path / "s.db"
        self.host_id, self.meg_id = self.keys.load_or_create("host"), self.keys.load_or_create("meg")
        self.service.redeem_registration_code(self.service.create_registration_code("hosty", can_host=True), self.host_id.public(), "hosty")
        self.service.redeem_registration_code(self.service.create_registration_code("megan", can_host=False), self.meg_id.public(), "megan")
        self.key = crypto.random_bytes(32)
        self.host_remote, self.meg_remote = self.signed_in(self.host_id), self.signed_in(self.meg_id)
        self.host = E2ERoomStore(self.host_remote, self.host_id, Keyring({1: self.key}), self.host_remote.room_devices)
        self.meg = E2ERoomStore(self.meg_remote, self.meg_id, Keyring({1: self.key}), self.meg_remote.room_devices)
        self.host.create_room(ROOM, "hosty", title="Cache work")
        self.host.add_member(ROOM, "megan")

    def signed_in(self, ident):
        store = RemoteRoomStore(SERVER, "", client=self.client)
        store.login(ident)
        return store

    def raw_events(self):
        with sqlite3.connect(self.db) as c:
            return c.execute("SELECT seq, type, thread_id, sender_agent, reply_to, body FROM events WHERE room_id=? ORDER BY seq", (ROOM,)).fetchall()


@pytest.fixture
def net(tmp_path):
    return Net(tmp_path)


def test_server_sees_no_type_thread_agent_or_text(net):
    net.meg.append(ev("megan", text="the launch codes are 0000", thread="secret-thread", agent="meg-claude"))
    net.host.append(ev("hosty", EventType.DECISION, body={"text": "approved the secret plan"}, cid="d1", thread="secret-thread", agent=None))
    rows = net.raw_events()
    assert [r[1] for r in rows] == ["envelope", "envelope"]
    everything = repr(rows) + net.client.get(f"/v1/rooms/{ROOM}/events", headers=net.meg_remote._headers).text  # noqa: SLF001
    for secret in ("launch codes", "secret-thread", "meg-claude", "secret plan", "claim", "decision", "plain words"):
        assert secret not in everything
    info = net.client.get(f"/v1/rooms/{ROOM}", headers=net.meg_remote._headers).json()  # noqa: SLF001
    assert info["crypto"] == "e2e-v1" and info["members"] == ["hosty", "megan"]


def test_round_trip_through_a_real_server_preserves_the_event_exactly(net):
    sent = [ev("hosty", EventType.SYSTEM, body={"subtype": "room_created", "host": "hosty", "repo": "/x", "base_sha": "a" * 40}, cid="rc", thread="room", agent=None),
            ev("megan", cid="m1", body={"text": "héllo ‮", "evidence": [{"path": "a.py", "start_line": 2}]}),
            ev("megan", EventType.QUESTION, cid="m2", thread="t2", agent=None)]
    stored = [(net.host if d.sender_participant == "hosty" else net.meg).append(d) for d in sent]
    assert [s.seq for s in stored] == [1, 2, 3]
    for reader in (net.host, net.meg):  # everyone sees the same thing
        got = reader.read_after(ROOM, 0)
        assert [(g.seq, g.type, g.thread_id, g.sender_participant, g.sender_agent, g.reply_to, g.body, g.client_event_id) for g in got] == \
            [(i + 1, d.type, d.thread_id, d.sender_participant, d.sender_agent, d.reply_to, d.body, d.client_event_id) for i, d in enumerate(sent)]
        assert reader.last_seq(ROOM) == 3
    assert [e.seq for e in net.meg.read_after(ROOM, 1)] == [2, 3]
    again = net.meg.append(sent[1])
    assert again.seq == stored[1].seq and len(net.raw_events()) == 3  # a retry is still idempotent


def test_a_server_refuses_plaintext_and_badly_shaped_events_in_an_encrypted_room(net):
    h = net.meg_remote._headers  # noqa: SLF001
    plain = {"type": "claim", "client_event_id": "p1", "thread_id": "t", "reply_to": None, "sender_participant": "megan", "sender_agent": None, "body": {"text": "hi"}}
    assert net.client.post(f"/v1/rooms/{ROOM}/events", json=plain, headers=h).status_code == 422
    good = seal_event(ev("megan"), net.meg_id, net.key, 1)

    def send(d, **over):
        body = {"type": "envelope", "client_event_id": d.client_event_id, "thread_id": d.thread_id, "reply_to": None, "sender_participant": d.sender_participant,
                "sender_agent": None, "body": d.body, **over}
        return net.client.post(f"/v1/rooms/{ROOM}/events", json=body, headers=h)

    assert send(good).status_code == 200
    assert send(good, body={**good.body, "extra": 1}, client_event_id="x1").status_code == 422
    assert send(good, body={**good.body, "ct": "A" * 300_000}, client_event_id="x2").status_code in (413, 422)
    assert send(good, thread_id="plain-thread-name", client_event_id="x3").status_code == 422
    assert send(good, body={**good.body, "class": "host"}, client_event_id="x4").status_code == 403  # a member cannot claim the host's class
    other = seal_event(ev("megan", cid="y1"), net.host_id, net.key, 1)  # signed by a device that is not megan's
    assert send(other).status_code == 403
    assert send(good, sender_participant="hosty", client_event_id="x5").status_code == 403  # not the token's person
    assert len(net.raw_events()) == 1


def test_envelopes_are_not_accepted_in_plain_rooms(net):
    net.host_remote.create_room("plain", "hosty")
    env = seal_event(ev("hosty", cid="e1"), net.host_id, net.key, 1)
    body = {"type": "envelope", "client_event_id": "e1", "thread_id": env.thread_id, "reply_to": None, "sender_participant": "hosty", "sender_agent": None, "body": env.body}
    assert net.client.post("/v1/rooms/plain/events", json=body, headers=net.host_remote._headers).status_code == 422  # noqa: SLF001
    assert net.host_remote.room_info("plain").crypto == "none"


def test_a_plain_room_still_works_exactly_as_before(net):
    net.host_remote.create_room("plain", "hosty")
    plain = {"type": "claim", "client_event_id": "p1", "thread_id": "t", "reply_to": None, "sender_participant": "hosty", "sender_agent": "a", "body": {"text": "hi"}}
    assert net.client.post("/v1/rooms/plain/events", json=plain, headers=net.host_remote._headers).status_code == 200  # noqa: SLF001
    assert net.host_remote.read_after("plain", 0)[0].body == {"text": "hi"}


def test_crypto_mode_is_fixed_at_creation(net):
    net.host_remote.create_room("fixed", "hosty", crypto="none")
    with pytest.raises(ProtocolError):
        net.host_remote.create_room("fixed", "hosty", crypto="e2e-v1")  # cannot be changed later
    with pytest.raises(ProtocolError):
        net.host_remote.create_room("bad", "hosty", crypto="rot13")
    assert net.host_remote.room_info("fixed").crypto == "none"


def test_member_cannot_forge_another_members_event(net):
    # the server only checks the token and the shape, so a member with a valid session can submit an envelope signed by the wrong key
    forged = seal_event(ev("megan", cid="f1", text="host says: wire the money"), net.host_id, net.key, 1)
    forged = dataclasses.replace(forged, body={**forged.body, "dev": net.meg_id.device_id})  # claims megan's device, signed by the host's key
    net.meg_remote.append(forged)
    net.meg.append(ev("megan", cid="ok", text="a real message"))
    got = net.host.read_after(ROOM, 0)
    assert [g.body["text"] for g in got] == ["a real message"]
    assert net.host.rejected == 1


def test_forged_host_only_event_from_a_member_is_ignored_even_if_the_server_accepted_it(tmp_path):
    keys = Keystore(tmp_path / "k")
    host_id, meg_id = keys.load_or_create("h"), keys.load_or_create("m")
    inner = SqliteRoomStore(tmp_path / "plain-server.db")  # a store with no server checks at all, like a hostile or buggy server
    members = {"hosty": [host_id.public()], "meg": [meg_id.public()]}
    key = crypto.random_bytes(32)
    reader = E2ERoomStore(inner, host_id, Keyring({1: key}), lambda room: members)
    reader.create_room(ROOM, "hosty")
    reader.add_member(ROOM, "meg")
    from aletheore.council import envelope

    decision = ev("meg", EventType.DECISION, body={"text": "I decide"}, cid="d1", agent=None)
    inner.append(seal_event(decision, meg_id, key, 1))  # class host, from a member
    sneaky = dataclasses.replace(decision, client_event_id="d1b")
    inner.append(envelope._seal(sneaky, meg_id, key, 1, "member"))  # noqa: SLF001  hides itself as a member event
    inner.append(seal_event(ev("hosty", EventType.DECISION, body={"text": "real"}, cid="d2", agent=None), host_id, key, 1))
    got = reader.read_after(ROOM, 0)
    assert [(g.sender_participant, g.body["text"]) for g in got] == [("hosty", "real")]
    assert reader.rejected == 2


def test_tampered_dropped_and_duplicated_events_are_detected_and_skipped(net):
    for i in range(1, 6):
        net.meg.append(ev("megan", cid=f"m{i}", text=f"message {i}"))
    with sqlite3.connect(net.db) as c:
        row = c.execute("SELECT body FROM events WHERE room_id=? AND seq=2", (ROOM,)).fetchone()[0]
        c.execute("UPDATE events SET body=? WHERE room_id=? AND seq=2", (row.replace('"ct": "', '"ct": "AAAA', 1), ROOM))  # tamper (seq 2)
        c.execute("DELETE FROM events WHERE room_id=? AND seq=4", (ROOM,))  # the server drops one (seq 4)
        c.execute("INSERT INTO events (room_id, seq, event_id, client_event_id, thread_id, type, sender_participant, sender_agent, reply_to, body, created_at) "
                  "SELECT room_id, 6, 'evt_dup', 'dup-id', thread_id, type, sender_participant, sender_agent, reply_to, body, created_at FROM events "
                  "WHERE room_id=? AND seq=5", (ROOM,))  # the server replays seq 5 as a new event
    got = net.host.read_after(ROOM, 0)
    assert [g.body["text"] for g in got] == ["message 1", "message 3", "message 5"]
    assert net.host.rejected == 2  # the tampered one and the replay under a new id
    assert net.host.gaps == [(4, 4)]
    again = net.host.read_after(ROOM, 0)
    assert len(again) == 3 and net.host.rejected == 2 and net.host.gaps == [(4, 4)]  # reading again does not double count


def test_wrong_epoch_or_missing_key_skips_without_crashing(net):
    net.meg.append(ev("megan", cid="a", text="epoch one"))
    stranger = E2ERoomStore(net.host_remote, net.host_id, Keyring({2: net.key}), net.host_remote.room_devices)
    assert stranger.read_after(ROOM, 0) == [] and stranger.unreadable == 1 and stranger.rejected == 0
    assert E2ERoomStore(net.host_remote, net.host_id, Keyring(), net.host_remote.room_devices).read_after(ROOM, 0) == []


def test_a_page_of_junk_does_not_hide_the_events_after_it(tmp_path):
    keys = Keystore(tmp_path / "k")
    host_id, meg_id = keys.load_or_create("h"), keys.load_or_create("m")
    inner = SqliteRoomStore(tmp_path / "s.db")
    members = {"hosty": [host_id.public()], "meg": [meg_id.public()]}
    key = crypto.random_bytes(32)
    store = E2ERoomStore(inner, host_id, Keyring({1: key}), lambda room: members)
    store.create_room(ROOM, "hosty")
    store.add_member(ROOM, "meg")
    inner.append(seal_event(ev("meg", cid="first", text="first", agent=None), meg_id, key, 1))
    junk = seal_event(ev("meg", cid="junk", agent=None), meg_id, key, 1)
    for i in range(520):  # more than a whole page of events that will not open
        inner.append(dataclasses.replace(junk, client_event_id=f"junk-{i}"))
    inner.append(seal_event(ev("meg", cid="last", text="last", agent=None), meg_id, key, 1))
    assert [e.body["text"] for e in read_all(store, ROOM)] == ["first", "last"]
    tail = store.read_after(ROOM, 1)  # starting inside the junk
    assert [e.body["text"] for e in tail] == ["last"]
    again = store.read_after(ROOM, tail[-1].seq)  # nothing new, and no re-reading the whole log for it
    assert again == []


def test_project_state_over_an_e2e_store_equals_state_over_a_plain_store(tmp_path):
    keys = Keystore(tmp_path / "k")
    host_id, meg_id = keys.load_or_create("h"), keys.load_or_create("m")
    members = {"hosty": [host_id.public()], "meg": [meg_id.public()]}
    key = crypto.random_bytes(32)
    plain = SqliteRoomStore(tmp_path / "plain.db")
    enc = E2ERoomStore(SqliteRoomStore(tmp_path / "enc.db"), host_id, Keyring({1: key}), lambda room: members)
    enc_meg = E2ERoomStore(enc._inner, meg_id, Keyring({1: key}), lambda room: members)  # noqa: SLF001
    script = [
        ("hosty", EventType.SYSTEM, "rc", {"subtype": "room_created", "host": "hosty", "repo": "/x", "base_sha": "a" * 40}),
        ("hosty", EventType.SYSTEM, "a1", {"subtype": "area_defined", "name": "redis", "globs": ["redis/**"], "owner": "meg", "agent_id": "meg-codex"}),
        ("hosty", EventType.TASK, "t1:request", {"op": "request", "task_id": "t1", "area": "redis", "instruction": "build the cache"}),
        ("meg", EventType.TASK, "t1:started", {"op": "started", "task_id": "t1", "branch": "b"}),
        ("meg", EventType.RESULT, "t1:result", {"text": "built", "ok": True, "task_id": "t1"}),
        ("meg", EventType.CLAIM, "c1", {"text": "cache is write-through"}),
        ("meg", EventType.DECISION, "bad", {"text": "a member cannot decide"}),  # ignored by both
        ("hosty", EventType.DECISION, "d1", {"text": "ok"}),
    ]
    for store in (plain, enc):
        store.create_room(ROOM, "hosty")
        store.add_member(ROOM, "meg")
    for sender, etype, cid, body in script:
        thread = body.get("task_id") or "room"
        d = EventDraft(ROOM, cid, thread, etype, sender, None, None, body)
        plain.append(d)
        (enc if sender == "hosty" else enc_meg).append(d)
    a, b = load_state(plain, ROOM), load_state(enc, ROOM)
    assert a == b and a.areas and a.tasks


def test_the_store_behaves_like_any_room_store(net):
    assert net.meg.supports_agent_tools is False  # agents reach it through a broker, never a key
    assert net.meg.members_of(ROOM) == ["hosty", "megan"] and net.meg.host_of(ROOM) == "hosty"
    assert [r.room_id for r in net.meg.list_rooms("megan")] == [ROOM]
    assert net.meg.room_info(ROOM).crypto == "e2e-v1"
    with pytest.raises(ProtocolError):
        net.meg.append(EventDraft(ROOM, "x", "t", EventType.ENVELOPE, "megan", None, None, {}))
