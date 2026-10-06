from aletheore.preferences import (
    has_shown_crash_reporting_notice,
    is_crash_reporting_enabled,
    mark_crash_reporting_notice_shown,
    set_crash_reporting_enabled,
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
