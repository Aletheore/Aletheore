import asyncio
import time

import pytest

from aletheore.council.protocol import EventDraft, EventType
from aletheore.council.sqlite_store import SqliteRoomStore
from aletheore.council.state import load_state
from aletheore.council.tui import CouncilApp, MessageView, Notice


def post(store, etype, body, cid, sender="alice", agent=None, thread="room"):
    return store.append(EventDraft("r1", cid, thread, etype, sender, agent, None, body))


@pytest.fixture
def store(tmp_path):
    s = SqliteRoomStore(tmp_path / "r.db")
    s.create_room("r1", "alice")
    post(s, EventType.SYSTEM, {"subtype": "room_created", "host": "alice"}, "c0")
    post(s, EventType.SYSTEM, {"subtype": "area_defined", "name": "redis", "globs": ["redis/**"], "owner": "alice", "agent_id": "alice-codex"}, "c1")
    post(s, EventType.SYSTEM, {"subtype": "area_defined", "name": "schema", "globs": ["schema/**"], "owner": "bob", "agent_id": "bob-claude"}, "c2")
    post(s, EventType.CLAIM, {"text": "**UUID** wins\x1b[31m (red)"}, "c3", sender="bob", agent="bob-claude", thread="ids")
    post(s, EventType.DECISION, {"verdict_id": "v1", "decision": "Use UUIDs everywhere", "affected_area": "schema",
                                 "winning_branch": "council/r1/bob-claude"}, "c4", thread="ids")
    return s


def run(coro):
    return asyncio.run(coro)


async def settle(pilot, n=6):
    for _ in range(n):
        await pilot.pause(0.08)


def test_stream_sidebar_and_action_panel_render_for_the_owner(store):
    async def go():
        app = CouncilApp(store, "r1", "bob", poll_s=0.05)
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(pilot)
            msgs = list(app.query(MessageView))
            assert any("UUID wins (red)" in str(m.render()) for m in msgs)
            assert not any("\x1b" in str(m.render()) for m in msgs)
            assert app.query_one("#action").has_class("show")
            assert "bob-claude" in str(app.query_one("#sidebar").render())
            assert "waiting on you" in str(app.query_one("#status").render())
    run(go())


def test_pressing_a_approves_and_applies(store):
    applied = []

    async def go():
        app = CouncilApp(store, "r1", "bob", on_approved=applied.append, poll_s=0.05)
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(pilot)
            await pilot.press("a")
            await settle(pilot, 10)
            assert load_state(store, "r1").verdicts["v1"].status == "approved"
            assert applied == ["v1"]
            assert not app.query_one("#action").has_class("show")
    run(go())


def test_non_owner_is_not_asked(store):
    async def go():
        app = CouncilApp(store, "r1", "carol", poll_s=0.05)
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(pilot)
            assert not app.query_one("#action").has_class("show")
            await pilot.press("a")
            await settle(pilot)
            assert load_state(store, "r1").verdicts["v1"].status == "pending"
    run(go())


def test_host_chooses_winner_with_a_number_key(store):
    post(store, EventType.DECISION, {"verdict_id": "v2", "decision": "tie", "affected_area": "redis", "winning_branch": None,
                                     "candidates": {"alice-codex": "council/r1/alice-codex", "bob-claude": "council/r1/bob-claude"},
                                     "affected_by_winner": {"alice-codex": "schema", "bob-claude": "redis"}}, "c5", thread="t2")
    post(store, EventType.SYSTEM, {"subtype": "verdict_approved", "verdict_id": "v1", "by": "alice"}, "c6")

    async def go():
        app = CouncilApp(store, "r1", "alice", poll_s=0.05)
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(pilot)
            assert "Choose the winner" in str(app.query_one("#action-text").render())
            await pilot.press("2")
            await settle(pilot, 10)
            assert load_state(store, "r1").verdicts["v2"].winning_branch == "council/r1/bob-claude"
    run(go())


def test_skip_hides_the_panel_without_deciding(store):
    async def go():
        app = CouncilApp(store, "r1", "bob", poll_s=0.05)
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(pilot)
            await pilot.press("s")
            await settle(pilot)
            assert not app.query_one("#action").has_class("show")
            assert load_state(store, "r1").verdicts["v1"].status == "pending"
    run(go())


def test_sidebar_hides_when_narrow_and_tab_toggles(store):
    async def go():
        app = CouncilApp(store, "r1", "bob", poll_s=0.05)
        async with app.run_test(size=(80, 30)) as pilot:
            await settle(pilot)
            assert app.query_one("#sidebar").has_class("hidden")
            await pilot.press("tab")
            await settle(pilot, 2)
            assert not app.query_one("#sidebar").has_class("hidden")
    run(go())


