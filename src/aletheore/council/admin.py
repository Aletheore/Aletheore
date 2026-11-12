"""What the person running a Council server can do about their own server: see rooms and usage, delete a room, revoke a device,
and change the admin token. Every action is written to an audit log that never holds a secret."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path
from typing import Callable

from .housekeeping import remove_room_files

_SECRET_WORDS = ("token", "secret", "code", "password", "key")


class AdminError(Exception):
    pass


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class AdminTool:
    def __init__(self, store, accounts, bundle_dir: Path, audit_path: Path, admin_hash: str = "", clock: Callable[[], float] = time.time) -> None:
        self.store, self.accounts, self.bundle_dir, self.audit_path, self.admin_hash, self.clock = store, accounts, Path(bundle_dir), Path(audit_path), admin_hash, clock

    # -- who may do this ------------------------------------------------------------------------------------------------
    def authorize(self, presented: str) -> None:
        """When an admin token is configured it must be presented. With none configured the person at the machine is the admin (they
        hold the database file anyway), and the action is still logged."""
        if self.admin_hash and not hmac.compare_digest(token_hash(presented or ""), self.admin_hash):
            self._audit("authorize", {}, False, "wrong or missing admin token")
            raise AdminError("the admin token is missing or wrong (set COUNCIL_ADMIN_TOKEN)")

    def _audit(self, command: str, args: dict, ok: bool, note: str = "") -> None:
        clean = {k: v for k, v in args.items() if not any(w in k.lower() for w in _SECRET_WORDS)}  # arguments named like a secret are never written
        line = json.dumps({"at": round(self.clock(), 3), "command": command, "args": clean, "ok": ok, "note": note[:200]}, sort_keys=True)
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.audit_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    # -- looking ----------------------------------------------------------------------------------------------------------
    def rooms(self) -> list[dict]:
        out = []
        for r in self.store.all_rooms():
            events, size = self.store.room_events_and_bytes(r.room_id)
            out.append({"room_id": r.room_id, "title": r.title, "host": r.host, "status": r.status, "members": len(r.members),
                        "events": events, "event_bytes": size, "last_activity": r.last_activity, "sealed": r.crypto == "e2e-v1"})
        return out

    def usage(self) -> dict:
        rooms = self.rooms()
        files = [f for f in self.bundle_dir.rglob("*") if f.is_file()] if self.bundle_dir.exists() else []
        per_host: dict[str, int] = {}
        for r in rooms:
            per_host[r["host"]] = per_host.get(r["host"], 0) + 1
        return {"rooms": len(rooms), "open_rooms": sum(1 for r in rooms if r["status"] == "open"), "events": sum(r["events"] for r in rooms),
                "event_bytes": sum(r["event_bytes"] for r in rooms), "files": len(files), "file_bytes": sum(f.stat().st_size for f in files),
                "rooms_per_host": per_host}

    # -- changing ----------------------------------------------------------------------------------------------------------
    def delete_room(self, room_id: str) -> dict:
        """Remove a room completely: events, members, invites, join requests, key grants, bundles and images."""
        if not self.store.delete_room(room_id):
            self._audit("delete-room", {"room": room_id}, False, "no such room")
            raise AdminError(f"no room called {room_id}")
        data = self.accounts.delete_room_data(room_id) if self.accounts is not None else {}
        files = remove_room_files(self.bundle_dir, room_id)
        self._audit("delete-room", {"room": room_id}, True, f"files {files}")
        return {"room_id": room_id, "files": files, **data}

    def revoke_device(self, device_id: str) -> None:
        if self.accounts is None:
            raise AdminError("this server has no accounts")
        try:
            self.accounts.revoke_device(device_id)
        except Exception as exc:  # AccountError: no such device
            self._audit("revoke-device", {"device": device_id}, False, str(exc))
            raise AdminError(str(exc)) from None
        self._audit("revoke-device", {"device": device_id}, True)

    def rotate_admin_token(self, hash_file: Path) -> str:
        """Make a new admin token and store only its hash. The token is returned once and is not kept anywhere."""
        token = secrets.token_urlsafe(32)
        hash_file.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(hash_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(token_hash(token))
        self.admin_hash = token_hash(token)
        self._audit("rotate-admin-token", {}, True)
        return token
