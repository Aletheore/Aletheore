import asyncio
import json
import random

import httpx
import pytest

from aletheore.council.server import create_app
from aletheore.council.server_config import ServerSettings
from aletheore.council.sqlite_store import SqliteRoomStore
from tests.council.loadtest.run import LatencyStats, LoadConfig, make_tokens, pick_size, plan_rooms, run_load, write_results


def server(tmp_path, settings=None, users=6, **kw):
    tokens = make_tokens(users)
    store = SqliteRoomStore(tmp_path / "lt.db")
    app = create_app(store, {tok: name for name, tok in tokens.items()}, tmp_path / "b", settings=settings,
                     max_rooms_per_host=1000, **kw)
    return store, app, tokens


def cfg(tokens, **kw):
    base = dict(server="http://test", rooms=3, participants=3, events_per_min=300.0, duration_s=2.0, poll_s=0.1, tokens=tokens,
                users=len(tokens), seed=7, sample_interval_s=0.5)
    base.update(kw)
    return LoadConfig(**base)


def run(config, app=None, transport=None):
    return asyncio.run(run_load(config, transport=transport or httpx.ASGITransport(app=app)))


def test_harness_creates_rooms_posts_and_measures_delivery_delay(tmp_path):
    store, app, tokens = server(tmp_path)
    res = run(cfg(tokens), app)
    assert res["rooms_created"] == 3 and res["setup_errors"] == 0
    assert res["events_posted"] >= 10
    assert res["append"]["count"] == res["events_posted"]
    assert res["events_seen"] > 0 and res["delivery"]["count"] > 0
    assert res["delivery"]["p50_ms"] < 1500  # polled every 0.1 s, in-process
    assert sum(res["errors"].values()) == 0 and res["rate_limited"] == 0
    stored = sum(len(store.read_after(r, 0, limit=10000)) for r in [x.room_id for u in ["lt-user-0"] for x in store.list_rooms(u)])
    assert stored >= 1  # the posts really reached the store


def test_every_member_sees_every_other_members_events(tmp_path):
    store, app, tokens = server(tmp_path)
    res = run(cfg(tokens, rooms=1, participants=3, duration_s=2.0), app)
    # each posted event is delivered to the 2 other members (readers keep polling to the end, so allow a little in flight)
    assert res["delivery"]["count"] >= 2 * res["events_posted"] * 0.8


def test_harness_reports_errors_and_429s_separately(tmp_path):
    store, app, tokens = server(tmp_path, settings=ServerSettings(rate_limit_events_per_min=60, rate_limit_events_burst=2,
                                                                  rate_limit_ip_per_min=100000, rate_limit_ip_burst=100000,
                                                                  auth_fail_per_min=100000, auth_fail_burst=100000, max_rooms_per_host=1000))
    res = run(cfg(tokens, rooms=2, participants=3, events_per_min=600.0), app)
    assert res["errors"]["429"] > 0 and res["rate_limited"] == res["errors"]["429"]
    assert res["errors"]["5xx"] == 0 and res["errors"]["transport"] == 0
    assert res["events_posted"] >= 1  # some got through before the limit


def test_server_errors_and_connection_failures_are_counted_by_kind(tmp_path):
    store, app, tokens = server(tmp_path)
    real = httpx.ASGITransport(app=app)

    class Chaotic(httpx.AsyncBaseTransport):
        n = 0

        async def handle_async_request(self, request):
            if request.method == "POST" and request.url.path.endswith("/events"):
                Chaotic.n += 1
                if Chaotic.n % 3 == 1:
                    return httpx.Response(503, json={"detail": "storage temporarily unavailable; try again"}, request=request)
                if Chaotic.n % 3 == 2:
                    raise httpx.ConnectError("boom", request=request)
            return await real.handle_async_request(request)

    res = run(cfg(tokens, rooms=1, participants=3), transport=Chaotic())
    assert res["errors"]["5xx"] > 0 and res["errors"]["transport"] > 0 and res["errors"]["429"] == 0
    assert res["append"]["errors"] == res["errors"]["5xx"] + res["errors"]["transport"]


def test_results_json_has_the_documented_fields_and_no_secrets(tmp_path):
    store, app, tokens = server(tmp_path)
    res = run(cfg(tokens, duration_s=1.0), app)
    out = tmp_path / "r.json"
    write_results(res, out)
    data = json.loads(out.read_text())
    for key in ("config", "started_at", "duration_s", "rooms_created", "setup_errors", "events_posted", "events_seen", "append",
                "read", "delivery", "errors", "rate_limited", "requests_per_s", "harness_busy_pct", "resource_samples", "notes"):
        assert key in data, key
    for section in ("append", "read", "delivery"):
        assert {"count", "p50_ms", "p95_ms", "p99_ms", "max_ms"} <= set(data[section])
    blob = out.read_text()
    assert not any(t in blob for t in tokens.values())  # no token ever lands in a results file
    assert data["config"]["rooms"] == 3 and "tokens" not in data["config"]


