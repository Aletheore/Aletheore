"""Room keys: the host makes epoch 1, hands each member a signed grant, and changes the key when someone is removed."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from aletheore.council import crypto
from aletheore.council.accounts import AccountService, SqliteAccountBackend
from aletheore.council.e2e_store import E2ERoomStore
from aletheore.council.identity import Keystore
from aletheore.council.keys import (GrantError, HostKeyError, KeyStore, KeyStoreError, RoomKeys, accept_grant, create_room_key, make_grant)
from aletheore.council.protocol import EventDraft, EventType
from aletheore.council.remote_store import RemoteRoomStore
from aletheore.council.server import create_app
from aletheore.council.sqlite_store import SqliteRoomStore

SERVER = "http://council.test"
ROOM = "r1"


def claim(sender, text, cid, agent="a"):
    return EventDraft(ROOM, cid, "t1", EventType.CLAIM, sender, agent, None, {"text": text})


class Person:
    def __init__(self, cluster, handle, ident, can_host=False):
        self.handle, self.ident = handle, ident
        cluster.service.redeem_registration_code(cluster.service.create_registration_code(handle, can_host=can_host), ident.public(), handle)
        self.remote = RemoteRoomStore(SERVER, "", client=cluster.client)
        self.remote.login(ident)
        self.keystore = KeyStore(cluster.tmp / f"keys-{handle}.bin", ident)
        self.keys = RoomKeys(ROOM, ident, self.keystore, self.remote, self.remote.room_devices, host="hosty")
        self.store = E2ERoomStore(self.remote, ident, self.keystore.keyring(ROOM), self.remote.room_devices, key_sync=lambda room: self.keys.sync())
        self.keys.store = self.store


class Cluster:
    def __init__(self, tmp_path, **app_kwargs):
        self.tmp = tmp_path
        self.idents = Keystore(tmp_path / "idents")
        self.inner = SqliteRoomStore(tmp_path / "s.db")
        self.service = AccountService(SqliteAccountBackend(tmp_path / "s.db"))
        self.app = create_app(self.inner, {}, tmp_path / "b", accounts=self.service, **app_kwargs)
        self.client = TestClient(self.app, base_url=SERVER)
        self.db = tmp_path / "s.db"
        self.host = Person(self, "hosty", self.idents.load_or_create("host"), can_host=True)
        self.meg = Person(self, "megan", self.idents.load_or_create("meg"))
        self.moe = Person(self, "moe", self.idents.load_or_create("moe"))
        self.host.remote.create_room(ROOM, "hosty", title="Cache work", crypto="e2e-v1")
        self.host.remote.add_member(ROOM, "megan")
        self.host.remote.add_member(ROOM, "moe")
        assert self.host.keys.start() == 1

    def raw_headers(self, person):
        return person.remote._headers  # noqa: SLF001


@pytest.fixture
def cl(tmp_path):
    return Cluster(tmp_path)


def test_host_creates_epoch_1_and_every_approved_member_receives_a_grant(cl):
    assert cl.host.keystore.epochs(ROOM) == [1]
    assert cl.meg.keystore.epochs(ROOM) == [] and cl.moe.keystore.epochs(ROOM) == []
    cl.host.store.append(claim("hosty", "welcome", "h1"))
    assert [e.body["text"] for e in cl.meg.store.read_after(ROOM, 0)] == ["welcome"]  # the first read fetched the grant on its own
    assert cl.meg.keystore.epochs(ROOM) == [1] and cl.meg.keys.sync() == 0  # nothing new the second time
    assert [e.body["text"] for e in cl.moe.store.read_after(ROOM, 0)] == ["welcome"]
    cl.meg.store.append(claim("megan", "thanks", "m1"))
    assert [e.body["text"] for e in cl.host.store.read_after(ROOM, 1)] == ["thanks"]


def test_grant_for_the_wrong_device_cannot_be_opened(tmp_path):
    ks = Keystore(tmp_path / "k")
    host, meg, moe = ks.load_or_create("h"), ks.load_or_create("m"), ks.load_or_create("o")
    key = create_room_key()
    blob = make_grant(ROOM, key, 1, meg.public(), host)
    assert accept_grant(ROOM, blob, meg, [host.public()]) == (1, key)
    with pytest.raises(GrantError):
        accept_grant(ROOM, blob, moe, [host.public()])  # sealed to meg's box key: moe cannot even open it
    assert key not in blob


def test_grant_must_be_signed_by_the_host(tmp_path):
    ks = Keystore(tmp_path / "k")
    host, meg, mallory = ks.load_or_create("h"), ks.load_or_create("m"), ks.load_or_create("x")
    key = create_room_key()
    forged = make_grant(ROOM, key, 1, meg.public(), mallory)  # a member hands meg a "key" of their choosing
    with pytest.raises(GrantError):
        accept_grant(ROOM, forged, meg, [host.public()])
    honest = make_grant(ROOM, key, 1, meg.public(), host)
    with pytest.raises(GrantError):
        accept_grant("other-room", honest, meg, [host.public()])  # a grant for one room is not valid in another
    with pytest.raises(GrantError):
        accept_grant(ROOM, honest[:-1] + bytes([honest[-1] ^ 1]), meg, [host.public()])
    with pytest.raises(GrantError):
        accept_grant(ROOM, b"junk", meg, [host.public()])
    assert accept_grant(ROOM, honest, meg, [host.public()]) == (1, key)
    other_device = ks.load_or_create("h2")
    assert accept_grant(ROOM, make_grant(ROOM, key, 2, meg.public(), other_device), meg, [host.public(), other_device.public()])[0] == 2  # the host's other device


def test_a_grant_binds_the_recipient_device(tmp_path):
    ks = Keystore(tmp_path / "k")
    host, meg = ks.load_or_create("h"), ks.load_or_create("m")
    blob = make_grant(ROOM, create_room_key(), 1, meg.public(), host)
    # the same grant replayed to a different device that shares meg's box key would still name meg's device, so it is refused
    clone = ks.load_or_create("clone")
    assert clone.public().box_pk != meg.public().box_pk
    with pytest.raises(GrantError):
        accept_grant(ROOM, blob, clone, [host.public()])


def test_removed_member_is_refused_by_the_server_at_once(cl):
    cl.host.store.append(claim("hosty", "before", "h1"))
    assert cl.moe.store.read_after(ROOM, 0)
    assert cl.host.keys.remove_member("moe") == 2
    h = cl.raw_headers(cl.moe)
    for call in (lambda: cl.client.get(f"/v1/rooms/{ROOM}", headers=h), lambda: cl.client.get(f"/v1/rooms/{ROOM}/events", headers=h),
                 lambda: cl.client.get(f"/v1/rooms/{ROOM}/devices", headers=h), lambda: cl.client.get(f"/v1/rooms/{ROOM}/grants/me", headers=h),
                 lambda: cl.client.get(f"/v1/rooms/{ROOM}/last_seq", headers=h)):
        assert call().status_code == 403
    assert "moe" not in cl.host.remote.room_info(ROOM).members
    assert [r.room_id for r in cl.moe.remote.list_rooms("moe")] == []
    posted = cl.client.post(f"/v1/rooms/{ROOM}/events", headers=h, json={"type": "envelope", "client_event_id": "x", "thread_id": "0" * 32, "reply_to": None,
                                                                         "sender_participant": "moe", "sender_agent": None, "body": {}})
    assert posted.status_code == 403
    assert cl.client.get(f"/v1/rooms/{ROOM}/events", headers=cl.raw_headers(cl.meg)).status_code == 200  # others are unaffected
    assert cl.moe.remote.whoami() == "moe"  # the account itself still works elsewhere


def test_only_the_host_can_remove_and_the_host_cannot_be_removed(cl):
    h = cl.raw_headers(cl.meg)
    assert cl.client.delete(f"/v1/rooms/{ROOM}/members/moe", headers=h).status_code == 403
    assert cl.client.delete(f"/v1/rooms/{ROOM}/members/hosty", headers=cl.raw_headers(cl.host)).status_code in (403, 422)
    assert cl.client.delete(f"/v1/rooms/{ROOM}/members/nobody", headers=cl.raw_headers(cl.host)).status_code == 404
    assert cl.client.delete(f"/v1/rooms/{ROOM}/members/moe").status_code == 401
    assert cl.host.remote.room_info(ROOM).members == ["hosty", "megan", "moe"]


def test_after_removal_new_events_use_the_next_epoch_and_the_removed_member_cannot_decrypt_them(cl, tmp_path):
    cl.host.store.append(claim("hosty", "old secret", "h1"))
    cl.moe.store.read_after(ROOM, 0)  # moe holds epoch 1
    assert cl.host.keys.remove_member("moe") == 2
    cl.host.store.append(claim("hosty", "new secret", "h2"))
    with sqlite3.connect(cl.db) as c:  # what the server stored, as moe could still copy it from a backup or a compromised server
        rows = c.execute("SELECT body FROM events WHERE room_id=? ORDER BY seq", (ROOM,)).fetchall()
    assert '"epoch": 1' in rows[0][0] and '"epoch": 2' in rows[-1][0]
    moe_direct = E2ERoomStore(SqliteRoomStore(cl.db), cl.moe.ident, cl.moe.keystore.keyring(ROOM), lambda room: {"hosty": [cl.host.ident.public()], "megan": [cl.meg.ident.public()]})
    texts = [e.body.get("text") for e in moe_direct.read_after(ROOM, 0)]
    assert "old secret" in texts and "new secret" not in texts and moe_direct.unreadable >= 1  # old history stays readable to them, new does not
    assert cl.moe.keys.sync() == 0  # and the server gives them no grant for epoch 2
    assert [e.body.get("text") for e in cl.meg.store.read_after(ROOM, 0) if e.type is EventType.CLAIM] == ["old secret", "new secret"]
    kinds = [(e.body.get("subtype"), e.body.get("epoch"), e.body.get("participant")) for e in cl.meg.store.read_after(ROOM, 0) if e.type is EventType.SYSTEM]
    assert ("member_removed", 2, "moe") in kinds and ("key_rotated", 2, None) in kinds


def test_remaining_members_still_read_old_history_with_old_epoch_keys(cl):
    for i in range(3):
        cl.host.store.append(claim("hosty", f"epoch one {i}", f"a{i}"))
    cl.host.keys.remove_member("moe")
    cl.host.store.append(claim("hosty", "epoch two", "b0"))
    fresh = cl.meg.store.read_after(ROOM, 0)  # megan had not read anything yet: she needs both epochs, both come with her grants
    assert [e.body["text"] for e in fresh if e.type is EventType.CLAIM] == ["epoch one 0", "epoch one 1", "epoch one 2", "epoch two"]
    assert cl.meg.keystore.epochs(ROOM) == [1, 2]


def test_a_member_added_later_gets_the_current_epoch_and_history_only_when_the_host_chooses(cl, tmp_path):
    cl.host.store.append(claim("hosty", "epoch one", "a0"))
    cl.host.keys.remove_member("moe")
    cl.host.store.append(claim("hosty", "epoch two", "b0"))
    late = Person(cl, "lena", cl.idents.load_or_create("lena"))
    cl.host.remote.add_member(ROOM, "lena")
    cl.host.keys.grant_to("lena")  # the default: the current epoch only
    got = [e.body["text"] for e in late.store.read_after(ROOM, 0) if e.type is EventType.CLAIM]
    assert got == ["epoch two"] and late.keystore.epochs(ROOM) == [2] and late.store.unreadable >= 1
    cl.host.keys.grant_to("lena", epochs=[1])  # an explicit decision, recorded in the log
    got = [e.body["text"] for e in late.store.read_after(ROOM, 0) if e.type is EventType.CLAIM]
    assert got == ["epoch one", "epoch two"] and late.keystore.epochs(ROOM) == [1, 2]
    granted = [e.body for e in cl.meg.store.read_after(ROOM, 0) if e.body.get("subtype") == "history_granted"]
    assert granted == [{"subtype": "history_granted", "participant": "lena", "epochs": [1]}]


def test_grants_routes_enforce_who_may_hand_out_and_receive_keys(cl):
    hh, mh = cl.raw_headers(cl.host), cl.raw_headers(cl.meg)
    dev = cl.meg.ident.device_id
    good = {"device_id": dev, "grant": "ab" * 40}
    assert cl.client.put(f"/v1/rooms/{ROOM}/grants/megan/9", json=good, headers=mh).status_code == 403  # only the host
    assert cl.client.put(f"/v1/rooms/{ROOM}/grants/megan/9", json=good).status_code == 401
    assert cl.client.put(f"/v1/rooms/{ROOM}/grants/megan/9", json=good, headers=hh).status_code == 200
    assert cl.client.put(f"/v1/rooms/{ROOM}/grants/stranger/9", json=good, headers=hh).status_code in (403, 404, 422)  # not a member
    assert cl.client.put(f"/v1/rooms/{ROOM}/grants/moe/9", json=good, headers=hh).status_code in (403, 422)  # the device is megan's, not moe's
    assert cl.client.put(f"/v1/rooms/{ROOM}/grants/megan/0", json=good, headers=hh).status_code == 422
    assert cl.client.put(f"/v1/rooms/{ROOM}/grants/megan/9", json={"device_id": dev, "grant": "zz"}, headers=hh).status_code == 422
    assert cl.client.put(f"/v1/rooms/{ROOM}/grants/megan/9", json={"device_id": dev, "grant": "ab" * 20000}, headers=hh).status_code in (413, 422)
    mine = cl.client.get(f"/v1/rooms/{ROOM}/grants/me", headers=mh).json()["grants"]
    assert [(g["epoch"], g["grant"]) for g in mine if g["epoch"] == 9] == [(9, "ab" * 40)]
    assert cl.client.get(f"/v1/rooms/{ROOM}/grants/me", headers=cl.raw_headers(cl.moe)).json()["grants"] != mine  # moe sees only their own
    assert cl.client.get(f"/v1/rooms/{ROOM}/grants/me").status_code == 401


def test_keyring_file_never_contains_a_key_in_plaintext(cl, tmp_path):
    cl.host.store.append(claim("hosty", "x", "h1"))
    cl.meg.store.read_after(ROOM, 0)
    key = cl.meg.keystore.keyring(ROOM).get(1)
    assert key is not None
    import base64

    for path in (cl.meg.keystore.path, cl.host.keystore.path):
        raw = path.read_bytes()
        for form in (key, key.hex().encode(), base64.b64encode(key), base64.urlsafe_b64encode(key)):
            assert form not in raw
    import os
    import stat

    if os.name != "nt":
        assert stat.S_IMODE(cl.meg.keystore.path.stat().st_mode) == 0o600


def test_keyring_persists_across_restarts_and_belongs_to_one_identity(cl, tmp_path):
    cl.host.store.append(claim("hosty", "x", "h1"))
    cl.meg.store.read_after(ROOM, 0)
    reopened = KeyStore(cl.meg.keystore.path, cl.meg.ident)
    assert reopened.epochs(ROOM) == [1] and reopened.keyring(ROOM).get(1) == cl.meg.keystore.keyring(ROOM).get(1)
    with pytest.raises(KeyStoreError):
        KeyStore(cl.meg.keystore.path, cl.moe.ident).epochs(ROOM)  # another identity cannot read it
    cl.meg.keystore.path.write_bytes(b"corrupt")
    with pytest.raises(KeyStoreError):
        KeyStore(cl.meg.keystore.path, cl.meg.ident).epochs(ROOM)


def test_losing_the_host_key_is_reported_clearly_no_recovery(cl, tmp_path):
    # the host's keystore file is gone (a new empty one), or the host identity is a different, newly made device
    fresh_identity = cl.idents.load_or_create("replacement-host-device")
    with pytest.raises(HostKeyError, match="cannot be recovered"):
        RoomKeys(ROOM, fresh_identity, KeyStore(tmp_path / "new.bin", fresh_identity), cl.host.remote, cl.host.remote.room_devices, host="hosty").rotate()
    lost_file = KeyStore(tmp_path / "empty.bin", cl.host.ident)
    with pytest.raises(HostKeyError, match="cannot be recovered"):
        RoomKeys(ROOM, cl.host.ident, lost_file, cl.host.remote, cl.host.remote.room_devices, host="hosty").rotate()  # the identity is right, the room key is gone
    with pytest.raises(HostKeyError):
        cl.meg.keys.rotate()  # a member is not the host


def test_keys_are_not_lost_when_a_rotation_is_interrupted(cl):
    """Grants are published before the host switches to the new epoch, so a crash in between leaves everyone able to read."""
    cl.host.store.append(claim("hosty", "stable", "h1"))
    original = cl.host.remote.put_grant
    calls = []

    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise ConnectionError("network cut")
        return original(*args, **kwargs)

    cl.host.remote.put_grant = flaky
    with pytest.raises(ConnectionError):
        cl.host.keys.rotate()
    cl.host.remote.put_grant = original
    assert cl.host.keystore.epochs(ROOM) == [1]  # the host kept writing under the old epoch
    cl.host.store.append(claim("hosty", "still fine", "h2"))
    assert [e.body["text"] for e in cl.meg.store.read_after(ROOM, 0)] == ["stable", "still fine"]
    assert cl.host.keys.rotate() == 2  # and a retry completes it


def link_second_device(cl, owner, name):
    """Link a second machine to `owner`'s account the way the server does it (a request, then approval from the first device)."""
    from aletheore.council.identity import safety_words

    ident = cl.idents.load_or_create(name)
    req = cl.service.create_link_request(owner.handle, ident.public())
    cl.service.approve_link_request(owner.ident.device_id, req.request_id, safety_words(ident.public().fingerprint))
    remote = RemoteRoomStore(SERVER, "", client=cl.client)
    remote.login(ident)
    ks = KeyStore(cl.tmp / f"keys-{name}.bin", ident)
    return ident, remote, ks, RoomKeys(ROOM, ident, ks, remote, remote.room_devices, host="hosty")


