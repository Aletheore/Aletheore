"""The autonomous run view."""

import asyncio

from aletheore.council import tui_run
from aletheore.council.protocol import EventDraft, EventType
from aletheore.council.sqlite_store import SqliteRoomStore
from aletheore.council.state import project
from aletheore.council.tui import CouncilApp


def post(s, etype, body, cid, sender="alice", thread="room"):
    s.append(EventDraft("r1", cid, thread, etype, sender, None, None, body))


def make(tmp_path, mode="autonomous"):
    s = SqliteRoomStore(tmp_path / "r.db")
    s.create_room("r1", "alice", title="Billing", mode=mode)
    post(s, EventType.SYSTEM, {"subtype": "room_created", "host": "alice"}, "c0")
    post(s, EventType.SYSTEM, {"subtype": "area_defined", "name": "api", "globs": ["api/**"], "owner": "alice", "agent_id": "alice-codex"}, "c1")
    post(s, EventType.QUESTION, {"text": "make invoices idempotent", "goal": True, "kind": "prompt"}, "g1", thread="g-1")
    post(s, EventType.PROPOSAL, {"plan_id": "p1", "reply": "start", "tasks": [{"area": "api", "instruction": "add keys"}]}, "p1", thread="g-1")
    return s


def tl_of(s):
    ev = s.read_after("r1", 0)
    return tui_run.run_timeline(ev, project(ev, host="alice"))


def test_goal_card_and_timeline_follow_the_log(tmp_path):
    tl = tl_of(make(tmp_path))
    assert tl.goal == "make invoices idempotent" and len(tl.rounds) == 1 and tl.steps == 1 and not tl.finished
    text = tui_run.run_card_text(tl, 60, True).plain
    assert text.startswith("┌ GOAL  running") and "round 1" in text and "ctrl+x stops the run" in text and text.rstrip().endswith("└")
    assert "round 1 of 4 · 1 of 16 steps (default limits)" in text


def test_an_ordinary_room_has_no_run(tmp_path):
    s = SqliteRoomStore(tmp_path / "x.db")
    s.create_room("r1", "alice")
    post(s, EventType.SYSTEM, {"subtype": "room_created", "host": "alice"}, "c0")
    assert tl_of(s) is None


def test_a_finished_run_shows_its_note_and_no_stop_hint(tmp_path):
    s = make(tmp_path)
    post(s, EventType.SYSTEM, {"subtype": "room_done", "outcome": "done", "summary": "invoices are idempotent now"}, "d1")
    tl = tl_of(s)
    text = tui_run.run_card_text(tl, 60, True).plain
    assert "goal met" in text and "invoices are idempotent now" in text and "ctrl+x" not in text


async def settle(pilot, n=10):
    for _ in range(n):
        await pilot.pause(0.1)


def test_card_appears_in_the_app_and_ctrl_x_stops_only_for_the_host_after_one_ask(tmp_path):
    s = make(tmp_path)

    async def go():
        app = CouncilApp(s, "r1", "alice", poll_s=0.05)
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot, 10)
            assert len(app.query(".runcard")) == 1 and "make invoices idempotent" in str(app.query_one(".runcard").render())
            await pilot.press("ctrl+x")
            await settle(pilot, 4)
            assert not any(e.body.get("subtype") == "conductor_stopped" for e in s.read_after("r1", 0))  # the first press only asks
            await pilot.press("ctrl+x")
            await settle(pilot, 12)
            assert any(e.body.get("subtype") == "conductor_stopped" for e in s.read_after("r1", 0))

    asyncio.run(go())


def test_a_member_cannot_stop_the_run(tmp_path):
    s = make(tmp_path)
    s.add_member("r1", "bob")

    async def go():
        app = CouncilApp(s, "r1", "bob", poll_s=0.05)
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot, 8)
            await pilot.press("ctrl+x", "ctrl+x")
            await settle(pilot, 6)
            assert not any(e.body.get("subtype") == "conductor_stopped" for e in s.read_after("r1", 0))

    asyncio.run(go())
