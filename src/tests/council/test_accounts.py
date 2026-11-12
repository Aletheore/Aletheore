"""Accounts: registration codes, signed login, sessions, revocation and linked devices.

The service tests run against SQLite always and against Postgres when COUNCIL_TEST_PG_URL is set. The route tests go through
the real HTTP app with real signatures.
"""

import hashlib
import os
import sqlite3
import uuid

import pytest
from fastapi.testclient import TestClient

from aletheore.council import crypto
from aletheore.council.accounts import AccountError, AccountService, SqliteAccountBackend
from aletheore.council.identity import Keystore, safety_words
from aletheore.council.server import create_app
from aletheore.council.sqlite_store import SqliteRoomStore

PG_URL = os.environ.get("COUNCIL_TEST_PG_URL")


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture(params=["sqlite", "postgres"])
def make_service(request, tmp_path, clock):
    if request.param == "postgres":
        if not PG_URL:
            pytest.skip("set COUNCIL_TEST_PG_URL to run the Postgres variant")
        import psycopg

        from aletheore.council.accounts import PostgresAccountBackend
        from aletheore.council.pg_store import PostgresRoomStore

        schema = f"t_{uuid.uuid4().hex[:10]}"
        with psycopg.connect(PG_URL, autocommit=True) as c:
            c.execute(f'CREATE SCHEMA "{schema}"')
        store = PostgresRoomStore(PG_URL, pool_size=5, schema=schema)

        def build(**kw):
            return AccountService(PostgresAccountBackend(store), clock=clock, **kw)

        yield build
        store.close()
        with psycopg.connect(PG_URL, autocommit=True) as c:
            c.execute(f'DROP SCHEMA "{schema}" CASCADE')
    else:
        def build(**kw):
            return AccountService(SqliteAccountBackend(tmp_path / "accounts.db"), clock=clock, **kw)

        yield build


@pytest.fixture
def service(make_service):
    return make_service()


@pytest.fixture
def keys(tmp_path):
    ks = Keystore(tmp_path / "keys")
    return lambda name: ks.load_or_create(name)


def register(service, keys, handle="alice", name=None, can_host=True):
    ident = keys(name or handle)
    code = service.create_registration_code(handle, can_host=can_host)
    service.redeem_registration_code(code, ident.public(), handle)
    return ident


def login(service, ident):
    nonce = service.login_challenge(ident.device_id)
    sig = ident.sign(service.login_message(ident.device_id, nonce))
    return service.login(ident.device_id, nonce, sig)


def test_registration_code_is_single_use_and_expires(service, keys, clock):
    ident = keys("a")
    code = service.create_registration_code("alice", can_host=True, ttl=60)
    account = service.redeem_registration_code(code, ident.public(), "alice")
    assert account.handle == "alice" and account.can_host is True
    with pytest.raises(AccountError):
        service.redeem_registration_code(code, keys("other").public(), "alice")  # a used code is dead
    late = service.create_registration_code("bob", ttl=60)
    clock.advance(61)
    with pytest.raises(AccountError):
        service.redeem_registration_code(late, keys("b").public(), "bob")
    with pytest.raises(AccountError):
        service.redeem_registration_code("cr_" + "0" * 40, keys("c").public(), "carol")  # an invented code


def test_registration_code_is_stored_only_as_a_hash(tmp_path, clock, keys):
    svc = AccountService(SqliteAccountBackend(tmp_path / "accounts.db"), clock=clock)
    code = svc.create_registration_code("alice")
    dump = "\n".join(sqlite3.connect(tmp_path / "accounts.db").iterdump())
    assert code not in dump and hashlib.sha256(code.encode()).hexdigest() in dump


def test_a_code_only_works_for_the_handle_it_was_made_for(service, keys):
    code = service.create_registration_code("alice")
    with pytest.raises(AccountError):
        service.redeem_registration_code(code, keys("x").public(), "mallory")
    service.redeem_registration_code(code, keys("x").public(), "alice")  # the failed try did not burn the code


def test_handle_is_unique_and_cannot_impersonate_an_existing_participant(make_service, keys):
    service = make_service(reserved={"carol"})  # carol exists through the stage 2 token file
    register(service, keys, "alice")
    with pytest.raises(AccountError):
        service.create_registration_code("alice")  # already taken
    with pytest.raises(AccountError):
        service.create_registration_code("carol")  # a legacy participant
    for bad in ("Alice", "al", "a" * 40, "al ice", "../x", "", "alеx"):
        with pytest.raises(AccountError):
            service.create_registration_code(bad)


