"""E2ERoomStore: a RoomStore that seals what it writes and opens what it reads, so nothing above it changes.

Planner, dispatcher, conductor, daemon, terminal view and commands all talk to a `RoomStore`. Wrap the real store (SQLite, or a
remote server) in this class and they work unchanged on an encrypted room: `append` turns a normal event into a signed envelope,
`read_after` turns envelopes back into the events they hold and silently skips anything that does not verify.

Skipped events are counted, not hidden: `rejected` (failed signature, wrong class, forged), `unreadable` (we do not hold that
epoch's key yet) and `gaps` (the server's sequence numbers skipped something: it dropped or withheld an event). A skipped event
never stops the read: a page that is all junk is followed by the next page, so junk in the middle of a log cannot hide what comes
after it.

`directory(room_id)` returns the room's members' device public keys ({participant: [PublicIdentity, ...]}). It is asked again when a
sender or device is not in the cached answer, so a newly added member or linked device is picked up without restarting.
"""

from __future__ import annotations

import threading
from typing import Callable, Mapping

from aletheore.council.envelope import Keyring, inspect_event, seal_event
from aletheore.council.identity import Identity
from aletheore.council.protocol import Event, EventDraft, EventType, ProtocolError, is_server_notice
from aletheore.council.store import RoomInfo

Directory = Callable[[str], Mapping]


class E2ERoomStore:
    supports_agent_tools = False  # an agent's tool process never holds a room key: it reaches this store through a broker

    def __init__(self, inner, identity: Identity, keyring: Keyring, directory: Directory,
                 key_sync: Callable[[str], int] | None = None) -> None:
        self._inner, self._identity, self._keyring, self._directory = inner, identity, keyring, directory
        self._key_sync = key_sync  # asked once per read when an event needs a key we lack: fetch the grants the host has published
        self._members: dict[str, Mapping] = {}
        self._resume: dict[str, tuple[int, int]] = {}  # room -> (last seq we returned, highest seq we processed)
        self._counted: set[tuple[str, str, int]] = set()  # (room, kind, seq) already counted
        self._gap_seen: set[tuple[str, int, int]] = set()
        self._lock = threading.Lock()
        self.rejected = 0
        self.unreadable = 0
        self.gaps: list[tuple[int, int]] = []
        if hasattr(inner, "whoami"):  # a remote store knows who the server thinks we are
            self.whoami = inner.whoami

    # -- rooms (the server sees and stores these in the clear: membership, titles and the host are not secret from it) ----------
    def create_room(self, room_id: str, host_participant: str, title: str = "", mode: str = "interactive", crypto: str = "e2e-v1") -> None:
        self._inner.create_room(room_id, host_participant, title=title, mode=mode, crypto=crypto)

    def room_info(self, room_id: str) -> RoomInfo:
        return self._inner.room_info(room_id)

    def members_of(self, room_id: str) -> list[str]:
        return self._inner.members_of(room_id)

    def add_member(self, room_id: str, participant: str) -> None:
        self._inner.add_member(room_id, participant)

    def remove_member(self, room_id: str, participant: str) -> None:
        self._inner.remove_member(room_id, participant)

    def close_room(self, room_id: str) -> None:
        self._inner.close_room(room_id)

    def list_rooms(self, participant: str = "") -> list[RoomInfo]:
        return self._inner.list_rooms(participant)

    def host_of(self, room_id: str) -> str:
        return self._inner.host_of(room_id)

    def last_seq(self, room_id: str) -> int:
        return self._inner.last_seq(room_id)

    # -- events ------------------------------------------------------------------------------------------------------
    def append(self, draft: EventDraft) -> Event:
        if draft.type is EventType.ENVELOPE:
            raise ProtocolError("write the real event; the store seals it")
        latest = self._keyring.latest()
        if latest is None and self._key_sync is not None:
            try:
                self._key_sync(draft.room_id)  # a device just let in has not fetched its key yet: its first action may be to write
            except (PermissionError, ConnectionError, KeyError):
                pass
            latest = self._keyring.latest()
        if latest is None:
            raise PermissionError("this device holds no key for the room yet: the host has not handed it over, or it is still on its way")
        epoch, key = latest
        stored = self._inner.append(seal_event(draft, self._identity, key, epoch))
        return Event(draft.room_id, draft.client_event_id, draft.thread_id, draft.type, draft.sender_participant, draft.sender_agent,
                     draft.reply_to, draft.body, stored.event_id, stored.seq, stored.created_at)

    def _members_for(self, room_id: str, refresh: bool = False) -> Mapping:
        with self._lock:
            if refresh or room_id not in self._members:
                self._members[room_id] = self._directory(room_id)
            return self._members[room_id]

    def _count(self, room_id: str, kind: str, seq: int) -> None:
        key = (room_id, kind, seq)
        if key in self._counted:
            return
        self._counted.add(key)
        if kind == "unreadable":
            self.unreadable += 1
        else:
            self.rejected += 1

    def read_after(self, room_id: str, after_seq: int, limit: int = 500) -> list[Event]:
        host = self._inner.host_of(room_id)
        start = after_seq
        resume = self._resume.get(room_id)
        if resume is not None and resume[0] == after_seq and resume[1] > after_seq:
            start = resume[1]  # skip the junk we already looked at after the last event we returned
        out: list[Event] = []
        prev = start
        settled = start  # everything up to here is decided for good (opened, or definitely not trustworthy); a missing key is not
        holding = False  # once an event is waiting for a key we stop advancing `settled`, so it is looked at again later
        synced = False
        while len(out) < limit:
            page = self._inner.read_after(room_id, prev, limit)
            if not page:
                break
            members, refreshed = self._members_for(room_id), False
            for stored in page:
                if is_server_notice(stored):  # the server's own notes (older events archived, the host left or came back): not a gap, not junk
                    prev = stored.seq
                    settled = max(settled, prev)
                    out.append(stored)
                    continue
                if stored.seq > prev + 1:
                    gap = (prev + 1, stored.seq - 1)
                    if (room_id, *gap) not in self._gap_seen:
                        self._gap_seen.add((room_id, *gap))
                        self.gaps.append(gap)
                prev = stored.seq
                inner, why = inspect_event(stored, self._keyring, members, host)
                if why == "unknown_device" and not refreshed:
                    members, refreshed = self._members_for(room_id, refresh=True), True  # a member or device we have not heard of yet
                    inner, why = inspect_event(stored, self._keyring, members, host)
                if why == "no_key" and self._key_sync is not None and not synced:
                    synced = True
                    try:
                        fetched = self._key_sync(room_id)
                    except (PermissionError, ConnectionError, KeyError):
                        fetched = 0
                    if fetched:
                        inner, why = inspect_event(stored, self._keyring, members, host)
                if inner is not None:
                    out.append(inner)
                elif why == "no_key":
                    self._count(room_id, "unreadable", stored.seq)
                    holding = True
                else:
                    self._count(room_id, "rejected", stored.seq)
                if not holding:
                    settled = stored.seq
                if len(out) >= limit:
                    break
            if out:
                break  # return what we have; the caller asks again from its last event
        if out or settled > start:
            self._resume[room_id] = (out[-1].seq if out else after_seq, settled)
        return out


