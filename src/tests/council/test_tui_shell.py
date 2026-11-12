"""The shell of the Council terminal: top bar, panes, composer bar, and how they respond to the window size."""

import asyncio

import pytest
from rich.cells import cell_len

from aletheore.council import theme
from aletheore.council.protocol import EventDraft, EventType
from aletheore.council.sqlite_store import SqliteRoomStore
from aletheore.council.tui import CouncilApp
from aletheore.council.tui_shell import key_hints, layout_mode, rooms_text, top_bar_text

SIZES = [(60, 20), (76, 24), (100, 30), (140, 40), (200, 50)]


def run(coro):
    return asyncio.run(coro)


async def settle(pilot, n=6):
    for _ in range(n):
        await pilot.pause(0.08)


def make_store(tmp_path, crypto="none", title="Cache work"):
    s = SqliteRoomStore(tmp_path / "r.db")
    s.create_room("r1", "alice", title=title, crypto=crypto)
    s.append(EventDraft("r1", "c0", "room", EventType.SYSTEM, "alice", None, None, {"subtype": "room_created", "host": "alice"}))
    s.append(EventDraft("r1", "c1", "room", EventType.SYSTEM, "alice", None, None,
                        {"subtype": "area_defined", "name": "redis", "globs": ["redis/**"], "owner": "alice", "agent_id": "alice-codex"}))
    s.append(EventDraft("r1", "c2", "t1", EventType.CLAIM, "bob", "bob-claude", None, {"text": "a message with some words in it"}))
    return s


@pytest.fixture
def store(tmp_path):
    return make_store(tmp_path)


def test_layout_mode_boundaries():
    assert layout_mode(59, 24) == "too-small" and layout_mode(80, 19) == "too-small" and layout_mode(10, 5) == "too-small"
    assert layout_mode(60, 20) == "stream-only" and layout_mode(75, 40) == "stream-only"
    assert layout_mode(76, 24) == "no-panel" and layout_mode(99, 24) == "no-panel"
    assert layout_mode(100, 24) == "full" and layout_mode(250, 70) == "full"


def test_top_bar_fits_the_width_keeps_the_right_side_and_truncates_the_title():
    kw = dict(title="A very long room title that cannot possibly fit on a narrow terminal", room_id="room-12345678", sealed=True, mode="interactive",
              round_no=0, closed=False, finished="", host="hosty", me="megan", autonomy=False, connected=True)
    for width in (60, 76, 100, 140):
        text = top_bar_text(width, **kw)
        assert cell_len(text.plain) <= width, (width, text.plain)
        assert "ALETHEORE" in text.plain and "SEALED" in text.plain and "megan" in text.plain
    wide = top_bar_text(160, **kw).plain
    assert "A very long room title that cannot possibly fit on a narrow terminal" in wide
    narrow = top_bar_text(60, **kw).plain
    assert "…" in narrow and "A very long room title that cannot possibly fit" not in narrow
    assert "SEALED" not in top_bar_text(100, **{**kw, "sealed": False}).plain


def test_top_bar_uses_amber_not_red_for_trouble_and_never_colour_alone():
    kw = dict(title="Room", room_id="r1", sealed=False, mode="interactive", round_no=0, closed=False, finished="", host="h", me="m", autonomy=False)
    down = top_bar_text(120, connected=False, **kw)
    assert "unreachable" in down.plain and "retrying" in down.plain  # said in words, with a symbol
    styles = " ".join(str(span.style) for span in down.spans)
    assert theme.rich_color("error", theme.DEPTH) not in styles  # an unreachable server is "waiting", not an error
    assert theme.rich_color("needs", theme.DEPTH) in styles


def test_key_hints_follow_the_context_and_the_width():
    normal = key_hints(needs=0, width=140).plain
    assert "ctrl+r rooms" in normal and "enter send" in normal
    waiting = key_hints(needs=2, width=140).plain
    assert "a approve" in waiting and "r reject" in waiting
    assert cell_len(key_hints(needs=0, width=60).plain) <= 60 and cell_len(key_hints(needs=2, width=60).plain) <= 60


