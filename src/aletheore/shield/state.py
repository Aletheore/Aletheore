"""Where Shield keeps per-repository state: config and one JSON file per agent session."""

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

_OFFLINE_VALUES = {"1", "true", "yes"}
_SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


@dataclass
class ShieldConfig:
    registry_lookups: bool = True
    # What `setup.apply_setup` created, so removal deletes exactly that and nothing the user owns: paths (relative to
    # the working-tree top, or absolute outside it) and the settings.local.json keys ("hooks", "hooks.Stop", ...).
    created_files: tuple[str, ...] = ()
    created_settings_keys: tuple[str, ...] = ()


def shield_dir(repo: Path, *, create: bool = False) -> Path:
    path = Path(repo) / ".aletheore" / "shield"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def _config_path(repo: Path) -> Path:
    return shield_dir(repo) / "config.json"


def load_config(repo: Path) -> ShieldConfig:
    try:
        data = json.loads(_config_path(repo).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ShieldConfig()
    if not isinstance(data, dict):
        return ShieldConfig()
    lookups = data.get("registry_lookups", True)
    return ShieldConfig(
        registry_lookups=lookups if isinstance(lookups, bool) else True,
        created_files=_strings(data.get("created_files")),
        created_settings_keys=_strings(data.get("created_settings_keys")),
    )


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item)


def save_config(repo: Path, config: ShieldConfig) -> None:
    directory = shield_dir(repo, create=True)
    data: dict = {"registry_lookups": config.registry_lookups}
    if config.created_files:
        data["created_files"] = list(config.created_files)
    if config.created_settings_keys:
        data["created_settings_keys"] = list(config.created_settings_keys)
    (directory / "config.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def registry_lookups_enabled(repo: Path) -> bool:
    if os.environ.get("ALETHEORE_SHIELD_OFFLINE", "").strip().lower() in _OFFLINE_VALUES:
        return False
    return load_config(repo).registry_lookups


def session_state_path(repo: Path, session_id: str) -> Path | None:
    """Path of the JSON file for one session, or None when the id is empty or is not a plain file-name token."""
    if not _SESSION_ID.fullmatch(session_id) or session_id.endswith("."):
        return None
    return shield_dir(repo) / "sessions" / f"{session_id}.json"


_BASE_EVIDENCE_SUFFIX = ".base-air"


def session_base_evidence_path(repo: Path, session_id: str) -> Path | None:
    """Path of the session's saved copy of the evidence taken at its start, or None for an unusable session id.

    Uses the same id rules as `session_state_path`, and refuses an id ending in the suffix itself so this file can
    never be the state file of another session ("x.base-air" would map to the same name as the base of "x").
    """
    if session_state_path(repo, session_id) is None or session_id.endswith(_BASE_EVIDENCE_SUFFIX):
        return None
    return shield_dir(repo) / "sessions" / f"{session_id}{_BASE_EVIDENCE_SUFFIX}.json"
