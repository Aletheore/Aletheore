"""Where Shield keeps per-repository state: config and one JSON file per agent session."""

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

_OFFLINE_VALUES = {"1", "true", "yes"}
_SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
# Bidi controls, zero-width and invisible format characters (soft hyphen, Arabic letter mark, word joiner and
# invisible operators, byte order mark, tag characters) that can make text read differently from what it is.
# A regex character-class body shared by the guard's and setup's text cleaners, which show each as "?".
FORMAT_CHARACTERS = "\u00ad\u061c\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff\U000e0000-\U000e007f"


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