def test_one_device_belongs_to_one_account(service, keys):
    ident = register(service, keys, "alice")
    code = service.create_registration_code("bob")
    with pytest.raises(AccountError):
        service.redeem_registration_code(code, ident.public(), "bob")


def test_login_requires_a_valid_signature_over_a_fresh_server_nonce(service, keys):
    ident = register(service, keys, "alice")
    token, expires = login(service, ident)
    assert service.session_participant(token) == ("alice", ident.device_id)
    nonce = service.login_challenge(ident.device_id)
    stranger = keys("stranger")
    with pytest.raises(AccountError):
        service.login(ident.device_id, nonce, stranger.sign(service.login_message(ident.device_id, nonce)))  # wrong key
    nonce2 = service.login_challenge(ident.device_id)
    with pytest.raises(AccountError):
        service.login(ident.device_id, nonce2, ident.sign(b"something else"))  # right key, wrong message
    with pytest.raises(AccountError):
        service.login(ident.device_id, "00" * 32, ident.sign(service.login_message(ident.device_id, "00" * 32)))  # nonce we never issued
    with pytest.raises(AccountError):
        service.login_challenge("dev_" + "0" * 24)  # unknown device: no nonce for it


def test_nonce_cannot_be_replayed_or_used_by_another_device(service, keys, clock):
    a, b = register(service, keys, "alice"), register(service, keys, "bobby")
    nonce = service.login_challenge(a.device_id)
    sig = a.sign(service.login_message(a.device_id, nonce))
    service.login(a.device_id, nonce, sig)
    with pytest.raises(AccountError):
        service.login(a.device_id, nonce, sig)  # the same signed answer again
    n2 = service.login_challenge(a.device_id)
    with pytest.raises(AccountError):
        service.login(b.device_id, n2, b.sign(service.login_message(b.device_id, n2)))  # a nonce issued to someone else
    n3 = service.login_challenge(a.device_id)
    clock.advance(61)
    with pytest.raises(AccountError):
        service.login(a.device_id, n3, a.sign(service.login_message(a.device_id, n3)))  # too late


def test_session_token_expires_and_is_stored_only_as_a_hash(tmp_path, clock, keys):
    svc = AccountService(SqliteAccountBackend(tmp_path / "accounts.db"), clock=clock, session_ttl=3600)
    ident = register(svc, keys, "alice")
    token, expires = login(svc, ident)
    assert expires == clock.now + 3600
    dump = "\n".join(sqlite3.connect(tmp_path / "accounts.db").iterdump())
    assert token not in dump and hashlib.sha256(token.encode()).hexdigest() in dump
    assert svc.session_participant(token) is not None
    clock.advance(3601)
    assert svc.session_participant(token) is None
    assert svc.session_participant("not-a-token") is None and svc.session_participant("") is None


def test_revoked_device_cannot_log_in_and_existing_sessions_stop_working(service, keys):
    ident = register(service, keys, "alice")
    token, _ = login(service, ident)
    service.revoke_device(ident.device_id)
    assert service.session_participant(token) is None
    with pytest.raises(AccountError):
        service.login_challenge(ident.device_id)
    nonce = "11" * 32
    with pytest.raises(AccountError):
        service.login(ident.device_id, nonce, ident.sign(service.login_message(ident.device_id, nonce)))


def link(service, keys, existing, handle="alice", name="second"):
    new = keys(name)
    req = service.create_link_request(handle, new.public())
    return new, req


def test_second_device_links_by_approval_from_the_first_and_the_server_never_sees_a_key(service, keys):
    first = register(service, keys, "alice")
    second, req = link(service, keys, first)
    assert req.words == safety_words(second.public().fingerprint)
    assert service.get_link_request(req.request_id).status == "pending"
    assert service.get_device(second.device_id) is None  # not a device until approved
    grants = crypto.seal(second.public().box_pk, b"room key for r1 epoch 1")  # what the first device would publish
    device = service.approve_link_request(first.device_id, req.request_id, req.words, grants)
    assert device.device_id == second.device_id and device.handle == "alice"
    got = service.get_link_request(req.request_id)
    assert got.status == "approved" and got.grants == grants
    assert second.open_sealed(got.grants) == b"room key for r1 epoch 1"  # only the new device can read it
    token, _ = login(service, second)
    assert service.session_participant(token) == ("alice", second.device_id)  # same account handle
    assert {d.device_id for d in service.list_devices("alice")} == {first.device_id, second.device_id}


