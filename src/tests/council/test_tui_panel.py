"""The side panel: agent progress, members with last seen, security summary, count badges."""

import asyncio
from datetime import datetime, timedelta, timezone

from aletheore.council import tui_panel
from aletheore.council.protocol import EventDraft, EventType
from aletheore.council.sqlite_store import SqliteRoomStore
from aletheore.council.tui import CouncilApp
from aletheore.council.view_model import AgentRow

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)


class T:
    def __init__(self, area, status):
        self.area, self.status = area, status


def test_bar_fills_in_proportion_and_sweeps_when_total_unknown():
    assert tui_panel.bar(2, 4, 8) == "████░░░░" and tui_panel.bar(0, 4, 8) == "░" * 8 and tui_panel.bar(9, 4, 8) == "█" * 8
    frames = {tui_panel.bar(0, 0, 8, f) for f in range(12)}
    assert len(frames) > 4 and all(len(f) == 8 for f in frames)  # it moves, and never changes length
    assert tui_panel.bar(0, 0, 8) == "░" * 8


def test_agent_rows_show_busy_idle_and_progress():
    rows = [AgentRow("alice-codex", "alice", "redis", "working", "building redis", "now"),
            AgentRow("bob-claude", "bob", "api", "idle", "", "3m")]
    text = tui_panel.agents_section(rows, [T("redis", "done"), T("redis", "running"), T("api", "done")], 26, 3, "◐").plain
    assert "◐ alice-codex" in text and "building redis" in text and "1/2" in text and "██" in text
    assert "○ bob-claude" in text and "idle, 3m" in text
    assert "no areas yet" in tui_panel.agents_section([], [], 26, 0, "◐").plain


def test_members_show_last_seen_not_online():
    seen = {"alice": (NOW - timedelta(seconds=30)).isoformat(), "bob": (NOW - timedelta(hours=2)).isoformat()}
    text = tui_panel.members_section(["alice", "bob", "carol"], "alice", "bob", seen, NOW, 26).plain
    assert "active now" in text and "last seen 2h ago" in text and "not seen yet" in text
    assert "online" not in text and "host" in text and "you" in text


def test_security_summary_numbers_match_the_sealing_store():
    class S:
        rejected, unreadable, gaps = 2, 1, [(3, 5)]

    facts = tui_panel.security_facts(S())
    assert facts["rejected"] == 2 and facts["unreadable"] == 1 and facts["gaps"] == 1
    text = tui_panel.security_section(True, facts, 40).plain
    assert "SEALED" in text and "40 read and checked" in text and "2 ignored" in text and "1 unreadable" in text and "1 gaps" in text
    clean = tui_panel.security_section(True, {"epoch": 3, "rejected": 0, "unreadable": 0, "gaps": 0}, 7).plain
    assert "key 3" in clean and "nothing ignored" in clean and "!" not in clean
    assert "not sealed" in tui_panel.security_section(False, {}, 0).plain
    assert tui_panel.security_facts(object()) == {}


def test_badge_shows_only_when_something_waits():
    assert tui_panel.badge(0).plain == "" and tui_panel.badge(3).plain.strip() == "3"


def test_app_panel_has_every_section_and_the_rooms_badge(tmp_path):
    s = SqliteRoomStore(tmp_path / "r.db")
    s.create_room("r1", "alice", title="Cache work")
    s.append(EventDraft("r1", "c0", "room", EventType.SYSTEM, "alice", None, None, {"subtype": "room_created", "host": "alice"}))
    s.append(EventDraft("r1", "c1", "room", EventType.SYSTEM, "alice", None, None,
                        {"subtype": "area_defined", "name": "redis", "globs": ["redis/**"], "owner": "alice", "agent_id": "alice-codex"}))

    async def go():
        app = CouncilApp(s, "r1", "alice", poll_s=0.05)
        async with app.run_test(size=(140, 40)) as pilot:
            for _ in range(8):
                await pilot.pause(0.08)
            side = str(app.query_one("#sidebar").render())
            for word in ("§ AGENTS", "§ MEMBERS", "§ SECURITY", "alice-codex", "active now", "not sealed"):
                assert word in side, word

    asyncio.run(go())


def test_files_today_shows_the_allowance_and_says_so_plainly_when_used_up():
    MB = 1048576
    assert tui_panel.budget_section(None).plain == "" and tui_panel.budget_section({"cap_bytes": 0}).plain == ""
    ok = tui_panel.budget_section({"cap_bytes": 5120 * MB, "used_bytes": 1024 * MB, "resets_in_s": 3600}).plain
    assert "FILES TODAY" in ok and "1024/5120 MB" in ok and "used up" not in ok
    near = tui_panel.budget_section({"cap_bytes": 100 * MB, "used_bytes": 85 * MB, "resets_in_s": 7200}).plain
    assert "nearly used up" in near
    full = tui_panel.budget_section({"cap_bytes": 100 * MB, "used_bytes": 100 * MB, "resets_in_s": 7200}).plain
    assert "used up, frees in about 2 h" in full and "messages still work" in full
