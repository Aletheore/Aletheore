import json
import logging

import pytest
from fastapi.testclient import TestClient

from aletheore.council.server import create_app
from aletheore.council.server_config import (
    Metrics, RateLimiter, ServerSettings, check_bind, client_ip, load_settings,
)
from aletheore.council.sqlite_store import SqliteRoomStore

TOK = {"tok-alice": "alice", "tok-bob": "bob"}


def auth(t="tok-alice"):
    return {"Authorization": f"Bearer {t}"}


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make(tmp_path, clock=None, client=("203.0.113.5", 50000), **kw):
    clock = clock or Clock()
    settings = ServerSettings(**kw.pop("settings", {}))
    app = create_app(SqliteRoomStore(tmp_path / "s.db"), TOK, tmp_path / "b", settings=settings, ops_clock=clock, **kw)
    return TestClient(app, client=client), clock


def room_and_event(c, cid, who="tok-alice"):
    return c.post("/v1/rooms/r1/events", headers=auth(who), json={
        "type": "question", "client_event_id": cid, "thread_id": "room", "reply_to": None,
        "sender_participant": "alice" if who == "tok-alice" else "bob", "sender_agent": None, "body": {"text": "x", "kind": "chat"}})


# ---- the limiter itself -------------------------------------------------------------------------------------------
def test_token_bucket_allows_a_burst_then_refills_over_time():
    clock = Clock()
    rl = RateLimiter(rate_per_min=60, burst=3, clock=clock)
    assert [rl.allow("k") for _ in range(4)] == [True, True, True, False]
    assert rl.retry_after("k") >= 1
    clock.t += 2.0  # 60/min = 1 per second
    assert [rl.allow("k") for _ in range(3)] == [True, True, False]
    assert rl.allow("other") is True  # buckets are per key


def test_limiter_memory_is_bounded():
    rl = RateLimiter(rate_per_min=60, burst=1, clock=Clock(), max_keys=100)
    for i in range(1000):
        rl.allow(f"k{i}")
    assert rl.size() <= 100


# ---- per-token event limit ------------------------------------------------------------------------------------------
def test_per_token_event_rate_limit_returns_429_then_recovers(tmp_path):
    c, clock = make(tmp_path, settings={"rate_limit_events_per_min": 60, "rate_limit_events_burst": 3, "rate_limit_ip_per_min": 100000})
    c.post("/v1/rooms/r1", json={"host": "alice"}, headers=auth())
    codes = [room_and_event(c, f"c{i}").status_code for i in range(5)]
    assert codes == [200, 200, 200, 429, 429]
    r = room_and_event(c, "c9")
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1
    clock.t += 5
    assert room_and_event(c, "c10").status_code == 200
    # another person's budget is separate
    c.post("/v1/rooms/r1/members", json={"participant": "bob"}, headers=auth())
    assert room_and_event(c, "b1", "tok-bob").status_code == 200


def test_a_retry_of_an_already_stored_event_still_counts_but_returns_the_same_event(tmp_path):
    c, clock = make(tmp_path, settings={"rate_limit_events_per_min": 60, "rate_limit_events_burst": 5, "rate_limit_ip_per_min": 100000})
    c.post("/v1/rooms/r1", json={"host": "alice"}, headers=auth())
    a, b = room_and_event(c, "same").json(), room_and_event(c, "same").json()
    assert a["event_id"] == b["event_id"]


# ---- per-IP limit and token guessing ----------------------------------------------------------------------------------
def test_per_ip_limit_applies_before_authentication_to_stop_token_guessing(tmp_path):
    c, clock = make(tmp_path, settings={"rate_limit_ip_per_min": 60, "rate_limit_ip_burst": 5, "auth_fail_per_min": 100000})
    codes = [c.get("/v1/whoami", headers=auth("wrong-guess-%d" % i)).status_code for i in range(8)]
    assert codes[:5] == [401] * 5 and codes[5:] == [429] * 3  # after the burst even a valid token is refused until it refills
    assert c.get("/v1/whoami", headers=auth()).status_code == 429
    clock.t += 10
    assert c.get("/v1/whoami", headers=auth()).status_code == 200