def test_harness_stops_on_time(tmp_path):
    import time
    store, app, tokens = server(tmp_path)
    t0 = time.monotonic()
    run(cfg(tokens, duration_s=1.5), app)
    assert time.monotonic() - t0 < 6.0  # duration plus setup and shutdown, not minutes


def test_a_busy_harness_is_flagged_so_nobody_trusts_a_saturated_test(tmp_path):
    store, app, tokens = server(tmp_path)
    res = run(cfg(tokens, duration_s=1.0), app)
    assert 0 <= res["harness_busy_pct"] <= 400
    assert isinstance(res["notes"], list)


# ---- pure pieces ------------------------------------------------------------------------------------------------------
def test_latency_stats_percentiles_are_close_to_exact():
    s = LatencyStats()
    for ms in range(1, 1001):
        s.add(ms / 1000.0)
    d = s.to_dict()
    assert d["count"] == 1000
    assert 470 <= d["p50_ms"] <= 530 and 920 <= d["p95_ms"] <= 1000 and 960 <= d["p99_ms"] <= 1040 and d["max_ms"] == pytest.approx(1000, abs=1)
    assert LatencyStats().to_dict()["p50_ms"] == 0.0


def test_message_size_mix_is_respected():
    rng = random.Random(1)
    sizes = [pick_size(rng) for _ in range(20000)]
    small = sum(1 for x in sizes if x < 1000) / len(sizes)
    medium = sum(1 for x in sizes if 1000 <= x < 10000) / len(sizes)
    large = sum(1 for x in sizes if x >= 10000) / len(sizes)
    assert 0.82 < small < 0.88 and 0.12 < medium < 0.16 and 0.003 < large < 0.02
    assert max(sizes) <= 60000


def test_rooms_share_a_user_pool_with_distinct_members_and_the_first_is_host():
    rooms = plan_rooms(rooms=10, participants=4, users=[f"u{i}" for i in range(6)], run_id="x")
    assert len(rooms) == 10
    for r in rooms:
        assert len(set(r.members)) == 4 and r.host == r.members[0]
    assert len({r.room_id for r in rooms}) == 10
    with pytest.raises(ValueError, match="users"):
        plan_rooms(rooms=2, participants=5, users=["a", "b"], run_id="x")


def test_make_tokens_gives_unique_unguessable_tokens():
    t = make_tokens(50)
    assert len(t) == 50 and len(set(t.values())) == 50 and all(len(v) >= 32 for v in t.values())


def test_resource_sampler_measures_cpu_over_the_interval_not_the_process_lifetime():
    import subprocess
    import sys
    import time
    from tests.council.loadtest.run import sample_resources

    child = subprocess.Popen([sys.executable, "-c",
                              "import time\ntime.sleep(2.0)\nt=time.time()\nwhile time.time()-t<3.0: sum(i*i for i in range(2000))"])
    try:
        async def go():
            time.sleep(2.2)  # the child is now burning CPU, after two idle seconds of its life
            await sample_resources(child.pid, None)  # primes the measurement
            await asyncio.sleep(1.0)
            return await sample_resources(child.pid, None)

        sample = asyncio.run(go())
    finally:
        child.kill()
        child.wait()
    assert sample is not None and sample["rss_mb"] > 1
    # busy for the whole interval (about 100%); a lifetime average over its 3+ seconds of life would read about 30%
    assert sample["cpu_pct"] > 70
    assert asyncio.run(sample_resources(None, None)) is None
    assert asyncio.run(sample_resources(2_000_000_000, None)) is None  # a process that does not exist is not an error


# ---- several test processes ------------------------------------------------------------------------------------------------
def test_rooms_are_split_evenly_across_processes():
    from tests.council.loadtest.run import split_rooms
    assert split_rooms(10, 3) == [4, 3, 3] and sum(split_rooms(1000, 7)) == 1000 and split_rooms(2, 5) == [1, 1]


