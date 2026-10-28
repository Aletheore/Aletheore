import json
from pathlib import Path

import pytest

from aletheore.read_guard_setup import (
    ReadGuardSetupError,
    install,
    plan_install,
    remove,
)


def test_setup_preview_shows_both_hooks_without_writing(tmp_path: Path):
    plan = plan_install(tmp_path)
    assert not plan.settings_path.exists()
    assert not plan.sidecar_path.exists()
    assert set(plan.entries) == {"PreToolUse", "PostToolUse"}
    for event, entry in plan.entries.items():
        assert entry["matcher"] == "Read"
        hook = entry["hooks"][0]
        command = [hook["command"], *hook["args"]]
        assert "aletheore.read_guard" in command
        assert "--setup-id" in command
        assert (
            ("pre" in command)
            if event == "PreToolUse"
            else ("post" in command)
        )


def test_setup_merges_and_removes_only_its_own_hook_entries(tmp_path: Path):
    config = tmp_path / ".claude" / "settings.local.json"
    config.parent.mkdir()
    original = {
        "permissions": {"allow": ["Read"]},
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Edit",
                    "hooks": [{"type": "command", "command": "user-edit-hook"}],
                }
            ],
            "PostToolUse": [
                {
                    "matcher": "Write",
                    "hooks": [{"type": "command", "command": "user-write-hook"}],
                }
            ],
        },
    }
    config.write_text(json.dumps(original))

    installed = install(tmp_path)
    assert installed.settings_path == config
    saved = json.loads(config.read_text())
    assert saved["permissions"] == original["permissions"]
    assert len(saved["hooks"]["PreToolUse"]) == 2
    assert len(saved["hooks"]["PostToolUse"]) == 2
    assert plan_install(tmp_path).already_installed

    assert set(remove(tmp_path)) == {"PreToolUse", "PostToolUse"}
    assert json.loads(config.read_text()) == original
    assert not installed.sidecar_path.exists()


def test_setup_remove_deletes_only_a_file_it_created(tmp_path: Path):
    installed = install(tmp_path)
    assert installed.settings_path.exists()
    remove(tmp_path)
    assert not installed.settings_path.exists()

    config = tmp_path / ".claude" / "settings.local.json"
    config.write_text("{}")
    installed = install(tmp_path)
    remove(tmp_path)
    assert config.exists()
    assert json.loads(config.read_text()) == {}


def test_setup_refuses_malformed_settings_without_changing_them(tmp_path: Path):
    config = tmp_path / ".claude" / "settings.local.json"
    config.parent.mkdir()
    config.write_text("{bad json")
    with pytest.raises(ReadGuardSetupError, match="nothing was changed"):
        install(tmp_path)
    assert config.read_text() == "{bad json"
    assert not (config.parent / "aletheore-read-guard-hooks.json").exists()


def test_setup_refuses_unmergeable_hook_shape(tmp_path: Path):
    config = tmp_path / ".claude" / "settings.local.json"
    config.parent.mkdir()
    config.write_text(json.dumps({"hooks": {"PreToolUse": {"bad": "shape"}}}))
    with pytest.raises(ReadGuardSetupError, match="nothing was changed"):
        install(tmp_path)
    assert json.loads(config.read_text()) == {"hooks": {"PreToolUse": {"bad": "shape"}}}


def test_remove_without_setup_is_a_noop(tmp_path: Path):
    assert remove(tmp_path) == []
    assert not (tmp_path / ".claude").exists()


def test_partial_hook_install_is_repaired_using_the_recorded_exact_entry(
    tmp_path: Path,
):
    from aletheore.read_guard_setup import is_installed

    original_plan = install(tmp_path)
    config = json.loads(original_plan.settings_path.read_text())
    config["hooks"].pop("PostToolUse")
    original_plan.settings_path.write_text(json.dumps(config))
    assert not is_installed(tmp_path)

    repair = plan_install(tmp_path)
    assert not repair.already_installed
    assert set(repair.entries) == {"PostToolUse"}
    install(tmp_path, repair)
    assert is_installed(tmp_path)
    assert set(remove(tmp_path)) == {"PreToolUse", "PostToolUse"}


