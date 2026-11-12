"""The command palette, key help, reduced motion, no colour, and saved text exports of the main screens."""

import asyncio
import os
import re
from pathlib import Path

import pytest

from aletheore.council import theme, tui_palette
from aletheore.council.protocol import EventDraft, EventType
from aletheore.council.sqlite_store import SqliteRoomStore
from aletheore.council.tui import CouncilApp

REF = Path(__file__).parent / "reference"


def make(tmp_path):
    s = SqliteRoomStore(tmp_path / "r.db")
    s.create_room("r1", "hosty", title="Cache work", crypto="e2e-v1")
    for m in ("megan", "moe"):
        s.add_member("r1", m)

    def post(etype, body, cid, sender="hosty", agent=None, thread="room"):
        s.append(EventDraft("r1", cid, thread, etype, sender, agent, None, body))

    post(EventType.SYSTEM, {"subtype": "room_created", "host": "hosty"}, "c0")
    post(EventType.SYSTEM, {"subtype": "area_defined", "name": "redis", "globs": ["redis/**"], "owner": "hosty", "agent_id": "hosty-codex"}, "c1")
    post(EventType.QUESTION, {"text": "can we make the session cache write-through?", "kind": "chat"}, "m1", sender="megan")
    post(EventType.CLAIM, {"text": "Write-through keeps Redis and Postgres in step."}, "m2", agent="hosty-codex", thread="cache")
    return s


async def settle(pilot, n=10):
    for _ in range(n):
        await pilot.pause(0.1)


def screen_text(app) -> str:
    """What is on screen as plain text, pane by pane. Times are the only thing that moves, so they are blanked."""
    parts = []
    for ident in ("#top", "#rooms", "#stream", "#sidebar", "#status"):
        try:
            widget = app.query_one(ident)
        except Exception:
            continue
        if not widget.display:
            continue
        body = "\n".join(str(w.render()) for w in ([widget] + list(widget.query("Static"))) if w is not widget or ident != "#stream")
        parts.append(f"[{ident}]\n{body}")
    text = "\n".join(parts)
    return re.sub(r"\b\d+[smhd]\b", "Nx", text)


def test_every_palette_action_exists_on_the_app():
    for label, key, action in tui_palette.ACTIONS:
        assert label and key
        if action:
            assert hasattr(CouncilApp, f"action_{action}"), action
    assert len({k for _, k, _ in tui_palette.ACTIONS}) == len(tui_palette.ACTIONS)  # no key listed twice


def test_help_lists_every_key():
    text = tui_palette.help_text().plain
    assert all(key in text for _, key, _ in tui_palette.ACTIONS) and "NO_COLOR" in text and "—" not in text and "–" not in text


def test_palette_filters_runs_an_action_and_help_opens(tmp_path):
    s = make(tmp_path)

    async def go():
        app = CouncilApp(s, "r1", "hosty", poll_s=0.05)
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(pilot)
            await pilot.press("ctrl+k")
            await settle(pilot, 3)
            assert isinstance(app.screen, tui_palette.PaletteScreen)
            for ch in "dev":
                await pilot.press(ch)
            await settle(pilot, 2)
            assert [a for _, _, a in app.screen.shown] == ["devices"]
            await pilot.press("escape")
            await settle(pilot, 2)
            await pilot.press("f1")
            await settle(pilot, 3)
            assert isinstance(app.screen, tui_palette.HelpScreen)
            await pilot.press("escape")
            await settle(pilot, 2)
            await pilot.press("question_mark")  # an empty message box: ? is help
            await settle(pilot, 3)
            assert isinstance(app.screen, tui_palette.HelpScreen)

    asyncio.run(go())


def test_reduced_motion_stops_the_pulse_and_the_spinner(tmp_path, monkeypatch):
    monkeypatch.setenv("COUNCIL_REDUCED_MOTION", "1")
    s = make(tmp_path)

    async def go():
        app = CouncilApp(s, "r1", "hosty", poll_s=0.05)
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(pilot, 4)
            first = (str(app.query_one("#top").render()), app.frame)
            await settle(pilot, 10)  # a second of ticks
            assert app.frame == 0 and (str(app.query_one("#top").render()), app.frame) == first
            assert not app.motion

    asyncio.run(go())


def test_motion_is_on_by_default_and_the_frame_advances(tmp_path, monkeypatch):
    monkeypatch.delenv("COUNCIL_REDUCED_MOTION", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    s = make(tmp_path)

    async def go():
        app = CouncilApp(s, "r1", "hosty", poll_s=0.05)
        async with app.run_test(size=(120, 40)) as pilot:
            for _ in range(60):  # a busy machine can be slow to run the first timer ticks: wait up to six seconds, not a fixed second
                if app.frame > 0:
                    break
                await pilot.pause(0.1)
            assert app.motion and app.frame > 0

    asyncio.run(go())


def test_no_colour_keeps_every_meaning_in_words_and_symbols():
    from aletheore.council.tui_connection import banner_text
    from aletheore.council.view_model import connection_state

    assert theme.motion_enabled({"NO_COLOR": "1"}) is False and theme.detect_depth({"NO_COLOR": "1"}) == "none"
    assert theme.rich_color("needs", "none") == "default"
    plain = banner_text(connection_state(60), "network").plain  # the state is in the words and the ◌ mark, not in the colour
    assert "unreachable" in plain and "◌" in plain


@pytest.mark.parametrize("size,name", [((100, 30), "main_100x30"), ((80, 24), "main_80x24")])
def test_reference_exports_of_the_main_screen(tmp_path, monkeypatch, size, name):
    """A saved text copy of the screen. An unplanned layout change shows up as a diff here; UPDATE_REFERENCE=1 re-saves it."""
    monkeypatch.setenv("COUNCIL_REDUCED_MOTION", "1")
    monkeypatch.setenv("COUNCIL_HOME", str(tmp_path))
    s = make(tmp_path)

    async def go():
        app = CouncilApp(s, "r1", "hosty", poll_s=0.05)
        async with app.run_test(size=size) as pilot:
            await settle(pilot, 14)
            return screen_text(app)

    got = asyncio.run(go())
    path = REF / f"{name}.txt"
    if os.environ.get("UPDATE_REFERENCE") or not path.exists():
        REF.mkdir(exist_ok=True)
        path.write_text(got, encoding="utf-8")
    assert got == path.read_text(encoding="utf-8")
