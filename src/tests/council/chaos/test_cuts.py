"""Network cuts, slow and silent peers. Real sockets, a real server, a proxy that breaks connections on purpose.
Each test states the invariant it protects."""

import hashlib
import threading
import time

import httpx
import pytest

from aletheore.council.protocol import EventDraft, EventType
from aletheore.council.remote_store import RemoteRoomStore
from aletheore.council.state import project
from aletheore.council.store import read_all
from aletheore.council.terminal import RoomTail
from tests.council.chaos.live import LiveServer
from tests.council.chaos.proxy import FaultProxy


def draft(cid, text="x", room="r1", who="bob"):
    return EventDraft(room, cid, "t", EventType.CLAIM, who, None, None, {"text": text})


@pytest.fixture
def world(tmp_path):
    server = LiveServer(tmp_path).start()
    proxy = FaultProxy(("127.0.0.1", server.port))
    port = proxy.start()
    direct = RemoteRoomStore(server.url, "tok-a", timeout_s=3, backoff_s=0.01)
    via = lambda token="tok-b", timeout=3: RemoteRoomStore(f"http://127.0.0.1:{port}", token, timeout_s=timeout, backoff_s=0.01)  # noqa: E731
    direct.create_room("r1", "alice", title="Chaos")
    direct.add_member("r1", "bob")
    direct.add_member("r1", "carol")
    yield server, proxy, direct, via
    proxy.stop()
    server.stop()


def test_append_cut_after_the_request_but_before_the_reply_is_retried_without_duplicating(world):
    server, proxy, direct, via = world
    proxy.cut_after_bytes(1, direction="down", once=True)  # the server stores the event, then the reply is cut off
    stored = via().append(draft("only-once", "hello"))
    assert stored.body["text"] == "hello" and stored.seq == 1
    events = direct.read_after("r1", 0)
    assert [e.client_event_id for e in events] == ["only-once"]  # the retry found the event the server already had


def test_reader_cut_mid_page_resumes_from_its_last_seq_with_no_gaps_or_repeats(world):
    server, proxy, direct, via = world
    for i in range(300):
        direct.append(draft(f"m{i}", f"line {i} " + "x" * 80, who="alice"))
    reader = via("tok-b")
    proxy.cut_after_bytes(3000, direction="down", once=True)  # dies part way through a page
    got = read_all(reader, "r1", 0)
    assert [e.seq for e in got] == list(range(1, 301))  # no gap, no repeat
    tail = RoomTail(via("tok-c"), "r1")
    proxy.cut_after_bytes(2000, direction="down", once=True)
    tail.poll()
    proxy.cut_after_bytes(500, direction="down", once=True)
    tail.poll()
    assert [e.seq for e in tail.all] == list(range(1, 301))


def test_a_client_offline_for_a_while_catches_up_to_the_same_state_as_one_that_never_left(world):
    server, proxy, direct, via = world
    steady, absent = RoomTail(direct, "r1"), RoomTail(via("tok-c"), "r1")
    direct.append(EventDraft("r1", "created", "room", EventType.SYSTEM, "alice", None, None, {"subtype": "room_created", "host": "alice"}))
    steady.poll()
    absent.poll()
    proxy.blackhole()  # the second client drops off the network
    for i in range(80):
        direct.append(draft(f"w{i}", "while you were away", who="alice"))
        if i % 10 == 0:
            steady.poll()
    steady.poll()
    proxy.heal()
    proxy.cut_all()
    absent.poll()
    assert [e.seq for e in absent.all] == [e.seq for e in steady.all]
    assert project(absent.all, host="alice") == project(steady.all, host="alice")  # the same RoomState


def test_blackholed_and_slow_clients_cannot_block_other_clients(world):
    server, proxy, direct, via = world
    proxy.blackhole()
    stuck = via("tok-b", timeout=2)
    results = []
    t = threading.Thread(target=lambda: results.append(_try(lambda: stuck.append(draft("stuck")))), daemon=True)
    t.start()
    started = time.time()
    for i in range(20):
        direct.append(draft(f"fast{i}", who="alice"))  # everyone else carries on at full speed
    assert time.time() - started < 2.0
    t.join(timeout=15)
    assert results and isinstance(results[0], ConnectionError)  # the blackholed client gives up cleanly, it does not hang forever
    proxy.heal()
    proxy.throttle(2000)  # a very slow reader
    slow = via("tok-c", timeout=20)
    t2 = threading.Thread(target=lambda: slow.read_after("r1", 0), daemon=True)
    t2.start()
    started = time.time()
    for i in range(20):
        direct.append(draft(f"fast2-{i}", who="alice"))
    assert time.time() - started < 2.0
    proxy.heal()


def _try(fn):
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        return exc


def test_upload_cut_mid_put_never_leaves_a_visible_partial_object(world, tmp_path):
    server, proxy, direct, via = world
    data = b"z" * 400_000
    sha = hashlib.sha256(data).hexdigest()
    proxy.cut_after_bytes(60_000, direction="up", once=True)  # the upload dies part way
    raw = httpx.Client(base_url=f"http://127.0.0.1:{proxy.port}", timeout=5, headers={"Authorization": "Bearer tok-a"})
    with pytest.raises(httpx.TransportError):
        raw.put(f"/v1/rooms/r1/bundles/{sha}", content=data)
    time.sleep(0.3)
    stored = list((server.bundles / "r1").glob("*")) if (server.bundles / "r1").exists() else []
    assert stored == []  # nothing visible, no half-written file left behind
    src = tmp_path / "b.bundle"
    src.write_bytes(data)
    proxy.cut_after_bytes(60_000, direction="up", once=True)
    via("tok-a").put_bundle("r1", sha, src)  # the client's own retry completes it
    assert (server.bundles / "r1" / f"{sha}.bundle").read_bytes() == data