def test_host_gives_a_members_new_device_the_key_and_rekeys_after_a_revoked_one(cl):
    from aletheore.council.keys import DeviceWatch

    watch = DeviceWatch(cl.host.keys)
    first = watch.check()  # the first look hands the current key to everyone, once
    assert set(first["granted"]) == {"megan", "moe"} and first["rotated"] is None
    assert watch.check() == {"granted": [], "rotated": None}  # nothing changed: nothing done
    ident, remote, ks, keys2 = link_second_device(cl, cl.meg, "meg2")
    assert ks.epochs(ROOM) == []
    second = watch.check()
    assert second["granted"] == ["megan"] and second["rotated"] is None
    assert keys2.sync() == 1 and ks.epochs(ROOM) == [1]  # the new device can read without anyone doing anything by hand
    cl.service.revoke_device(ident.device_id)
    third = watch.check()
    assert third["rotated"] == 2
    cl.meg.keys.sync()
    assert cl.meg.keystore.epochs(ROOM) == [1, 2]  # the member's other device follows the new key
    assert keys2.sync() == 0 and ks.epochs(ROOM) == [1]  # the revoked device never receives it


def test_a_newly_admitted_member_can_write_as_its_very_first_action(cl):
    """Found by the real acceptance rehearsal: a guest whose first act was to send a message (council say) failed with 'holds no key yet',
    because keys were only fetched on a read. The write now fetches the grant the host published."""
    assert cl.meg.keystore.epochs(ROOM) == []  # megan has read nothing and fetched nothing
    sent = cl.meg.store.append(claim("megan", "my first act is to write", "m-first"))
    assert sent.seq > 0 and cl.meg.keystore.epochs(ROOM) == [1]
    assert [e.body["text"] for e in cl.host.store.read_after(ROOM, 0) if e.sender_participant == "megan"] == ["my first act is to write"]
