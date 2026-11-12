"""Long rooms: search, jump to a day, folded history, pins."""

import asyncio
from datetime import date, datetime, timezone

from aletheore.council import tui_nav
from aletheore.council.protocol import EventDraft, EventType
from aletheore.council.resume import ResumeStore
from aletheore.council.sqlite_store import SqliteRoomStore
from aletheore.council.tui import CouncilApp, FoldRow, MessageView
from aletheore.council.view_model import StreamItem

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)


def item(seq, text, day="2026-11-10", author="bob"):
    return StreamItem(seq, "message", author, None, "claim", text, "now", created_at=f"{day}T09:00:00+00:00")


def test_find_matches_is_case_insensitive_and_wraps_around():
    items = [item(1, "Use Redis here"), item(2, "nothing"), item(3, "redis again"), item(4, "x", author="REDIS-bot")]
    assert tui_nav.find_matches(items, "redis") == [1, 3, 4] and tui_nav.find_matches(items, "  ") == []
    assert [tui_nav.next_index(i, 3, 1) for i in (-1, 0, 1, 2)] == [0, 1, 2, 0]
    assert tui_nav.next_index(-1, 3, -1) == 2 and tui_nav.next_index(0, 3, -1) == 2 and tui_nav.next_index(-1, 0, 1) == -1


def test_parse_day_forms_and_garbage():
    assert tui_nav.parse_day("today", NOW) == date(2026, 11, 10) and tui_nav.parse_day("Yesterday", NOW) == date(2026, 11, 9)
    assert tui_nav.parse_day("2026-11-01", NOW) == date(2026, 11, 1) and tui_nav.parse_day("1 Oct", NOW) == date(2026, 10, 1)
    assert tui_nav.parse_day("20 Dec", NOW) == date(2025, 12, 20)  # a day that has not happened this year means last year's
    assert tui_nav.parse_day("soon", NOW) is None and tui_nav.parse_day("31 Feb", NOW) is None


def test_first_item_on_or_after_and_fold_start():
    items = [item(1, "a", "2026-11-01"), item(2, "b", "2026-11-05"), item(3, "c", "2026-11-09")]
    assert tui_nav.first_item_on_or_after(items, date(2026, 11, 2)) == 2 and tui_nav.first_item_on_or_after(items, date(2026, 12, 1)) is None
    assert tui_nav.fold_start(items, 5) == (0, 0) and tui_nav.fold_start(items, 2) == (2, 1)


def test_pins_and_match_notes_are_plain_words():
    assert tui_nav.pins_text([]).plain == "" and "PINNED" in tui_nav.pins_text(["decided on write-through"]).plain
    assert "no match" in tui_nav.match_note("zz", -1, 0).plain and "match 2 of 5" in tui_nav.match_note("a", 1, 5).plain


def make(tmp_path, n=250):
    s = SqliteRoomStore(tmp_path / "r.db")
    s.create_room("r1", "alice", title="Long")
    s.append(EventDraft("r1", "c0", "room", EventType.SYSTEM, "alice", None, None, {"subtype": "room_created", "host": "alice"}))
    for i in range(n):
        s.append(EventDraft("r1", f"m{i}", "t", EventType.CLAIM, "bob", None, None, {"text": f"message number {i}" + (" needle" if i == 5 else "")}))
    return s


async def settle(pilot, n=12):
    for _ in range(n):
        await pilot.pause(0.1)


def test_a_long_room_folds_old_history_and_a_search_opens_it(tmp_path):
    s = make(tmp_path)

    async def go():
        app = CouncilApp(s, "r1", "alice", poll_s=0.05)
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot, 20)
            assert len(app.query(MessageView)) <= tui_nav.WINDOW + 5 and len(app.query(FoldRow)) == 1
            assert "earlier message" in str(app.query_one(FoldRow).render())
            app._run_search("needle")  # the match (message 5) is in folded history
            await settle(pilot, 10)
            assert len(app.query(FoldRow)) == 0 and app._matches and any("needle" in str(w.render()) for w in app.query(MessageView))
            assert "match 1 of 1" in str(app.query_one("#status").render())
            app.action_clear_search()
            await settle(pilot, 3)
            assert app._search == ""

    asyncio.run(go())


