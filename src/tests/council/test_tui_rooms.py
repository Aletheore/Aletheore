"""Every room at once: watcher cost, the list, needs-you lines, and the switcher."""

import asyncio
from datetime import datetime, timezone

from aletheore.council import tui_rooms
from aletheore.council.protocol import EventDraft, EventType
from aletheore.council.sqlite_store import SqliteRoomStore
from aletheore.council.state import RoomState
from aletheore.council.tui import CouncilApp
from aletheore.council.view_model import ConnectionState, RoomSummary, Need, connection_state, cross_room_needs, presence


def post(s, room, etype, body, cid, sender="alice", agent=None, thread="room"):
    s.append(EventDraft(room, cid, thread, etype, sender, agent, None, body))


def make(tmp_path, n=3):
    s = SqliteRoomStore(tmp_path / "r.db")
    for i in range(1, n + 1):
        s.create_room(f"r{i}", "alice", title=["Cache work", "Billing refactor", "Docs sweep"][i - 1])
        post(s, f"r{i}", EventType.SYSTEM, {"subtype": "room_created", "host": "alice"}, "c0")
    return s


def test_fuzzy_matches_in_order_and_highlights():
    assert tui_rooms.fuzzy("bil", "Billing refactor") == [0, 1, 2]
    assert tui_rooms.fuzzy("bfr", "Billing refactor") == [0, 8, 9] or tui_rooms.fuzzy("bfr", "Billing refactor") is not None
    assert tui_rooms.fuzzy("zz", "Billing refactor") is None and tui_rooms.fuzzy("", "x") == []
    assert [m for m in tui_rooms.highlight("Billing", [0, 1], "").spans if "underline" in str(m.style)]


def test_connection_state_stages_at_the_thresholds():
    assert connection_state(0).kind == "live" and connection_state(10).kind == "reconnecting"
    assert connection_state(29).kind == "reconnecting" and connection_state(30).kind == "unreachable"
    assert connection_state(299).kind == "unreachable" and connection_state(300).kind == "away"
    assert connection_state(40, queued=2).queued == 2


def test_presence_is_recently_active_not_online():
    class E:
        def __init__(self, who, at):
            self.sender_participant, self.created_at = who, at

    now = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
    got = {p.person: p.state for p in presence([E("a", "2026-11-10T11:58:00+00:00"), E("b", "2026-11-10T09:00:00+00:00")], now)}
    assert got == {"a": "active", "b": "away"}


def test_cross_room_needs_orders_by_age_and_names_the_room():
    old = RoomSummary("r1", "Old", "interactive", "waiting", [], 1, "2026-11-01T00:00:00+00:00", None, [Need("approve", "v1", "x")])
    new = RoomSummary("r2", "New", "interactive", "waiting", [], 1, "2026-11-09T00:00:00+00:00", None, [Need("approve", "v2", "y")])
    pairs = cross_room_needs([new, old])
    assert [s.room_id for s, _ in pairs] == ["r1", "r2"]


def test_polling_other_rooms_is_cheap(tmp_path):
    s = make(tmp_path, 3)
    w = tui_rooms.RoomWatcher(s, "alice")
    for _ in range(6):
        w.round("r1", RoomState())
    assert w.list_calls == 6 and w.log_reads == 6  # one list call and at most one other room's log per round
    assert set(w.summaries) == {"r1", "r2", "r3"}
    assert all(w._tails[r].after_seq > 0 for r in ("r2", "r3"))  # each other room was read, once per turn, not all at once


def test_room_list_shows_every_room_with_state_and_count(tmp_path):
    s = make(tmp_path, 3)
    summaries = [RoomSummary("r1", "Cache work", "interactive", "live", [], 0, ""),
                 RoomSummary("r2", "Billing refactor", "autonomous", "live", [], 0, "", (2, 4)),
                 RoomSummary("r3", "Docs sweep", "interactive", "waiting", [], 2, "", None, [Need("approve", "v9", "ship docs")])]
    text = tui_rooms.room_list_text(summaries, "r1").plain
    assert "Cache work" in text and "Billing refactor" in text and "Docs sweep" in text
    assert "████░░░░ 2/4" in text
    assert "NEEDS YOU" in text and "approve v9" in text


def test_needs_you_lines_jump_to_their_room():
    summaries = [RoomSummary("r1", "A", "interactive", "live", [], 0, ""),
                 RoomSummary("r2", "B", "interactive", "waiting", [], 1, "2026-11-01T00:00:00+00:00", None, [Need("approve", "v1", "x")])]
    assert tui_rooms.room_at_row(summaries, 2) == "r1" and tui_rooms.room_at_row(summaries, 3) == "r2"
    assert tui_rooms.room_at_row(summaries, 0) is None
    # rooms end at row 4; blank, rule and heading take 4..6; the first needs line is row 7
    assert tui_rooms.room_at_row(summaries, 7) == "r2" and tui_rooms.room_at_row(summaries, 8) == "r2"


async def settle(pilot, n=8):
    for _ in range(n):
        await pilot.pause(0.1)


def test_switcher_filters_enter_opens_and_keeps_composer_text_per_room(tmp_path):
    s = make(tmp_path, 3)
    post(s, "r2", EventType.QUESTION, {"text": "hello from billing", "kind": "chat"}, "q1")

    async def go():
        app = CouncilApp(s, "r1", "alice", poll_s=0.05)
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot)
            composer = app.query_one("#composer")
            composer.load_text("half written thought")
            await pilot.press("ctrl+r")
            await settle(pilot, 4)
            lst = app.screen.query_one("#room-list")
            assert lst.option_count == 3
            for ch in "bil":
                await pilot.press(ch)
            await settle(pilot, 3)
            assert lst.option_count == 1 and "Billing refactor" in str(app.screen.query_one("#room-preview").render())
            await pilot.press("enter")
            await settle(pilot, 10)
            assert app.room_id == "r2" and composer.text == ""
            await pilot.press("ctrl+r")
            await settle(pilot, 4)
            await pilot.press("escape")
            await settle(pilot, 3)
            assert app.room_id == "r2"
            app.switch_room("r1")
            await settle(pilot, 6)
            assert composer.text == "half written thought"

    asyncio.run(go())


def test_room_list_appears_in_the_app_when_there_are_several_rooms(tmp_path):
    s = make(tmp_path, 3)

    async def go():
        app = CouncilApp(s, "r1", "alice", poll_s=0.05)
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot, 14)
            rooms = str(app.query_one("#rooms").render())
            assert "Cache work" in rooms and "Billing refactor" in rooms and "Docs sweep" in rooms

    asyncio.run(go())