def test_linking_needs_the_safety_words_to_match_a_wrong_approval_is_refused(service, keys):
    first = register(service, keys, "alice")
    second, req = link(service, keys, first)
    with pytest.raises(AccountError):
        service.approve_link_request(first.device_id, req.request_id, "wrong-words-here-now", b"")
    assert service.get_link_request(req.request_id).status == "pending"
    with pytest.raises(AccountError):
        service.approve_link_request(first.device_id, "lr_" + "0" * 32, req.words, b"")  # unknown request
    service.approve_link_request(first.device_id, req.request_id, req.words, b"")
    with pytest.raises(AccountError):
        service.approve_link_request(first.device_id, req.request_id, req.words, b"")  # approved once only


def test_only_an_active_device_of_the_same_account_can_approve(service, keys, clock):
    first, other = register(service, keys, "alice"), register(service, keys, "bobby")
    second, req = link(service, keys, first)
    with pytest.raises(AccountError):
        service.approve_link_request(other.device_id, req.request_id, req.words, b"")  # another person's device
    service.revoke_device(first.device_id)
    with pytest.raises(AccountError):
        service.approve_link_request(first.device_id, req.request_id, req.words, b"")  # a revoked device
    third = keys("third")
    req2 = service.create_link_request("bobby", third.public())
    clock.advance(16 * 60)
    with pytest.raises(AccountError):
        service.approve_link_request(other.device_id, req2.request_id, req2.words, b"")  # request expired


def test_link_requests_are_limited_and_cannot_take_an_existing_device(service, keys):
    first = register(service, keys, "alice")
    for i in range(5):
        service.create_link_request("alice", keys(f"d{i}").public())
    with pytest.raises(AccountError):
        service.create_link_request("alice", keys("d99").public())  # too many pending: someone is spamming this handle
    with pytest.raises(AccountError):
        service.create_link_request("nobody-here", keys("dx").public())  # no such account
    with pytest.raises(AccountError):
        service.create_link_request("alice", first.public())  # that device is already in an account


def test_revoking_one_device_leaves_the_other_working(service, keys):
    first = register(service, keys, "alice")
    second, req = link(service, keys, first)
    service.approve_link_request(first.device_id, req.request_id, req.words, b"")
    t1, _ = login(service, first)
    t2, _ = login(service, second)
    service.revoke_device(first.device_id)
    assert service.session_participant(t1) is None
    assert service.session_participant(t2) == ("alice", second.device_id)
    assert [d.device_id for d in service.list_devices("alice") if d.active] == [second.device_id]
    # Rotating the room keys the revoked device held is Task 9; the revocation record it will read is `Device.revoked_at`.
    assert service.get_device(first.device_id).revoked_at is not None


# -- over HTTP ------------------------------------------------------------------------------------------------------
@pytest.fixture
def http(tmp_path, clock, keys):
    service = AccountService(SqliteAccountBackend(tmp_path / "accounts.db"), clock=clock, reserved={"legacy"})
    store = SqliteRoomStore(tmp_path / "r.db")
    app = create_app(store, {"tok-legacy": "legacy"}, tmp_path / "bundles", clock=clock, accounts=service)
    return TestClient(app), service, store, keys


def hex_of(b):
    return b.hex()


def http_register(client, code, handle, ident):
    p = ident.public()
    return client.post("/v1/register", json={"code": code, "handle": handle, "sign_vk": hex_of(p.sign_vk), "box_pk": hex_of(p.box_pk)})


