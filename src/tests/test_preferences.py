import sys
from pathlib import Path

import pytest

from aletheore.preferences import (
    _default_home_dir,
    has_shown_crash_reporting_notice,
    is_crash_reporting_enabled,
    mark_crash_reporting_notice_shown,
    set_crash_reporting_enabled,
)

# Matches tests/test_credentials.py's own marker: os.chmod on Windows only
# toggles a read-only *file* attribute - it does not enforce directory
# write permissions the way POSIX mode bits do, so this reproduction
# wouldn't reliably trigger the failure there. The underlying fix
# (catching OSError broadly in _save_preference) is itself OS-agnostic;
# only this specific repro mechanism is POSIX-only.
_posix_only_permissions = pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX file permission bits don't apply on Windows"
)


def test_crash_reporting_defaults_to_enabled_when_nothing_set(monkeypatch, tmp_path):
    monkeypatch.delenv("ALETHEORE_CRASH_REPORTING", raising=False)
    assert is_crash_reporting_enabled(tmp_path / "prefs.json") is True


def test_crash_reporting_env_var_zero_disables(monkeypatch, tmp_path):
    monkeypatch.setenv("ALETHEORE_CRASH_REPORTING", "0")
    assert is_crash_reporting_enabled(tmp_path / "prefs.json") is False


def test_crash_reporting_env_var_false_disables_case_insensitive(monkeypatch, tmp_path):
    monkeypatch.setenv("ALETHEORE_CRASH_REPORTING", "FALSE")
    assert is_crash_reporting_enabled(tmp_path / "prefs.json") is False


def test_crash_reporting_env_var_empty_string_does_not_disable(monkeypatch, tmp_path):
    # An env var merely being *set* (e.g. exported empty by some shell
    # config) must not silently disable reporting - only a recognized
    # "off" value should.
    monkeypatch.setenv("ALETHEORE_CRASH_REPORTING", "")
    assert is_crash_reporting_enabled(tmp_path / "prefs.json") is True


def test_crash_reporting_env_var_overrides_a_disabled_file(monkeypatch, tmp_path):
    prefs_path = tmp_path / "prefs.json"
    set_crash_reporting_enabled(False, prefs_path)
    monkeypatch.setenv("ALETHEORE_CRASH_REPORTING", "1")
    assert is_crash_reporting_enabled(prefs_path) is True


def test_set_crash_reporting_enabled_persists_across_reads(monkeypatch, tmp_path):
    monkeypatch.delenv("ALETHEORE_CRASH_REPORTING", raising=False)
    prefs_path = tmp_path / "prefs.json"
    set_crash_reporting_enabled(False, prefs_path)
    assert is_crash_reporting_enabled(prefs_path) is False


def test_corrupted_preferences_file_defaults_to_enabled(monkeypatch, tmp_path):
    # Fail open, not closed: a malformed file must not crash CLI startup,
    # and must not silently disable the very safety net meant to catch
    # bugs like this one.
    monkeypatch.delenv("ALETHEORE_CRASH_REPORTING", raising=False)
    prefs_path = tmp_path / "prefs.json"
    prefs_path.write_text("{not valid json")
    assert is_crash_reporting_enabled(prefs_path) is True


def test_set_crash_reporting_enabled_creates_missing_config_directory(tmp_path):
    prefs_path = tmp_path / "nested" / "does" / "not" / "exist" / "preferences.json"
    set_crash_reporting_enabled(False, prefs_path)
    assert prefs_path.exists()


def test_notice_not_shown_by_default(tmp_path):
    assert has_shown_crash_reporting_notice(tmp_path / "prefs.json") is False


def test_mark_notice_shown_persists(tmp_path):
    prefs_path = tmp_path / "prefs.json"
    mark_crash_reporting_notice_shown(prefs_path)
    assert has_shown_crash_reporting_notice(prefs_path) is True


def test_setting_crash_reporting_does_not_clobber_notice_shown_flag(tmp_path):
    prefs_path = tmp_path / "prefs.json"
    mark_crash_reporting_notice_shown(prefs_path)
    set_crash_reporting_enabled(False, prefs_path)
    assert has_shown_crash_reporting_notice(prefs_path) is True


def test_crash_reporting_env_var_off_disables(monkeypatch, tmp_path):
    # Matches this repo's existing ALETHEORE_MCP_WATCH convention
    # (watch.py's _FALSE_VALUES), which already accepts "off" - a user
    # reaching for the feature's own on/off vocabulary must actually work.
    monkeypatch.setenv("ALETHEORE_CRASH_REPORTING", "off")
    assert is_crash_reporting_enabled(tmp_path / "prefs.json") is False


def test_is_crash_reporting_enabled_does_not_crash_on_unreadable_text(monkeypatch, tmp_path):
    # UnicodeDecodeError (non-UTF-8 bytes on disk) must fail open, same as
    # a JSON parse error - a corrupted preferences file must never crash
    # CLI startup.
    monkeypatch.delenv("ALETHEORE_CRASH_REPORTING", raising=False)
    prefs_path = tmp_path / "prefs.json"
    prefs_path.write_bytes(b"\xff\xfe\x00not valid utf-8")
    assert is_crash_reporting_enabled(prefs_path) is True


@_posix_only_permissions
def test_set_crash_reporting_enabled_does_not_crash_when_home_is_unwritable(tmp_path):
    # A read-only/unwritable HOME (sandboxed builds, some CI, read-only
    # containers) must not brick every CLI command over a preference
    # write - writing is best-effort.
    readonly_dir = tmp_path / "ro_home"
    readonly_dir.mkdir()
    prefs_path = readonly_dir / "aletheore" / "preferences.json"
    readonly_dir.chmod(0o500)
    try:
        set_crash_reporting_enabled(False, prefs_path)  # must not raise
    finally:
        readonly_dir.chmod(0o700)


def test_default_home_dir_returns_none_when_path_home_raises(monkeypatch):
    # Real, tested scenario in this repo's own CI (smoke "linux edge
    # cases": an arbitrary UID with no HOME env var and no passwd entry) -
    # Path.home() itself raises RuntimeError, not just resolves to
    # something unwritable. This runs at *module import time*
    # (DEFAULT_PREFERENCES_PATH is a module-level constant), so an
    # unguarded Path.home() call there crashes `import aletheore.cli`
    # before any of this module's own try/except logic ever runs.
    def _raise(*args, **kwargs):
        raise RuntimeError("Could not determine home directory.")

    monkeypatch.setattr(Path, "home", _raise)

    assert _default_home_dir() is None


def test_is_crash_reporting_enabled_defaults_to_enabled_when_preferences_path_is_none(
    monkeypatch,
):
    monkeypatch.delenv("ALETHEORE_CRASH_REPORTING", raising=False)
    assert is_crash_reporting_enabled(None) is True


def test_set_crash_reporting_enabled_does_not_crash_when_preferences_path_is_none():
    set_crash_reporting_enabled(False, None)  # nowhere to persist - must not raise


def test_has_shown_crash_reporting_notice_defaults_to_false_when_preferences_path_is_none():
    assert has_shown_crash_reporting_notice(None) is False


def test_mark_crash_reporting_notice_shown_does_not_crash_when_preferences_path_is_none():
    mark_crash_reporting_notice_shown(None)  # must not raise
