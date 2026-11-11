"""Envelope v2: one room event, sealed so the server can route it but not read it.

What the server (and anyone who can read its database) sees of an event: the room, the sender's account name, an opaque event id
(a keyed hash of the real client event id: those ids often spell out what the event is, like "area:redis", so they are hidden too,
yet a retry of the same event still maps to the same id and is stored once), a `thread_tag` (a keyed hash of the thread id, so it
cannot be guessed or linked to a name), the key epoch, whether the event is a
"host" or a "member" event, the sending device's id, a nonce, the ciphertext and a signature. It does not see the real event type,
thread id, reply-to, agent or body: those are inside the ciphertext.

Authenticity is checked by the reader, not the server. Every envelope is signed by the sending *device* (Ed25519) over the
plaintext routing fields plus the nonce and ciphertext, and the ciphertext is bound to the same fields as authenticated data
(XChaCha20-Poly1305), so an envelope cannot be moved to another room, sender, thread, class or epoch, replayed under a new client
event id, or edited. A reader drops anything that fails, and also drops a "host" event unless the room's host sent it, whatever
the server accepted: the server is only trusted to order and store, never to vouch.

Known limit: the size of the ciphertext reveals roughly how long a message is. Padding is not done in v2.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import zlib
import re
import struct
from typing import Iterable, Mapping

from aletheore.council import crypto
from aletheore.council.identity import Identity, PublicIdentity
from aletheore.council.protocol import (MAX_ENVELOPE_BYTES, Event, EventDraft, EventType, ProtocolError, check_body, is_host_only)

VERSION = 2
MAX_EPOCH = 1_000_000
_TAG_RE = re.compile(r"[0-9a-f]{32}")
_DEV_RE = re.compile(r"dev_[0-9a-f]{24}")
_B64_RE = re.compile(r"[A-Za-z0-9_-]+")
_KEYS = frozenset({"v", "epoch", "class", "dev", "nonce", "ct", "sig"})
_NONCE_HEX = 2 * crypto.NONCE_BYTES
_SIG_HEX = 128
_MAX_CT_B64 = (MAX_ENVELOPE_BYTES * 4) // 3  # the most the shape check will accept before looking inside
_EVENT_KEY_CONTEXT = "council/event/v2"


class HostOnlyError(ProtocolError):
    """A member tried to send an envelope that claims the host's class."""


class Keyring:
    """Room keys by epoch. Rotation and distribution are Task 9; this is just the holder."""

    def __init__(self, keys: Mapping[int, bytes] | None = None) -> None:
        self._keys: dict[int, bytes] = dict(keys or {})

    def add(self, epoch: int, key: bytes) -> None:
        self._keys[epoch] = key

    def get(self, epoch: int) -> bytes | None:
        return self._keys.get(epoch)

    def epochs(self) -> list[int]:
        return sorted(self._keys)

    def latest(self) -> tuple[int, bytes] | None:
        return (max(self._keys), self._keys[max(self._keys)]) if self._keys else None

    def __repr__(self) -> str:  # never print key material
        return f"Keyring(epochs={sorted(self._keys)})"