def test_new_events_appear_live(store):
    async def go():
        app = CouncilApp(store, "r1", "carol", poll_s=0.05)
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(pilot)
            before = len(app.query(MessageView))
            post(store, EventType.CHALLENGE, {"text": "I disagree"}, "c9", sender="alice", agent="alice-codex", thread="ids")
            await settle(pilot, 8)
            assert len(app.query(MessageView)) == before + 1
    run(go())


def test_composer_sends_prompts_and_chat(store):
    async def go():
        app = CouncilApp(store, "r1", "carol", poll_s=0.05)
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(pilot)
            await pilot.press(*"build accounts", "enter")
            await settle(pilot, 8)
            await pilot.press(*"/say hello", "enter")
            await settle(pilot, 8)
            sent = [(e.body.get("kind"), e.body.get("text")) for e in store.read_after("r1", 0) if e.type is EventType.QUESTION]
            assert ("prompt", "build accounts") in sent and ("chat", "hello") in sent
            assert app.query_one("#composer").text == ""
    run(go())


def test_host_approves_a_plan_from_the_panel(store):
    post(store, EventType.QUESTION, {"text": "add accounts", "kind": "prompt"}, "q1", sender="bob", thread="p-1")
    post(store, EventType.PROPOSAL, {"plan_id": "plan-p-1", "reply": "Splitting", "tasks": [{"area": "schema", "instruction": "users"}]},
         "pl1", agent="planner", thread="p-1")
    post(store, EventType.SYSTEM, {"subtype": "verdict_approved", "verdict_id": "v1", "by": "alice"}, "va")

    async def go():
        app = CouncilApp(store, "r1", "alice", poll_s=0.05)
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(pilot)
            assert "Approve plan" in str(app.query_one("#action-text").render())
            assert not app.query_one("#composer").display  # the panel replaces the composer while it waits
            await pilot.press("a")
            await settle(pilot, 10)
            assert load_state(store, "r1").plans["plan-p-1"].status == "approved"
            assert app.query_one("#composer").display
    run(go())


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def test_composer_attaches_images_from_at_paths_and_pasted_drops(store, tmp_path):
    from textual import events
    from aletheore.council.bundle_store import LocalBundleStore
    img = tmp_path / "my shot.png"
    img.write_bytes(PNG_BYTES)

    async def go():
        app = CouncilApp(store, "r1", "carol", poll_s=0.05, blobs=LocalBundleStore(tmp_path / "blobs"))
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(pilot)
            composer = app.query_one("#composer")
            import os
            dropped = f'"{img}"' if os.name == "nt" else str(img).replace(" ", "\\ ")
            composer.post_message(events.Paste(dropped))  # a file dropped onto the terminal
            await settle(pilot, 3)
            assert composer.text.startswith("@") and "my shot.png" in composer.text
            await pilot.press(*" make it look like this", "enter")
            await settle(pilot, 10)
            sent = [e for e in store.read_after("r1", 0) if e.type is EventType.QUESTION and e.sender_participant == "carol"]
            assert len(sent) == 1 and sent[0].body["attachments"][0]["name"] == "my shot.png"
            assert "make it look like this" in sent[0].body["text"] and "@" not in sent[0].body["text"]
    run(go())


def test_bad_image_is_reported_and_nothing_is_sent(store, tmp_path):
    from aletheore.council.bundle_store import LocalBundleStore
    fake = tmp_path / "fake.png"
    fake.write_bytes(b"MZ" + b"\x00" * 40)

    async def go():
        app = CouncilApp(store, "r1", "carol", poll_s=0.05, blobs=LocalBundleStore(tmp_path / "blobs"))
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(pilot)
            await pilot.press(*f"look @{fake}", "enter")
            await settle(pilot, 8)
            assert not [e for e in store.read_after("r1", 0) if e.sender_participant == "carol"]
    run(go())


def test_composer_takes_several_lines_and_grows(store):
    async def go():
        app = CouncilApp(store, "r1", "carol", poll_s=0.05)
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(pilot)
            composer = app.query_one("#composer")
            one_line = composer.region.height
            await pilot.press(*"line one", "ctrl+j", *"line two", "ctrl+j", *"line three")
            await settle(pilot, 4)
            assert composer.text == "line one\nline two\nline three"
            assert composer.region.height > one_line
            await pilot.press("enter")
            await settle(pilot, 8)
            sent = [e.body["text"] for e in store.read_after("r1", 0) if e.type is EventType.QUESTION and e.sender_participant == "carol"]
            assert sent == ["line one\nline two\nline three"]
            assert composer.text == "" and composer.region.height == one_line
    run(go())


