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


def _default_home_dir() -> Path | None:
    try:
        return Path.home()
    except RuntimeError:
        # Path.home() itself can raise - not just resolve to something
        # unwritable - when there is no HOME env var and no passwd entry
        # (e.g. a process running as an arbitrary UID with neither, a
        # real scenario this repo's own CI smoke suite exercises). This
        # runs at *module import time* (DEFAULT_PREFERENCES_PATH below),
        # so an unguarded call here would crash `import aletheore.cli`
        # itself, before any later try/except in this module ever runs.
        return None


_HOME_DIR = _default_home_dir()
DEFAULT_PREFERENCES_PATH = (
    _HOME_DIR / ".config" / "aletheore" / "preferences.json" if _HOME_DIR is not None else None
)

_CRASH_REPORTING_KEY = "crash_reporting"
_NOTICE_SHOWN_KEY = "crash_reporting_notice_shown"

# Recognized "disable" values for ALETHEORE_CRASH_REPORTING, matched
# case-insensitively. Any other non-empty value - including "" - is
# treated as enabled: an env var merely being *set* must not silently
# disable reporting, only an explicit, recognized "off" value should.
# Matches this repo's existing ALETHEORE_MCP_WATCH convention
# (watch.py's _FALSE_VALUES) so the same vocabulary works everywhere.
_DISABLE_VALUES = {"0", "false", "no", "off"}


def is_crash_reporting_enabled(preferences_path: Path | None = DEFAULT_PREFERENCES_PATH) -> bool:
    env_value = os.environ.get("ALETHEORE_CRASH_REPORTING")
    if env_value is not None:
        return env_value.strip().lower() not in _DISABLE_VALUES

    data = _load_preferences(preferences_path)
    value = data.get(_CRASH_REPORTING_KEY)
    return value if isinstance(value, bool) else True


def set_crash_reporting_enabled(
    enabled: bool, preferences_path: Path | None = DEFAULT_PREFERENCES_PATH
) -> None:
    _save_preference(preferences_path, _CRASH_REPORTING_KEY, enabled)


def has_shown_crash_reporting_notice(
    preferences_path: Path | None = DEFAULT_PREFERENCES_PATH,
) -> bool:
    data = _load_preferences(preferences_path)
    return bool(data.get(_NOTICE_SHOWN_KEY, False))


def mark_crash_reporting_notice_shown(
    preferences_path: Path | None = DEFAULT_PREFERENCES_PATH,
) -> None:
    _save_preference(preferences_path, _NOTICE_SHOWN_KEY, True)


def _load_preferences(preferences_path: Path | None) -> dict:
    if preferences_path is None or not preferences_path.exists():
        return {}
    try:
        # ValueError covers json.JSONDecodeError (a subclass) and also
        # UnicodeDecodeError raised by read_text() on non-UTF-8 bytes -
        # both are "this file is unreadable", not a reason to crash.
        data = json.loads(preferences_path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_preference(preferences_path: Path | None, key: str, value: bool) -> None:
    if preferences_path is None:
        # No determinable home directory (see _default_home_dir) -
        # nowhere to persist. Same "best effort, never crash" outcome as
        # the OSError case below, just a different reason.
        return
    try:
        preferences_path.parent.mkdir(parents=True, exist_ok=True)
        data = _load_preferences(preferences_path)
        data[key] = value
        preferences_path.write_text(json.dumps(data, indent=2))
    except OSError:
        # Best-effort: a read-only/unwritable HOME (sandboxed builds, some
        # CI, read-only containers) must never crash the CLI over a
        # preference write - every command, including --help, goes
        # through this path via the first-run notice in main(). Silently
        # not persisting (the notice reprints next run; an explicit
        # toggle doesn't stick) is strictly better than bricking the tool.
        pass