def opaque_event_id(room_key: bytes, client_event_id: str) -> str:
    return hmac.new(room_key, b"council/event-id/v2\n" + client_event_id.encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def thread_tag(room_key: bytes, thread_id: str) -> str:
    return hmac.new(room_key, b"council/thread-tag/v2\n" + thread_id.encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def envelope_class(etype: EventType, body: dict) -> str:
    return "host" if is_host_only(etype, body) else "member"


def _pack(*parts: bytes) -> bytes:
    return b"".join(struct.pack(">I", len(p)) + p for p in parts)


def _fields(room_id: str, client_event_id: str, sender: str, dev: str, cls: str, epoch: int, tag: str) -> bytes:
    return _pack(b"council/envelope/v2", room_id.encode(), client_event_id.encode(), sender.encode(), dev.encode(), cls.encode(),
                 str(epoch).encode(), tag.encode())


def _signed(fields: bytes, nonce: bytes, ct: bytes) -> bytes:
    return _pack(b"council/envelope/v2/signature", fields, nonce, ct)


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    if _b64(raw) != text:  # the last character carries spare bits: two spellings of the same bytes would be two different stored events
        raise ValueError("not canonical base64")
    return raw


# Larger messages are compressed before they are encrypted (ciphertext cannot be compressed afterwards). The marker is INSIDE the
# encrypted payload, so it is authenticated like everything else; JSON never starts with a zero byte, so a plain payload is unambiguous.
# Compressing before encrypting is acceptable here because the compressed content is only the author's own message: an attacker cannot
# mix guesses into it and watch the size (the CRIME style leak needs attacker-chosen text beside a secret).
_COMPRESS_OVER = 1024
_Z_MARK = b"\x00Z"
_MAX_INFLATED = 4 * 1024 * 1024  # a decompression bomb is refused, not expanded


def _maybe_compress(payload: bytes) -> bytes:
    if len(payload) <= _COMPRESS_OVER:
        return payload
    packed = _Z_MARK + zlib.compress(payload, 6)
    return packed if len(packed) < len(payload) else payload


def _maybe_decompress(payload: bytes) -> bytes:
    if not payload.startswith(_Z_MARK):
        return payload
    d = zlib.decompressobj()
    out = d.decompress(payload[len(_Z_MARK):], _MAX_INFLATED)
    if d.unconsumed_tail or not d.eof:
        raise ValueError("compressed payload is too large or damaged")
    return out


def _seal(draft: EventDraft, identity: Identity, room_key: bytes, epoch: int, cls: str) -> EventDraft:
    if draft.type is EventType.ENVELOPE:
        raise ProtocolError("an envelope cannot be sealed again")
    if isinstance(epoch, bool) or not isinstance(epoch, int) or not 1 <= epoch <= MAX_EPOCH:
        raise ProtocolError("epoch must be a whole number from 1")
    check_body(draft.body)
    tag = thread_tag(room_key, draft.thread_id)
    outer_id = opaque_event_id(room_key, draft.client_event_id)
    dev = identity.device_id
    fields = _fields(draft.room_id, outer_id, draft.sender_participant, dev, cls, epoch, tag)
    payload = json.dumps({"cid": draft.client_event_id, "type": draft.type.value, "thread": draft.thread_id, "reply_to": draft.reply_to,
                          "agent": draft.sender_agent, "body": draft.body}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    payload = _maybe_compress(payload)
    nonce, ct = crypto.aead_encrypt(crypto.derive_key(room_key, _EVENT_KEY_CONTEXT), payload, fields)
    sig = identity.sign(_signed(fields, nonce, ct))
    body = {"v": VERSION, "epoch": epoch, "class": cls, "dev": dev, "nonce": nonce.hex(), "ct": _b64(ct), "sig": sig.hex()}
    return EventDraft(draft.room_id, outer_id, tag, EventType.ENVELOPE, draft.sender_participant, None, None, body)


def seal_event(draft: EventDraft, identity: Identity, room_key: bytes, epoch: int) -> EventDraft:
    """The envelope for `draft`, signed by this device. The class (host or member) is derived from the content."""
    return _seal(draft, identity, room_key, epoch, envelope_class(draft.type, draft.body))


def check_envelope(*, body: object, sender: str, host: str, thread_id: object, sender_agent: object, reply_to: object) -> None:
    """What the server checks without any key: the shape, the sizes and that only the host sends host-class envelopes.
    Raises HostOnlyError (a ProtocolError) for the last, ProtocolError for the rest."""
    if not isinstance(body, dict) or set(body) != _KEYS:
        raise ProtocolError("an envelope body has exactly: v, epoch, class, dev, nonce, ct, sig")
    if body["v"] != VERSION or isinstance(body["v"], bool):
        raise ProtocolError("unsupported envelope version")
    epoch = body["epoch"]
    if isinstance(epoch, bool) or not isinstance(epoch, int) or not 1 <= epoch <= MAX_EPOCH:
        raise ProtocolError("epoch must be a whole number from 1")
    if body["class"] not in ("host", "member"):
        raise ProtocolError("class must be host or member")
    for name, rx, size in (("dev", _DEV_RE, None), ("nonce", re.compile(r"[0-9a-f]+"), _NONCE_HEX), ("sig", re.compile(r"[0-9a-f]+"), _SIG_HEX)):
        v = body[name]
        if not isinstance(v, str) or not rx.fullmatch(v) or (size is not None and len(v) != size):
            raise ProtocolError(f"{name} is malformed")
    ct = body["ct"]
    if not isinstance(ct, str) or not ct or len(ct) > _MAX_CT_B64 or not _B64_RE.fullmatch(ct):
        raise ProtocolError("ct is malformed or too large")
    if not isinstance(thread_id, str) or not _TAG_RE.fullmatch(thread_id):
        raise ProtocolError("thread_id of an envelope is a 32 character tag")
    if sender_agent is not None or reply_to is not None:
        raise ProtocolError("an envelope carries no agent or reply-to in the clear")
    if body["class"] == "host" and sender != host:
        raise HostOnlyError("only the room host can send host-class events")


def _devices_of(members: Mapping, sender: str) -> list[PublicIdentity]:
    found = members.get(sender)
    if found is None:
        return []
    return [found] if isinstance(found, PublicIdentity) else list(found)


def inspect_event(event: Event, keyring: Keyring, members: Mapping, host: str) -> tuple[Event | None, str]:
    """Open one stored event. Returns (inner event, "ok") or (None, why) with why in:
    malformed, no_key (we do not hold that epoch's key), unknown_device (the sender or device is not in `members`), rejected."""
    try:
        if event.type is not EventType.ENVELOPE:
            return None, "malformed"
        b = event.body
        check_envelope(body=b, sender=event.sender_participant, host=host, thread_id=event.thread_id, sender_agent=event.sender_agent,
                       reply_to=event.reply_to)  # the server's shape rules are re-checked here: a hostile server may not have
    except HostOnlyError:
        return None, "rejected"
    except (ProtocolError, AttributeError, TypeError, KeyError):
        return None, "malformed"
    try:
        room_key = keyring.get(b["epoch"])
        if room_key is None:
            return None, "no_key"
        device = next((p for p in _devices_of(members, event.sender_participant) if p.device_id == b["dev"]), None)
        if device is None:
            return None, "unknown_device"
        nonce, ct, sig = bytes.fromhex(b["nonce"]), _unb64(b["ct"]), bytes.fromhex(b["sig"])
        fields = _fields(event.room_id, event.client_event_id, event.sender_participant, b["dev"], b["class"], b["epoch"], event.thread_id)
        if not crypto.verify(device.sign_vk, _signed(fields, nonce, ct), sig):
            return None, "rejected"
        inner = json.loads(_maybe_decompress(crypto.aead_decrypt(crypto.derive_key(room_key, _EVENT_KEY_CONTEXT), nonce, ct, fields)).decode("utf-8"))
        etype = EventType(inner["type"])
        if etype is EventType.ENVELOPE:
            return None, "rejected"
        thread, reply_to, agent, body, cid = inner["thread"], inner["reply_to"], inner["agent"], inner["body"], inner["cid"]
        if not isinstance(thread, str) or not thread or thread_tag(room_key, thread) != event.thread_id:
            return None, "rejected"
        if not isinstance(cid, str) or not cid.strip():
            return None, "rejected"
        if reply_to is not None and not isinstance(reply_to, str) or agent is not None and not isinstance(agent, str):
            return None, "rejected"
        check_body(body)
        if envelope_class(etype, body) != b["class"]:
            return None, "rejected"  # the declared class must match what was actually inside
        return Event(event.room_id, cid, thread, etype, event.sender_participant, agent, reply_to, body,
                     event.event_id, event.seq, event.created_at), "ok"
    except (crypto.CryptoError, ProtocolError, ValueError, KeyError, TypeError, AttributeError, UnicodeError, binascii.Error, RecursionError):
        return None, "rejected"


def open_event(event: Event, keyring: Keyring, members: Mapping, host: str) -> Event | None:
    """The inner event, or None when it cannot be trusted. Never raises."""
    return inspect_event(event, keyring, members, host)[0]


__all__ = ["HostOnlyError", "Keyring", "VERSION", "check_envelope", "envelope_class", "inspect_event", "open_event", "seal_event", "thread_tag"]
