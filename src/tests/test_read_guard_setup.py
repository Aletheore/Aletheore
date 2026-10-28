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
