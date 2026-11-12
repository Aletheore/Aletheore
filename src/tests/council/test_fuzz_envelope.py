"""Hostile input: whatever bytes arrive (mutated envelopes, links, grants, route payloads), the code refuses cleanly, quickly and without growing.

Every test is seeded and prints its seed in the failure message, so a failure can be replayed. These are not a substitute for a human security
review; they pin one property: malformed input is a refusal, never an exception that escapes and never an expensive operation."""

import copy
import json
import random
import time
import tracemalloc

import pytest
from fastapi.testclient import TestClient

from aletheore.council import crypto, invites
from aletheore.council.accounts import AccountService, SqliteAccountBackend
from aletheore.council.envelope import Keyring, inspect_event, open_event, seal_event
from aletheore.council.identity import Keystore
from aletheore.council.keys import GrantError, accept_grant, make_grant
from aletheore.council.protocol import Event, EventDraft, EventType
from aletheore.council.remote_store import RemoteRoomStore
from aletheore.council.server import create_app
from aletheore.council.sqlite_store import SqliteRoomStore

SEEDS = range(40)
ROOM = "r1"
TIME_BUDGET_S = 5.0  # for a whole batch of hostile inputs; real runs take a small fraction of this


class Cast:
    def __init__(self, tmp_path):
        ks = Keystore(tmp_path / "keys")
        self.host, self.meg = ks.load_or_create("host"), ks.load_or_create("meg")
        self.key = crypto.random_bytes(32)
        self.keyring = Keyring({1: self.key})
        self.members = {"hosty": [self.host.public()], "meg": [self.meg.public()]}


@pytest.fixture
def cast(tmp_path):
    return Cast(tmp_path)


def good_event(cast, text="hello") -> Event:
    d = EventDraft(ROOM, "c1", "task-1", EventType.CLAIM, "meg", "meg-claude", None, {"text": text})
    env = seal_event(d, cast.meg, cast.key, 1)
    return Event(ROOM, env.client_event_id, env.thread_id, env.type, env.sender_participant, env.sender_agent, env.reply_to, env.body,
                 event_id="evt_1", seq=1, created_at="2026-11-09T00:00:00Z")


def junk(rng: random.Random, depth=0):
    """A random JSON-ish value of any type, including very long strings, huge numbers and nesting."""
    kinds = ["int", "float", "str", "bytes_like", "none", "bool", "list", "dict", "long", "bigint", "unicode"]
    k = rng.choice(kinds if depth < 3 else kinds[:6])
    if k == "int": return rng.randint(-2**31, 2**31)
    if k == "float": return rng.choice([float("inf"), float("-inf"), float("nan"), rng.random() * 1e308, -0.0])
    if k == "str": return "".join(rng.choice("abcxyz0123456789_-:/.# ") for _ in range(rng.randint(0, 40)))
    if k == "bytes_like": return rng.choice(["\x00", "\x00Z", "../../etc/passwd", "'; DROP TABLE events;--", "%s%s%s", "{{7*7}}", "\ud800"])
    if k == "none": return None
    if k == "bool": return rng.random() < 0.5
    if k == "list": return [junk(rng, depth + 1) for _ in range(rng.randint(0, 5))]
    if k == "dict": return {str(junk(rng, 3)): junk(rng, depth + 1) for _ in range(rng.randint(0, 5))}
    if k == "long": return "a" * rng.choice([10_000, 100_000, 1_000_000])
    if k == "bigint": return 10 ** rng.randint(20, 400)
    return "名前" * rng.randint(1, 50) + "‮​"


def mutate_body(rng: random.Random, body: dict) -> dict:
    body = copy.deepcopy(body)
    for _ in range(rng.randint(1, 4)):
        op = rng.choice(["replace", "drop", "add", "flip", "truncate"])
        keys = list(body)
        if op == "replace" and keys: body[rng.choice(keys)] = junk(rng)
        elif op == "drop" and keys: body.pop(rng.choice(keys))
        elif op == "add": body[str(junk(rng, 3))[:20] or "x"] = junk(rng)
        elif op in ("flip", "truncate") and keys:
            k = rng.choice(keys)
            if isinstance(body[k], str) and body[k]:
                s = body[k]
                body[k] = s[: rng.randint(0, len(s))] if op == "truncate" else s[:-1] + chr(ord(s[-1]) ^ 1)
    return body


