"""Which devices this person has compared by their four words. Kept locally, per device id, never sent anywhere.

A person's device key is its id (the id is derived from the key), so a *changed* key shows up as a new device id the person has
never compared: the old mark does not carry over to it, and the screen says so."""

from __future__ import annotations

import json
from pathlib import Path


class VerifiedMarks:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        try:
            self._data: dict[str, list[str]] = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self._data = {}

    def mark(self, handle: str, device_id: str) -> None:
        ids = set(self._data.get(handle, []))
        ids.add(device_id)
        self._data[handle] = sorted(ids)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data))

    def status(self, handle: str, device_ids: list[str]) -> str:
        """compared: every device compared; partly: some; none: no mark yet; changed: a mark exists but a device has an id never compared."""
        known = set(self._data.get(handle, []))
        have = [d for d in device_ids if d in known]
        if not known:
            return "none"
        if len(have) == len(device_ids) and device_ids:
            return "compared"
        return "changed" if have == [] or len(have) < len(device_ids) else "partly"
