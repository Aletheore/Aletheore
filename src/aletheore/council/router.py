"""RoomRouter: the one store the commands use once a device is signed in to a server.

Commands do not know or care whether a room is sealed. They call `open_store()` and get this: for every room it looks at the room's
fixed encryption mode and sends reads and writes through that room's `E2ERoomStore` (and files through its `EncryptedBundleStore`),
or straight to the server for a plain room. Keys, grants and the host's key duties for a sealed room are reached with `keys_for`.

Everything that is not a room-store operation (signing in, invitations, join requests, whoami) goes to the server client unchanged.
"""

from __future__ import annotations

import threading
from pathlib import Path

from aletheore.council.e2e_store import E2ERoomStore, EncryptedBundleStore
from aletheore.council.identity import Identity
from aletheore.council.keys import KeyStore, RoomKeys
from aletheore.council.store import RoomInfo


class RoomRouter:
    supports_agent_tools = False  # an agent's tool process never holds a key or a session: Room reaches this store for it through a broker

    def __init__(self, remote, identity: Identity, keystore: KeyStore) -> None:
        self.remote, self.identity, self.keystore = remote, identity, keystore
        self._watches: dict = {}
        self._rooms: dict[str, tuple[E2ERoomStore | None, RoomKeys | None, EncryptedBundleStore | None]] = {}
        self._lock = threading.RLock()

    def _resolve(self, room_id: str):
        with self._lock:
            entry = self._rooms.get(room_id)
            if entry is not None:
                return entry
            info = self.remote.room_info(room_id)  # raises for an unknown room or no access: nothing is cached for those
            if info.crypto != "e2e-v1":
                entry = (None, None, None)
            else:
                keys = RoomKeys(room_id, self.identity, self.keystore, self.remote, self.remote.room_devices, host=info.host)
                sealed = E2ERoomStore(self.remote, self.identity, self.keystore.keyring(room_id), self.remote.room_devices,
                                      key_sync=lambda room: keys.sync())
                keys.store = sealed
                entry = (sealed, keys, EncryptedBundleStore(self.remote, self.keystore.keyring(room_id), room_id, key_sync=lambda room: keys.sync()))
            self._rooms[room_id] = entry
            return entry

    def store_for(self, room_id: str):
        return self._resolve(room_id)[0] or self.remote

    def keys_for(self, room_id: str) -> RoomKeys | None:
        """The key handler for a sealed room, or None for a plain one."""
        return self._resolve(room_id)[1]

    def files_for(self, room_id: str):
        return self._resolve(room_id)[2] or self.remote

    # -- rooms ---------------------------------------------------------------------------------------------------------
    def create_room(self, room_id: str, host_participant: str, title: str = "", mode: str = "interactive", crypto: str = "none") -> None:
        self.remote.create_room(room_id, host_participant, title=title, mode=mode, crypto=crypto)
        if crypto == "e2e-v1":
            with self._lock:
                self._rooms.pop(room_id, None)
            self.keys_for(room_id).start()  # epoch 1, before the first event is written

    def room_info(self, room_id: str) -> RoomInfo:
        return self.remote.room_info(room_id)

    def members_of(self, room_id: str) -> list[str]:
        return self.remote.members_of(room_id)

    def add_member(self, room_id: str, participant: str) -> None:
        self.remote.add_member(room_id, participant)

    def remove_member(self, room_id: str, participant: str) -> None:
        keys = self.keys_for(room_id)
        if keys is not None:
            keys.remove_member(participant)  # the server removes them, then everyone left gets a new key
        else:
            self.remote.remove_member(room_id, participant)

    def grant_member(self, room_id: str, participant: str) -> None:
        """After the host lets someone in: hand them the room's current key (nothing to do for a plain room)."""
        keys = self.keys_for(room_id)
        if keys is not None:
            keys.grant_to(participant)

    def reconcile_devices(self, room_id: str) -> dict:
        """Host only, sealed rooms only: give members' new devices the key and re-key after a revoked device. {} when not applicable."""
        from aletheore.council.keys import DeviceWatch, HostKeyError

        keys = self.keys_for(room_id)
        if keys is None:
            return {}
        watch = self._watches.setdefault(room_id, DeviceWatch(keys))
        try:
            return watch.check()
        except HostKeyError:
            return {}  # not the host's device: nothing for this device to do

    def held_epochs(self, room_id: str) -> list[int]:
        try:
            return self.keystore.epochs(room_id)
        except Exception:
            return []

    def current_epoch(self, room_id: str) -> int | None:
        """The key epoch a newly approved member would receive (None for a plain room or when this device holds no key)."""
        try:
            epochs = self.keystore.epochs(room_id)
        except Exception:
            return None
        return max(epochs) if epochs else None

    def close_room(self, room_id: str) -> None:
        self.remote.close_room(room_id)

    def list_rooms(self, participant: str = "") -> list[RoomInfo]:
        return self.remote.list_rooms(participant)

    def host_of(self, room_id: str) -> str:
        return self.remote.host_of(room_id)

    def last_seq(self, room_id: str) -> int:
        return self.remote.last_seq(room_id)

    # -- events --------------------------------------------------------------------------------------------------------
    def append(self, draft):
        return self.store_for(draft.room_id).append(draft)

    def read_after(self, room_id: str, after_seq: int, limit: int = 500):
        return self.store_for(room_id).read_after(room_id, after_seq, limit)

    # -- files ---------------------------------------------------------------------------------------------------------
    def put_bundle(self, room_id: str, sha256: str, path: Path) -> None:
        self.files_for(room_id).put_bundle(room_id, sha256, path)

    def get_bundle(self, room_id: str, sha256: str, dest: Path) -> None:
        self.files_for(room_id).get_bundle(room_id, sha256, dest)

    def put_blob(self, room_id: str, sha256: str, path: Path) -> None:
        self.files_for(room_id).put_blob(room_id, sha256, path)

    def get_blob(self, room_id: str, sha256: str, dest: Path) -> None:
        self.files_for(room_id).get_blob(room_id, sha256, dest)

    def __getattr__(self, name: str):
        # signing in, invitations, join requests, whoami: server operations that have no sealed form
        if name.startswith("_") or name in ("remote", "identity", "keystore"):
            raise AttributeError(name)
        return getattr(self.remote, name)