def test_repeated_failed_logins_from_one_address_are_cut_off_sooner(tmp_path):
    c, clock = make(tmp_path, settings={"rate_limit_ip_per_min": 100000, "auth_fail_per_min": 6, "auth_fail_burst": 3})
    codes = [c.get("/v1/whoami", headers=auth("bad%d" % i)).status_code for i in range(6)]
    assert codes == [401, 401, 401, 429, 429, 429]
    assert c.get("/v1/whoami", headers=auth()).status_code == 429  # even the right token waits out the penalty


def test_other_addresses_are_unaffected_by_one_addresss_penalty(tmp_path):
    c, clock = make(tmp_path, settings={"auth_fail_per_min": 6, "auth_fail_burst": 2, "rate_limit_ip_per_min": 100000})
    for i in range(4):
        c.get("/v1/whoami", headers=auth("bad%d" % i))
    other = TestClient(c.app, client=("198.51.100.7", 4000))
    assert other.get("/v1/whoami", headers=auth()).status_code == 200


# ---- who is the client? ------------------------------------------------------------------------------------------------
def test_x_forwarded_for_is_ignored_unless_the_peer_is_a_trusted_proxy():
    trusted = ["10.0.0.0/8", "127.0.0.1"]
    assert client_ip("203.0.113.5", "1.2.3.4", trusted) == "203.0.113.5"  # an outsider cannot choose its own address
    assert client_ip("10.1.2.3", "198.51.100.9", trusted) == "198.51.100.9"  # a trusted proxy reports the real client
    assert client_ip("10.1.2.3", "198.51.100.9, 10.9.9.9", trusted) == "198.51.100.9"  # more than one proxy in the chain
    assert client_ip("10.1.2.3", "1.1.1.1, 198.51.100.9", trusted) == "198.51.100.9"  # a client-supplied prefix is not believed
    assert client_ip("10.1.2.3", None, trusted) == "10.1.2.3"
    assert client_ip("10.1.2.3", "not-an-ip", trusted) == "10.1.2.3"


def test_a_spoofed_forwarded_header_does_not_dodge_the_ip_limit(tmp_path):
    c, clock = make(tmp_path, settings={"rate_limit_ip_per_min": 60, "rate_limit_ip_burst": 3, "auth_fail_per_min": 100000})
    codes = [c.get("/v1/whoami", headers={**auth(), "X-Forwarded-For": f"9.9.9.{i}"}).status_code for i in range(5)]
    assert codes == [200, 200, 200, 429, 429]


# ---- health and metrics ------------------------------------------------------------------------------------------------
def test_healthz_is_unauthenticated_and_readyz_checks_the_store(tmp_path):
    c, _ = make(tmp_path)
    assert c.get("/healthz").json() == {"status": "ok"}
    assert c.get("/readyz").status_code == 200

    class DownStore(SqliteRoomStore):
        def host_of(self, room_id):
            raise ConnectionError("database unavailable")

    app = create_app(DownStore(tmp_path / "d.db"), TOK, tmp_path / "b2", settings=ServerSettings())
    t = TestClient(app)
    assert t.get("/healthz").status_code == 200  # alive...
    assert t.get("/readyz").status_code == 503  # ...but not ready


def test_health_endpoints_are_not_rate_limited_so_monitors_never_lock_themselves_out(tmp_path):
    c, _ = make(tmp_path, settings={"rate_limit_ip_per_min": 60, "rate_limit_ip_burst": 1})
    assert all(c.get("/healthz").status_code == 200 for _ in range(10))


def test_metrics_requires_the_admin_token_and_never_contains_tokens_or_bodies(tmp_path):
    import hashlib
    admin = "admin-secret-token"
    c, _ = make(tmp_path, settings={"admin_token_hash": hashlib.sha256(admin.encode()).hexdigest()})
    assert c.get("/metrics").status_code == 401
    assert c.get("/metrics", headers=auth("tok-alice")).status_code == 401  # a participant token is not the admin token
    c.post("/v1/rooms/r1", json={"host": "alice"}, headers=auth())
    room_and_event(c, "m1")
    text = c.get("/metrics", headers={"Authorization": f"Bearer {admin}"}).text
    assert 'council_requests_total{route="/v1/rooms/{room_id}/events",status="200"} 1' in text
    assert "council_events_appended_total 1" in text and "council_request_seconds_bucket" in text
    for secret in ("tok-alice", "tok-bob", admin, "chat"):
        assert secret not in text


