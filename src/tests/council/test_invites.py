"""Invitation links and host approval, through the real HTTP app with real keys."""

import base64
import os
import uuid

import pytest
from fastapi.testclient import TestClient

from aletheore.council import crypto, invites
from aletheore.council.accounts import AccountService, SqliteAccountBackend
from aletheore.council.crypto import CryptoError
from aletheore.council.identity import Keystore, safety_words
from aletheore.council.remote_store import RemoteRoomStore
from aletheore.council.server import create_app
from aletheore.council.server_config import ServerSettings
from aletheore.council.sqlite_store import SqliteRoomStore

SERVER = "http://council.test"
PG_URL = os.environ.get("COUNCIL_TEST_PG_URL")


class Clock:
    now = 1_000_000.0

    def __call__(self):
        return self.now


class World:
    def __init__(self, tmp_path, settings=None, schema=None):
        self.clock = Clock()
        self.keys = Keystore(tmp_path / "keys")
        if schema:
            from aletheore.council.accounts import PostgresAccountBackend
            from aletheore.council.pg_store import PostgresRoomStore

            self.room_store = PostgresRoomStore(PG_URL, pool_size=5, schema=schema)
            self.service = AccountService(PostgresAccountBackend(self.room_store), clock=self.clock)
        else:
            self.room_store = SqliteRoomStore(tmp_path / "s.db")
            self.service = AccountService(SqliteAccountBackend(tmp_path / "s.db"), clock=self.clock)
        self.app = create_app(self.room_store, {}, tmp_path / "b", clock=self.clock, accounts=self.service, settings=settings)
        self.client = TestClient(self.app, base_url=SERVER)
        self.seen: list[tuple] = []  # every request the clients made: (url, headers, body)
        self.client.event_hooks["request"] = [lambda r: self.seen.append((str(r.url), dict(r.headers), r.read()))]
        self.host_ident = self.keys.load_or_create("host")
        self.service.redeem_registration_code(self.service.create_registration_code("hosty", can_host=True), self.host_ident.public(), "hosty")
        self.host = self.store()
        self.host.login(self.host_ident)
        self.host.create_room("r1", "hosty", title="Cache work")
        self.room_key = crypto.random_bytes(32)

    def store(self):
        return RemoteRoomStore(SERVER, "", client=self.client)

    def invite(self, ttl=86400):
        return invites.make_invite(self.host, SERVER, self.host_ident, "r1", key_provider=lambda: self.room_key, ttl=ttl)


@pytest.fixture(params=["sqlite", "postgres"])
def w(request, tmp_path):
    if request.param == "sqlite":
        yield World(tmp_path)
        return
    if not PG_URL:
        pytest.skip("set COUNCIL_TEST_PG_URL to run the Postgres variant")
    import psycopg

    schema = f"t_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(PG_URL, autocommit=True) as c:
        c.execute(f'CREATE SCHEMA "{schema}"')
    world = World(tmp_path, schema=schema)
    yield world
    world.room_store.close()
    with psycopg.connect(PG_URL, autocommit=True) as c:
        c.execute(f'DROP SCHEMA "{schema}" CASCADE')


def test_link_round_trips_and_rejects_garbage():
    secret = crypto.random_bytes(32)
    url = invites.build_link("https://x.example", "inv_abc", secret, "dev_" + "a" * 24)
    p = invites.parse_link(url)
    assert (p.server, p.invite_id, p.secret, p.host_device_id) == ("https://x.example", "inv_abc", secret, "dev_" + "a" * 24)
    for bad in ("", "nonsense", "ftp://x/j/inv_a#abcd", "https://x/j/inv_a", "https://x/j/#abc", "https://x/other/inv_a#" + "A" * 43,
                "https://x/j/inv_a#!!!!", "https://x/j/inv_a#" + base64.urlsafe_b64encode(b"short").decode().rstrip("=")):
        with pytest.raises(ValueError):
            invites.parse_link(bad)


def test_link_fragment_is_never_sent_to_the_server(w):
    link = w.invite()
    parsed = invites.parse_link(link.url)
    needle_b64 = base64.urlsafe_b64encode(parsed.secret).decode().rstrip("=")
    joiner = w.keys.load_or_create("joiner")
    store = w.store()
    info = store.get_invite(parsed.invite_id)
    invites.check_host_pin(parsed.host_device_id, info["host"])
    store.request_join(parsed.invite_id, "joiny", joiner, invites.claim_key(parsed.secret))
    blob = repr(w.seen).encode()
    assert needle_b64.encode() not in blob and parsed.secret.hex().encode() not in blob and parsed.secret not in blob
    assert b"#" not in b"".join(u.encode() for u, _, _ in w.seen)  # no URL we requested had a fragment