# -- files: code bundles and images -------------------------------------------------------------------------------------
import hashlib  # noqa: E402
import hmac  # noqa: E402
import os  # noqa: E402
import struct  # noqa: E402
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

from aletheore.council import crypto  # noqa: E402
from aletheore.council.attachments import MAX_IMAGE_BYTES, sniff_image  # noqa: E402
from aletheore.council.bundle_store import OBJECT_MAGIC, OBJECT_MIN, check_ids  # noqa: E402
from aletheore.council.protocol import MAX_BUNDLE_BYTES  # noqa: E402

_OBJECT_KEY_CONTEXT = "council/object/v2"


def object_id(room_key: bytes, plaintext_sha256: str) -> str:
    """The name the server stores a file under: a keyed hash of the file's real sha-256, so the server cannot tell whether it
    holds a file it already knows, and two rooms with different keys store the same file under unrelated names."""
    return hmac.new(room_key, b"council/object-id/v2\n" + plaintext_sha256.encode(), hashlib.sha256).hexdigest()


def open_object(keyring: Keyring, src: Path, dest: Path, expected_sha256: str) -> None:
    """Decrypt a sealed object to `dest`, only if it verifies end to end and is the file whose plaintext sha-256 was announced.
    Raises ValueError (nothing is left at `dest`) for anything wrong: damaged, cut, swapped, reordered, or no key for its epoch."""
    raw = Path(src).read_bytes()
    if len(raw) < OBJECT_MIN or not raw.startswith(OBJECT_MAGIC):
        raise ValueError("this is not a sealed Council object")
    (epoch,) = struct.unpack(">I", raw[8:12])
    key = keyring.get(epoch)
    if key is None:
        raise ValueError(f"this device has no key for epoch {epoch}, so it cannot open this file")
    header = raw[12:36]
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(dir=dest.parent, prefix=".open-"))
    try:
        sealed, plain = work / "sealed", work / "plain"
        sealed.write_bytes(raw[36:])
        try:
            crypto.stream_decrypt(crypto.derive_key(key, _OBJECT_KEY_CONTEXT), header, sealed, plain)
        except crypto.CryptoError as exc:
            raise ValueError("this file could not be verified: it was damaged, cut short, reordered or swapped") from exc
        digest = hashlib.sha256()
        with open(plain, "rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                digest.update(block)
        if digest.hexdigest() != expected_sha256:
            raise ValueError("the decrypted file does not match the sha-256 it was announced with (the server may have swapped it)")
        os.replace(plain, dest)
    finally:
        for leftover in work.glob("*"):
            leftover.unlink(missing_ok=True)
        work.rmdir()


class EncryptedBundleStore:
    """A BundleStore for an encrypted room. Callers keep using the plaintext sha-256 they already put in events; files are sealed
    with the room's current key before they leave this machine, stored under `object_id`, and verified end to end when fetched.
    A reader tries the ids for each key epoch it holds, newest first (the id depends on the epoch the writer used)."""

    def __init__(self, inner, keyring: Keyring, room_id: str, key_sync: Callable[[str], int] | None = None) -> None:
        self._inner, self._keyring, self._room_id, self._key_sync = inner, keyring, room_id, key_sync

    def _check(self, room_id: str, sha256: str) -> None:
        check_ids(room_id, sha256)
        if room_id != self._room_id:
            raise ValueError("this vault holds the files of a different room")

    def _put(self, kind: str, room_id: str, sha256: str, path: Path) -> None:
        self._check(room_id, sha256)
        path = Path(path)
        limit = MAX_BUNDLE_BYTES if kind == "bundle" else MAX_IMAGE_BYTES
        if path.stat().st_size > limit:
            raise ValueError(f"{kind} too large")
        digest = hashlib.sha256()
        with open(path, "rb") as f:
            head = f.read(16)
            digest.update(head)
            for block in iter(lambda: f.read(1 << 20), b""):
                digest.update(block)
        if digest.hexdigest() != sha256:
            raise ValueError(f"{kind} does not match its sha-256")
        if kind == "blob" and sniff_image(head) is None:
            raise ValueError("not a supported image")
        latest = self._keyring.latest()
        if latest is None:
            raise PermissionError("this device holds no key for the room yet")
        epoch, key = latest
        with tempfile.TemporaryDirectory() as work:
            sealed, obj = Path(work) / "sealed", Path(work) / "object"
            header = crypto.stream_encrypt(crypto.derive_key(key, _OBJECT_KEY_CONTEXT), path, sealed)
            obj.write_bytes(OBJECT_MAGIC + struct.pack(">I", epoch) + header + sealed.read_bytes())
            put = self._inner.put_bundle if kind == "bundle" else self._inner.put_blob
            put(room_id, object_id(key, sha256), obj)

    def _get(self, kind: str, room_id: str, sha256: str, dest: Path) -> None:
        self._check(room_id, sha256)
        fetch = self._inner.get_bundle if kind == "bundle" else self._inner.get_blob
        synced = False
        with tempfile.TemporaryDirectory() as work:
            obj = Path(work) / "object"
            while True:
                for epoch in reversed(self._keyring.epochs()):
                    try:
                        fetch(room_id, object_id(self._keyring.get(epoch), sha256), obj)
                        break
                    except KeyError:
                        continue
                else:
                    if self._key_sync is not None and not synced:
                        synced = True
                        try:
                            if self._key_sync(room_id):
                                continue  # new keys arrived: the file may be under one of them
                        except (PermissionError, ConnectionError, KeyError):
                            pass
                    raise KeyError(f"no such {kind}: {sha256}")
                break
            open_object(self._keyring, obj, Path(dest), sha256)

    def put_bundle(self, room_id: str, sha256: str, path: Path) -> None:
        self._put("bundle", room_id, sha256, path)

    def get_bundle(self, room_id: str, sha256: str, dest: Path) -> None:
        self._get("bundle", room_id, sha256, dest)

    def put_blob(self, room_id: str, sha256: str, path: Path) -> None:
        self._put("blob", room_id, sha256, path)

    def get_blob(self, room_id: str, sha256: str, dest: Path) -> None:
        self._get("blob", room_id, sha256, dest)