def test_metrics_is_disabled_when_no_admin_token_is_configured(tmp_path):
    c, _ = make(tmp_path)
    assert c.get("/metrics", headers=auth()).status_code == 404


def test_metrics_registry_renders_prometheus_text():
    m = Metrics()
    m.inc("council_x_total", {"a": 'q"uote'})
    m.inc("council_x_total", {"a": 'q"uote'}, 2)
    m.observe("council_request_seconds", 0.02)
    out = m.render()
    assert 'council_x_total{a="q\\"uote"} 3' in out and "council_request_seconds_count 1" in out


# ---- logs ----------------------------------------------------------------------------------------------------------------
def test_logs_are_json_without_authorization_headers_or_event_bodies(tmp_path, caplog):
    c, _ = make(tmp_path)
    with caplog.at_level(logging.INFO, logger="council.server"):
        c.post("/v1/rooms/r1", json={"host": "alice"}, headers=auth())
        room_and_event(c, "l1")
        c.get("/v1/whoami", headers=auth("a-wrong-token"))
    lines = [json.loads(r.getMessage()) for r in caplog.records if r.name == "council.server"]
    assert lines and all({"ts", "method", "route", "status", "ms"} <= set(rec) for rec in lines)
    blob = " ".join(r.getMessage() for r in caplog.records)
    for secret in ("tok-alice", "a-wrong-token", "Bearer", "chat", "\"text\""):
        assert secret not in blob
    assert any(rec["route"] == "/v1/rooms/{room_id}/events" and rec["who"] and rec["who"] != "alice" for rec in lines)  # hashed, not the name


# ---- storage trouble --------------------------------------------------------------------------------------------------
def test_a_database_outage_is_a_503_with_retry_after_not_a_500(tmp_path):
    class Flaky(SqliteRoomStore):
        down = False

        def room_info(self, room_id):
            if self.down:
                raise ConnectionError("database unavailable")
            return super().room_info(room_id)

    store = Flaky(tmp_path / "f.db")
    app = create_app(store, TOK, tmp_path / "b", settings=ServerSettings())
    c = TestClient(app)
    c.post("/v1/rooms/r1", json={"host": "alice"}, headers=auth())
    store.down = True
    r = c.get("/v1/rooms/r1", headers=auth())
    assert r.status_code == 503 and r.headers["retry-after"] and "unavailable" in r.json()["detail"]
    assert "database" not in r.text.lower().replace("unavailable", "")  # no internals leaked
    store.down = False
    assert c.get("/v1/rooms/r1", headers=auth()).status_code == 200


def test_the_store_is_closed_when_the_app_shuts_down(tmp_path):
    closed = []

    class Closing(SqliteRoomStore):
        def close(self):
            closed.append(True)

    app = create_app(Closing(tmp_path / "c.db"), TOK, tmp_path / "b", settings=ServerSettings())
    with TestClient(app) as c:
        assert c.get("/healthz").status_code == 200
    assert closed == [True]


# ---- settings and binding ------------------------------------------------------------------------------------------------
def test_settings_come_from_the_environment_with_safe_defaults():
    s = load_settings(environ={})
    assert s.store == "sqlite" and s.host == "127.0.0.1" and s.allow_public is False
    s = load_settings(environ={"COUNCIL_STORE": "postgres", "COUNCIL_PG_DSN": "postgresql://u:p@db/x", "COUNCIL_PORT": "9000",
                               "COUNCIL_TRUSTED_PROXIES": "10.0.0.0/8, 127.0.0.1", "COUNCIL_RATE_EVENTS_PER_MIN": "120"})
    assert s.store == "postgres" and s.dsn == "postgresql://u:p@db/x" and s.port == 9000
    assert s.trusted_proxies == ["10.0.0.0/8", "127.0.0.1"] and s.rate_limit_events_per_min == 120


