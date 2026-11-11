"""Signed, encrypted envelopes: what the server may see, and every way a reader refuses a bad one."""

import json
import random

import pytest

from aletheore.council import crypto
from aletheore.council.envelope import (HostOnlyError, Keyring, check_envelope, envelope_class, inspect_event, open_event, seal_event,
                                        thread_tag)
from aletheore.council.identity import Keystore
from aletheore.council.protocol import Event, EventDraft, EventType, ProtocolError

ROOM = "r1"


class Cast:
    def __init__(self, tmp_path):
        ks = Keystore(tmp_path / "keys")
        self.host, self.meg, self.moe = ks.load_or_create("host"), ks.load_or_create("meg"), ks.load_or_create("moe")
        self.meg2 = ks.load_or_create("meg-second-device")
        self.key = crypto.random_bytes(32)
        self.keyring = Keyring({1: self.key})
        self.members = {"hosty": [self.host.public()], "meg": [self.meg.public()], "moe": [self.moe.public()]}


@pytest.fixture
def cast(tmp_path):
    return Cast(tmp_path)


def draft(sender="meg", etype=EventType.CLAIM, text="the cache is stale", thread="task-1", agent="meg-claude", cid="c1", body=None, reply_to=None):
    return EventDraft(ROOM, cid, thread, etype, sender, agent, reply_to, body if body is not None else {"text": text})


def as_event(d: EventDraft, seq=1, room=None) -> Event:
    return Event(room or d.room_id, d.client_event_id, d.thread_id, d.type, d.sender_participant, d.sender_agent, d.reply_to, d.body,
                 event_id=f"evt_{seq}", seq=seq, created_at="2026-11-09T00:00:00Z")


def sealed(cast, d=None, who=None, key=None, epoch=1):
    d = d or draft()
    return seal_event(d, who or cast.meg, key or cast.key, epoch)


def test_seal_hides_everything_but_the_routing_fields(cast):
    d = draft(text="launch codes are 0000", thread="secret-thread", agent="meg-claude", reply_to="evt_9")
    env = sealed(cast, d)
    assert env.type is EventType.ENVELOPE and env.sender_participant == "meg" and env.client_event_id != "c1" and len(env.client_event_id) == 32
    assert env.sender_agent is None and env.reply_to is None
    assert env.thread_id == thread_tag(cast.key, "secret-thread") and len(env.thread_id) == 32
    dump = json.dumps({"body": env.body, "thread": env.thread_id, "agent": env.sender_agent, "reply": env.reply_to})
    for secret in ("launch codes", "secret-thread", "meg-claude", "claim", "evt_9", '"text"'):
        assert secret not in dump
    assert set(env.body) == {"v", "epoch", "class", "dev", "nonce", "ct", "sig"} and env.body["v"] == 2 and env.body["class"] == "member"
    assert env.body["dev"] == cast.meg.device_id


def test_round_trip_preserves_the_event_exactly(cast):
    d = draft(reply_to="evt_3", body={"text": "héllo ‮", "evidence": [{"path": "a.py", "start_line": 1}], "n": [1, 2, {"x": None}]})
    got = open_event(as_event(sealed(cast, d), seq=7), cast.keyring, cast.members, "hosty")
    assert got is not None
    assert (got.type, got.thread_id, got.sender_participant, got.sender_agent, got.reply_to, got.body, got.client_event_id, got.seq) == \
        (EventType.CLAIM, "task-1", "meg", "meg-claude", "evt_3", d.body, "c1", 7)


def test_every_event_type_and_a_missing_agent_round_trip(cast):
    for etype in (t for t in EventType if t is not EventType.ENVELOPE):  # an envelope is never sealed inside an envelope
        d = draft(etype=etype, agent=None, body={"text": "x", "op": "assign", "subtype": "room_created"} if etype in (EventType.TASK, EventType.SYSTEM) else {"text": "x"})
        who = cast.host if envelope_class(d.type, d.body) == "host" else cast.meg
        sender = "hosty" if who is cast.host else "meg"
        d = EventDraft(d.room_id, d.client_event_id, d.thread_id, d.type, sender, None, None, d.body)
        got = open_event(as_event(seal_event(d, who, cast.key, 1)), cast.keyring, cast.members, "hosty")
        assert got is not None and got.type is etype and got.sender_agent is None


