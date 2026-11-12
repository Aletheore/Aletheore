import asyncio

from aletheore.council import tui_security
from aletheore.council.marks import VerifiedMarks
from aletheore.council.protocol import EventDraft, EventType
from aletheore.council.sqlite_store import SqliteRoomStore
from aletheore.council.tui import CouncilApp
from aletheore.council.tui_security import SecurityScreen


def room(**kw):
    base = {"sealed": True, "epoch": 3, "host_is_me": True,
            "members": [{"name": "alice", "host": True, "you": True, "devices": 2, "status": "compared"},
                        {"name": "bob", "host": False, "you": False, "devices": 1, "status": "none", "words": ["calm-otter-reef-lamp"]},
                        {"name": "cat", "host": False, "you": False, "devices": 1, "status": "changed"}],
            "counters": {"verified": 40, "rejected": 2, "unreadable": 1, "gaps": 0}}
    base.update(kw)
    return base


def test_members_and_devices_with_compared_or_not():
    t = tui_security.security_text(room(), 1).plain
    assert "alice host you  2 devices  ✓ words compared" in t and "not compared yet" in t
    assert "a device is new or changed since you compared" in t and "calm otter reef lamp" in t


def test_counters_with_reasons_and_what_the_server_sees():
    t = tui_security.security_text(room(), 0).plain
    assert "40 messages read and checked" in t and "2 failed a check" in t and "1 could not be opened" in t and "gaps" not in t.split("CHECKED")[1].split("WHAT THE SERVER")[0]
    assert "can see" in t and "cannot    the text" in t
    clean = tui_security.security_text(room(counters={"verified": 5, "rejected": 0, "unreadable": 0, "gaps": 0}), 0).plain
    assert "nothing ignored, no gaps" in clean
    assert "not sealed" in tui_security.security_text({"sealed": False}, 0).plain


def test_marks_are_local_and_a_changed_device_clears_the_mark(tmp_path):
    m = VerifiedMarks(tmp_path / "v.json")
    assert m.status("bob", ["d1"]) == "none"
    m.mark("bob", "d1")
    assert m.status("bob", ["d1"]) == "compared" and VerifiedMarks(tmp_path / "v.json").status("bob", ["d1"]) == "compared"  # persisted
    assert m.status("bob", ["d1", "d2"]) == "changed"  # a device never compared: the mark does not cover it
    assert m.status("bob", ["dX"]) == "changed"  # the old key is gone and a new one appeared
    assert m.status("carol", ["d9"]) == "none"


def test_compare_in_the_app_records_a_local_mark(tmp_path):
    s = SqliteRoomStore(tmp_path / "r.db")
    s.create_room("r1", "alice", title="T", crypto="e2e-v1")
    s.add_member("r1", "bob")
    s.append(EventDraft("r1", "c0", "room", EventType.SYSTEM, "alice", None, None, {"subtype": "room_created", "host": "alice"}))
    marks = VerifiedMarks(tmp_path / "v.json")

    class D:
        device_id, fingerprint = "devbob", "ab" * 32

    s.room_devices = lambda room_id: {"bob": [D()], "alice": []}

    async def go():
        app = CouncilApp(s, "r1", "alice", poll_s=0.05, marks=marks)
        async with app.run_test(size=(130, 40)) as pilot:
            for _ in range(6):
                await pilot.pause(0.1)
            await pilot.press("ctrl+s")
            for _ in range(4):
                await pilot.pause(0.1)
            assert isinstance(app.screen, SecurityScreen)
            await pilot.press("down", "c")  # members sort: alice, bob
            for _ in range(3):
                await pilot.pause(0.1)
            assert marks.status("bob", ["devbob"]) == "compared"

    asyncio.run(go())
