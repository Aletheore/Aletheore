import pytest

from aletheore.council.protocol import (
    MAX_BODY_BYTES,
    EventType,
    ProtocolError,
    evidence_ref,
    validate_draft,
)

SENDER = dict(room_id="r1", sender_participant="alice", sender_agent="claude")


def raw(**over):
    base = {"type": "claim", "client_event_id": "c1", "thread_id": "t1", "reply_to": None, "body": {"text": "hi"}}
    base.update(over)
    return base


def test_unknown_type_rejected():
    with pytest.raises(ProtocolError):
        validate_draft(raw(type="shout"), **SENDER)


def test_oversized_body_rejected():
    with pytest.raises(ProtocolError):
        validate_draft(raw(body={"text": "x" * (MAX_BODY_BYTES + 1)}), **SENDER)


def test_missing_client_event_id_rejected():
    r = raw()
    del r["client_event_id"]
    with pytest.raises(ProtocolError):
        validate_draft(r, **SENDER)


def test_client_supplied_sender_cannot_be_forged():
    d = validate_draft(raw(sender={"participant_id": "mallory", "agent_id": "evil"}), **SENDER)
    assert d.sender_participant == "alice"
    assert d.sender_agent == "claude"


def test_evidence_ref_requires_full_40_char_sha():
    with pytest.raises(ProtocolError):
        evidence_ref("abc123", "a.py", 1, 2)
    ref = evidence_ref("a" * 40, "a.py", 1, 2, symbol="f")
    assert ref == {"commit_sha": "a" * 40, "path": "a.py", "start_line": 1, "end_line": 2, "symbol": "f"}


def test_claim_without_evidence_allowed_but_flagged():
    d = validate_draft(raw(), **SENDER)
    assert d.type is EventType.CLAIM
    assert "evidence" not in d.body


def test_body_must_be_dict_and_not_too_deep():
    with pytest.raises(ProtocolError):
        validate_draft(raw(body="text"), **SENDER)
    deep = cur = {}
    for _ in range(12):
        cur["n"] = {}
        cur = cur["n"]
    with pytest.raises(ProtocolError):
        validate_draft(raw(body=deep), **SENDER)


@pytest.mark.parametrize("bad", ["a\nb", "a b", "x<<y", "t\x1b[2J", ""])
def test_thread_id_must_be_plain(bad):
    with pytest.raises(ProtocolError):
        validate_draft(raw(thread_id=bad), **SENDER)
