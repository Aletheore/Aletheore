"""Consent-gated, project-local installation of the optional unchanged-read guard hooks."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aletheore.hook_config import python_hook
from aletheore.user_paths import is_link_or_junction, set_replacement_mode

SETTINGS_RELATIVE_PATH = Path(".claude") / "settings.local.json"
SIDECAR_NAME = "aletheore-read-guard-hooks.json"
_EVENTS = ("PreToolUse", "PostToolUse")


def _is_link(path: Path) -> bool:
    try:
        return is_link_or_junction(path)
    except OSError as exc:
        raise ReadGuardSetupError(f"{path} cannot be inspected; nothing was changed") from exc


class ReadGuardSetupError(ValueError):
    pass


@dataclass(frozen=True)
class InstallPlan:
    settings_path: Path
    sidecar_path: Path
    entries: dict[str, dict[str, Any]]
    settings_after: dict[str, Any]
    sidecar_after: dict[str, Any]
    settings_fingerprint: str | None
    sidecar_fingerprint: str | None
    already_installed: bool = False


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ReadGuardSetupError(
            f"{path} is unreadable or invalid JSON; nothing was changed"
        ) from exc
    if not isinstance(data, dict):
        raise ReadGuardSetupError(f"{path} is not a JSON object; nothing was changed")
    return data


def _fingerprint(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise ReadGuardSetupError(
            f"{path} cannot be read; nothing was changed"
        ) from exc


def _write_json(
    path: Path, data: dict[str, Any], *, expected_fingerprint: str | None
) -> None:
    if _fingerprint(path) != expected_fingerprint:
        raise ReadGuardSetupError(
            f"{path} changed since preview; nothing was changed; run the command again"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("x", encoding="utf-8", newline="\n") as stream:
            set_replacement_mode(path, stream.fileno())
            json.dump(data, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def _command(event: str, setup_id: str) -> dict:
    event_arg = "pre" if event == "PreToolUse" else "post"
    return python_hook("aletheore.read_guard", event_arg, "--setup-id", setup_id)


def _validated_existing_entries(
    sidecar: dict[str, Any], sidecar_path: Path
) -> dict[str, dict[str, Any]]:
    entries = sidecar.get("entries")
    if (
        sidecar.get("schema") != 1
        or not isinstance(sidecar.get("created_settings_file"), bool)
        or not isinstance(entries, dict)
        or any(
            event not in _EVENTS or not isinstance(entry, dict)
            for event, entry in entries.items()
        )
    ):
        raise ReadGuardSetupError(
            f"{sidecar_path}: invalid hook ownership record; nothing was changed"
        )
    return entries


def is_installed(project: Path) -> bool:
    project = Path(project)
    settings_path = project / SETTINGS_RELATIVE_PATH
    sidecar_path = settings_path.parent / SIDECAR_NAME
    if (
        _is_link(settings_path.parent)
        or _is_link(settings_path)
        or _is_link(sidecar_path)
        or not settings_path.is_file()
        or not sidecar_path.is_file()
    ):
        return False
    try:
        owned = _validated_existing_entries(_load_json(sidecar_path), sidecar_path)
        settings = _load_json(settings_path)
    except ReadGuardSetupError:
        return False
    hooks = settings.get("hooks", {})
    return (
        isinstance(hooks, dict)
        and set(owned) == set(_EVENTS)
        and all(
            isinstance(hooks.get(event), list) and entry in hooks[event]
            for event, entry in owned.items()
        )
    )


def plan_install(project: Path) -> InstallPlan:
    project = Path(project)
    settings_path = project / SETTINGS_RELATIVE_PATH
    sidecar_path = settings_path.parent / SIDECAR_NAME
    if (
        _is_link(settings_path.parent)
        or _is_link(settings_path)
        or _is_link(sidecar_path)
    ):
        raise ReadGuardSetupError(
            "Claude settings paths may not be symlinks; nothing was changed"
        )
    settings = _load_json(settings_path)
    sidecar = _load_json(sidecar_path)
    settings_fingerprint = _fingerprint(settings_path)
    sidecar_fingerprint = _fingerprint(sidecar_path)
    if sidecar_fingerprint is not None and not sidecar:
        raise ReadGuardSetupError(
            f"{sidecar_path}: empty ownership record; nothing was changed"
        )
    hooks = settings.get("hooks", {})
    if not isinstance(hooks, dict):
        raise ReadGuardSetupError(
            f"{settings_path}: 'hooks' is not an object; nothing was changed"
        )
    for event in _EVENTS:
        if event in hooks and not isinstance(hooks[event], list):
            raise ReadGuardSetupError(
                f"{settings_path}: hooks.{event} is not a list; nothing was changed"
            )

    if sidecar:
        owned = _validated_existing_entries(sidecar, sidecar_path)
        if set(owned) != set(_EVENTS):
            raise ReadGuardSetupError(
                f"{sidecar_path}: incomplete hook ownership record; nothing was changed"
            )
        missing = {
            event: entry
            for event, entry in owned.items()
            if entry not in hooks.get(event, [])
        }
        if not missing:
            return InstallPlan(
                settings_path,
                sidecar_path,
                owned,
                settings,
                sidecar,
                settings_fingerprint,
                sidecar_fingerprint,
                True,
            )
        after = json.loads(json.dumps(settings))
        after_hooks = after.setdefault("hooks", {})
        for event, entry in missing.items():
            after_hooks.setdefault(event, []).append(entry)
        return InstallPlan(
            settings_path,
            sidecar_path,
            missing,
            after,
            sidecar,
            settings_fingerprint,
            sidecar_fingerprint,
        )

    setup_id = uuid.uuid4().hex
    entries = {
        "PreToolUse": {
            "matcher": "Read",
            "hooks": [_command("PreToolUse", setup_id)],
        },
        "PostToolUse": {
            "matcher": "Read",
            "hooks": [_command("PostToolUse", setup_id)],
        },
    }
    after = json.loads(json.dumps(settings))
    after_hooks = after.setdefault("hooks", {})
    for event, entry in entries.items():
        after_hooks.setdefault(event, []).append(entry)
    state = {
        "schema": 1,
        "created_settings_file": not settings_path.exists(),
        "entries": entries,
    }
    return InstallPlan(
        settings_path,
        sidecar_path,
        entries,
        after,
        state,
        settings_fingerprint,
        sidecar_fingerprint,
    )


def install(project: Path, plan: InstallPlan | None = None) -> InstallPlan:
    plan = plan or plan_install(project)
    if plan.already_installed:
        return plan
    # Refuse stale previews before creating the ownership record. Persist that
    # record first so a crash after settings land remains removable.
    if (
        _fingerprint(plan.settings_path) != plan.settings_fingerprint
        or _fingerprint(plan.sidecar_path) != plan.sidecar_fingerprint
    ):
        raise ReadGuardSetupError(
            "settings changed since preview; nothing was changed; run the command again"
        )
    _write_json(
        plan.sidecar_path,
        plan.sidecar_after,
        expected_fingerprint=plan.sidecar_fingerprint,
    )
    try:
        _write_json(
            plan.settings_path,
            plan.settings_after,
            expected_fingerprint=plan.settings_fingerprint,
        )
    except (ReadGuardSetupError, OSError, UnicodeError):
        # If the settings file changed after the first check, remove only the
        # ownership record this install just wrote, leaving the changed file.
        if plan.sidecar_fingerprint is None:
            try:
                written = (json.dumps(plan.sidecar_after, indent=2) + "\n").encode("utf-8")
                if _fingerprint(plan.sidecar_path) == hashlib.sha256(written).hexdigest():
                    plan.sidecar_path.unlink(missing_ok=True)
            except (OSError, ReadGuardSetupError):
                pass
        raise
    return plan


def remove(project: Path) -> list[str]:
    settings_path = Path(project) / SETTINGS_RELATIVE_PATH
    sidecar_path = settings_path.parent / SIDECAR_NAME
    if (
        _is_link(settings_path.parent)
        or _is_link(settings_path)
        or _is_link(sidecar_path)
    ):
        raise ReadGuardSetupError(
            "Claude settings paths may not be symlinks; nothing was changed"
        )
    if not sidecar_path.exists():
        return []
    sidecar_fingerprint = _fingerprint(sidecar_path)
    sidecar = _load_json(sidecar_path)
    entries = _validated_existing_entries(sidecar, sidecar_path)
    settings = _load_json(settings_path)
    settings_fingerprint = _fingerprint(settings_path)
    hooks = settings.get("hooks", {})
    if not isinstance(hooks, dict):
        raise ReadGuardSetupError(
            f"{settings_path}: 'hooks' is not an object; nothing was changed"
        )

    removed: list[str] = []
    for event, owned_entry in entries.items():
        registered = hooks.get(event, [])
        if not isinstance(registered, list):
            raise ReadGuardSetupError(
                f"{settings_path}: hooks.{event} is not a list; nothing was changed"
            )
        kept = [entry for entry in registered if entry != owned_entry]
        if len(kept) != len(registered):
            removed.append(event)
        if kept:
            hooks[event] = kept
        else:
            hooks.pop(event, None)
    if not hooks:
        settings.pop("hooks", None)
    if _fingerprint(sidecar_path) != sidecar_fingerprint:
        raise ReadGuardSetupError(f"{sidecar_path} changed during removal; nothing was changed")
    if settings:
        _write_json(settings_path, settings, expected_fingerprint=settings_fingerprint)
    elif sidecar.get("created_settings_file") and settings_path.exists():
        if _fingerprint(settings_path) != settings_fingerprint:
            raise ReadGuardSetupError(
                f"{settings_path} changed during removal; nothing was changed"
            )
        settings_path.unlink()
    elif settings_path.exists():
        _write_json(settings_path, settings, expected_fingerprint=settings_fingerprint)
    if _fingerprint(sidecar_path) != sidecar_fingerprint:
        raise ReadGuardSetupError(
            f"{sidecar_path} changed during removal; hooks were removed but the changed ownership record was preserved"
        )
    sidecar_path.unlink()
    return removed
