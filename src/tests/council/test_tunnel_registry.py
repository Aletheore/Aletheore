"""The tunnel registry: leases, the queue, the counter, release, and the signed lookup. Fake provider and fake clock only."""

import pytest

from aletheore.council import crypto
from aletheore.council.identity import Keystore
from aletheore.council.tunnel_registry import (GRACE_S, QUEUE_WAIT_S, FakeProvider, Registry, RegistryError, verify_answer,
                                               verify_host_proof, host_proof_message)


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def env(tmp_path):
    clock, provider = Clock(), FakeProvider()
    sk, vk = crypto.generate_signing_keypair()
    ks = Keystore(tmp_path / "ids")
    reg = Registry(tmp_path / "reg.db", provider, "aletheorecouncil.com", (sk, vk), slots=2, clock=clock)

    class E:
        pass

    e = E()
    e.clock, e.provider, e.reg, e.vk, e.ident = clock, provider, reg, vk, lambda n: ks.load_or_create(n).public()
    return e


def test_a_claim_gets_a_slot_a_token_and_the_counter_moves(env):
    a = env.reg.claim(env.ident("a"))
    assert a.status == "leased" and a.hostname == "slot-001.aletheorecouncil.com" and env.provider.connect(a.tunnel_id, a.token)
    assert env.reg.counter() == {"in_use": 1, "total": 2, "queued": 0}
    b = env.reg.claim(env.ident("b"))
    assert b.slot == "slot-002" and env.reg.counter()["in_use"] == 2


def test_the_same_host_keeps_its_slot_and_names_are_not_vanity(env):
    first = env.reg.claim(env.ident("a"))
    again = env.reg.claim(env.ident("a"))
    assert again.slot == first.slot and env.reg.counter()["in_use"] == 1  # one name per host
    assert not env.provider.connect(first.tunnel_id, first.token)  # the earlier token was replaced by a fresh one
    assert env.provider.connect(again.tunnel_id, again.token)


def test_a_full_pool_queues_in_order_and_the_head_gets_the_next_free_slot(env):
    a, b = env.reg.claim(env.ident("a")), env.reg.claim(env.ident("b"))
    c = env.reg.claim(env.ident("c"))
    d = env.reg.claim(env.ident("d"))
    assert c.status == "queued" and c.position == 1 and d.position == 2 and "all 2 free slots are in use" in c.message
    assert env.reg.release(env.ident("a").device_id)
    late = env.reg.claim(env.ident("d"))  # not the head: still waits, even though a slot is free
    assert late.status == "queued" and late.position == 2
    head = env.reg.claim(env.ident("c"))
    assert head.status == "leased" and head.slot == a.slot
    assert env.reg.counter() == {"in_use": 2, "total": 2, "queued": 1}


def test_a_released_slots_old_token_is_refused(env):
    a = env.reg.claim(env.ident("a"))
    assert env.provider.connect(a.tunnel_id, a.token) and env.provider.connections[a.tunnel_id]
    env.reg.release(env.ident("a").device_id)
    assert not env.provider.connections[a.tunnel_id]  # the old connection was dropped
    assert not env.provider.connect(a.tunnel_id, a.token)  # and the old token no longer works
    assert env.reg.release(env.ident("a").device_id) is False


def test_lease_expiry_and_reuse_with_a_fake_clock(env):
    a = env.reg.claim(env.ident("a"))
    env.clock.t += GRACE_S - 10
    env.reg.heartbeat(env.ident("a").device_id)  # alive: lease runs a full grace from now
    env.clock.t += GRACE_S - 10
    assert env.reg.counter()["in_use"] == 1
    env.clock.t += 20  # now more than a grace with no heartbeat
    assert env.reg.counter()["in_use"] == 0
    with pytest.raises(RegistryError):
        env.reg.heartbeat(env.ident("a").device_id)
    b = env.reg.claim(env.ident("b"))
    assert b.slot == a.slot and not env.provider.connect(a.tunnel_id, a.token)  # the name is reused, the old token is dead


def test_a_queued_host_that_stops_asking_loses_its_place(env):
    env.reg.claim(env.ident("a")), env.reg.claim(env.ident("b"))
    assert env.reg.claim(env.ident("c")).status == "queued"
    env.clock.t += QUEUE_WAIT_S + 1
    assert env.reg.counter()["queued"] == 0


def test_a_blocked_host_loses_its_slot_and_cannot_claim(env):
    env.reg.claim(env.ident("a"))
    env.reg.ban(env.ident("a").device_id)
    assert env.reg.counter()["in_use"] == 0
    assert env.reg.claim(env.ident("a")).status == "banned"


