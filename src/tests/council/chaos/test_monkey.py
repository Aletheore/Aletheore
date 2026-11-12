"""The chaos monkey: several clients doing random things while the network is cut, stalled, slowed and made lossy at random.

Invariants, checked after everything heals:
  1. every message a client was told is sent (or queued, then delivered) is stored exactly once;
  2. the stored log has no gaps and is in order, and each client's own messages keep their order;
  3. every client reads the identical log and projects the identical RoomState;
  4. no exception escapes.
The seed is part of every failure message, so a failure replays with COUNCIL_CHAOS_SEEDS=<seed>."""

import os
import random
import time

import pytest

from aletheore.council.outbox import OutboxStore
from aletheore.council.protocol import EventDraft, EventType
from aletheore.council.remote_store import RemoteRoomStore
from aletheore.council.state import project
from aletheore.council.store import read_all
from aletheore.council.terminal import RoomTail
from tests.council.chaos.live import LiveServer
from tests.council.chaos.proxy import FaultProxy

SEEDS = [int(s) for s in os.environ.get("COUNCIL_CHAOS_SEEDS", "0,1,2,3,4").split(",")]
NAMES = {"alice": "tok-a", "bob": "tok-b", "carol": "tok-c", "dave": "tok-d"}


@pytest.mark.parametrize("seed", SEEDS)
def test_random_clients_random_cuts_converge(tmp_path, seed):
    rng = random.Random(seed)
    server = LiveServer(tmp_path).start()
    proxy = FaultProxy(("127.0.0.1", server.port), seed=seed)
    port = proxy.start()
    admin = RemoteRoomStore(server.url, "tok-a", backoff_s=0.01)
    admin.create_room("r1", "alice", title="Monkey")
    for who in ("bob", "carol", "dave"):
        admin.add_member("r1", who)
    admin.append(EventDraft("r1", "created", "room", EventType.SYSTEM, "alice", None, None, {"subtype": "room_created", "host": "alice"}))
    clients = {n: OutboxStore(RemoteRoomStore(f"http://127.0.0.1:{port}", t, timeout_s=0.6, backoff_s=0.005), tmp_path / f"{n}.json") for n, t in NAMES.items()}
    tails = {n: RoomTail(RemoteRoomStore(f"http://127.0.0.1:{port}", t, timeout_s=0.6, backoff_s=0.005), "r1") for n, t in NAMES.items()}
    sent: dict[str, list[str]] = {n: [] for n in NAMES}
    errors: list[str] = []
    try:
        for step in range(120):
            action = rng.choice(["post", "post", "post", "ask", "approve", "poll", "cut", "blackhole", "delay", "loss", "heal", "heal"])
            who = rng.choice(list(NAMES))
            try:
                if action in ("post", "ask", "approve"):
                    cid = f"{who}-{step}"
                    etype, body = {"post": (EventType.CLAIM, {"text": f"msg {step}"}),
                                   "ask": (EventType.QUESTION, {"text": f"question {step}", "kind": "chat"}),
                                   "approve": (EventType.SYSTEM, {"subtype": "note", "text": f"step {step}"})}[action]
                    clients[who].append(EventDraft("r1", cid, "t", etype, who, None, None, body))
                    sent[who].append(cid)
                elif action == "poll":
                    tails[who].poll()
                elif action == "cut":
                    proxy.cut_all()
                elif action == "blackhole":
                    proxy.blackhole()
                elif action == "delay":
                    proxy.delay(rng.choice([5, 20, 60]))
                elif action == "loss":
                    proxy.loss(rng.choice([0.2, 0.5]))
                else:
                    proxy.heal()
            except (ConnectionError, TimeoutError):
                pass  # an outage is expected; what matters is what is true after it
            except Exception as exc:  # noqa: BLE001
                errors.append(f"seed {seed} step {step} {action}: {type(exc).__name__}: {exc}")
        proxy.heal()
        proxy.cut_all()
        time.sleep(0.2)
        for _ in range(200):  # deliver everything that was queued
            for c in clients.values():
                c.flush()
            if not any(c.queued() for c in clients.values()):
                break
            time.sleep(0.05)
        assert not errors, errors
        for name, c in clients.items():
            assert c.queued() == [], f"seed {seed}: {name} still has undelivered messages"
        stored = read_all(admin, "r1", 0)
        assert [e.seq for e in stored] == list(range(1, len(stored) + 1)), f"seed {seed}: gaps or disorder in the log"
        stored_ids = [e.client_event_id for e in stored if e.client_event_id != "created"]
        assert len(stored_ids) == len(set(stored_ids)), f"seed {seed}: a message was stored twice"
        assert set(stored_ids) == {cid for ids in sent.values() for cid in ids}, f"seed {seed}: a sent message is missing or invented"
        for name, ids in sent.items():
            mine = [e.client_event_id for e in stored if e.sender_participant == name and e.client_event_id in set(ids)]
            assert mine == ids, f"seed {seed}: {name}'s messages are out of order"
        views = {}
        for name, tail in tails.items():
            for _ in range(5):
                tail.poll()
            views[name] = [(e.seq, e.client_event_id) for e in tail.all]
        assert all(v == views["alice"] for v in views.values()), f"seed {seed}: clients disagree about the log"
        states = {n: project(t.all, host="alice") for n, t in tails.items()}
        assert all(s == states["alice"] for s in states.values()), f"seed {seed}: clients disagree about the room state"
    finally:
        proxy.stop()
        server.stop()