@pytest.mark.parametrize("seed", SEEDS)
def test_mutated_envelopes_never_raise_and_are_never_accepted(cast, seed):
    rng = random.Random(seed)
    good = good_event(cast)
    assert open_event(good, cast.keyring, cast.members, "hosty") is not None  # the unmutated one is fine
    started = time.monotonic()
    for i in range(60):
        body = mutate_body(rng, good.body)
        event = Event(good.room_id, rng.choice([good.client_event_id, junk(rng)]), rng.choice([good.thread_id, junk(rng)]),
                      rng.choice([EventType.ENVELOPE, EventType.CLAIM]), rng.choice(["meg", "hosty", "nobody", junk(rng)]),
                      rng.choice([None, junk(rng)]), rng.choice([None, junk(rng)]), body, event_id="e", seq=i, created_at="x")
        try:
            got, why = inspect_event(event, cast.keyring, cast.members, "hosty")
            opened = open_event(event, cast.keyring, cast.members, "hosty")
        except Exception as exc:  # the only failure this test has
            pytest.fail(f"seed {seed} input {i}: open raised {type(exc).__name__}: {exc}")
        if event.body != good.body or event.sender_participant != "meg" or event.thread_id != good.thread_id or event.client_event_id != good.client_event_id:
            assert opened is None, f"seed {seed} input {i}: a changed envelope was accepted ({why})"
    assert time.monotonic() - started < TIME_BUDGET_S, f"seed {seed}: hostile envelopes were slow"


