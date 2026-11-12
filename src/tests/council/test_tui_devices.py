import asyncio

from aletheore.council import tui_devices
from aletheore.council.protocol import EventDraft, EventType
from aletheore.council.sqlite_store import SqliteRoomStore
from aletheore.council.tui import CouncilApp
from aletheore.council.tui_devices import DevicesScreen

DATA = {"handle": "alice", "devices": [
    {"device_id": "devmac-1234567890", "words": "calm-otter-reef-lamp", "created_at": 0, "revoked": False, "current": True},
    {"device_id": "devwin-0987654321", "words": "gold-fern-wind-owl", "created_at": 0, "revoked": False, "current": False},
    {"device_id": "devold", "words": "x-y-z-w", "created_at": 0, "revoked": True, "current": False}],
    "pending": [{"request_id": "lr_1", "device_id": "devnew-5555555555", "words": "mint-hawk-dune-bell"}]}


def test_screen_lists_devices_with_words_and_skips_revoked():
    t = tui_devices.devices_text(DATA, 86400 * 3, 0, False, False).plain
    assert "devmac" in t and "(this device)" in t and "calm-otter-reef-lamp" in t and "added 3d ago" in t
    assert "devwin" in t and "devold" not in t


def test_pending_link_shows_four_words_and_a_waits_for_the_words_check():
    t = tui_devices.devices_text(DATA, 10, 2, False, False).plain
    assert "WAITING TO BE LINKED" in t and "MINT   HAWK   DUNE   BELL" in t and "press w" in t
    assert "press a to link" in tui_devices.devices_text(DATA, 10, 2, True, False).plain


def test_revoke_states_the_consequence():
    t = tui_devices.devices_text(DATA, 10, 1, False, True).plain
    assert "can no longer sign in" in t and "move to a new key" in t and "Press v again" in t


class DevStore(SqliteRoomStore):
    def __init__(self, path):
        super().__init__(path)
        self.calls = []
        self.data = {k: [dict(x) for x in v] if isinstance(v, list) else v for k, v in DATA.items()}

    def my_devices(self):
        return self.data

    def approve_link(self, request_id, words, grants=b""):
        self.calls.append(("link", request_id, words))
        self.data["pending"] = []

    def revoke_device(self, device_id):
        self.calls.append(("revoke", device_id))
        for d in self.data["devices"]:
            if d["device_id"] == device_id:
                d["revoked"] = True


async def settle(pilot, n=6):
    for _ in range(n):
        await pilot.pause(0.1)


def make(tmp_path):
    s = DevStore(tmp_path / "r.db")
    s.create_room("r1", "alice", title="T")
    s.append(EventDraft("r1", "c0", "room", EventType.SYSTEM, "alice", None, None, {"subtype": "room_created", "host": "alice"}))
    return s


def test_link_needs_the_words_check_and_revoke_needs_two_presses(tmp_path):
    s = make(tmp_path)

    async def go():
        app = CouncilApp(s, "r1", "alice", poll_s=0.05)
        async with app.run_test(size=(130, 40)) as pilot:
            await settle(pilot)
            await pilot.press("ctrl+d")
            await settle(pilot)
            assert isinstance(app.screen, DevicesScreen)
            await pilot.press("down", "down")  # the pending request
            await pilot.press("a")
            await settle(pilot, 3)
            assert s.calls == []  # refused until the words were confirmed
            await pilot.press("w", "a")
            await settle(pilot, 4)
            assert s.calls == [("link", "lr_1", "mint-hawk-dune-bell")]
            await pilot.press("up")
            await pilot.press("v")
            await settle(pilot, 2)
            assert [c for c in s.calls if c[0] == "revoke"] == []  # the first press only asks
            await pilot.press("v")
            await settle(pilot, 4)
            assert ("revoke", "devwin-0987654321") in s.calls
            await pilot.press("escape")
            await settle(pilot, 2)
            assert not isinstance(app.screen, DevicesScreen)

    asyncio.run(go())


def test_the_current_device_cannot_be_revoked(tmp_path):
    s = make(tmp_path)

    async def go():
        app = CouncilApp(s, "r1", "alice", poll_s=0.05)
        async with app.run_test(size=(130, 40)) as pilot:
            await settle(pilot)
            await pilot.press("ctrl+d")
            await settle(pilot)
            await pilot.press("v", "v")
            await settle(pilot, 3)
            assert s.calls == []

    asyncio.run(go())