def test_install_refuses_settings_changed_after_preview(tmp_path: Path):
    from aletheore.read_guard_setup import install

    config = tmp_path / ".claude" / "settings.local.json"
    config.parent.mkdir()
    config.write_text(json.dumps({"model": "sonnet"}))
    plan = plan_install(tmp_path)
    config.write_text(json.dumps({"model": "opus"}))
    with pytest.raises(ReadGuardSetupError, match="changed since preview"):
        install(tmp_path, plan)
    assert json.loads(config.read_text()) == {"model": "opus"}
    assert not plan.sidecar_path.exists()


def test_setup_refuses_symlinked_claude_settings_directory(tmp_path: Path):
    import os

    if os.name == "nt":
        return
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / ".claude").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ReadGuardSetupError, match="may not be symlinks"):
        plan_install(tmp_path)
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize('during_write', [False, True])
def test_remove_preserves_concurrent_sidecar_changes_and_reports_accurately(tmp_path, monkeypatch, during_write):
    from aletheore import read_guard_setup as setup
    config = tmp_path / '.claude' / 'settings.local.json'
    config.parent.mkdir()
    config.write_text('{"custom": true}')
    plan = install(tmp_path)
    before = config.read_bytes()
    fingerprint = setup._fingerprint
    calls = 0

    def inspect(path):
        nonlocal calls
        if path == plan.sidecar_path:
            calls += 1
            if calls == (3 if during_write else 2):
                path.write_text(path.read_text() + '\n')
        return fingerprint(path)

    monkeypatch.setattr(setup, '_fingerprint', inspect)
    with pytest.raises(ReadGuardSetupError) as failure:
        remove(tmp_path)
    assert plan.sidecar_path.exists()
    if during_write:
        assert 'hooks were removed' in str(failure.value)
        assert json.loads(config.read_text()) == {'custom': True}
    else:
        assert 'nothing was changed' in str(failure.value)
        assert config.read_bytes() == before


def test_install_rolls_back_new_sidecar_on_settings_oserror(tmp_path, monkeypatch):
    from aletheore import read_guard_setup as setup
    writer = setup._write_json
    def fail_settings(path, *args, **kwargs):
        if path.name == 'settings.local.json':
            raise PermissionError('denied')
        return writer(path, *args, **kwargs)
    monkeypatch.setattr(setup, '_write_json', fail_settings)
    with pytest.raises(PermissionError):
        install(tmp_path)
    assert not (tmp_path / '.claude' / setup.SIDECAR_NAME).exists()


def test_failed_install_does_not_delete_concurrently_changed_sidecar(tmp_path, monkeypatch):
    from aletheore import read_guard_setup as setup
    writer = setup._write_json
    sidecar = tmp_path / '.claude' / setup.SIDECAR_NAME
    def fail_settings(path, *args, **kwargs):
        if path.name == 'settings.local.json':
            sidecar.write_text('{"other": true}')
            raise PermissionError('denied')
        return writer(path, *args, **kwargs)
    monkeypatch.setattr(setup, '_write_json', fail_settings)
    with pytest.raises(PermissionError):
        install(tmp_path)
    assert json.loads(sidecar.read_text()) == {'other': True}


def test_partial_sidecar_write_never_reaches_destination(tmp_path, monkeypatch):
    from aletheore import read_guard_setup as setup
    def fail_mid_write(data, stream, **kwargs):
        stream.write('{"partial":')
        raise OSError('disk full')
    monkeypatch.setattr(setup.json, 'dump', fail_mid_write)
    with pytest.raises(OSError, match='disk full'):
        install(tmp_path)
    assert not (tmp_path / '.claude' / setup.SIDECAR_NAME).exists()
    assert not list((tmp_path / '.claude').glob('*.tmp'))
