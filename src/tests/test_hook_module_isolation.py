"""Hooks run with the repository as their current folder; a repository's own `aletheore/` package must never run in them."""

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import aletheore
from aletheore import evidence_hook, hook_config, read_guard_setup
from aletheore.hook_config import python_hook

SRC = str(Path(aletheore.__file__).resolve().parent.parent)


def _hostile_repo(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "hostile"
    package = repo / "aletheore"
    package.mkdir(parents=True)
    marker = tmp_path / "marker"
    # Written outside the repo so nothing the hook itself does to the repo can fake or hide it.
    (package / "__init__.py").write_text(f"open({str(marker)!r}, 'w').write('ran')\n", encoding="utf-8")
    (repo / ".aletheore").mkdir()
    (repo / "tree_sitter.py").write_text(f"open({str(marker)!r}, 'w').write('ran')\n", encoding="utf-8")
    return repo, marker


def _run(argv: list[str], repo: Path, payload: dict | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": SRC}
    env.pop("PYTHONSAFEPATH", None)
    return subprocess.run(
        argv, cwd=repo, env=env, input=json.dumps(payload or {}), capture_output=True, text=True, timeout=60, check=False
    )


def _hook_argv(hook: dict) -> list[str]:
    return [hook["command"], *hook["args"]]


def test_python_hook_isolates_the_current_folder():
    args = python_hook("aletheore.read_guard", "pre")["args"]
    assert args == ["-X", "utf8", "-P", "-m", "aletheore.read_guard", "pre"]


def test_the_hostile_repo_really_shadows_without_the_flag(tmp_path):
    # Guards the tests below: if the plain shape stopped being shadowed they would pass for the wrong reason.
    repo, marker = _hostile_repo(tmp_path)
    _run([sys.executable, "-X", "utf8", "-m", "aletheore.hallbook_hook", "--mode", "hint"], repo)
    assert marker.exists()


@pytest.mark.parametrize(
    "hook",
    [
        python_hook("aletheore.read_guard", "pre", "--setup-id", "x"),
        python_hook("aletheore.read_guard", "post", "--setup-id", "x"),
        python_hook("aletheore.evidence_hook", "--mode", "inject"),
        python_hook("aletheore.hallbook_hook", "--mode", "hint"),
    ],
    ids=["read-guard-pre", "read-guard-post", "evidence", "hallbook"],
)
def test_hook_never_imports_the_repositorys_own_aletheore(tmp_path, hook):
    repo, marker = _hostile_repo(tmp_path)
    target = repo / "a.py"
    target.write_text("x = 1\n", encoding="utf-8")
    payload = {
        "session_id": "s", "cwd": str(repo), "tool_name": "Read", "tool_input": {"file_path": str(target)},
        "hook_event_name": "UserPromptSubmit", "prompt": "fix the failing test",
    }
    result = _run(_hook_argv(hook), repo, payload)
    assert not marker.exists(), result.stderr
    assert "Traceback" not in result.stderr


def test_background_index_build_never_imports_the_repositorys_own_aletheore(tmp_path, monkeypatch):
    repo, marker = _hostile_repo(tmp_path)
    spawned: list[list[str]] = []
    monkeypatch.setattr(evidence_hook, "subprocess", SimpleNamespace(Popen=lambda argv, **kw: spawned.append(argv), DEVNULL=-3))
    evidence_hook._spawn_background_build(repo)
    assert len(spawned) == 1 and "-P" in spawned[0]
    result = _run(spawned[0], repo)
    assert not marker.exists(), result.stderr


@pytest.mark.parametrize("hook_fn", [hook_config.with_isolation, hook_config.without_isolation])
def test_isolation_helpers_leave_other_hooks_alone(hook_fn):
    for other in ({"type": "command", "command": "user-hook"}, {"command": "x", "args": ["--flag"]}, "text", None):
        assert hook_fn(other) == other


def _old_shape(entry: dict) -> dict:
    return {**entry, "hooks": [hook_config.without_isolation(h) for h in entry["hooks"]]}


def _install_old_shape(project: Path) -> dict:
    """Settings and ownership record exactly as a version without `-P` wrote them."""
    plan = read_guard_setup.plan_install(project)
    old = {event: _old_shape(entry) for event, entry in plan.entries.items()}
    settings = {"hooks": {event: [entry] for event, entry in old.items()}}
    sidecar = {"schema": 1, "created_settings_file": True, "entries": old}
    plan.settings_path.parent.mkdir(parents=True)
    plan.settings_path.write_text(json.dumps(settings), encoding="utf-8")
    plan.sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")
    return old


def test_installing_over_the_old_shape_migrates_it_in_place(tmp_path):
    old = _install_old_shape(tmp_path)
    assert read_guard_setup.is_installed(tmp_path)  # the old shape still counts as installed
    plan = read_guard_setup.plan_install(tmp_path)
    assert not plan.already_installed
    read_guard_setup.install(tmp_path, plan)
    settings = json.loads(plan.settings_path.read_text(encoding="utf-8"))
    sidecar = json.loads(plan.sidecar_path.read_text(encoding="utf-8"))
    for event, entry in old.items():
        assert len(settings["hooks"][event]) == 1
        hook = settings["hooks"][event][0]["hooks"][0]
        assert hook["args"][:4] == ["-X", "utf8", "-P", "-m"]
        assert hook["args"][4:] == entry["hooks"][0]["args"][3:]
        assert sidecar["entries"][event] == settings["hooks"][event][0]
    assert read_guard_setup.plan_install(tmp_path).already_installed
    assert set(read_guard_setup.remove(tmp_path)) == {"PreToolUse", "PostToolUse"}
    assert not plan.settings_path.exists()


def test_remove_takes_out_the_old_shape(tmp_path):
    _install_old_shape(tmp_path)
    assert set(read_guard_setup.remove(tmp_path)) == {"PreToolUse", "PostToolUse"}
    assert not (tmp_path / ".claude" / "settings.local.json").exists()
    assert not (tmp_path / ".claude" / read_guard_setup.SIDECAR_NAME).exists()


def test_remove_takes_out_a_migrated_entry_even_when_the_record_still_has_the_old_shape(tmp_path):
    old = _install_old_shape(tmp_path)
    settings_path = tmp_path / ".claude" / "settings.local.json"
    settings_path.write_text(
        json.dumps({"hooks": {e: [read_guard_setup._modern(v)] for e, v in old.items()}}), encoding="utf-8"
    )
    assert set(read_guard_setup.remove(tmp_path)) == {"PreToolUse", "PostToolUse"}


def test_a_fresh_install_writes_the_isolated_shape(tmp_path):
    plan = read_guard_setup.plan_install(tmp_path)
    for entry in plan.entries.values():
        assert entry["hooks"][0]["args"][:4] == ["-X", "utf8", "-P", "-m"]


def test_current_settings_with_an_old_ownership_record_only_refresh_the_record(tmp_path):
    old = _install_old_shape(tmp_path)
    settings_path = tmp_path / ".claude" / "settings.local.json"
    settings_path.write_text(
        json.dumps({"hooks": {e: [read_guard_setup._modern(v)] for e, v in old.items()}}), encoding="utf-8"
    )
    before = settings_path.read_bytes()
    plan = read_guard_setup.plan_install(tmp_path)
    assert plan.entries == {} and not plan.already_installed
    read_guard_setup.install(tmp_path, plan)
    assert settings_path.read_bytes() == before
    sidecar = json.loads(plan.sidecar_path.read_text(encoding="utf-8"))
    assert all(e["hooks"][0]["args"][2] == "-P" for e in sidecar["entries"].values())
    assert read_guard_setup.plan_install(tmp_path).already_installed
