"""The server's housekeeping: remove what has outlived its purpose. Runs on a timer, and the admin tool can run it on demand."""

from __future__ import annotations

import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .server_config import ServerSettings


def _age_days(iso: str, now: float) -> float | None:
    try:
        then = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return (now - then.timestamp()) / 86400


def remove_room_files(bundle_dir: Path, room_id: str) -> int:
    """Delete a room's bundles and images. Returns how many files went. The room id is checked so it cannot point outside the folder."""
    target = (Path(bundle_dir) / room_id).resolve()
    root = Path(bundle_dir).resolve()
    if root not in target.parents or not target.is_dir():
        return 0
    count = sum(1 for f in target.rglob("*") if f.is_file())
    shutil.rmtree(target, ignore_errors=True)
    return count


def archive_old_events(store, settings: ServerSettings, clock: Callable[[], float] = time.time) -> dict[str, int]:
    """Move events into compressed cold storage once they are far behind the room's newest snapshot (`archive_keep_events`) AND old
    enough (`archive_min_age_days`, so a client retrying a message it queued while away still finds its original event for the
    idempotency check). Rooms without a snapshot are never archived. Returns {room_id: events moved}."""
    from .snapshots import latest_snapshot
    from .protocol import is_horizon

    moved: dict[str, int] = {}
    if settings.archive_keep_events <= 0 or not hasattr(store, "archive_before"):
        return moved
    now = clock()
    for room in store.all_rooms():
        snap = latest_snapshot(store, room.room_id, room.host)
        if snap is None:
            continue
        target = snap[0] - settings.archive_keep_events
        if target <= store.horizon(room.room_id) + 1000:
            continue  # not worth a pass yet
        probe = [e for e in store.read_after(room.room_id, target - 1, 1) if not is_horizon(e)]
        age = _age_days(probe[0].created_at, now) if probe else None
        if age is None or age < settings.archive_min_age_days:
            continue  # events only get older: a later sweep will find the target old enough
        n = store.archive_before(room.room_id, target)
        if n:
            moved[room.room_id] = n
    return moved
    now = clock()
    for room in store.all_rooms():
        snap = latest_snapshot(store, room.room_id, room.host)
        if snap is None:
            continue
        target = snap[0] - settings.archive_keep_events
        if target <= store.horizon(room.room_id) + 1000:
            continue  # not worth a pass yet
        # only events older than the minimum age: find the newest old-enough seq at or below the target
        events = [e for e in store.read_after(room.room_id, store.horizon(room.room_id), 1) if not is_horizon(e)]
        limit_seq = target
        while limit_seq > store.horizon(room.room_id):
            probe = store.read_after(room.room_id, limit_seq - 1, 1)
            probe = [e for e in probe if not is_horizon(e)]
            age = _age_days(probe[0].created_at, now) if probe else None
            if age is not None and age >= settings.archive_min_age_days:
                break
            limit_seq -= max(1000, (limit_seq - store.horizon(room.room_id)) // 2)
        if limit_seq > store.horizon(room.room_id):
            n = store.archive_before(room.room_id, limit_seq)
            if n:
                moved[room.room_id] = n
    return moved


def run_sweep(store, accounts, bundle_dir: Path, settings: ServerSettings, clock: Callable[[], float] = time.time) -> dict:
    """One pass: delete closed rooms past their retention (events, members, files, invites, grants), and expire old account records."""
    now = clock()
    deleted: list[str] = []
    if settings.closed_room_retention_days > 0 and hasattr(store, "all_rooms") and hasattr(store, "delete_room"):
        for room in store.all_rooms():
            if room.status != "closed":
                continue
            age = _age_days(room.last_activity, now) if room.last_activity else None
            if age is None or age < settings.closed_room_retention_days:
                continue  # a room with no events has no age to judge by: it is left for the admin
            if store.delete_room(room.room_id):
                if accounts is not None:
                    accounts.delete_room_data(room.room_id)
                remove_room_files(bundle_dir, room.room_id)
                deleted.append(room.room_id)
    expired = accounts.purge_expired() if accounts is not None else {}
    return {"rooms_deleted": deleted, "expired": expired, "archived": archive_old_events(store, settings, clock)}


def sweep_loop(store, accounts, bundle_dir: Path, settings: ServerSettings, stop: threading.Event, on_result=lambda r: None,
               on_error=lambda e: None) -> None:
    while not stop.wait(settings.sweep_interval_s):
        try:
            on_result(run_sweep(store, accounts, bundle_dir, settings))
        except Exception as exc:  # noqa: BLE001 - housekeeping must never take the server down
            on_error(exc)
