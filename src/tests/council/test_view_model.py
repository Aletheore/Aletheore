from datetime import datetime, timedelta, timezone

from aletheore.council.protocol import Event, EventType
from aletheore.council.state import project
from aletheore.council.view_model import (
    agent_rows, build_stream, clean_text, needs_me, relative_time, summarize,
)

NOW = datetime(2026, 11, 9, 12, 0, 0, tzinfo=timezone.utc)


def ev(seq, etype, sender, body, agent=None, thread="room", minutes_ago=1):
    ts = (NOW - timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")
    return Event("r1", f"c{seq}", thread, etype, sender, agent, None, body, f"e{seq}", seq, ts)


def base_events():
    return [
        ev(1, EventType.SYSTEM, "alice", {"subtype": "room_created", "host": "alice"}),
        ev(2, EventType.SYSTEM, "alice", {"subtype": "area_defined", "name": "redis", "globs": ["redis/**"], "owner": "alice", "agent_id": "alice-codex"}),
        ev(3, EventType.SYSTEM, "alice", {"subtype": "area_defined", "name": "schema", "globs": ["schema/**"], "owner": "bob", "agent_id": "bob-claude"}),
    ]


def test_clean_text_strips_markdown_links_and_terminal_escapes():
    raw = "**Decision:** use `UUID` see [cache.py](</Users/x/cache.py>) \x1b[31mred\x1b[0m\x07\n\n\n\nnext"
    out = clean_text(raw)
    assert out == "Decision: use UUID see cache.py red\n\nnext"


def test_summarize_clips_to_lines_and_says_how_much_more():
    text = "\n".join(f"line {i}" for i in range(10))
    short, more = summarize(text, max_lines=3)
    assert short.splitlines() == ["line 0", "line 1", "line 2"] and more == 7
    assert summarize("one line", 3) == ("one line", 0)


def test_relative_time():
    assert relative_time("2026-11-09T11:59:40Z", NOW) == "now"
    assert relative_time("2026-11-09T11:55:00Z", NOW) == "5m"
    assert relative_time("2026-11-09T09:00:00Z", NOW) == "3h"
    assert relative_time("garbage", NOW) == ""


def test_stream_shows_messages_with_author_and_hides_bookkeeping():
    events = base_events() + [
        ev(4, EventType.TASK, "alice", {"op": "request", "task_id": "t1", "area": "schema", "instruction": "x"}),
        ev(5, EventType.TASK, "bob", {"op": "claimed", "task_id": "t1"}, agent="bob-claude"),
        ev(6, EventType.CLAIM, "bob", {"text": "**UUID** wins"}, agent="bob-claude", thread="ids"),
    ]
    items = build_stream(events, project(events, host="alice"), NOW)
    kinds = [(i.kind, i.author) for i in items]
    assert ("message", "bob") in kinds
    assert not any(i.text.startswith("claimed") for i in items)
    msg = next(i for i in items if i.kind == "message")
    assert msg.text == "UUID wins" and msg.agent == "bob-claude" and msg.label == "claim" and msg.thread == "ids"
    assert msg.age == "1m"


def test_failures_are_muted_one_liners():
    events = base_events() + [ev(4, EventType.SYSTEM, "bob", {"subtype": "turn_failed", "note": "claude.CMD is a shim"}, agent="bob-claude")]
    items = build_stream(events, project(events, host="alice"), NOW)
    note = items[-1]
    assert note.kind == "notice" and note.muted and "claude.CMD is a shim" in note.text


def test_agent_rows_show_working_when_a_task_runs_or_a_turn_is_pending():
    events = base_events() + [
        ev(4, EventType.TASK, "alice", {"op": "request", "task_id": "t1", "area": "schema", "instruction": "x"}),
        ev(5, EventType.TASK, "bob", {"op": "started", "task_id": "t1", "branch": "council/r1/bob-claude"}, agent="bob-claude"),
    ]
    rows = {r.agent_id: r for r in agent_rows(events, project(events, host="alice"), NOW)}
    assert rows["bob-claude"].status == "working" and "schema" in rows["bob-claude"].detail
    assert rows["alice-codex"].status == "idle" and rows["alice-codex"].owner == "alice"

    events += [ev(6, EventType.TASK, "bob", {"op": "finished", "task_id": "t1"}, agent="bob-claude"),
               ev(7, EventType.TASK, "alice", {"op": "debate_turn", "turn_id": "T", "participant": "alice", "agent": "alice-codex", "round": 1, "instruction": "i"}, thread="ids")]
    rows = {r.agent_id: r for r in agent_rows(events, project(events, host="alice"), NOW)}
    assert rows["bob-claude"].status == "idle" and rows["alice-codex"].status == "working"
    events.append(ev(8, EventType.CLAIM, "alice", {"text": "answer", "turn_id": "T"}, agent="alice-codex", thread="ids"))
    rows = {r.agent_id: r for r in agent_rows(events, project(events, host="alice"), NOW)}
    assert rows["alice-codex"].status == "idle"


def test_needs_me_lists_approvals_for_owner_and_winner_choice_for_host():
    events = base_events() + [
        ev(4, EventType.DECISION, "alice", {"verdict_id": "v1", "decision": "d", "affected_area": "schema", "winning_branch": "b1"}, thread="ids"),
        ev(5, EventType.DECISION, "alice", {"verdict_id": "v2", "decision": "tie", "affected_area": "redis", "winning_branch": None,
                                            "candidates": {"alice-codex": "b2", "bob-claude": "b3"}}, thread="t2"),
    ]
    state = project(events, host="alice")
    bob = needs_me(state, "bob")
    assert [(n.kind, n.verdict_id) for n in bob] == [("approve", "v1")]
    alice = needs_me(state, "alice")
    assert [(n.kind, n.verdict_id) for n in alice] == [("approve", "v1"), ("choose", "v2")]
    assert alice[1].options == ["alice-codex", "bob-claude"]


def test_code_fences_disappear_but_code_stays():
    assert clean_text("Evidence:\n```sql\nSELECT 1;\n```\ndone") == "Evidence:\n\nSELECT 1;\n\ndone"


from aletheore.council.view_model import normalize_pasted_path, parse_composer


def test_parse_composer_pulls_out_image_paths_in_several_spellings(tmp_path):
    a = tmp_path / "shot one.png"
    a.write_bytes(b"x")
    b = tmp_path / "b.jpg"
    b.write_bytes(b"x")
    text, paths = parse_composer(f'match @"{a}" and @{b} please')
    assert text == "match and please" and paths == [a, b]
    import os
    if os.name != "nt":  # shell-style escapes are a macOS/Linux terminal habit; Windows quotes paths instead
        esc = str(a).replace(" ", "\\ ")
        assert parse_composer(f"see @{esc}")[1] == [a]
    # not a file and not an image name: it is just a mention, left alone
    assert parse_composer("ping @bob about it") == ("ping @bob about it", [])
    # image-looking but missing: still treated as an attachment so the error is shown instead of silently dropped
    assert parse_composer("see @nope.png")[1] == [__import__("pathlib").Path("nope.png")]


def test_normalize_pasted_path_only_accepts_a_single_existing_image(tmp_path):
    img = tmp_path / "my pic.png"
    img.write_bytes(b"x")
    import os
    if os.name != "nt":
        assert normalize_pasted_path(str(img).replace(" ", "\\ ")) == str(img)
    else:
        assert normalize_pasted_path(str(img)) == str(img)  # a Windows path keeps its backslashes
    assert normalize_pasted_path(f"'{img}'") == str(img)
    assert normalize_pasted_path(f"file://{str(img).replace(' ', '%20')}") == str(img)
    assert normalize_pasted_path("some pasted sentence") is None
    assert normalize_pasted_path(str(tmp_path / "gone.png")) is None
    txt = tmp_path / "a.txt"
    txt.write_text("x")
    assert normalize_pasted_path(str(txt)) is None
    assert normalize_pasted_path(f"{img}\n{img}") is None


def test_attachments_show_in_the_stream_as_file_lines():
    events = base_events() + [ev(4, EventType.QUESTION, "bob", {"text": "match this", "kind": "prompt",
                              "attachments": [{"name": "ui.png", "sha256": "a" * 64, "size": 204800, "mime": "image/png"}]}, thread="p-1")]
    msg = next(i for i in build_stream(events, project(events, host="alice"), NOW) if i.kind == "message")
    assert "match this" in msg.text and "ui.png" in msg.text and "200 KB" in msg.text


def test_room_outcomes_show_in_the_stream():
    events = base_events() + [ev(4, EventType.SYSTEM, "alice", {"subtype": "room_done", "outcome": "done", "summary": "cache written and reviewed"}, agent="planner"),
                              ev(5, EventType.SYSTEM, "alice", {"subtype": "conductor_stopped"})]
    items = build_stream(events, project(events, host="alice"), NOW)
    done = next(i for i in items if "finished" in i.text)
    assert "done" in done.text and "cache written and reviewed" in done.text and not done.muted


def test_answers_summaries_asks_and_limits_are_labelled_clearly():
    events = base_events() + [
        ev(4, EventType.QUESTION, "bob", {"kind": "ask", "to": "all", "question": "ints or uuids?", "text": "@all ints or uuids?"}, thread="q-1"),
        ev(5, EventType.CLAIM, "alice", {"text": "ints", "round": 1, "kind": "answer"}, agent="alice-codex", thread="q-1"),
        ev(6, EventType.CHALLENGE, "bob", {"text": "uuids", "round": 2, "kind": "answer"}, agent="bob-claude", thread="q-1"),
        ev(7, EventType.RESULT, "alice", {"text": "They differ: ...", "kind": "summary"}, agent="planner", thread="q-1"),
        ev(8, EventType.SYSTEM, "alice", {"subtype": "ask_limited", "asker": "bob", "limit": 25}, thread="q-2"),
        ev(9, EventType.SYSTEM, "alice", {"subtype": "ask_done", "ok": False, "note": "no agent or person named zed in this room"}, thread="q-3"),
        ev(10, EventType.SYSTEM, "alice", {"subtype": "ask_done", "ok": True}, thread="q-1"),
        ev(11, EventType.QUESTION, "bob", {"kind": "task", "area": "redis", "instruction": "write it", "text": "/task redis: write it"}, thread="p-5"),
    ]
    items = build_stream(events, project(events, host="alice"), NOW)
    by = {(i.author, i.label): i for i in items if i.kind == "message"}
    asks = [i.text for i in items if i.kind == "message" and i.label == "asks"]
    assert asks == ["@all ints or uuids?", "/task redis: write it"]
    assert ("alice", "answer") in by and ("bob", "answer") in by and ("alice", "summary") in by
    notices = [i.text for i in items if i.kind == "notice"]
    assert any("limit" in t and "bob" in t and "25" in t for t in notices)
    assert any("not answered" in t and "zed" in t for t in notices)
    assert not any(t.startswith("ask done") for t in notices)  # a normal finish is not worth a line