def test_member_cannot_forge_another_members_event(cast):
    # moe's device signs, but the envelope says it is meg's: the signature does not match meg's key
    forged = seal_event(draft(sender="meg"), cast.moe, cast.key, 1)
    assert open_event(as_event(forged), cast.keyring, cast.members, "hosty") is None
    # moe names meg's device while signing with moe's key
    body = dict(sealed(cast).body)
    stolen = seal_event(draft(sender="meg"), cast.moe, cast.key, 1)
    assert open_event(as_event(EventDraft(ROOM, "c1", stolen.thread_id, EventType.ENVELOPE, "meg", None, None, {**stolen.body, "dev": cast.meg.device_id})),
                      cast.keyring, cast.members, "hosty") is None
    assert body["dev"] == cast.meg.device_id


def test_forged_host_only_event_from_a_member_is_ignored_even_if_the_server_accepted_it(cast):
    decision = draft(sender="meg", etype=EventType.DECISION, body={"text": "I decide"})
    assert envelope_class(decision.type, decision.body) == "host"
    env = seal_event(decision, cast.meg, cast.key, 1)  # declared class "host", sent by a member
    assert env.body["class"] == "host"
    assert open_event(as_event(env), cast.keyring, cast.members, "hosty") is None
    from aletheore.council import envelope

    lying = envelope._seal(decision, cast.meg, cast.key, 1, "member")  # noqa: SLF001  declares itself an ordinary member event
    assert open_event(as_event(lying), cast.keyring, cast.members, "hosty") is None  # the server could not tell; the reader can


def test_declared_class_must_match_decrypted_content(cast):
    from aletheore.council import envelope

    claim_as_host = envelope._seal(draft(sender="hosty", etype=EventType.CLAIM), cast.host, cast.key, 1, "host")  # noqa: SLF001
    assert open_event(as_event(claim_as_host), cast.keyring, cast.members, "hosty") is None
    decision_as_member = envelope._seal(draft(sender="hosty", etype=EventType.DECISION), cast.host, cast.key, 1, "member")  # noqa: SLF001
    assert open_event(as_event(decision_as_member), cast.keyring, cast.members, "hosty") is None
    honest = seal_event(draft(sender="hosty", etype=EventType.DECISION, body={"text": "done"}), cast.host, cast.key, 1)
    assert open_event(as_event(honest), cast.keyring, cast.members, "hosty") is not None


def test_tampering_with_any_part_is_refused(cast):
    env = sealed(cast)

    def refused(**changes):
        d = EventDraft(**{**env.__dict__, **changes})
        return open_event(as_event(d), cast.keyring, cast.members, "hosty") is None

    flip = lambda s: s[:-2] + ("A" if s[-2] != "A" else "B") + s[-1]  # noqa: E731
    assert refused(body={**env.body, "ct": flip(env.body["ct"])})
    assert refused(body={**env.body, "sig": "00" * 64})
    assert refused(body={**env.body, "nonce": "11" * 24})
    assert refused(body={**env.body, "epoch": 2})
    assert refused(body={**env.body, "class": "host"})
    assert refused(sender_participant="moe")
    assert refused(client_event_id="c2")  # an old envelope replayed under a new client_event_id
    assert refused(thread_id="0" * 32)
    assert open_event(as_event(env, room="r2"), cast.keyring, cast.members, "hosty") is None  # moved to another room
    assert open_event(as_event(env), cast.keyring, cast.members, "hosty") is not None  # the untouched one still opens


def test_wrong_epoch_or_missing_key_skips_without_crashing(cast):
    env = sealed(cast)
    assert open_event(as_event(env), Keyring({2: cast.key}), cast.members, "hosty") is None
    assert open_event(as_event(env), Keyring(), cast.members, "hosty") is None
    assert open_event(as_event(env), Keyring({1: crypto.random_bytes(32)}), cast.members, "hosty") is None
    ev, why = inspect_event(as_event(env), Keyring(), cast.members, "hosty")
    assert ev is None and why == "no_key"
    ev, why = inspect_event(as_event(env), Keyring({1: crypto.random_bytes(32)}), cast.members, "hosty")
    assert ev is None and why == "rejected"


