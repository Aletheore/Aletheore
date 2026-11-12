"""Where you were: the room you last had open, how far you had read in each room, and any message you had not sent.

Kept only on this machine, in a file only the owner can read. Nothing here is ever sent to a server."""

from __future__ import annotations

import json
import os
from pathlib import Path


class ResumeStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._data = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            self._data = {}  # a missing or damaged file just means "nothing remembered"
        self._data.setdefault("rooms", {})
        self._dirty = False

    def _room(self, room_id: str) -> dict:
        rooms = self._data["rooms"]
        if not isinstance(rooms.get(room_id), dict):
            rooms[room_id] = {}
        return rooms[room_id]

    def last_room(self) -> str:
        value = self._data.get("last_room")
        return value if isinstance(value, str) else ""

    def set_last(self, room_id: str) -> None:
        if self._data.get("last_room") != room_id:
            self._data["last_room"] = room_id
            self._dirty = True

    def read_seq(self, room_id: str) -> int:
        value = self._room(room_id).get("read_seq", 0)
        return value if isinstance(value, int) and value >= 0 else 0

    def set_read(self, room_id: str, seq: int) -> None:
        if seq > self.read_seq(room_id):  # reading only moves forward
            self._room(room_id)["read_seq"] = int(seq)
            self._dirty = True

    def draft(self, room_id: str) -> str:
        value = self._room(room_id).get("draft", "")
        return value if isinstance(value, str) else ""

    def set_draft(self, room_id: str, text: str) -> None:
        if text != self.draft(room_id):
            if text:
                self._room(room_id)["draft"] = text
            else:
                self._room(room_id).pop("draft", None)
            self._dirty = True

    def pins(self, room_id: str) -> list[int]:
        value = self._room(room_id).get("pins", [])
        return [v for v in value if isinstance(v, int)] if isinstance(value, list) else []

    def toggle_pin(self, room_id: str, seq: int) -> bool:
        """Pin or unpin a message; True when it is now pinned."""
        pins = self.pins(room_id)
        if seq in pins:
            pins.remove(seq)
        else:
            pins.append(seq)
        self._room(room_id)["pins"] = pins
        self._dirty = True
        return seq in pins

    def save(self) -> None:
        if not self._dirty:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._data, f)
            os.replace(tmp, self.path)
            self._dirty = False
        except OSError:
            pass  # not being able to remember must never stop the app