def test_wrapped_key_blob_is_useless_without_the_secret(w):
    link = w.invite()
    parsed = invites.parse_link(link.url)
    wrapped = bytes.fromhex(w.store().get_invite(parsed.invite_id)["wrapped_room_key"])
    assert w.room_key not in wrapped
    assert invites.unwrap_room_key(parsed.secret, wrapped) == w.room_key
    with pytest.raises(CryptoError):
        invites.unwrap_room_key(crypto.random_bytes(32), wrapped)
    with pytest.raises(CryptoError):
        invites.unwrap_room_key(parsed.secret, wrapped[:-1] + bytes([wrapped[-1] ^ 1]))


def test_full_join_flow_host_approves_and_the_guest_gets_in(w):
    parsed = invites.parse_link(w.invite().url)
    guest_ident = w.keys.load_or_create("guest")
    guest = w.store()
    req = guest.request_join(parsed.invite_id, "guesty", guest_ident, invites.claim_key(parsed.secret))
    assert req["words"] == safety_words(guest_ident.public().fingerprint)
    assert guest.join_status(req["request_id"])["status"] == "pending"
    pending = w.host.list_join_requests("r1")
    assert [(p["handle"], p["words"], p["device_id"]) for p in pending] == [("guesty", req["words"], guest_ident.device_id)]
    w.host.approve_join("r1", req["request_id"])
    assert guest.join_status(req["request_id"])["status"] == "approved"
    assert guest.login(guest_ident) == "guesty"
    assert guest.room_info("r1").title == "Cache work"
    assert "guesty" in w.host.room_info("r1").members
    assert w.service.can_host("guesty") is False  # guests can join rooms, not host them
    w.host.approve_join("r1", req["request_id"])  # approving again is harmless (a retry after a hiccup)
    assert w.host.room_info("r1").members.count("guesty") == 1


def test_invite_is_single_use_a_second_claim_is_refused(w):
    parsed = invites.parse_link(w.invite().url)
    w.store().request_join(parsed.invite_id, "first", w.keys.load_or_create("g1"), invites.claim_key(parsed.secret))
    from aletheore.council.protocol import ProtocolError

    with pytest.raises(ProtocolError):
        w.store().request_join(parsed.invite_id, "second", w.keys.load_or_create("g2"), invites.claim_key(parsed.secret))
    assert [p["handle"] for p in w.host.list_join_requests("r1")] == ["first"]


def test_expired_invite_is_refused_and_leaves_no_request(w):
    parsed = invites.parse_link(w.invite(ttl=60).url)
    w.clock.now += 61
    from aletheore.council.protocol import ProtocolError

    with pytest.raises(ProtocolError):
        w.store().request_join(parsed.invite_id, "late", w.keys.load_or_create("g1"), invites.claim_key(parsed.secret))
    assert w.host.list_join_requests("r1") == []
    with pytest.raises(ProtocolError):
        w.store().get_invite(parsed.invite_id)


def test_unapproved_request_gets_no_access_to_events_or_blobs(w):
    parsed = invites.parse_link(w.invite().url)
    ident = w.keys.load_or_create("guest")
    guest = w.store()
    guest.request_join(parsed.invite_id, "guesty", ident, invites.claim_key(parsed.secret))
    with pytest.raises(PermissionError):
        guest.login(ident)  # the device is not registered until the host approves
    for path in ("/v1/rooms/r1", "/v1/rooms/r1/events", f"/v1/rooms/r1/blobs/{'0' * 64}", "/v1/rooms"):
        assert w.client.get(path).status_code == 401
        assert w.client.get(path, headers={"Authorization": "Bearer made-up"}).status_code == 401
    assert "guesty" not in w.host.room_info("r1").members


def test_host_sees_handle_and_safety_words_and_can_reject(w):
    parsed = invites.parse_link(w.invite().url)
    ident = w.keys.load_or_create("guest")
    guest = w.store()
    req = guest.request_join(parsed.invite_id, "guesty", ident, invites.claim_key(parsed.secret))
    w.host.reject_join("r1", req["request_id"])
    assert guest.join_status(req["request_id"])["status"] == "rejected"
    assert w.host.list_join_requests("r1") == []
    with pytest.raises(PermissionError):
        guest.login(ident)
    assert w.service.get_device(ident.device_id) is None and not w.service.handle_exists("guesty")