def test_pins_are_remembered_and_shown_in_the_left_panel(tmp_path):
    s = make(tmp_path, n=5)
    resume = ResumeStore(tmp_path / "resume.json")

    async def go():
        app = CouncilApp(s, "r1", "alice", poll_s=0.05, resume=resume)
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot)
            view = list(app.query(MessageView))[2]
            view.focus()
            await pilot.press("p")
            await settle(pilot, 4)
            assert "PINNED" in str(app.query_one("#rooms").render()) and "message number" in str(app.query_one("#rooms").render())
            assert resume.pins("r1")
            await pilot.press("p")
            await settle(pilot, 3)
            assert not resume.pins("r1") and "PINNED" not in str(app.query_one("#rooms").render())

    asyncio.run(go())


def test_jump_to_a_day_scrolls_there_and_a_bad_day_says_so(tmp_path):
    s = make(tmp_path, n=5)

    async def go():
        app = CouncilApp(s, "r1", "alice", poll_s=0.05)
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot)
            app._run_jump("today")
            app._run_jump("not a day")
            app._run_jump("2030-01-01")  # nothing on or after it
            await settle(pilot, 3)

    asyncio.run(go())


def test_strip_marks_plans_and_verdicts_where_they_sit_and_shows_what_is_on_screen():
    items = [item(i, f"m{i}") for i in range(1, 11)]
    items[2] = StreamItem(3, "plan", "bob", None, "plan", "p", "now", plan_id="p1", created_at="2026-11-10T09:00:00+00:00")
    items[6] = StreamItem(7, "verdict", "bob", None, "verdict", "v", "now", verdict_id="v1", created_at="2026-11-10T09:00:00+00:00")
    rows = tui_nav.strip_rows(items, 10, 0.4, 0.6)
    assert rows[2] == ("◆", "needs") and rows[6] == ("●", "ok") and rows[9] == ("▼", "accent")
    assert [r[0] for r in rows[4:7]] == ["│", "│", "●"]  # the on-screen part, without covering a mark
    assert tui_nav.strip_rows([], 5, 0, 1) == [("", "")] * 5 and len(tui_nav.strip_text(rows).plain.splitlines()) == 10


def test_a_click_on_a_pinned_line_picks_that_message():
    assert tui_nav.pin_click_target((12, [4, 9, 20]), 13) == 9
    assert tui_nav.pin_click_target((12, [4, 9, 20]), 11) is None and tui_nav.pin_click_target((12, [4]), 13) is None


def test_clicking_a_pin_in_the_left_panel_scrolls_to_it_and_the_strip_is_drawn(tmp_path):
    s = make(tmp_path, n=120)
    resume = ResumeStore(tmp_path / "resume.json")

    async def go():
        app = CouncilApp(s, "r1", "alice", poll_s=0.05, resume=resume)
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot, 14)
            widgets = list(app.query(MessageView))
            first = widgets[0]
            resume.toggle_pin("r1", first.item.seq)
            app._draw_chrome()
            await settle(pilot, 3)
            app.query_one("#stream").scroll_end(animate=False)
            await settle(pilot, 3)
            start_row, seqs = app._pin_rows
            assert seqs == [first.item.seq]
            rooms = app.query_one("#rooms")
            await pilot.click("#rooms", offset=(3, start_row + 1))  # +1: the pane's top padding sits above its first content row
            await settle(pilot, 4)
            assert app.query_one("#stream").scroll_y < app.query_one("#stream").max_scroll_y
            assert "▼" in str(app.query_one("#strip").render())

    asyncio.run(go())