def http_login(client, ident):
    ch = client.post("/v1/login/challenge", json={"device_id": ident.device_id})
    assert ch.status_code == 200, ch.text
    nonce = ch.json()["nonce"]
    sig = ident.sign(AccountService.login_message(ident.device_id, nonce))
    return client.post("/v1/login", json={"device_id": ident.device_id, "nonce": nonce, "signature": hex_of(sig)})


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_register_login_whoami_over_http(http):
    client, service, store, keys = http
    ident = keys("alice")
    code = service.create_registration_code("alice", can_host=True)
    r = http_register(client, code, "alice", ident)
    assert r.status_code == 200 and r.json()["device_id"] == ident.device_id
    assert http_register(client, code, "alice", ident).status_code in (400, 409, 422)  # a used code
    login_resp = http_login(client, ident)
    assert login_resp.status_code == 200
    token = login_resp.json()["token"]
    assert client.get("/v1/whoami", headers=bearer(token)).json() == {"participant": "alice"}
    assert client.get("/v1/whoami", headers=bearer("garbage")).status_code == 401
    assert client.get("/v1/whoami").status_code == 401


def test_malformed_registration_and_login_bodies_are_refused_cleanly(http):
    client, service, store, keys = http
    for body in ({}, {"code": 1}, {"code": "x", "handle": "alice", "sign_vk": "zz", "box_pk": "00"},
                 {"code": "x", "handle": "alice", "sign_vk": "00" * 31, "box_pk": "00" * 32}):
        assert client.post("/v1/register", json=body).status_code in (400, 422)
    assert client.post("/v1/register", content=b"not json").status_code == 422
    assert client.post("/v1/login/challenge", json={}).status_code in (400, 422)
    assert client.post("/v1/login", json={"device_id": "d", "nonce": "n", "signature": "zz"}).status_code in (400, 401, 422)


def test_only_hosts_registered_with_can_host_may_create_rooms(http):
    client, service, store, keys = http
    host, guest = keys("h"), keys("g")
    service.redeem_registration_code(service.create_registration_code("hosty", can_host=True), host.public(), "hosty")
    service.redeem_registration_code(service.create_registration_code("guesty", can_host=False), guest.public(), "guesty")
    ht = http_login(client, host).json()["token"]
    gt = http_login(client, guest).json()["token"]
    assert client.post("/v1/rooms/r1", json={"host": "hosty"}, headers=bearer(ht)).status_code == 200
    denied = client.post("/v1/rooms/r2", json={"host": "guesty"}, headers=bearer(gt))
    assert denied.status_code == 403 and "host" in denied.json()["detail"]
    client.post("/v1/rooms/r1/members", json={"participant": "guesty"}, headers=bearer(ht))
    assert client.get("/v1/rooms/r1", headers=bearer(gt)).status_code == 200  # a guest can still be a member


def test_stage_2_token_file_mode_still_works_for_lan_servers(http):
    client, service, store, keys = http
    assert client.get("/v1/whoami", headers=bearer("tok-legacy")).json() == {"participant": "legacy"}
    assert client.post("/v1/rooms/r1", json={"host": "legacy"}, headers=bearer("tok-legacy")).status_code == 200


def test_a_server_with_only_accounts_and_no_tokens_starts(tmp_path, clock):
    service = AccountService(SqliteAccountBackend(tmp_path / "a.db"), clock=clock)
    create_app(SqliteRoomStore(tmp_path / "r.db"), {}, tmp_path / "b", accounts=service)
    with pytest.raises(ValueError):
        create_app(SqliteRoomStore(tmp_path / "r2.db"), {}, tmp_path / "b2")  # neither tokens nor accounts: nobody could log in


def test_a_revoked_device_is_locked_out_of_http_at_once(http):
    client, service, store, keys = http
    ident = keys("alice")
    service.redeem_registration_code(service.create_registration_code("alice", can_host=True), ident.public(), "alice")
    token = http_login(client, ident).json()["token"]
    assert client.get("/v1/whoami", headers=bearer(token)).status_code == 200
    service.revoke_device(ident.device_id)
    assert client.get("/v1/whoami", headers=bearer(token)).status_code == 401


