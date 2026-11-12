import asyncio
import os
import stat

from aletheore.council import tui_rooms
from aletheore.council.protocol import EventDraft, EventType
from aletheore.council.resume import ResumeStore
from aletheore.council.sqlite_store import SqliteRoomStore
from aletheore.council.tui import CouncilApp
from aletheore.council.view_model import RoomSummary


def test_store_remembers_last_room_read_position_and_draft_across_restarts(tmp_path):
    r = ResumeStore(tmp_path / "x" / "resume.json")
    r.set_last("r2")
    r.set_read("r2", 7)
    r.set_read("r2", 3)  # reading never moves backwards
    r.set_draft("r2", "half a thought")
    r.save()
    again = ResumeStore(tmp_path / "x" / "resume.json")
    assert again.last_room() == "r2" and again.read_seq("r2") == 7 and again.draft("r2") == "half a thought"
    again.set_draft("r2", "")
    again.save()
    assert ResumeStore(tmp_path / "x" / "resume.json").draft("r2") == ""


def test_file_is_private_and_a_damaged_file_is_just_forgotten(tmp_path):
    p = tmp_path / "resume.json"
    r = ResumeStore(p)
    r.set_last("r1")
    r.save()
    if os.name != "nt":
        assert stat.S_IMODE(p.stat().st_mode) == 0o600
    p.write_text("{not json")
    assert ResumeStore(p).last_room() == "" and ResumeStore(p).read_seq("r1") == 0
    p.write_text('["a list"]')
    assert ResumeStore(p).last_room() == ""


def test_unread_shows_in_the_room_list():
    s = [RoomSummary("r1", "Cache", "interactive", "live", [], 0, ""), RoomSummary("r2", "Billing", "interactive", "live", [], 0, "", unread=3)]
    text = tui_rooms.room_list_text(s, "r1").plain
    assert "3 new" in text
    assert tui_rooms.room_at_row(s, 2) == "r1" and tui_rooms.room_at_row(s, 3) == "r2" and tui_rooms.room_at_row(s, 4) == "r2"


def make(tmp_path):
    s = SqliteRoomStore(tmp_path / "r.db")
    for rid in ("r1", "r2"):
        s.create_room(rid, "alice", title=rid.upper())
        s.append(EventDraft(rid, "c0", "room", EventType.SYSTEM, "alice", None, None, {"subtype": "room_created", "host": "alice"}))
    for i in range(3):
        s.append(EventDraft("r1", f"m{i}", "t", EventType.CLAIM, "alice", None, None, {"text": f"old {i}"}))
    return s


async def settle(pilot, n=10):
    for _ in range(n):
        await pilot.pause(0.1)


def test_reopening_restores_the_draft_and_marks_what_is_new_since_you_left(tmp_path):
    s = make(tmp_path)
    path = tmp_path / "resume.json"

    async def first():
        app = CouncilApp(s, "r1", "alice", poll_s=0.05, resume=ResumeStore(path))
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot)
            app.query_one("#composer").load_text("unsent words")
            await settle(pilot, 2)

    asyncio.run(first())
    for i in range(2):  # while away, two messages arrive
        s.append(EventDraft("r1", f"n{i}", "t", EventType.CLAIM, "alice", None, None, {"text": f"fresh {i}"}))
    s.append(EventDraft("r2", "x1", "t", EventType.CLAIM, "alice", None, None, {"text": "elsewhere"}))

    async def second():
        resume = ResumeStore(path)
        assert resume.last_room() == "r1" and resume.draft("r1") == "unsent words"
        app = CouncilApp(s, "r1", "alice", poll_s=0.05, resume=resume)
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot, 14)
            assert app.query_one("#composer").text == "unsent words"
            days = [str(w.render()) for w in app.query(".day")]
            assert any("2 new since you left" in d for d in days)
            assert "new" in str(app.query_one("#rooms").render())  # r2 has something unread

    asyncio.run(second())