def test_merging_results_adds_counts_and_recomputes_percentiles_from_the_combined_histogram():
    from tests.council.loadtest.run import LatencyStats, merge_results

    def part(ms_values, **extra):
        a = LatencyStats()
        for ms in ms_values:
            a.add(ms / 1000)
        base = {"config": {"rooms": 5}, "started_at": "t", "duration_s": 10.0, "rooms_created": 5, "setup_errors": 0, "events_posted": 3,
                "events_seen": 7, "append": {**a.to_dict(), "errors": 1}, "read": {**LatencyStats().to_dict(), "errors": 0},
                "delivery": LatencyStats().to_dict(), "errors": {"429": 1, "5xx": 0, "4xx_other": 0, "transport": 0}, "rate_limited": 1,
                "requests_per_s": 50.0, "harness_busy_pct": 40.0, "resource_samples": [{"t": 1, "cpu_pct": 10, "rss_mb": 50}], "notes": ["n"],
                "_hist": {"append": a.counts, "read": {}, "delivery": {}}}
        base.update(extra)
        return base

    merged = merge_results([part(range(1, 501)), part(range(501, 1001))])
    assert merged["rooms_created"] == 10 and merged["events_posted"] == 6 and merged["events_seen"] == 14
    assert merged["errors"]["429"] == 2 and merged["rate_limited"] == 2 and merged["append"]["errors"] == 2
    assert merged["append"]["count"] == 1000 and 470 <= merged["append"]["p50_ms"] <= 530  # of all 1000 values, not an average of two medians
    assert merged["requests_per_s"] == 100.0 and merged["harness_busy_pct"] == 40.0 and merged["harness_procs"] == 2
    assert "_hist" not in merged and merged["config"]["rooms"] == 10
    assert merged["notes"] == ["n"]


def test_multi_process_run_against_a_real_server(tmp_path):
    import threading
    import time
    import uvicorn
    from tests.council.loadtest.run import run_load_multi

    tokens = make_tokens(8)
    store = SqliteRoomStore(tmp_path / "mp.db")
    app = create_app(store, {t: n for n, t in tokens.items()}, tmp_path / "b", max_rooms_per_host=1000)
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
    srv = uvicorn.Server(config)
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    port = srv.servers[0].sockets[0].getsockname()[1]
    try:
        res = run_load_multi(cfg(tokens, server=f"http://127.0.0.1:{port}", rooms=6, participants=3, duration_s=2.0, events_per_min=120.0), procs=2)
    finally:
        srv.should_exit = True
        t.join(5)
    assert res["rooms_created"] == 6 and res["harness_procs"] == 2 and res["events_posted"] > 0
    assert res["append"]["count"] == res["events_posted"] and sum(res["errors"].values()) == 0


def test_rooms_whose_setup_failed_are_not_driven_so_they_do_not_pollute_the_error_counts(tmp_path):
    tokens = make_tokens(3)
    store = SqliteRoomStore(tmp_path / "f.db")
    # one host for every room (3 users, 3 per room) and a limit of 2 rooms per host: only 2 of 6 rooms can exist
    app = create_app(store, {t: n for n, t in tokens.items()}, tmp_path / "b", max_rooms_per_host=2)
    res = run(cfg(tokens, rooms=6, participants=3, duration_s=1.5), app)
    assert res["rooms_created"] == 2 and res["setup_errors"] == 4
    assert res["errors"]["4xx_other"] == 0  # no traffic was sent to rooms that were never created
    assert any("setup" in n for n in res["notes"])


# ---- a light HTTP driver for real runs ------------------------------------------------------------------------------------
def _real_server(tmp_path, tokens):
    import threading
    import time
    import uvicorn

    store = SqliteRoomStore(tmp_path / "real.db")
    app = create_app(store, {t: n for n, t in tokens.items()}, tmp_path / "b", max_rooms_per_host=1000)
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    return srv, t, srv.servers[0].sockets[0].getsockname()[1]


def test_the_aiohttp_driver_gives_the_same_kind_of_result_as_the_httpx_one(tmp_path):
    pytest.importorskip("aiohttp")
    tokens = make_tokens(6)
    srv, t, port = _real_server(tmp_path, tokens)
    try:
        res = asyncio.run(run_load(cfg(tokens, server=f"http://127.0.0.1:{port}", rooms=3, participants=3, duration_s=2.0, driver="aiohttp")))
    finally:
        srv.should_exit = True
        t.join(5)
    assert res["config"]["driver"] == "aiohttp"
    assert res["rooms_created"] == 3 and res["events_posted"] > 5 and res["delivery"]["count"] > 0
    assert sum(res["errors"].values()) == 0 and res["append"]["count"] == res["events_posted"]


def test_connection_failures_are_counted_as_transport_errors_by_the_aiohttp_driver(tmp_path):
    pytest.importorskip("aiohttp")
    tokens = make_tokens(4)
    res = asyncio.run(run_load(cfg(tokens, server="http://127.0.0.1:1", rooms=1, participants=3, duration_s=1.0, driver="aiohttp")))
    assert res["rooms_created"] == 0 and res["setup_errors"] >= 1  # nothing to talk to: setup fails cleanly, no crash
