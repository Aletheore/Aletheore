"""The join request screen."""

import asyncio

from aletheore.council.protocol import EventDraft, EventType
from aletheore.council.sqlite_store import SqliteRoomStore
from aletheore.council.tui import CouncilApp
from aletheore.council.tui_join import JoinModal, join_text

REQ = {"request_id": "rq1", "handle": "dana", "device_id": "dev-abcdef", "words": "calm-otter-reef-lamp"}


class JoinStore(SqliteRoomStore):
    """A sqlite store that also answers the join calls the server store has."""

    def __init__(self, path):
        super().__init__(path)
        self.requests = [dict(REQ)]
        self.log = []

    def list_join_requests(self, room_id):
        return list(self.requests)

    def approve_join(self, room_id, request_id):
        self.requests = [r for r in self.requests if r["request_id"] != request_id]
        self.log.append(("approve", request_id))
        return {"handle": "dana"}

    def grant_member(self, room_id, participant):
        self.log.append(("grant", participant))

    def reject_join(self, room_id, request_id):
        self.requests = [r for r in self.requests if r["request_id"] != request_id]
        self.log.append(("reject", request_id))
        return {"handle": "dana"}

    def current_epoch(self, room_id):
        return 3


def make(tmp_path, crypto="e2e-v1"):
    s = JoinStore(tmp_path / "r.db")
    s.create_room("r1", "alice", title="Cache work", crypto=crypto)
    s.append(EventDraft("r1", "c0", "room", EventType.SYSTEM, "alice", None, None, {"subtype": "room_created", "host": "alice"}))
    return s


async def settle(pilot, n=10):
    for _ in range(n):
        await pilot.pause(0.1)


def test_text_shows_handle_words_device_and_the_epoch_they_would_receive():
    t = join_text(REQ, "Cache work", 3, True, False).plain
    assert "dana" in t and "CALM   OTTER   REEF   LAMP" in t and "dev-abcdef" in t and "epoch 3" in t and "tab" in t
    assert "tab" not in join_text(REQ, "Cache work", 3, True, True).plain
    assert "epoch" not in join_text(REQ, "Cache work", None, False, True).plain


def test_untrusted_request_text_stays_inert():
    t = join_text({**REQ, "handle": "[red]x[/red]"}, "t", 1, True, True).plain
    assert "[red]x[/red]" in t


def test_a_pending_join_opens_the_modal_and_approve_waits_for_the_words(tmp_path):
    s = make(tmp_path)

    async def go():
        app = CouncilApp(s, "r1", "alice", poll_s=0.05)
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(pilot, 14)
            assert isinstance(app.screen, JoinModal)
            assert app.screen.query_one("#approve").disabled
            await pilot.press("a")
            await settle(pilot, 3)
            assert isinstance(app.screen, JoinModal) and s.log == []  # a refused click-through
            await pilot.press("tab")
            await settle(pilot, 3)
            assert not app.screen.query_one("#approve").disabled
            await pilot.press("a")
            await settle(pilot, 10)
            assert s.log == [("approve", "rq1"), ("grant", "dana")] and not isinstance(app.screen, JoinModal)

    asyncio.run(go())


def test_reject_turns_them_down_and_later_leaves_it_pending(tmp_path):
    s = make(tmp_path)

    async def go():
        app = CouncilApp(s, "r1", "alice", poll_s=0.05)
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(pilot, 14)
            await pilot.press("escape")
            await settle(pilot, 4)
            assert not isinstance(app.screen, JoinModal) and s.log == []
            await pilot.press("ctrl+g")
            await settle(pilot, 4)
            assert isinstance(app.screen, JoinModal)
            await pilot.press("r")
            await settle(pilot, 10)
            assert s.log == [("reject", "rq1")]

    asyncio.run(go())


def test_only_the_host_sees_join_requests(tmp_path):
    s = make(tmp_path)
    s.add_member("r1", "bob")

    async def go():
        app = CouncilApp(s, "r1", "bob", poll_s=0.05)
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(pilot, 14)
            assert not isinstance(app.screen, JoinModal) and app.joins == []
            await pilot.press("ctrl+g")
            await settle(pilot, 3)
            assert not isinstance(app.screen, JoinModal)

    asyncio.run(go())
