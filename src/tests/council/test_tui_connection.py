import asyncio

from aletheore.council import tui_connection
from aletheore.council.protocol import EventDraft, EventType
from aletheore.council.sqlite_store import SqliteRoomStore
from aletheore.council.tui import CouncilApp
from aletheore.council.view_model import connection_state


def test_banner_explains_in_plain_words_and_counts_down():
    assert tui_connection.banner_text(connection_state(0), "") is None
    r = tui_connection.banner_text(connection_state(10, 3), "network").plain
    assert "reconnecting" in r and "trying again in 3 s" in r and "Nothing is lost" in r
    u = tui_connection.banner_text(connection_state(60, 2, queued=2), "network").plain
    assert "unreachable for 60 s" in u and "your connection to the server is down" in u and "2 waiting" in u
    a = tui_connection.banner_text(connection_state(400), "host").plain
    assert "away for 6 min" in a and "the host is not answering" in a and "hold results" in a


def test_distinguishes_host_down_from_my_network_down():
    mine = tui_connection.banner_text(connection_state(60), "network").plain
    theirs = tui_connection.banner_text(connection_state(60), "host").plain
    assert "your connection" in mine and "host is not answering" in theirs and mine != theirs


def test_banner_uses_amber_not_red():
    from aletheore.council import theme

    t = tui_connection.banner_text(connection_state(60), "network")
    styles = " ".join(str(s.style) for s in t.spans)
    assert theme.rich_color("error", theme.DEPTH) not in styles and theme.rich_color("needs", theme.DEPTH) in styles


def test_right_panel_lists_what_still_works_and_what_waits():
    t = tui_connection.works_text(connection_state(60)).plain
    assert "✓ reading what you have" in t and "○ approvals" in t and "✓ agents starting tasks" in t
    assert "○ agents starting tasks" in tui_connection.works_text(connection_state(400)).plain
    assert tui_connection.works_text(connection_state(0)).plain == ""


class Flaky(SqliteRoomStore):
    down = False

    def read_after(self, *a, **k):
        if self.down:
            raise ConnectionError("down")
        return super().read_after(*a, **k)


def test_app_shows_the_banner_when_polls_fail_and_a_returned_note_after_a_long_outage(tmp_path):
    s = Flaky(tmp_path / "r.db")
    s.create_room("r1", "alice", title="T")
    s.append(EventDraft("r1", "c0", "room", EventType.SYSTEM, "alice", None, None, {"subtype": "room_created", "host": "alice"}))

    async def go():
        app = CouncilApp(s, "r1", "alice", poll_s=0.05)
        async with app.run_test(size=(120, 40)) as pilot:
            for _ in range(5):
                await pilot.pause(0.1)
            assert not app.query_one("#banner").display
            s.down = True
            for _ in range(6):
                await pilot.pause(0.1)
            assert app.query_one("#banner").display and "reconnecting" in str(app.query_one("#banner").render())
            app.monitor.down_since -= 45  # pretend the outage was long
            s.down = False
            for _ in range(8):
                await pilot.pause(0.1)
            assert not app.query_one("#banner").display
            assert any("the host is back" in str(w.render()) for w in app.query("#stream Static"))

    asyncio.run(go())


def test_queued_messages_show_as_waiting_to_send_and_clear_on_return(tmp_path):
    from aletheore.council.outbox import OutboxStore

    inner = Flaky(tmp_path / "r.db")
    inner.create_room("r1", "alice", title="T")
    inner.append(EventDraft("r1", "c0", "room", EventType.SYSTEM, "alice", None, None, {"subtype": "room_created", "host": "alice"}))
    store = OutboxStore(inner, tmp_path / "o.json")

    async def go():
        app = CouncilApp(store, "r1", "alice", poll_s=0.05)
        async with app.run_test(size=(120, 40)) as pilot:
            for _ in range(4):
                await pilot.pause(0.1)
            inner.down = True
            for _ in range(5):
                await pilot.pause(0.1)
            app.monitor.down_since -= 40  # the outage has lasted past the 30 second stage
            store.outbox.add(EventDraft("r1", "q1", "t", EventType.QUESTION, "alice", None, None, {"text": "sent while away", "kind": "chat"}), 0.0)
            for _ in range(4):
                await pilot.pause(0.1)
            assert app.query_one("#queued").display and "waiting to send" in str(app.query_one("#queued").render())
            assert "sent while away" in str(app.query_one("#queued").render()) and "1 waiting" in str(app.query_one("#banner").render())
            inner.down = False
            for _ in range(14):
                await pilot.pause(0.1)
            assert not app.query_one("#queued").display and not app.query_one("#banner").display
            assert any(e.body.get("text") == "sent while away" for e in inner.read_after("r1", 0))

    asyncio.run(go())
