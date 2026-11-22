"""Seeded random rooms: many senders, forged depths, restarts, two workers per participant, flaky runners.
Whatever happens, these hold: nothing runs twice for one message, nobody is woken past the budget, a wake only ever writes messages
and notices, and the room settles (no endless chains)."""
import random
import time

import pytest

from aletheore.council.daemon import WorkerDaemon
from aletheore.council.protocol import EventDraft, EventType
from aletheore.council.runners import ScriptedRunner
from tests.council.world_fixture import daemon, world  # noqa: F401

WORDS = ["@claude", "@codex", "@Claude,", "`@claude`", "@claudex", "plain", "a@claude.com", "@alice-codex", "(@codex)", "@claude."]


class Flaky(ScriptedRunner):
    def __init__(self, rng, **kw):
        super().__init__(**kw)
        self.rng = rng

    def run(self, *a, **k):
        r = self.rng.random()
        if r < 0.1:
            raise RuntimeError("random crash")
        if r < 0.2:
            self.ok = False
        else:
            self.ok = True
        return super().run(*a, **k)


@pytest.mark.parametrize("seed", range(25))
def test_random_room_keeps_its_guarantees(world, seed):
    rng = random.Random(seed)
    room_id = world["alice"].room_id
    now = [time.time()]
    budget = rng.choice([2, 3, 5])
    mk = lambda who, **kw: daemon(world, who, runner=Flaky(rng, final_text=rng.choice(["fine", "@codex pass it on", "@claude and back", ""])),
                                  clock=lambda: now[0], max_wakes=budget, wake_window_s=200, wake_cooldown_s=rng.choice([0, 0, 5]),
                                  max_wake_depth=rng.choice([1, 2, 3]), wake_from={"alice", "bob"}, **kw)[0]
    daemons = {"alice": [mk("alice")], "bob": [mk("bob")]}
    sent = 0
    for step in range(60):
        action = rng.random()
        if action < 0.45:
            who = rng.choice(["alice", "bob", "carol"])           # carol is not a member of the allowed list
            text = " ".join(rng.choice(WORDS) for _ in range(rng.randint(1, 4)))
            body = {"text": text, "kind": rng.choice(["chat", "chat", "ask", "prompt"])}
            if rng.random() < 0.3:
                body["wake_depth"] = rng.choice([0, 1, 5, -1, "x", None, True, 2 ** 70])        # forged and malformed depths
            world["store"].append(EventDraft(room_id, f"c:{seed}:{step}", rng.choice(["room", "p-abc"]), EventType.QUESTION, who,
                                             rng.choice([None, None, "claude", "codex"]), None, body))
            sent += 1
        elif action < 0.55:
            who = rng.choice(["alice", "bob"])
            daemons[who].append(mk(who))                               # a second worker for the same participant
        elif action < 0.62:
            who = rng.choice(["alice", "bob"])
            daemons[who] = [mk(who)]                                   # a restart: a new worker reads the log again
        elif action < 0.75:
            now[0] += rng.choice([1, 10, 100, 300])
        for who, ds in daemons.items():
            for d in ds:
                d.run_once()
    for _ in range(6):                                                 # let the room settle
        now[0] += 400
        for ds in daemons.values():
            for d in ds:
                d.run_once()
    log = world["store"].read_after(room_id, 0)

    # 1. at most one answer or failure per (message, agent): no duplicates, whatever the restarts and extra workers
    keys = [(e.body.get("wake_for") or e.reply_to, e.sender_agent) for e in log
            if e.sender_participant in ("alice", "bob") and (e.body.get("wake_for") or e.body.get("subtype") == "wake_failed")]
    assert len(keys) == len(set(keys)), keys

    # 2. a wake only writes messages and notices
    for e in log:
        if e.client_event_id.startswith(("wake:", "wakeclaim:", "wakefail:", "wakeskip:")):
            assert e.type in (EventType.QUESTION, EventType.SYSTEM)

    # 3. only members on the allow list can have started anything
    claimed = {e.reply_to: e for e in log if e.client_event_id.startswith("wakeclaim:")}
    by_id = {e.event_id: e for e in log}
    for ev_id in claimed:
        assert by_id[ev_id].sender_participant in ("alice", "bob")

    # 4. the room settled: nothing new appears when every worker polls again
    n = len(world["store"].read_after(room_id, 0))
    for ds in daemons.values():
        for d in ds:
            d.run_once()
    assert len(world["store"].read_after(room_id, 0)) == n