def test_a_bit_flip_anywhere_in_the_ciphertext_nonce_or_signature_is_refused(cast):
    good = good_event(cast)
    for field in ("ct", "nonce", "sig"):
        text = good.body[field]
        for pos in range(0, len(text), max(1, len(text) // 25)):
            flipped = text[:pos] + ("A" if text[pos] != "A" else "B") + text[pos + 1:]
            body = {**good.body, field: flipped}
            bad = Event(good.room_id, good.client_event_id, good.thread_id, good.type, good.sender_participant, None, None, body, "e", 1, "x")
            assert open_event(bad, cast.keyring, cast.members, "hosty") is None, f"{field}[{pos}]"


def test_a_decompression_bomb_is_refused_without_using_much_memory(cast):
    payload = {"cid": "c1", "type": "claim", "thread": "task-1", "reply_to": None, "agent": None, "body": {"text": "x"}}
    from aletheore.council import envelope as env_mod

    bomb = env_mod._Z_MARK + __import__("zlib").compress(b"0" * (64 * 1024 * 1024), 9)
    assert len(bomb) < 100_000
    tracemalloc.start()
    with pytest.raises(ValueError):
        env_mod._maybe_decompress(bomb)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert peak < 16 * 1024 * 1024, f"inflating a 64 MB bomb peaked at {peak} bytes"
    assert payload  # (kept so the shape of a real payload sits next to the bomb for readers)


@pytest.mark.parametrize("seed", SEEDS)
def test_invite_link_parser_only_ever_says_value_error(seed):
    rng = random.Random(seed)
    secret = crypto.random_bytes(32)
    good = invites.build_link("https://slot-001.aletheorecouncil.com", "inv_" + "a" * 32, secret, "dev_" + "b" * 24)
    started = time.monotonic()
    for _ in range(200):
        text = rng.choice([
            good[: rng.randint(0, len(good))], good.replace(rng.choice("/#.:"), rng.choice(["", "/", "#", "%", "\x00", "@"])),
            str(junk(rng, 3)), good + str(junk(rng)), "https://" + "a" * 100_000 + "/j/x#y", good.replace("#", "#" + "A" * 100_000),
            "http://[::1", "https://user:pass@host/j/inv#x", "\x00" * 10,
        ])
        try:
            invites.parse_link(text)
        except ValueError as exc:
            assert "A" * 20 not in str(exc) and (secret.hex() not in str(exc)), "an error message repeated link content"
        except Exception as exc:
            pytest.fail(f"seed {seed}: parse_link raised {type(exc).__name__} for {text[:60]!r}")
    assert time.monotonic() - started < TIME_BUDGET_S


@pytest.mark.parametrize("seed", SEEDS)
def test_grants_only_ever_say_grant_error_for_anything_but_the_exact_grant(cast, seed):
    rng = random.Random(seed)
    grant = make_grant(ROOM, cast.key, 1, cast.meg.public(), cast.host)
    assert accept_grant(ROOM, grant, cast.meg, [cast.host.public()]) == (1, cast.key)
    for _ in range(100):
        blob = rng.choice([
            bytes(rng.getrandbits(8) for _ in range(rng.randint(0, 400))), grant[: rng.randint(0, len(grant))], b"",
            grant[:-1] + bytes([grant[-1] ^ 1]), grant + b"\x00", bytes([grant[0] ^ 0x80]) + grant[1:],
        ])
        if blob == grant:
            continue
        try:
            accept_grant(ROOM, blob, cast.meg, [cast.host.public()])
            pytest.fail(f"seed {seed}: an altered grant was accepted")
        except GrantError:
            pass
        except Exception as exc:
            pytest.fail(f"seed {seed}: accept_grant raised {type(exc).__name__}")
    # a grant for someone else, or for another room, or from a non-host, is refused too
    with pytest.raises(GrantError):
        accept_grant("other-room", grant, cast.meg, [cast.host.public()])
    with pytest.raises(GrantError):
        accept_grant(ROOM, grant, cast.meg, [cast.meg.public()])


# -- the server's routes ---------------------------------------------------------------------------------------------------------------
class Wire:
    def __init__(self, tmp_path):
        self.service = AccountService(SqliteAccountBackend(tmp_path / "s.db"))
        app = create_app(SqliteRoomStore(tmp_path / "s.db"), {}, tmp_path / "b", accounts=self.service)
        self.client = TestClient(app, base_url="http://council.test", raise_server_exceptions=False)
        self.ident = Keystore(tmp_path / "k").load_or_create("device")
        code = self.service.create_registration_code("hosty", can_host=True)
        store = RemoteRoomStore("http://council.test", "", client=self.client)
        store.register(code, "hosty", self.ident)
        store.login(self.ident)
        store.create_room(ROOM, "hosty", title="t")
        self.session = dict(store._headers)


@pytest.fixture
def wire(tmp_path):
    return Wire(tmp_path)


def test_hostile_payloads_to_every_route_never_make_the_server_fail(wire):
    rng = random.Random(7)
    routes = [("POST", "/v1/register"), ("POST", "/v1/login/challenge"), ("POST", "/v1/login"), ("POST", "/v1/devices/link-requests"),
              ("POST", f"/v1/rooms/{ROOM}/invites"), ("POST", "/v1/invites/inv_x/requests"), ("POST", f"/v1/rooms/{ROOM}/members"),
              ("POST", f"/v1/rooms/{ROOM}/events"), ("PUT", f"/v1/rooms/{ROOM}/grants/hosty/1"), ("POST", f"/v1/rooms/{ROOM}/close"),
              ("POST", "/v1/rooms/new-room")]
    started = time.monotonic()
    for i in range(300):
        method, path = rng.choice(routes)
        headers = rng.choice([{}, wire.session, {"Authorization": "Bearer " + "x" * 10_000}, {"Authorization": "Bearer "}, {"Authorization": str(junk(rng))[:50].encode("ascii", "ignore").decode().strip()}])
        payload = rng.choice([junk(rng), mutate_body(rng, {"code": "x", "handle": "hosty", "client_event_id": "c", "thread_id": "t", "type": "claim", "body": {}}),
                              {"body": {"v": 2, "epoch": junk(rng), "class": junk(rng), "dev": junk(rng), "nonce": junk(rng), "ct": junk(rng), "sig": junk(rng)}}])
        try:
            resp = wire.client.request(method, path, content=json.dumps(payload, default=str, allow_nan=True).encode("utf-8", "replace"),
                                       headers={**headers, "content-type": "application/json"})
        except Exception as exc:
            pytest.fail(f"request {i} {method} {path}: the client library failed before the server answered: {type(exc).__name__}")
        assert resp.status_code < 500 or resp.status_code in (503, 507, 509), f"request {i} {method} {path} -> {resp.status_code}: {resp.text[:120]}"
    assert time.monotonic() - started < 30, "hostile requests were slow"


def test_garbage_that_is_not_json_and_odd_paths_never_make_the_server_fail(wire):
    rng = random.Random(11)
    bodies = [b"", b"\xff\xfe\x00", b"{", b"[" * 5000, b"null", b'{"a":' * 3000, bytes(rng.getrandbits(8) for _ in range(5000)), b"\x00" * 100_000]
    paths = ["/v1/rooms/" + "a" * 5000, "/v1/rooms/%00", "/v1/rooms/..%2f..%2fetc", "/v1/invites/%ff", f"/v1/rooms/{ROOM}/events?after=-1&limit=999999999999",
             f"/v1/rooms/{ROOM}/events?after=abc", f"/v1/rooms/{ROOM}/bundles/" + "z" * 64, f"/v1/rooms/{ROOM}/blobs/../../x", "/v1/host-proof?nonce=" + "a" * 60_000]
    for path in paths:
        for method in ("GET", "POST", "PUT", "DELETE"):
            resp = wire.client.request(method, path, content=rng.choice(bodies), headers={**wire.session, "content-type": "application/json"})
            assert resp.status_code < 500 or resp.status_code in (503, 507, 509), f"{method} {path[:50]} -> {resp.status_code}"


def test_an_impostor_server_cannot_pass_a_link_check_just_by_saying_not_found(wire):
    """A recycled address may answer 404 to the host proof (the proof is allowed to be absent for older servers). The link's own secret is
    what stops it: the invitation it would have to show is sealed under a key only the link holds, so nothing it invents unwraps."""
    secret = crypto.random_bytes(32)
    wrapped = invites.wrap_room_key(secret, b"")
    forged = bytes([wrapped[0] ^ 1]) + wrapped[1:]
    assert invites.unwrap_room_key(secret, wrapped) == b""
    with pytest.raises(Exception):
        invites.unwrap_room_key(secret, forged)
    with pytest.raises(Exception):
        invites.unwrap_room_key(secret, crypto.random_bytes(len(wrapped)))


def test_two_spellings_of_the_same_ciphertext_are_not_two_events(cast):
    """Found by the fuzzer (seed 9): the last base64 character has spare bits, so changing it kept the same bytes and the signature still held."""
    good = good_event(cast)
    ct = good.body["ct"]
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    twins = [c for c in alphabet if c != ct[-1] and ct[:-1] + c]
    refused = 0
    for c in twins:
        alt = Event(good.room_id, good.client_event_id, good.thread_id, good.type, good.sender_participant, None, None, {**good.body, "ct": ct[:-1] + c}, "e", 1, "x")
        refused += open_event(alt, cast.keyring, cast.members, "hosty") is None
    assert refused == len(twins), "some other spelling of the same ciphertext was accepted"
    secret = crypto.random_bytes(32)
    link = invites.build_link("https://h.example", "inv_" + "a" * 32, secret, "")
    frag = link.split("#")[1]
    for c in alphabet:
        if c != frag[-1]:
            try:
                parsed = invites.parse_link(link.split("#")[0] + "#" + frag[:-1] + c)
            except ValueError:
                continue
            assert parsed.secret != secret or False, "a non-canonical spelling of the secret was accepted"


def test_no_two_signed_message_formats_can_be_confused_with_each_other(cast):
    """The server signs a caller-chosen nonce for /v1/host-proof. That is safe only while no other signed message can start with the same bytes,
    so every format keeps its own distinct leading context. Adding a new signed format means adding it here."""
    from aletheore.council.accounts import AccountService
    from aletheore.council.envelope import _signed
    from aletheore.council.keys import _GRANT_CONTEXT
    from aletheore.council.registry_client import signing_message
    from aletheore.council.tunnel_registry import host_proof_message

    proof = host_proof_message(b"\x00" * 8)
    others = [AccountService.login_message("dev_" + "a" * 24, "n" * 64), _GRANT_CONTEXT + b"x", _signed(b"f", b"n", b"c"),
              signing_message("GET", "/v1/lookup/x", "1", "2", b"")]
    for message in others:
        assert not message.startswith(proof[: len(host_proof_message(b""))]), "a signed format starts like the host proof"
        assert not proof.startswith(message[:12]), "the host proof starts like another signed format"


# -- deeply nested JSON: Python's parser raises RecursionError (not ValueError) beyond a platform-dependent depth. Found by the Windows run of
# the fuzz tests, where 5,000 levels was enough; on a Mac only the size limits hid it. Every place that parses JSON from someone else must
# turn that into a refusal. A sentinel makes the failure deterministic on any machine.
DEEP = b'{"DEEPNEST": 1}'


@pytest.fixture
def deep_parser(monkeypatch):
    real = json.loads

    def fake(s, *args, **kwargs):
        raw = s if isinstance(s, (bytes, bytearray)) else str(s).encode("utf-8", "replace")
        if b"DEEPNEST" in raw:
            raise RecursionError("maximum recursion depth exceeded while decoding a JSON document")
        return real(s, *args, **kwargs)

    monkeypatch.setattr(json, "loads", fake)


def test_an_event_body_nested_thousands_of_levels_deep_is_refused_not_a_crash():
    from aletheore.council.protocol import ProtocolError, check_body

    body: dict = {}
    cur = body
    for _ in range(5000):
        cur["a"] = {}
        cur = cur["a"]
    with pytest.raises(ProtocolError):
        check_body(body)


def test_a_member_cannot_make_every_reader_fail_with_a_sealed_payload_that_is_nested_too_deep(cast, deep_parser):
    """The server cannot see inside a sealed event, so only the reader can refuse it. open_event must never raise, whatever a member seals."""
    from aletheore.council import envelope as env_mod

    good = good_event(cast)
    fields = env_mod._fields(good.room_id, good.client_event_id, "meg", good.body["dev"], "member", 1, good.thread_id)
    payload = b'{"cid":"c9","type":"claim","thread":"task-1","reply_to":null,"agent":null,"body":{"DEEPNEST": 1}}'
    nonce, ct = crypto.aead_encrypt(crypto.derive_key(cast.key, env_mod._EVENT_KEY_CONTEXT), payload, fields)
    sig = cast.meg.sign(env_mod._signed(fields, nonce, ct))
    body = {"v": 2, "epoch": 1, "class": "member", "dev": good.body["dev"], "nonce": nonce.hex(), "ct": env_mod._b64(ct), "sig": sig.hex()}
    event = Event(good.room_id, good.client_event_id, good.thread_id, good.type, "meg", None, None, body, "e", 1, "x")
    assert open_event(event, cast.keyring, cast.members, "hosty") is None


def test_the_server_answers_deeply_nested_json_with_a_refusal_on_every_route_that_reads_json(wire, deep_parser):
    for method, path in (("POST", "/v1/register"), ("POST", "/v1/login"), ("POST", f"/v1/rooms/{ROOM}/invites"), ("POST", "/v1/invites/inv_x/requests"),
                         ("POST", "/v1/rooms/new-room"), ("POST", f"/v1/rooms/{ROOM}/members"), ("POST", f"/v1/rooms/{ROOM}/events")):
        resp = wire.client.request(method, path, content=DEEP, headers={**wire.session, "content-type": "application/json"})
        assert resp.status_code in (400, 401, 403, 404, 413, 422), f"{method} {path} -> {resp.status_code}"


def test_a_client_that_is_sent_deeply_nested_json_by_a_hostile_server_gets_a_connection_error_not_a_crash(deep_parser):
    import httpx

    from aletheore.council.remote_store import RemoteRoomStore

    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=DEEP, headers={"content-type": "application/json"}))
    store = RemoteRoomStore("http://hostile.test", "t", client=httpx.Client(transport=transport, base_url="http://hostile.test"), backoff_s=0)
    with pytest.raises(ConnectionError):
        store.last_seq("r1")