def test_ctrl_v_and_slash_paste_attach_a_clipboard_image(store, tmp_path):
    img = tmp_path / "clip.png"
    img.write_bytes(PNG_BYTES)
    answers = iter([img, img, None])

    async def go():
        app = CouncilApp(store, "r1", "carol", poll_s=0.05, clipboard=lambda dest: next(answers))
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(pilot)
            composer = app.query_one("#composer")
            await pilot.press("ctrl+v")
            await settle(pilot, 3)
            assert f"@{img}" in composer.text
            composer.clear()
            await pilot.press(*"/paste", "enter")
            await settle(pilot, 4)
            assert f"@{img}" in composer.text  # inserted for review, not sent
            assert not [e for e in store.read_after("r1", 0) if e.sender_participant == "carol"]
            composer.clear()
            await pilot.press(*"/paste", "enter")  # clipboard has no image this time
            await settle(pilot, 4)
            assert "@" not in composer.text
    run(go())


def test_o_opens_a_messages_image_in_the_system_viewer(store, tmp_path):
    from aletheore.council.attachments import upload_image
    from aletheore.council.bundle_store import LocalBundleStore
    blobs = LocalBundleStore(tmp_path / "blobs")
    img = tmp_path / "design.png"
    img.write_bytes(PNG_BYTES)
    ref = upload_image(blobs, "r1", img)
    post(store, EventType.QUESTION, {"text": "like this", "kind": "prompt", "attachments": [ref]}, "qi", sender="bob", thread="p-9")
    opened = []

    async def go():
        app = CouncilApp(store, "r1", "carol", poll_s=0.05, blobs=blobs, opener=opened.append)
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(pilot)
            msg = next(m for m in app.query(MessageView) if m.item.attachments)
            assert "o to open" in str(msg.render())
            msg.focus()
            await settle(pilot, 2)
            await pilot.press("o")
            await settle(pilot, 10)
            assert len(opened) == 1 and opened[0].read_bytes() == PNG_BYTES and opened[0].suffix == ".png"
    run(go())


# ---- debates, alerts, badges, room switcher ---------------------------------------------------------------------
def test_debate_command_posts_a_request_and_rejects_unknown_areas(store):
    async def go():
        app = CouncilApp(store, "r1", "carol", poll_s=0.05)
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(pilot)
            await pilot.press(*"/debate redis schema should ids be int or uuid", "enter")
            await settle(pilot, 8)
            reqs = [e.body for e in store.read_after("r1", 0) if e.type is EventType.QUESTION and e.body.get("kind") == "debate"]
            assert reqs == [{"kind": "debate", "a": "redis", "b": "schema", "subject": "should ids be int or uuid",
                             "text": "debate redis vs schema: should ids be int or uuid"}]
            await pilot.press(*"/debate redis nowhere whatever", "enter")
            await settle(pilot, 6)
            assert len([e for e in store.read_after("r1", 0) if e.body.get("kind") == "debate"]) == 1  # the second was refused
    run(go())


def test_a_new_thing_waiting_on_you_rings_the_bell_and_shows_in_the_title(store):
    rang = []

    async def go():
        app = CouncilApp(store, "r1", "bob", poll_s=0.05)
        app.bell = lambda: rang.append(1)
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(pilot)
            assert rang == []  # what was already waiting when you opened the window is not news
            assert "1 waiting" in app.title
            post(store, EventType.DECISION, {"verdict_id": "v7", "decision": "another", "affected_area": "schema",
                                             "winning_branch": "council/r1/bob-claude"}, "c70", thread="t7")
            await settle(pilot, 8)
            assert rang == [1] and "2 waiting" in app.title
    run(go())


def test_header_shows_title_mode_and_how_an_autonomous_room_ended(tmp_path):
    s = SqliteRoomStore(tmp_path / "r.db")
    s.create_room("r9", "alice", title="Signup flow", mode="autonomous")
    s.append(EventDraft("r9", "a", "room", EventType.SYSTEM, "alice", None, None, {"subtype": "room_created", "host": "alice"}))
    s.append(EventDraft("r9", "b", "room", EventType.SYSTEM, "alice", "planner", None, {"subtype": "room_done", "outcome": "done", "summary": "all built"}))

    async def go():
        app = CouncilApp(s, "r9", "alice", poll_s=0.05)
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(pilot, 8)
            top = str(app.query_one("#top").render())
            assert "Signup flow" in top and "autonomous" in top and "finished (done)" in top
    run(go())