def test_reject_burns_the_invite(w):
    parsed = invites.parse_link(w.invite().url)
    req = w.store().request_join(parsed.invite_id, "guesty", w.keys.load_or_create("g1"), invites.claim_key(parsed.secret))
    w.host.reject_join("r1", req["request_id"])
    from aletheore.council.protocol import ProtocolError

    with pytest.raises(ProtocolError):
        w.store().request_join(parsed.invite_id, "retry", w.keys.load_or_create("g2"), invites.claim_key(parsed.secret))


def test_only_the_host_can_list_approve_or_reject(w):
    parsed = invites.parse_link(w.invite().url)
    req = w.store().request_join(parsed.invite_id, "guesty", w.keys.load_or_create("g1"), invites.claim_key(parsed.secret))
    other_ident = w.keys.load_or_create("other")
    w.service.redeem_registration_code(w.service.create_registration_code("otherp", can_host=True), other_ident.public(), "otherp")
    other = w.store()
    other.login(other_ident)
    w.host.add_member("r1", "otherp")  # a member, but not the host
    for call in (lambda: other.list_join_requests("r1"), lambda: other.approve_join("r1", req["request_id"]),
                 lambda: other.reject_join("r1", req["request_id"]), lambda: other.create_invite("r1", b"x", 60)):
        with pytest.raises(PermissionError):
            call()
    assert w.client.get("/v1/rooms/r1/join-requests").status_code == 401
    # a request for another room cannot be approved through this room's route
    w.host.create_room("r2", "hosty")
    from aletheore.council.protocol import ProtocolError

    with pytest.raises((ProtocolError, KeyError)):
        w.host.approve_join("r2", req["request_id"])


def test_invite_endpoint_is_rate_limited_against_guessing(tmp_path):
    w = World(tmp_path, settings=ServerSettings(auth_fail_per_min=6, auth_fail_burst=3, rate_limit_ip_burst=1000, rate_limit_ip_per_min=100000))
    codes = [w.client.get(f"/v1/invites/inv_{i:032x}").status_code for i in range(8)]
    assert codes[:3] == [404, 404, 404] and 429 in codes[3:]
    assert w.client.get("/v1/invites/inv_" + "0" * 32).status_code == 429  # even a correct-looking id waits


def test_the_guest_can_be_an_existing_account_joining_another_room(w):
    ident = w.keys.load_or_create("old")
    w.service.redeem_registration_code(w.service.create_registration_code("oldie"), ident.public(), "oldie")
    parsed = invites.parse_link(w.invite().url)
    store = w.store()
    req = store.request_join(parsed.invite_id, "oldie", ident, invites.claim_key(parsed.secret))
    w.host.approve_join("r1", req["request_id"])
    store.login(ident)
    assert "oldie" in store.room_info("r1").members
    # a different device claiming that handle is refused: that is the linked-device flow, not a join
    parsed2 = invites.parse_link(w.invite().url)
    from aletheore.council.protocol import ProtocolError

    with pytest.raises(ProtocolError):
        w.store().request_join(parsed2.invite_id, "oldie", w.keys.load_or_create("impostor"), invites.claim_key(parsed2.secret))
    with pytest.raises(ProtocolError):
        w.store().request_join(parsed2.invite_id, "hosty", w.keys.load_or_create("impostor2"), invites.claim_key(parsed2.secret))


def test_a_host_record_that_does_not_match_the_link_is_refused(w):
    parsed = invites.parse_link(w.invite().url)
    info = w.store().get_invite(parsed.invite_id)
    invites.check_host_pin(parsed.host_device_id, info["host"])  # the real server passes
    imposter = w.keys.load_or_create("imposter").public()
    fake = {"device_id": imposter.device_id, "sign_vk": imposter.sign_vk.hex(), "box_pk": imposter.box_pk.hex()}
    with pytest.raises(ValueError):
        invites.check_host_pin(parsed.host_device_id, fake)  # a different key claiming to be the host
    lying = dict(info["host"], device_id=parsed.host_device_id, sign_vk=imposter.sign_vk.hex())
    with pytest.raises(ValueError):
        invites.check_host_pin(parsed.host_device_id, lying)  # the right name over the wrong keys