def test_rooms_text_names_the_current_room_and_how_to_switch():
    text = rooms_text(title="Cache work", room_id="r1", status="open", mode="interactive", state_line="you · bob").plain
    assert "ROOMS" in text and "Cache work" in text and "ctrl+r" in text


@pytest.mark.parametrize("size", SIZES)
def test_panes_follow_the_width_rules_and_nothing_overflows(tmp_path, size):
    store = make_store(tmp_path)
    width, height = size

    async def go():
        app = CouncilApp(store, "r1", "bob", poll_s=0.05)
        async with app.run_test(size=size) as pilot:
            await settle(pilot)
            mode = layout_mode(width, height)
            assert app.screen.has_class(f"mode-{mode}")
            assert app.query_one("#stream").display and app.query_one("#composer").display and app.query_one("#top").display
            assert app.query_one("#rooms").display == (mode in ("full", "no-panel"))
            assert app.query_one("#sidebar").display == (mode == "full")
            top = app.query_one("#top").render()
            assert all(cell_len(line) <= width for line in str(top).splitlines())
            assert not app.query_one("#too-small").display
            assert all(cell_len(line) <= width for line in str(app.query_one("#status").render()).splitlines())  # the hint line never overflows

    run(go())


def test_too_small_shows_one_clear_line_and_hides_the_rest(store):
    async def go():
        app = CouncilApp(store, "r1", "bob", poll_s=0.05)
        async with app.run_test(size=(50, 15)) as pilot:
            await settle(pilot)
            assert app.screen.has_class("too-small") and app.query_one("#too-small").display
            note = str(app.query_one("#too-small").render())
            assert "60" in note and "20" in note and "50" in note and "15" in note  # what is needed and what this window is
            assert not app.query_one("#body").display and not app.query_one("#top").display
            await pilot.resize_terminal(100, 30)  # making the window bigger brings everything back
            await settle(pilot)
            assert not app.query_one("#too-small").display and app.query_one("#body").display and not app.screen.has_class("too-small")

    run(go())


def test_resizing_live_changes_the_layout(store):
    async def go():
        app = CouncilApp(store, "r1", "bob", poll_s=0.05)
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot)
            assert app.screen.has_class("mode-full") and app.query_one("#sidebar").display
            await pilot.resize_terminal(80, 30)
            await settle(pilot)
            assert app.screen.has_class("mode-no-panel") and not app.query_one("#sidebar").display and app.query_one("#rooms").display
            await pilot.resize_terminal(64, 24)
            await settle(pilot)
            assert app.screen.has_class("mode-stream-only") and not app.query_one("#rooms").display

    run(go())


def test_top_bar_shows_the_sealed_stamp_only_in_sealed_rooms(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    sealed, plain = make_store(tmp_path / "a", crypto="e2e-v1"), make_store(tmp_path / "b")

    async def go(s):
        app = CouncilApp(s, "r1", "alice", poll_s=0.05)
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(pilot, 8)
            return str(app.query_one("#top").render())

    assert "SEALED" in run(go(sealed))
    assert "SEALED" not in run(go(plain))


def test_existing_widgets_keep_their_ids_and_the_new_panes_exist(store):
    async def go():
        app = CouncilApp(store, "r1", "bob", poll_s=0.05)
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(pilot)
            for ident in ("#top", "#body", "#stream", "#sidebar", "#footer", "#composer", "#status", "#action", "#rooms", "#too-small"):
                app.query_one(ident)
            rooms = str(app.query_one("#rooms").render())
            assert "ROOMS" in rooms and "Cache work" in rooms
            side = str(app.query_one("#sidebar").render())
            assert "§ AGENTS" in side and "§ TASKS" in side and "§ VERDICTS" in side and "alice-codex" in side
            status = str(app.query_one("#status").render())
            assert "ctrl+r rooms" in status

    run(go())
