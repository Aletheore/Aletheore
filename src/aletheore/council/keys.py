"""Room keys for an end-to-end encrypted room: making them, handing them out, keeping them, and changing them.

* The host makes the room's first key (epoch 1) and hands each member device a **grant**: the key sealed to that device's box key,
  with the host's signature over (room, epoch, key, recipient device) inside the seal. Only the intended device can open it, and
  only a grant signed by one of the host's devices is accepted, so a member cannot slip a key of their choosing to another member.
* A key is for one **epoch**. Removing a member makes the host start a new epoch and grant it to the people who remain; the server
  refuses the removed person at once, and they cannot read anything sealed under the new epoch. They keep what they already had:
  nothing can take back a key someone has already seen, and the plan says so plainly.
* A member added later gets the current epoch only. Older history is opened up by an explicit host action
  (`grant_to(person, epochs=[...])`), which is recorded in the log as a host event so everyone can see it happened.
* Each device keeps its keys in one file, encrypted with a key derived from its own identity, never in plaintext. If a host loses that
  file or the identity that made the room, **nothing can recover it**: the room cannot get a new key and members are told so.
* A rotation publishes every grant *before* the host switches to the new epoch (the new key is parked in the keystore first, so a retry
  after a crash hands out the same key), so an interrupted rotation never leaves anyone unable to read.
"""

from __future__ import annotations

import base64
import json
import os
import struct
import threading
from pathlib import Path
from typing import Callable, Iterable, Mapping

from aletheore.council import crypto
from aletheore.council.crypto import CryptoError
from aletheore.council.envelope import MAX_EPOCH, Keyring
from aletheore.council.identity import Identity, PublicIdentity
from aletheore.council.protocol import EventDraft, EventType

_GRANT_CONTEXT = b"council/grant/v1"
_STORE_CONTEXT = "council/keystore/v1"


class GrantError(Exception):
    """A grant could not be opened or did not verify."""


class KeyStoreError(Exception):
    """The key file cannot be read by this identity (damaged, or made by another identity). There is no recovery."""


class HostKeyError(Exception):
    """Only the room's host, holding the room's keys, can do this."""


def create_room_key() -> bytes:
    return crypto.random_bytes(32)


def _pack(*parts: bytes) -> bytes:
    return b"".join(struct.pack(">I", len(p)) + p for p in parts)


def _unpack(blob: bytes, count: int) -> list[bytes]:
    parts, pos = [], 0
    for _ in range(count):
        if pos + 4 > len(blob):
            raise GrantError("the grant is malformed")
        (n,) = struct.unpack(">I", blob[pos:pos + 4])
        pos += 4
        if n > len(blob) - pos:
            raise GrantError("the grant is malformed")
        parts.append(blob[pos:pos + n])
        pos += n
    if pos != len(blob):
        raise GrantError("the grant is malformed")
    return parts


def make_grant(room_id: str, room_key: bytes, epoch: int, recipient: PublicIdentity, sender: Identity) -> bytes:
    inner = _pack(room_id.encode(), str(epoch).encode(), room_key, recipient.device_id.encode())
    signature = sender.sign(_GRANT_CONTEXT + inner)
    return crypto.seal(recipient.box_pk, _pack(inner, sender.device_id.encode(), signature))


def accept_grant(room_id: str, blob: bytes, me: Identity, host_keys: Iterable[PublicIdentity]) -> tuple[int, bytes]:
    """The (epoch, key) in a grant meant for this device, signed by one of the host's devices. Raises GrantError otherwise."""
    try:
        opened = me.open_sealed(blob)
    except CryptoError as exc:
        raise GrantError("this grant is not for this device") from exc
    inner, signer, signature = _unpack(opened, 3)
    host = {p.device_id: p for p in host_keys}.get(signer.decode("utf-8", "replace"))
    if host is None or not crypto.verify(host.sign_vk, _GRANT_CONTEXT + inner, signature):
        raise GrantError("this grant was not signed by the room's host")
    room, epoch_text, key, device = _unpack(inner, 4)
    if room.decode("utf-8", "replace") != room_id or device.decode("utf-8", "replace") != me.device_id:
        raise GrantError("this grant is for a different room or device")
    try:
        epoch = int(epoch_text)
    except ValueError as exc:
        raise GrantError("the grant is malformed") from exc
    if not 1 <= epoch <= MAX_EPOCH or len(key) != 32:
        raise GrantError("the grant is malformed")
    return epoch, key


class _PersistentKeyring(Keyring):
    def __init__(self, store: "KeyStore", room_id: str, keys: Mapping[int, bytes]) -> None:
        super().__init__(keys)
        self._store, self._room = store, room_id

    def add(self, epoch: int, key: bytes) -> None:
        self._store._save_epoch(self._room, epoch, key)  # noqa: SLF001
        super().add(epoch, key)


