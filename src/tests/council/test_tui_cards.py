"""Stream cards, day dividers and the new-messages marker."""

import asyncio
from datetime import datetime, timezone

from rich.cells import cell_len

from aletheore.council import theme, tui_cards
from aletheore.council.protocol import EventDraft, EventType
from aletheore.council.sqlite_store import SqliteRoomStore
from aletheore.council.tui import CouncilApp, MessageView


def run(coro):
    return asyncio.run(coro)


async def settle(pilot, n=6):
    for _ in range(n):
        await pilot.pause(0.08)


NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)


def test_plan_verdict_cards_have_corner_brackets_and_amber_while_waiting():
    waiting = tui_cards.card_text("plan", "p1", "pending", "hosty", "→ redis: add a cache", 50)
    assert waiting.plain.startswith("┌ PLAN p1  pending") and waiting.plain.rstrip().endswith("┘".replace("┘", "└"))
    assert theme.rich_color("needs", theme.DEPTH) in " ".join(str(s.style) for s in waiting.spans)
    done = tui_cards.card_text("verdict", "v1", "applied", "", "ship it", 50)
    styles = " ".join(str(s.style) for s in done.spans)
    assert "applied" in done.plain and theme.rich_color("ok", theme.DEPTH) in styles
    rej = tui_cards.card_text("plan", "p2", "rejected", "", "x", 50)
    assert "rejected" in rej.plain and theme.rich_color("error", theme.DEPTH) in " ".join(str(s.style) for s in rej.spans)


def test_day_dividers_name_the_day():
    assert tui_cards.day_label("2026-11-10T09:00:00+00:00", NOW) == "Today"
    assert tui_cards.day_label("2026-11-09T09:00:00+00:00", NOW) == "Yesterday"
    assert tui_cards.day_label("2026-10-01T09:00:00+00:00", NOW) == "1 Oct"
    assert tui_cards.day_label("garbage", NOW) == ""
    d = tui_cards.day_divider("Today", 40)
    assert d.plain.startswith("── Today ─") and cell_len(d.plain) == 40


def test_untrusted_text_stays_inert_and_wide_characters_keep_alignment():
    t = tui_cards.card_text("plan", "[red]p1[/red]", "pending", "名前名前", "[bold]x[/bold]\x1b[31m", 50)
    assert "[red]p1[/red]" in t.plain and "[bold]x[/bold]" in t.plain
    assert tui_cards.fit("名前名前名前", 7) == "名前名…" and cell_len(tui_cards.fit("名前名前名前", 7)) <= 7
    assert tui_cards.fit("short", 10) == "short" and tui_cards.fit("abc", 0) == ""


def _store(tmp_path):
    s = SqliteRoomStore(tmp_path / "r.db")
    s.create_room("r1", "alice", title="Cache work")
    s.append(EventDraft("r1", "c0", "room", EventType.SYSTEM, "alice", None, None, {"subtype": "room_created", "host": "alice"}))
    s.append(EventDraft("r1", "c1", "t1", EventType.CLAIM, "bob", "bob-claude", None, {"text": "first message"}))
    return s


def test_app_shows_a_day_divider_and_plan_cards_are_message_views(tmp_path):
    s = _store(tmp_path)

    async def go():
        app = CouncilApp(s, "r1", "bob", poll_s=0.05)
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(pilot)
            assert len(app.query(".day")) == 1
            assert str(app.query_one(".day").render()).startswith("── Today")

    run(go())


def test_new_messages_marker_appears_when_scrolled_up_and_clears_at_the_end(tmp_path):
    s = _store(tmp_path)
    for i in range(60):
        s.append(EventDraft("r1", f"m{i}", "t1", EventType.CLAIM, "bob", "bob-claude", None, {"text": f"line {i}"}))

    async def go():
        app = CouncilApp(s, "r1", "bob", poll_s=0.05)
        async with app.run_test(size=(120, 30)) as pilot:
            await settle(pilot, 10)
            stream = app.query_one("#stream")
            assert not app.query_one("#new-marker").display
            stream.scroll_home(animate=False)
            await settle(pilot)
            for i in range(3):
                s.append(EventDraft("r1", f"n{i}", "t1", EventType.CLAIM, "bob", "bob-claude", None, {"text": f"fresh {i}"}))
            await settle(pilot, 10)
            marker = app.query_one("#new-marker")
            assert marker.display and "3 new messages" in str(marker.render())
            await pilot.press("end")
            await settle(pilot)
            assert not app.query_one("#new-marker").display

    run(go())