def test_room_switcher_lists_my_rooms_and_switches_the_whole_view(store):
    store.create_room("r2", "alice", title="Second room")
    store.append(EventDraft("r2", "z1", "room", EventType.SYSTEM, "alice", None, None, {"subtype": "room_created", "host": "alice"}))
    store.append(EventDraft("r2", "z2", "room", EventType.QUESTION, "alice", None, None, {"text": "hello from the second room", "kind": "chat"}))

    async def go():
        app = CouncilApp(store, "r1", "alice", poll_s=0.05)
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(pilot)
            assert not any("second room" in str(m.render()) for m in app.query(MessageView))
            await pilot.press("ctrl+r")
            await settle(pilot, 4)
            options = app.screen.query_one("#room-list")
            assert options.option_count == 2
            await pilot.press("down", "enter")
            await settle(pilot, 10)
            assert app.room_id == "r2"
            assert any("hello from the second room" in str(m.render()) for m in app.query(MessageView))
            assert "r2" in str(app.query_one("#top").render()) or "Second room" in str(app.query_one("#top").render())
    run(go())


# ---- asking the room, one agent, a person; /task; the hourly limit --------------------------------------------------
def sent_by(store, who):
    return [e.body for e in store.read_after("r1", 0) if e.type is EventType.QUESTION and e.sender_participant == who]


def test_at_mentions_ask_the_room_an_agent_or_a_person_and_unknown_ones_stay_ordinary_prompts(store):
    async def go():
        app = CouncilApp(store, "r1", "carol", poll_s=0.05)
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(pilot)
            for line in ("@all what do you think about ids?", "@bob-claude is the schema ok?", "@alice any thoughts?",
                         "/discuss anyone see a problem here?", "@nobody-like-this please build it"):
                await pilot.press(*line, "enter")
                await settle(pilot, 6)
            got = sent_by(store, "carol")
            asks = [(b["to"], b["question"]) for b in got if b["kind"] == "ask"]
            assert asks == [("all", "what do you think about ids?"), ("bob-claude", "is the schema ok?"),
                            ("alice", "any thoughts?"), ("all", "anyone see a problem here?")]
            prompts = [b for b in got if b["kind"] == "prompt"]
            assert len(prompts) == 1 and prompts[0]["text"].startswith("@nobody-like-this")  # not a real target: an ordinary request
    run(go())


def test_slash_task_proposes_work_for_an_area(store):
    async def go():
        app = CouncilApp(store, "r1", "carol", poll_s=0.05)
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(pilot)
            await pilot.press(*"/task redis write the cache", "enter")
            await settle(pilot, 6)
            await pilot.press(*"/task nowhere do something", "enter")
            await settle(pilot, 6)
            tasks = [b for b in sent_by(store, "carol") if b["kind"] == "task"]
            assert tasks == [{"kind": "task", "area": "redis", "instruction": "write the cache", "text": "/task redis: write the cache"}]
    run(go())


async def wait_until(pilot, cond, secs=20.0):
    """Wait for a condition instead of guessing a delay: a slow disk (Windows) can take much longer than a fixed pause."""
    end = time.monotonic() + secs
    while time.monotonic() < end:
        if cond():
            return True
        await pilot.pause(0.1)
    return False


def test_your_own_hourly_limit_is_checked_before_sending(store):
    async def go():
        app = CouncilApp(store, "r1", "carol", poll_s=0.05, ask_limit=2)
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(pilot)
            for i in range(3):
                await pilot.press(*f"@all question {i}", "enter")
            count = lambda: len([b for b in sent_by(store, "carol") if b["kind"] == "ask"])
            assert await wait_until(pilot, lambda: count() >= 2)
            await settle(pilot, 15)  # and give a wrongly-sent third one time to show up
            assert count() == 2  # the third was held back with a notice
    run(go())


def test_messages_sent_in_quick_succession_are_all_sent_even_when_sending_is_slow(store, monkeypatch):
    import time as _time
    import aletheore.council.tui as tui
    real = tui.send_message

    def slow(*a, **k):
        _time.sleep(3.0)
        return real(*a, **k)

    monkeypatch.setattr(tui, "send_message", slow)

    async def go():
        app = CouncilApp(store, "r1", "carol", poll_s=0.05)
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(pilot)
            for text in ("first request", "second request", "third request"):
                await pilot.press(*text, "enter")
            await settle(pilot, 90)
            sent = [b["text"] for b in sent_by(store, "carol")]
            assert sent == ["first request", "second request", "third request"] or sorted(sent) == sorted(["first request", "second request", "third request"])
    run(go())


def test_the_hourly_limit_counts_questions_still_being_sent(store, monkeypatch):
    import time as _time
    import aletheore.council.tui as tui
    real = tui.send_ask

    def slow(*a, **k):
        _time.sleep(3.0)  # slower than the poll: the log has not shown the earlier asks yet
        return real(*a, **k)

    monkeypatch.setattr(tui, "send_ask", slow)

    async def go():
        app = CouncilApp(store, "r1", "carol", poll_s=0.05, ask_limit=2)
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(pilot)
            for i in range(4):
                await pilot.press(*f"@all question {i}", "enter")
            await settle(pilot, 90)
            assert len([b for b in sent_by(store, "carol") if b["kind"] == "ask"]) == 2
    run(go())