class KeyStore:
    """All of a device's room keys in one file, encrypted under a key derived from the device's identity."""

    def __init__(self, path, identity: Identity) -> None:
        self.path, self._identity = Path(path), identity
        self._lock = threading.RLock()
        self._rooms: dict | None = None
        self._rings: dict[str, Keyring] = {}  # one live keyring per room, shared by everything on this device that reads it

    def _secret(self) -> bytes:
        return self._identity.derive_secret(_STORE_CONTEXT)

    def _load(self) -> dict:
        with self._lock:
            if self._rooms is not None:
                return self._rooms
            if not self.path.exists():
                self._rooms = {}
                return self._rooms
            try:
                doc = json.loads(self.path.read_text(encoding="utf-8"))
                plain = crypto.aead_decrypt(self._secret(), bytes.fromhex(doc["nonce"]), base64.b64decode(doc["ct"]), b"council/keystore")
                rooms = json.loads(plain.decode("utf-8"))
                if not isinstance(rooms, dict):
                    raise ValueError("not a key store")
            except (OSError, ValueError, KeyError, TypeError, CryptoError) as exc:
                raise KeyStoreError("this device's key file cannot be read (it is damaged, or it belongs to a different identity). "
                                    "Keys cannot be recovered; ask the host for fresh grants.") from exc
            self._rooms = rooms
            return rooms

    def _flush(self) -> None:
        with self._lock:
            nonce, ct = crypto.aead_encrypt(self._secret(), json.dumps(self._rooms or {}).encode("utf-8"), b"council/keystore")
            data = json.dumps({"v": 1, "nonce": nonce.hex(), "ct": base64.b64encode(ct).decode("ascii")}).encode("utf-8")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            if os.name == "nt":
                from aletheore.council.identity import _restrict_windows

                _restrict_windows(tmp)
            os.replace(tmp, self.path)

    def _room(self, room_id: str) -> dict:
        return self._load().setdefault(room_id, {"epochs": {}, "pending": None})

    def _save_epoch(self, room_id: str, epoch: int, key: bytes) -> None:
        with self._lock:
            room = self._room(room_id)
            room["epochs"][str(epoch)] = base64.b64encode(key).decode("ascii")
            self._flush()

    def epochs(self, room_id: str) -> list[int]:
        with self._lock:
            return sorted(int(e) for e in self._room(room_id)["epochs"])

    def keyring(self, room_id: str) -> Keyring:
        with self._lock:
            if room_id not in self._rings:
                self._rings[room_id] = _PersistentKeyring(self, room_id, {int(e): base64.b64decode(k) for e, k in self._room(room_id)["epochs"].items()})
            return self._rings[room_id]

    def pending(self, room_id: str) -> tuple[int, bytes] | None:
        with self._lock:
            p = self._room(room_id)["pending"]
            return (p["epoch"], base64.b64decode(p["key"])) if p else None

    def set_pending(self, room_id: str, epoch: int, key: bytes) -> None:
        with self._lock:
            self._room(room_id)["pending"] = {"epoch": epoch, "key": base64.b64encode(key).decode("ascii")}
            self._flush()

    def clear_pending(self, room_id: str) -> None:
        with self._lock:
            self._room(room_id)["pending"] = None
            self._flush()


