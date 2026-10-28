"""Where Shield keeps per-repository state: config and one JSON file per agent session."""

import json
import os
from dataclasses import dataclass
from pathlib import Path

_OFFLINE_VALUES = {"1", "true", "yes"}


@dataclass
class ShieldConfig:
    registry_lookups: bool = True


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
    return ShieldConfig(registry_lookups=lookups if isinstance(lookups, bool) else True)


def save_config(repo: Path, config: ShieldConfig) -> None:
    directory = shield_dir(repo, create=True)
    (directory / "config.json").write_text(
        json.dumps({"registry_lookups": config.registry_lookups}, indent=2) + "\n", encoding="utf-8"
    )


def registry_lookups_enabled(repo: Path) -> bool:
    if os.environ.get("ALETHEORE_SHIELD_OFFLINE", "").strip().lower() in _OFFLINE_VALUES:
        return False
    return load_config(repo).registry_lookups


def session_state_path(repo: Path, session_id: str) -> Path | None:
    """Path of the JSON file for one session, or None when the id is empty or could escape the directory."""
    if (
        not session_id
        or "\x00" in session_id
        or "/" in session_id
        or "\\" in session_id
        or session_id in (".", "..")
    ):
        return None
    return shield_dir(repo) / "sessions" / f"{session_id}.json"
