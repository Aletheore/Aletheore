"""Local, non-secret CLI preferences - today just the crash-reporting
opt-out. Sibling to credentials.py (same ~/.config/aletheore/ directory,
same "patch the loader, not the path constant" test-isolation pattern -
see tests/conftest.py), kept in a separate file since this isn't a secret
and has a simpler read/write pattern: load the whole file, mutate one key,
write it back whole. Deliberately skips credentials.py's cross-platform
file locking (_locked_rw_credentials_file) - losing a race between two
concurrent CLI invocations on a preference toggle just means re-running
the toggle, not silently losing a saved API token, so that complexity
isn't worth carrying here.
"""
import json
import os
from pathlib import Path

DEFAULT_PREFERENCES_PATH = Path.home() / ".config" / "aletheore" / "preferences.json"

_CRASH_REPORTING_KEY = "crash_reporting"
_NOTICE_SHOWN_KEY = "crash_reporting_notice_shown"

# Recognized "disable" values for ALETHEORE_CRASH_REPORTING, matched
# case-insensitively. Any other non-empty value - including "" - is
# treated as enabled: an env var merely being *set* must not silently
# disable reporting, only an explicit, recognized "off" value should.
_DISABLE_VALUES = {"0", "false", "no"}


def is_crash_reporting_enabled(preferences_path: Path = DEFAULT_PREFERENCES_PATH) -> bool:
    env_value = os.environ.get("ALETHEORE_CRASH_REPORTING")
    if env_value is not None:
        return env_value.strip().lower() not in _DISABLE_VALUES

    data = _load_preferences(preferences_path)
    value = data.get(_CRASH_REPORTING_KEY)
    return value if isinstance(value, bool) else True


def set_crash_reporting_enabled(
    enabled: bool, preferences_path: Path = DEFAULT_PREFERENCES_PATH
) -> None:
    _save_preference(preferences_path, _CRASH_REPORTING_KEY, enabled)


def has_shown_crash_reporting_notice(preferences_path: Path = DEFAULT_PREFERENCES_PATH) -> bool:
    data = _load_preferences(preferences_path)
    return bool(data.get(_NOTICE_SHOWN_KEY, False))


def mark_crash_reporting_notice_shown(preferences_path: Path = DEFAULT_PREFERENCES_PATH) -> None:
    _save_preference(preferences_path, _NOTICE_SHOWN_KEY, True)


def _load_preferences(preferences_path: Path) -> dict:
    if not preferences_path.exists():
        return {}
    try:
        data = json.loads(preferences_path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_preference(preferences_path: Path, key: str, value: bool) -> None:
    preferences_path.parent.mkdir(parents=True, exist_ok=True)
    data = _load_preferences(preferences_path)
    data[key] = value
    preferences_path.write_text(json.dumps(data, indent=2))