def test_lookup_is_signed_and_a_client_checks_everything(env):
    a = env.ident("a")
    env.reg.claim(a)
    answer = env.reg.lookup(a.device_id)
    assert verify_answer(answer, env.vk, a.device_id, env.clock.t) == "https://slot-001.aletheorecouncil.com"
    with pytest.raises(ValueError, match="different host"):
        verify_answer(answer, env.vk, env.ident("b").device_id, env.clock.t)
    with pytest.raises(ValueError, match="expired"):
        verify_answer(answer, env.vk, a.device_id, env.clock.t + 10_000)
    other_vk = crypto.generate_signing_keypair()[1]
    with pytest.raises(ValueError, match="not signed by the registry"):
        verify_answer(answer, other_vk, a.device_id, env.clock.t)
    forged = {"payload": {**answer["payload"], "address": "https://evil.example"}, "signature": answer["signature"]}
    with pytest.raises(ValueError, match="not signed"):
        verify_answer(forged, env.vk, a.device_id, env.clock.t)
    with pytest.raises(ValueError, match="malformed"):
        verify_answer({"payload": {}}, env.vk, a.device_id, env.clock.t)
    with pytest.raises(RegistryError):
        env.reg.lookup(env.ident("nobody").device_id)  # no slot: no address


def test_a_recycled_name_serving_a_different_host_fails_the_proof(tmp_path):
    ks = Keystore(tmp_path / "k")
    real, impostor = ks.load_or_create("real"), ks.load_or_create("impostor")
    nonce = crypto.random_bytes(16)
    good = real.sign(host_proof_message(nonce))
    assert verify_host_proof(real.public(), nonce, good)
    assert not verify_host_proof(real.public(), nonce, impostor.sign(host_proof_message(nonce)))  # a different host's key
    assert not verify_host_proof(real.public(), crypto.random_bytes(16), good)  # a replayed answer to another nonce


def test_the_pool_does_not_shrink_under_a_holder_and_can_grow(env):
    a = env.reg.claim(env.ident("a"))
    env.reg.ensure_slots(1)
    assert env.reg.counter()["total"] == 2 and env.reg.claim(env.ident("a")).slot == a.slot
    env.reg.ensure_slots(3)
    assert env.reg.counter()["total"] == 3


# ---- over HTTP: signed requests, rate limits, the status page ----------------------------------------------------------
@pytest.fixture
def web(tmp_path, env):
    from fastapi.testclient import TestClient

    from aletheore.council.registry_client import RegistryClient
    from aletheore.council.registry_server import create_registry_app

    app = create_registry_app(env.reg, env.clock, admin_token="adm")
    http = TestClient(app)
    ks = Keystore(tmp_path / "ids")
    env.http, env.client = http, lambda name: RegistryClient(http, ks.load_or_create(name), env.vk, env.clock)
    return env


def test_claim_heartbeat_release_and_lookup_over_http(web):
    a = web.client("a")
    got = a.claim()
    assert got["status"] == "leased" and got["hostname"].startswith("slot-001") and got["counter"]["in_use"] == 1
    assert a.heartbeat() > web.clock.t
    me = web.client("a").identity.public().device_id
    assert a.where_is(me) == "https://slot-001.aletheorecouncil.com"
    assert a.release() is True and a.release() is False
    with pytest.raises(ConnectionError):
        a.where_is(me)  # no slot: no address


def test_requests_must_be_signed_fresh_and_not_replayed(web):
    assert web.http.post("/v1/claim").status_code == 401  # unsigned
    a = web.client("a")
    headers = a._signed("POST", "/v1/claim")
    assert web.http.post("/v1/claim", headers=headers).status_code == 200
    assert web.http.post("/v1/claim", headers=headers).status_code == 401  # the same request again
    stale = a._signed("POST", "/v1/claim")
    web.clock.t += 120
    assert web.http.post("/v1/claim", headers=stale).status_code == 401  # outside the time window
    tampered = a._signed("POST", "/v1/claim")
    assert web.http.post("/v1/heartbeat", headers=tampered).status_code == 401  # a signature for one path is not good for another


def test_claims_are_rate_limited_per_device(web):
    a = web.client("a")
    codes = []
    for _ in range(9):
        try:
            a.claim()
            codes.append(200)
        except ConnectionError as exc:
            codes.append(429 if "too many" in str(exc) else 0)
    assert codes[:6] == [200] * 6 and 429 in codes[6:]


def test_status_page_and_json_show_the_honest_counter_and_the_sponsor_line(web):
    from aletheore.council.sponsor import SPONSOR

    web.client("a").claim()
    status = web.http.get("/v1/status").json()
    assert (status["in_use"], status["total"]) == (1, 2) and status["sponsor"] == SPONSOR
    page = web.http.get("/").text
    assert "free slots in use: 1 / 2" in page and "student developed open source project" in page and "—" not in page


def test_only_the_admin_can_block_a_host(web):
    host = web.client("a")
    host.claim()
    target = host.identity.public().device_id
    assert web.http.post(f"/v1/admin/ban/{target}").status_code == 403
    assert web.http.post(f"/v1/admin/ban/{target}", headers={"authorization": "Bearer wrong"}).status_code == 403
    assert web.http.post(f"/v1/admin/ban/{target}", headers={"authorization": "Bearer adm"}).status_code == 200
    assert host.claim()["status"] == "banned"