class RoomKeys:
    """Key handling for one room on one device.

    `api` offers put_grant(room, participant, epoch, device_id, blob), my_grants(room) and remove_member(room, participant) (a
    RemoteRoomStore does). `directory(room)` returns {participant: [PublicIdentity, ...]} for the room's members. `store`, when set,
    is the room's encrypting store; the host's key events are written through it."""

    def __init__(self, room_id: str, identity: Identity, keystore: KeyStore, api, directory: Callable[[str], Mapping], host: str, store=None) -> None:
        self.room_id, self.identity, self.keystore, self.api, self.directory, self.host, self.store = room_id, identity, keystore, api, directory, host, store
        self._keyring = keystore.keyring(room_id)

    # -- host side ---------------------------------------------------------------------------------------------------
    def _require_host(self) -> Mapping:
        members = self.directory(self.room_id)
        mine = {p.device_id for p in members.get(self.host, [])}
        if self.identity.device_id not in mine:
            raise HostKeyError(f"this device is not one of {self.host}'s devices: only the host can do this, and a host's key cannot be recovered "
                               "if it is lost")
        return members

    def _recipients(self, members: Mapping, only: str | None = None) -> list[tuple[str, PublicIdentity]]:
        out = []
        for person, devices in members.items():
            if only is not None and person != only:
                continue
            out += [(person, d) for d in devices if d.device_id != self.identity.device_id]
        return out

    def _publish(self, epoch: int, key: bytes, recipients: list[tuple[str, PublicIdentity]]) -> None:
        for person, device in recipients:
            self.api.put_grant(self.room_id, person, epoch, device.device_id, make_grant(self.room_id, key, epoch, device, self.identity))

    def _post(self, cid: str, body: dict) -> None:
        if self.store is not None:
            self.store.append(EventDraft(self.room_id, cid, "room", EventType.SYSTEM, self.host, None, None, body))

    def _next_epoch(self) -> tuple[int, bytes]:
        parked = self.keystore.pending(self.room_id)
        if parked is not None:
            return parked  # a rotation that was interrupted: hand out the same key again
        latest = self._keyring.latest()
        if latest is None:
            raise HostKeyError("the room key is not on this device and cannot be recovered; the room cannot get a new key. "
                               "Start a new room, or restore this device's key file from a backup.")
        epoch = latest[0] + 1
        self.keystore.set_pending(self.room_id, epoch, create_room_key())
        return self.keystore.pending(self.room_id)  # type: ignore[return-value]

    def start(self) -> int:
        """Epoch 1: make the room's first key and give it to everyone in the room now."""
        members = self._require_host()
        if self._keyring.latest() is not None:
            return self._keyring.latest()[0]  # already started
        parked = self.keystore.pending(self.room_id)
        if parked is None:
            parked = (1, create_room_key())
            self.keystore.set_pending(self.room_id, *parked)
        epoch, key = parked  # an interrupted start hands out the same key again
        self._publish(epoch, key, self._recipients(members))
        self._keyring.add(epoch, key)
        self.keystore.clear_pending(self.room_id)
        return epoch

    def rotate(self) -> int:
        """A new epoch for the people in the room now. Grants first, then the switch, then a host event that tells members to fetch."""
        members = self._require_host()
        epoch, key = self._next_epoch()
        self._publish(epoch, key, self._recipients(members))
        self._keyring.add(epoch, key)
        self.keystore.clear_pending(self.room_id)
        self._post(f"key-rotated:{epoch}", {"subtype": "key_rotated", "epoch": epoch})
        return epoch

    def remove_member(self, participant: str) -> int:
        """Take someone out of the room and start a new epoch for everyone else. The server stops them at once."""
        self._require_host()
        self.api.remove_member(self.room_id, participant)
        epoch = self.rotate()
        self._post(f"member-removed:{participant}:{epoch}", {"subtype": "member_removed", "participant": participant, "epoch": epoch})
        return epoch

    def grant_to(self, participant: str, epochs: Iterable[int] | None = None) -> None:
        """Give a member keys: the current epoch by default, or older ones by explicit choice (recorded in the log)."""
        members = self._require_host()
        latest = self._keyring.latest()
        if latest is None:
            raise HostKeyError("the room key is not on this device and cannot be recovered")
        wanted = [latest[0]] if epochs is None else sorted(set(epochs))
        for epoch in wanted:
            key = self._keyring.get(epoch)
            if key is None:
                raise HostKeyError(f"this device has no key for epoch {epoch}, so it cannot grant it")
            self._publish(epoch, key, self._recipients(members, only=participant))
        if epochs is not None:
            self._post(f"history-granted:{participant}:{'-'.join(map(str, wanted))}",
                       {"subtype": "history_granted", "participant": participant, "epochs": wanted})

    # -- member side -------------------------------------------------------------------------------------------------
    def sync(self, room_id: str | None = None) -> int:
        """Fetch the grants the server holds for this device and keep the ones we do not have. Returns how many new epochs."""
        try:
            grants = self.api.my_grants(self.room_id)
            host_devices = self.directory(self.room_id).get(self.host, [])
        except (PermissionError, ConnectionError, KeyError):
            return 0  # no access (removed) or no connection: nothing new to learn
        added = 0
        for epoch, blob in grants:
            if self._keyring.get(epoch) is not None:
                continue
            try:
                got_epoch, key = accept_grant(self.room_id, blob, self.identity, host_devices)
            except GrantError:
                continue  # a bad grant is skipped, never trusted
            if got_epoch == epoch:
                self._keyring.add(epoch, key)
                added += 1
        return added


class DeviceWatch:
    """Host side: keeps every member's devices in step with the room key.

    A device that appears (a member linked a second machine) is given the current key. A device that disappears while its owner is
    still in the room (it was revoked) starts a new key epoch, which the revoked device is left out of. The first look only hands out
    the current key to everyone, which is harmless to repeat. What was seen is remembered for this session only."""

    def __init__(self, keys: RoomKeys) -> None:
        self.keys = keys
        self.known: dict[str, set[str]] | None = None

    def check(self) -> dict:
        members = self.keys._require_host()  # raises HostKeyError when this device is not the host's
        now = {person: {d.device_id for d in devices} for person, devices in members.items()}
        granted: list[str] = []
        rotated = None
        if self.known is None:
            for person in now:
                if now[person] - {self.keys.identity.device_id}:
                    self.keys.grant_to(person)
                    granted.append(person)
        else:
            for person, ids in now.items():
                if ids - self.known.get(person, set()):
                    self.keys.grant_to(person)
                    granted.append(person)
            if any(self.known.get(person, set()) - ids for person, ids in now.items() if person in self.known):
                rotated = self.keys.rotate()
        self.known = now
        return {"granted": granted, "rotated": rotated}