def test_unknown_sender_or_device_is_refused(cast):
    env = sealed(cast)
    assert open_event(as_event(env), cast.keyring, {"hosty": cast.members["hosty"]}, "hosty") is None  # not a known member
    assert inspect_event(as_event(env), cast.keyring, {}, "hosty")[1] == "unknown_device"
    assert open_event(as_event(env), cast.keyring, {"meg": [cast.moe.public()]}, "hosty") is None  # meg's list lacks this device


def test_events_from_two_devices_of_one_account_verify_against_their_own_keys(cast):
    members = {"meg": [cast.meg.public(), cast.meg2.public()], "hosty": [cast.host.public()]}
    a = open_event(as_event(seal_event(draft(cid="a"), cast.meg, cast.key, 1)), cast.keyring, members, "hosty")
    b = open_event(as_event(seal_event(draft(cid="b", agent="meg-codex"), cast.meg2, cast.key, 1)), cast.keyring, members, "hosty")
    assert a is not None and b is not None and (a.sender_agent, b.sender_agent) == ("meg-claude", "meg-codex")
    one_device_only = {"meg": cast.meg.public(), "hosty": [cast.host.public()]}  # a single key (not a list) is accepted too
    assert open_event(as_event(seal_event(draft(), cast.meg, cast.key, 1)), cast.keyring, one_device_only, "hosty") is not None
    assert open_event(as_event(seal_event(draft(), cast.meg2, cast.key, 1)), cast.keyring, one_device_only, "hosty") is None


def test_garbage_never_raises(cast):
    rng = random.Random(7)
    good = sealed(cast)
    junk_values = [None, 0, -1, True, "", "x" * 5000, [], {}, [1, 2], {"a": {"b": {"c": 1}}}, 2**70, 1.5]
    for _ in range(300):
        body = dict(good.body)
        for k in rng.sample(sorted(body), rng.randint(1, len(body))):
            body[k] = rng.choice(junk_values)
        if rng.random() < 0.2:
            body[rng.choice(["v", "extra", "ct"])] = rng.choice(junk_values)
        d = EventDraft(**{**good.__dict__, "body": body})
        assert open_event(as_event(d), cast.keyring, cast.members, "hosty") is None
    for notdict in (None, 5, "s", [1]):
        assert open_event(as_event(EventDraft(**{**good.__dict__, "body": notdict})), cast.keyring, cast.members, "hosty") is None
    plain = as_event(draft())
    assert open_event(plain, cast.keyring, cast.members, "hosty") is None  # a plaintext event is not an envelope


def test_oversize_inner_bodies_are_refused_when_sealing(cast):
    with pytest.raises(ProtocolError):
        sealed(cast, draft(body={"text": "x" * 70_000}))
    with pytest.raises(ProtocolError):
        seal_event(draft(), cast.meg, cast.key, 0)
    with pytest.raises(ProtocolError):
        seal_event(EventDraft(ROOM, "c", "t", EventType.ENVELOPE, "meg", None, None, {}), cast.meg, cast.key, 1)


def test_thread_tags_are_stable_per_key_and_hide_the_name(cast):
    assert thread_tag(cast.key, "t1") == thread_tag(cast.key, "t1")
    assert thread_tag(cast.key, "t1") != thread_tag(cast.key, "t2") and thread_tag(crypto.random_bytes(32), "t1") != thread_tag(cast.key, "t1")