def test_link_a_second_device_over_http(http):
    client, service, store, keys = http
    first, second = keys("mac"), keys("win")
    service.redeem_registration_code(service.create_registration_code("alice", can_host=True), first.public(), "alice")
    ft = http_login(client, first).json()["token"]
    p = second.public()
    req = client.post("/v1/devices/link-requests", json={"handle": "alice", "sign_vk": hex_of(p.sign_vk), "box_pk": hex_of(p.box_pk)})
    assert req.status_code == 200
    rid, words = req.json()["request_id"], req.json()["words"]
    assert client.get(f"/v1/devices/link-requests/{rid}").json()["status"] == "pending"
    grants = crypto.seal(p.box_pk, b"k").hex()
    assert client.post(f"/v1/devices/link-requests/{rid}/approve", json={"words": "wrong", "grants": grants}, headers=bearer(ft)).status_code in (400, 422)
    assert client.post(f"/v1/devices/link-requests/{rid}/approve", json={"words": words, "grants": grants}).status_code == 401  # no session
    ok = client.post(f"/v1/devices/link-requests/{rid}/approve", json={"words": words, "grants": grants}, headers=bearer(ft))
    assert ok.status_code == 200 and ok.json()["device_id"] == second.device_id
    done = client.get(f"/v1/devices/link-requests/{rid}").json()
    assert done["status"] == "approved" and bytes.fromhex(done["grants"]) == bytes.fromhex(grants)
    st = http_login(client, second).json()["token"]
    assert client.get("/v1/whoami", headers=bearer(st)).json() == {"participant": "alice"}


def test_grants_are_stored_per_device_replaced_by_epoch_and_only_for_the_right_person(service, keys):
    first, other = register(service, keys, "alice"), register(service, keys, "bobby")
    service.put_grant("r1", "alice", 1, first.device_id, b"one")
    service.put_grant("r1", "alice", 2, first.device_id, b"two")
    service.put_grant("r1", "alice", 1, first.device_id, b"one-again")  # a retry replaces, it does not duplicate
    assert service.my_grants("r1", first.device_id) == [(1, b"one-again"), (2, b"two")]
    assert service.my_grants("r1", other.device_id) == [] and service.my_grants("r2", first.device_id) == []
    for bad in (dict(participant="bobby"), dict(epoch=0), dict(epoch=True), dict(grant=b""), dict(grant=b"x" * 9000), dict(device_id="dev_" + "0" * 24)):
        args = dict(room_id="r1", participant="alice", epoch=3, device_id=first.device_id, grant=b"g") | bad
        with pytest.raises(AccountError):
            service.put_grant(**args)
    service.revoke_device(first.device_id)
    with pytest.raises(AccountError):
        service.put_grant("r1", "alice", 4, first.device_id, b"g")  # no new keys for a revoked device


def test_devices_route_lists_only_my_devices_and_pending_requests_and_revokes_only_mine(http):
    client, service, store, keys = http
    mac, win, other = keys("mac"), keys("win"), keys("bobdev")
    service.redeem_registration_code(service.create_registration_code("alice", can_host=True), mac.public(), "alice")
    service.redeem_registration_code(service.create_registration_code("bob", can_host=True), other.public(), "bob")
    token = http_login(client, mac).json()["token"]
    assert client.get("/v1/devices").status_code == 401
    p = win.public()
    client.post("/v1/devices/link-requests", json={"handle": "alice", "sign_vk": hex_of(p.sign_vk), "box_pk": hex_of(p.box_pk)})
    got = client.get("/v1/devices", headers=bearer(token)).json()
    assert got["handle"] == "alice" and [d["device_id"] for d in got["devices"]] == [mac.device_id]
    assert got["devices"][0]["current"] and len(got["devices"][0]["words"].split("-")) == 4
    assert len(got["pending"]) == 1 and got["pending"][0]["device_id"] == win.device_id
    rid, words = got["pending"][0]["request_id"], got["pending"][0]["words"]
    assert client.post(f"/v1/devices/link-requests/{rid}/approve", json={"words": words}, headers=bearer(token)).status_code == 200
    assert {d["device_id"] for d in client.get("/v1/devices", headers=bearer(token)).json()["devices"]} == {mac.device_id, win.device_id}
    assert client.get("/v1/devices", headers=bearer(token)).json()["pending"] == []
    bob_token = http_login(client, other).json()["token"]
    assert client.post(f"/v1/devices/{win.device_id}/revoke", headers=bearer(bob_token)).status_code == 404
    assert client.post(f"/v1/devices/{mac.device_id}/revoke", headers=bearer(token)).status_code == 400
    assert client.post(f"/v1/devices/{win.device_id}/revoke", headers=bearer(token)).status_code == 200
    assert sorted(d["revoked"] for d in client.get("/v1/devices", headers=bearer(token)).json()["devices"]) == [False, True]