def test_a_config_file_may_not_hold_secrets(tmp_path):
    f = tmp_path / "c.json"
    f.write_text(json.dumps({"port": 9100, "rate_limit_events_per_min": 90}))
    assert load_settings(config_file=f, environ={}).port == 9100
    for bad in ({"admin_token": "x"}, {"tokens": {"a": "b"}}, {"dsn": "postgresql://u:p@h/db"}, {"token": "x"}):
        f.write_text(json.dumps(bad))
        with pytest.raises(ValueError, match="secret"):
            load_settings(config_file=f, environ={})
    f.write_text(json.dumps({"no_such_setting": 1}))
    with pytest.raises(ValueError, match="unknown"):
        load_settings(config_file=f, environ={})


def test_invalid_settings_are_refused_with_a_clear_message():
    for env in ({"COUNCIL_STORE": "mongo"}, {"COUNCIL_PORT": "99999"}, {"COUNCIL_RATE_EVENTS_PER_MIN": "-1"},
                {"COUNCIL_STORE": "postgres"}):  # postgres needs a DSN
        with pytest.raises(ValueError):
            load_settings(environ=env)


def test_default_bind_is_localhost_and_a_public_bind_requires_a_flag():
    for ok in ("127.0.0.1", "localhost", "::1", "192.168.0.210", "10.1.2.3", "172.20.10.253"):
        check_bind(ok, allow_public=False)  # loopback and private networks are fine
    for risky in ("0.0.0.0", "::", "8.8.8.8", "203.0.113.5", "council.example.com"):
        with pytest.raises(ValueError, match="allow-public"):
            check_bind(risky, allow_public=False)
        check_bind(risky, allow_public=True)


def test_authentication_cost_does_not_grow_with_the_number_of_tokens(tmp_path):
    import time as _t

    def per_request_ms(n_tokens: int, name: str) -> float:
        tokens = {f"tok-{i}-" + "x" * 30: f"user{i}" for i in range(n_tokens)}
        tokens["tok-real"] = "alice"
        (tmp_path / name).mkdir()
        app = create_app(SqliteRoomStore(tmp_path / name / "m.db"), tokens, tmp_path / name / "b", settings=ServerSettings())
        c = TestClient(app)
        c.get("/v1/whoami", headers=auth("tok-real"))  # warm up
        t0 = _t.perf_counter()
        for _ in range(200):
            assert c.get("/v1/whoami", headers=auth("tok-real")).status_code == 200
        cost = (_t.perf_counter() - t0) / 200 * 1000
        assert c.get("/v1/whoami", headers=auth("tok-wrong")).status_code == 401
        assert c.get("/v1/whoami", headers=auth("tok-real ")).status_code == 401  # near misses do not match
        return cost

    few, many = per_request_ms(10, "few"), per_request_ms(5000, "many")
    # the property is that the token count does not matter, not an absolute speed (a Windows test client alone takes ~10 ms)
    assert many < few * 2 + 1.0, (few, many)


# ---- a slow database must not stall every other request ------------------------------------------------------------------
def _slow_store(tmp_path, delay=0.3):
    import time as _t

    class Slow(SqliteRoomStore):
        def read_after(self, room_id, after_seq, limit=500):
            _t.sleep(delay)  # a blocking database call, like a real driver's
            return super().read_after(room_id, after_seq, limit)

        def append(self, draft):
            _t.sleep(delay)
            return super().append(draft)

    return Slow(tmp_path / "slow.db")


def test_concurrent_reads_overlap_instead_of_queueing_behind_a_slow_database(tmp_path):
    import asyncio
    import time as _t
    import httpx

    store = _slow_store(tmp_path)
    app = create_app(store, TOK, tmp_path / "b", settings=ServerSettings(rate_limit_ip_per_min=10**8, rate_limit_ip_burst=10**8))
    store.create_room("r1", "alice")

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            t0 = _t.perf_counter()
            rs = await asyncio.gather(*[c.get("/v1/rooms/r1/events?after_seq=0", headers=auth()) for _ in range(10)])
            return _t.perf_counter() - t0, [r.status_code for r in rs]

    took, codes = asyncio.run(go())
    assert codes == [200] * 10
    assert took < 1.5  # ten 0.3 s calls one after another would take 3 s