def test_the_server_side_shape_check(cast):
    env = sealed(cast)

    def check(**kw):
        args = dict(body=env.body, sender="meg", host="hosty", thread_id=env.thread_id, sender_agent=None, reply_to=None)
        args.update(kw)
        check_envelope(**args)

    check()  # a good one passes
    for bad in (
        {"body": {**env.body, "v": 1}}, {"body": {**env.body, "extra": 1}}, {"body": {k: v for k, v in env.body.items() if k != "sig"}},
        {"body": {**env.body, "epoch": 0}}, {"body": {**env.body, "epoch": True}}, {"body": {**env.body, "epoch": "1"}},
        {"body": {**env.body, "epoch": 10**9}}, {"body": {**env.body, "class": "admin"}}, {"body": {**env.body, "nonce": "zz"}},
        {"body": {**env.body, "nonce": "00" * 8}}, {"body": {**env.body, "sig": "00" * 10}}, {"body": {**env.body, "ct": "!!!"}},
        {"body": {**env.body, "ct": "A" * 200_000}}, {"body": {**env.body, "dev": "nope"}}, {"body": "string"}, {"body": None},
        {"thread_id": "not-a-tag"}, {"thread_id": "G" * 32}, {"sender_agent": "meg-claude"}, {"reply_to": "evt_1"},
    ):
        with pytest.raises(ProtocolError):
            check(**bad)
    with pytest.raises(HostOnlyError):
        check(body={**env.body, "class": "host"})  # a member claiming the host's class
    check(body={**env.body, "class": "host"}, sender="hosty")  # the host may


def test_the_client_event_id_is_hidden_but_retries_still_map_to_one_stored_event(cast):
    spelled_out = draft(cid="area:redis")
    a, b = sealed(cast, spelled_out), sealed(cast, spelled_out)
    assert "redis" not in a.client_event_id and "area" not in a.client_event_id
    assert a.client_event_id == b.client_event_id  # the server can still deduplicate a retry
    assert sealed(cast, draft(cid="area:schema")).client_event_id != a.client_event_id
    assert open_event(as_event(a), cast.keyring, cast.members, "hosty").client_event_id == "area:redis"  # readers get the real one back


def test_compression_round_trips_exactly_and_saves_space_on_a_recorded_sample():
    import json
    import zlib

    from aletheore.council import envelope as env

    text = ("The cache layer keeps Redis and Postgres in step. " * 60) + "naïve café 名前 \u0000 \n tabs\t"
    small = b'{"x":1}'
    big = json.dumps({"text": text}, ensure_ascii=False).encode("utf-8")
    assert env._maybe_decompress(env._maybe_compress(small)) == small and env._maybe_compress(small) == small  # small stays as is
    packed = env._maybe_compress(big)
    assert packed != big and len(packed) < len(big) / 3 and env._maybe_decompress(packed) == big  # real savings, exact round trip
    noise = bytes(range(256)) * 8
    assert env._maybe_compress(noise) in (noise, env._maybe_compress(noise)) and env._maybe_decompress(env._maybe_compress(noise)) == noise
    bomb = env._Z_MARK + zlib.compress(b"0" * (20 * 1024 * 1024))
    with pytest.raises(ValueError, match="too large or damaged"):
        env._maybe_decompress(bomb)  # a decompression bomb is refused
    with pytest.raises(zlib.error):
        env._maybe_decompress(env._Z_MARK + b"not zlib at all")


def test_a_long_sealed_message_is_smaller_on_the_wire_and_opens_to_exactly_what_was_sent(cast):
    long_text = ("Write-through keeps Redis and Postgres in step; one extra write per login. " * 120) + "名前 ́ end"
    d = draft(text=long_text)
    env = sealed(cast, d)
    assert len(env.body["ct"]) < len(json.dumps({"text": long_text})) / 3  # the ciphertext is much smaller than the text
    got = open_event(as_event(env), cast.keyring, cast.members, "hosty")
    assert got is not None and got.body == {"text": long_text} and got.client_event_id == "c1"
    short = sealed(cast, draft(text="hi"))
    assert open_event(as_event(short), cast.keyring, cast.members, "hosty").body == {"text": "hi"}
    # tampering with the compressed ciphertext is still caught: the signature and the AEAD cover it
    bad = {**env.body, "ct": env.body["ct"][:-4] + ("AAAA" if not env.body["ct"].endswith("AAAA") else "BBBB")}
    assert open_event(as_event(EventDraft(ROOM, env.client_event_id, env.thread_id, EventType.ENVELOPE, "meg", None, None, bad)), cast.keyring, cast.members, "hosty") is None
