"""Waking over an end-to-end sealed room on a real signed-in server: the worker reads the real messages, answers inside the seal, and the
server only ever sees envelopes."""
import types

import pytest

from aletheore.council.daemon import WorkerDaemon
from aletheore.council.protocol import EventDraft, EventType
from aletheore.council.runners import ScriptedRunner
from aletheore.council.waiting import wait_for
from tests.council.test_e2e_store import ROOM, Net  # noqa: F401


@pytest.fixture
def net(tmp_path):
    n = Net(tmp_path)
    n.host.append(EventDraft(ROOM, "area:x", "room", EventType.SYSTEM, "hosty", None, None,
                             {"subtype": "area_defined", "name": "x", "globs": ["x/**"], "owner": "megan", "agent_id": "meg-claude"}))
    return n


def fake_room(tmp_path):
    return types.SimpleNamespace(room_id=ROOM, host="hosty", _worktree=lambda a: (tmp_path, "b"), _mcp_config=lambda p, a: tmp_path / "m.json",
                                 _identity=lambda p, a: {}, close_brokers=lambda: None)


def test_a_sealed_mention_is_answered_and_the_server_sees_only_envelopes(net, tmp_path):
    runner = ScriptedRunner(final_text="sealed answer")
    d = WorkerDaemon(net.meg, fake_room(tmp_path), "megan", {"meg-claude": runner})
    net.host.append(EventDraft(ROOM, "m1", "room", EventType.QUESTION, "hosty", None, None, {"text": "@meg-claude what is the status", "kind": "chat"}))
    d.run_once()
    assert len(runner.calls) == 1
    got = [e for e in net.host.read_after(ROOM, 0) if e.body.get("wake_for")]
    assert len(got) == 1 and got[0].sender_agent == "meg-claude" and got[0].body["text"] == "sealed answer"
    raw = repr(net.raw_events())
    assert "sealed answer" not in raw and "what is the status" not in raw and "wake_for" not in raw


def test_wait_works_in_a_sealed_room(net, tmp_path):
    net.host.append(EventDraft(ROOM, "m2", "room", EventType.QUESTION, "hosty", None, None, {"text": "@megan ping", "kind": "chat"}))
    got = wait_for(net.meg, ROOM, "megan", since=0, timeout_s=0.5, poll_s=0.05)
    assert [e.body["text"] for e in got] == ["@megan ping"]