def test_concurrent_appends_overlap_too(tmp_path):
    import asyncio
    import time as _t
    import httpx

    store = _slow_store(tmp_path)
    app = create_app(store, TOK, tmp_path / "b", settings=ServerSettings(rate_limit_ip_per_min=10**8, rate_limit_ip_burst=10**8,
                                                                         rate_limit_events_per_min=10**8, rate_limit_events_burst=10**8))
    for i in range(10):
        store.create_room(f"r{i}", "alice")

    def post(c, i):
        return c.post(f"/v1/rooms/r{i}/events", headers=auth(), json={
            "type": "question", "client_event_id": f"c{i}", "thread_id": "room", "reply_to": None, "sender_participant": "alice",
            "sender_agent": None, "body": {"text": "x", "kind": "chat"}})

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            t0 = _t.perf_counter()
            rs = await asyncio.gather(*[post(c, i) for i in range(10)])
            return _t.perf_counter() - t0, [r.status_code for r in rs]

    took, codes = asyncio.run(go())
    assert codes == [200] * 10 and took < 1.5


# ---- operating knobs found by measuring: worker threads and how much to log ---------------------------------------------------
def test_threads_and_request_logging_are_settings_with_validation():
    s = load_settings(environ={})
    assert s.threads == 16 and s.log_requests == "all"
    s = load_settings(environ={"COUNCIL_THREADS": "6", "COUNCIL_LOG_REQUESTS": "errors"})
    assert s.threads == 6 and s.log_requests == "errors"
    for env in ({"COUNCIL_THREADS": "0"}, {"COUNCIL_LOG_REQUESTS": "verbose"}, {"COUNCIL_THREADS": "x"}):
        with pytest.raises(ValueError):
            load_settings(environ=env)


def test_errors_only_logging_skips_successful_requests_but_keeps_failures(tmp_path, caplog):
    c, _ = make(tmp_path, settings={"log_requests": "errors"})
    with caplog.at_level(logging.INFO, logger="council.server"):
        c.post("/v1/rooms/r1", json={"host": "alice"}, headers=auth())
        c.get("/v1/rooms/r1/events?after_seq=0", headers=auth())
        c.get("/v1/whoami", headers=auth("wrong"))
    lines = [json.loads(r.getMessage()) for r in caplog.records if r.name == "council.server"]
    assert [rec["status"] for rec in lines] == [401]  # the 401 is logged; the successful calls are not
    c2, _ = make(tmp_path / "off", settings={"log_requests": "off"}) if (tmp_path / "off").mkdir() is None else (None, None)
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="council.server"):
        c2.get("/v1/whoami", headers=auth("wrong"))
    assert not [r for r in caplog.records if r.name == "council.server"]


def test_the_worker_thread_limit_is_applied_when_the_app_starts(tmp_path):
    import anyio.to_thread
    c, _ = make(tmp_path, settings={"threads": 5})
    with c:  # starts the app (runs its startup)
        seen = {}

        async def look():
            seen["n"] = anyio.to_thread.current_default_thread_limiter().total_tokens

        c.portal.call(look)
    assert seen["n"] == 5


def test_large_json_answers_are_gzipped_and_bundles_are_not(tmp_path):
    import hashlib

    store = SqliteRoomStore(tmp_path / "g.db")
    store.create_room("r1", "alice")
    from aletheore.council.protocol import EventDraft, EventType

    for i in range(40):
        store.append(EventDraft("r1", f"m{i}", "t", EventType.CLAIM, "alice", None, None, {"text": "compressible " * 40}))
    c = TestClient(create_app(store, {"tok": "alice"}, tmp_path / "b"))
    page = c.get("/v1/rooms/r1/events", headers={**auth("tok"), "accept-encoding": "gzip"})
    assert page.headers.get("content-encoding") == "gzip" and len(page.json()["events"]) == 40  # smaller on the wire, same content
    assert c.get("/v1/rooms/r1/events", headers={**auth("tok"), "accept-encoding": "identity"}).headers.get("content-encoding") is None
    data = b"b" * 5000
    sha = hashlib.sha256(data).hexdigest()
    assert c.put(f"/v1/rooms/r1/bundles/{sha}", content=data, headers=auth("tok")).status_code == 200
    got = c.get(f"/v1/rooms/r1/bundles/{sha}", headers={**auth("tok"), "accept-encoding": "gzip"})
    assert got.headers.get("content-encoding") is None and got.content == data  # already-compressed material is not recompressed