def test_bad_invite_requests_are_refused_cleanly(w):
    h = {"Authorization": "Bearer " + w.host._headers["Authorization"][7:]}
    assert w.client.post("/v1/rooms/r1/invites", json={"wrapped_room_key": "zz"}, headers=h).status_code == 422
    assert w.client.post("/v1/rooms/r1/invites", json={"wrapped_room_key": "00" * 9000}, headers=h).status_code in (413, 422)
    assert w.client.post("/v1/rooms/r1/invites", json={"wrapped_room_key": "00", "ttl_seconds": 99 * 86400}, headers=h).status_code == 422
    assert w.client.post("/v1/rooms/r1/invites", json={"wrapped_room_key": "00", "ttl_seconds": 0}, headers=h).status_code == 422
    assert w.client.post("/v1/invites/inv_x/requests", json={}).status_code in (404, 422)
    assert w.client.post("/v1/invites/inv_x/requests", content=b"not json").status_code in (404, 422)
    assert w.client.get("/v1/join-requests/jr_nope").status_code == 404


# ---- the server proves it is the host's -----------------------------------------------------------------------------------
def test_a_server_run_by_the_host_proves_it_and_a_recycled_address_is_refused(tmp_path):
    from fastapi.testclient import TestClient

    from aletheore.council.identity import Keystore
    from aletheore.council.invites import check_server_proves_host
    from aletheore.council.server import create_app
    from aletheore.council.sqlite_store import SqliteRoomStore

    ks = Keystore(tmp_path / "k")
    host, stranger = ks.load_or_create("host"), ks.load_or_create("stranger")

    def client(identity):
        app = create_app(SqliteRoomStore(tmp_path / f"{id(identity)}.db"), {"tok": "alice"}, tmp_path / "b", host_identity=identity)
        return TestClient(app, base_url="http://council.test")

    assert check_server_proves_host("http://council.test", host.device_id, http=client(host)) is True
    with pytest.raises(ValueError, match="not the host's"):
        check_server_proves_host("http://council.test", host.device_id, http=client(stranger))  # the address now belongs to someone else
    plain = TestClient(create_app(SqliteRoomStore(tmp_path / "p.db"), {"tok": "alice"}, tmp_path / "b"), base_url="http://council.test")
    assert check_server_proves_host("http://council.test", host.device_id, http=plain) is False  # an older server: not checked, not refused
    assert check_server_proves_host("http://council.test", "", http=plain) is False
    assert client(host).get("/v1/host-proof", params={"nonce": "zz"}).status_code == 400
    assert client(host).get("/v1/host-proof", params={"nonce": "00"}).status_code == 400


def test_claiming_an_invite_needs_the_links_secret_not_just_its_id(w):
    """The id travels in the visible part of the link (chat previews, logs); only the fragment secret may open the door."""
    parsed = invites.parse_link(w.invite().url)
    with pytest.raises(PermissionError):
        w.store().request_join(parsed.invite_id, "thief", w.keys.load_or_create("thief"))  # id only
    with pytest.raises(PermissionError):
        w.store().request_join(parsed.invite_id, "thief", w.keys.load_or_create("thief"), crypto.random_bytes(32))  # a guess
    assert w.host.list_join_requests("r1") == []  # nothing was recorded and the invitation is not spent
    req = w.store().request_join(parsed.invite_id, "guesty", w.keys.load_or_create("guest"), invites.claim_key(parsed.secret))
    assert req["request_id"]


def test_the_claim_key_cannot_be_turned_back_into_the_link_secret_or_the_room_key(w):
    parsed = invites.parse_link(w.invite().url)
    key = invites.claim_key(parsed.secret)
    assert len(key) == 32 and key != parsed.secret
    stored_blob = bytes.fromhex(w.store().get_invite(parsed.invite_id)["wrapped_room_key"])
    assert key not in stored_blob and parsed.secret not in stored_blob and w.room_key not in stored_blob


def test_a_database_made_before_claim_keys_is_upgraded_in_place(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE invites (invite_id TEXT PRIMARY KEY, room_id TEXT NOT NULL, host_device_id TEXT NOT NULL, wrapped BLOB NOT NULL, "
                 "title TEXT NOT NULL, expires_at REAL NOT NULL, status TEXT NOT NULL DEFAULT 'open', created_at REAL NOT NULL)")
    conn.execute("INSERT INTO invites VALUES ('inv_old', 'r1', 'dev_x', x'00', 't', 9999999999, 'open', 1)")
    conn.commit(); conn.close()
    from aletheore.council.accounts import SqliteAccountBackend

    SqliteAccountBackend(path)  # opening it adds the column
    SqliteAccountBackend(path)  # and opening it again is harmless
    cols = {r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info(invites)")}
    assert "claim_verifier" in cols
    assert sqlite3.connect(path).execute("SELECT claim_verifier FROM invites WHERE invite_id='inv_old'").fetchone()[0] == b""  # an old invitation keeps working
