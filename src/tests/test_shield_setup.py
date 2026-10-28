"""Shield setup and removal: consent-free planning, merge-safe edits of user files, exact reversal."""

import json
import os
import shlex
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from aletheore import hook_command
from aletheore.shield import checkpoint as cp
from aletheore.shield import setup
from aletheore.shield.setup import SetupBlocked, SetupChange

MARK_START = "# >>> aletheore shield >>>"
MARK_END = "# <<< aletheore shield <<<"
FAKE_PY = "/opt/shield py/bin/python"


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch, tmp_path):
    # The developer's own git config (a global core.hooksPath, a template dir with hooks) must not leak in.
    empty = tmp_path / "empty-gitconfig"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("ALETHEORE_SHIELD_OFFLINE", "1")
    monkeypatch.setattr(setup, "_interpreter", lambda: FAKE_PY)


def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _init(path):
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "--template=")  # no sample hooks, and no hooks folder at all
    _git(path, "config", "user.name", "T")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "commit.gpgsign", "false")
    (path / "base.txt").write_text("base\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "init")
    return path


def _snapshot(root: Path) -> dict:
    """Every path under root with its bytes (files), target (links) or a dir marker, plus the mode bits."""
    found = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            path = Path(dirpath) / name
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                found[path] = ("link", os.readlink(path))
            elif stat.S_ISDIR(info.st_mode):
                found[path] = ("dir", stat.S_IMODE(info.st_mode))
            else:
                found[path] = ("file", path.read_bytes(), stat.S_IMODE(info.st_mode))
    return found


def _settings(repo):
    return repo / ".claude" / "settings.local.json"


def _hook(repo):
    return repo / ".git" / "hooks" / "pre-commit"


def _kinds(changes):
    return {(change.path, change.kind) for change in changes}


USER_SETTINGS = {
    "permissions": {"allow": ["Bash(ls:*)"]},
    "hooks": {
        "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo mine"}]}],
        "Stop": [{"hooks": [{"type": "command", "command": "say done"}]}],
    },
    "env": {"NAME": "café"},
}
USER_SETTINGS_BYTES = (json.dumps(USER_SETTINGS, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
USER_HOOK_BYTES = b"#!/bin/sh\nset -e\necho user hook ran\nexit 0\n"


# ------------------------------------------------------------------------------------------- hook command quoting


def _old_snippet_command(argv, platform):
    """The quoting `hook_evidence_snippet` did inline before it was extracted, verbatim."""
    if platform == "win32":
        return " ".join(
            '"' + arg.replace('"', '\\"') + '"' if not arg or any(c in arg for c in ' \t"&^|<>()') else arg
            for arg in argv
        )
    return shlex.join(argv)


ARGVS = [
    ["/usr/bin/python3", "-m", "aletheore.evidence_hook", "--mode", "inject"],
    ["/Users/a/Library/Application Support/venv/bin/python", "-m", "aletheore.shield.hooks", "pre-edit"],
    ["C:\\Program Files\\Python 3\\python.exe", "-m", "aletheore.shield.hooks", "turn-end"],
    ['C:\\odd"name\\python.exe', "", "a&b", "x(y)", "tab\there", "it's"],
]


@pytest.mark.parametrize("platform", ["linux", "darwin", "win32"])
@pytest.mark.parametrize("argv", ARGVS)
def test_build_hook_command_is_byte_identical_to_the_old_snippet_quoting(argv, platform):
    assert hook_command.build_hook_command(argv, platform=platform) == _old_snippet_command(argv, platform)


def test_build_hook_command_quotes_spaces_for_posix_and_windows():
    argv = ["/a b/python", "-m", "aletheore.shield.hooks", "pre-edit"]
    assert hook_command.build_hook_command(argv, platform="linux") == "'/a b/python' -m aletheore.shield.hooks pre-edit"
    win = ["C:\\Program Files\\py\\python.exe", "-m", "aletheore.shield.hooks", "pre-edit"]
    assert (
        hook_command.build_hook_command(win, platform="win32")
        == '"C:\\Program Files\\py\\python.exe" -m aletheore.shield.hooks pre-edit'
    )


def test_build_hook_command_defaults_to_the_running_platform(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert hook_command.build_hook_command(["a b"]) == '"a b"'
    monkeypatch.setattr(sys, "platform", "linux")
    assert hook_command.build_hook_command(["a b"]) == "'a b'"


def test_evidence_snippet_output_is_unchanged(monkeypatch):
    from typer.testing import CliRunner

    from aletheore import cli

    for platform, exe in (("linux", "/x y/python"), ("win32", "C:\\x y\\python.exe")):
        monkeypatch.setattr(sys, "platform", platform)
        monkeypatch.setattr(sys, "executable", exe)
        result = CliRunner().invoke(cli.app, ["hook", "evidence-snippet", "--mode", "pointer"])
        assert result.exit_code == 0
        command = _old_snippet_command([exe, "-m", "aletheore.evidence_hook", "--mode", "pointer"], platform)
        entry = lambda matcher: [{"matcher": matcher, "hooks": [{"type": "command", "command": command}]}]  # noqa: E731
        hooks = {"PreToolUse": entry("Edit|MultiEdit|Write"), "PostToolUse": entry("Read")}
        expected = json.dumps({"hooks": hooks}, indent=2)
        assert result.stdout == expected + "\n"


# ------------------------------------------------------------------------------------------------------ planning


def test_plan_setup_writes_nothing(tmp_path):
    repo = _init(tmp_path / "repo")
    before = _snapshot(tmp_path)
    plan = setup.plan_setup(repo)
    assert not isinstance(plan, SetupBlocked)
    assert _kinds(plan) == {
        (_settings(repo), "create"),
        (_hook(repo), "create"),
        (repo / ".aletheore" / "shield" / "config.json", "create"),
    }
    assert all(change.description for change in plan)
    assert _snapshot(tmp_path) == before


def test_plan_setup_is_blocked_outside_git_and_in_unsupported_states(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    blocked = setup.plan_setup(plain)
    assert isinstance(blocked, SetupBlocked) and "not a git repository" in blocked.reason
    with pytest.raises(ValueError):
        setup.apply_setup(plain)
    assert list(plain.iterdir()) == []

    repo = _init(tmp_path / "repo")
    head = _git(repo, "rev-parse", "HEAD").stdout.decode().strip()
    _git(repo, "checkout", "-q", head)
    blocked = setup.plan_setup(repo)
    assert isinstance(blocked, SetupBlocked) and "detached" in blocked.reason


# --------------------------------------------------------------------------------------------- apply and remove


def test_apply_registers_the_four_agent_hooks_and_the_git_hook(tmp_path):
    repo = _init(tmp_path / "repo")
    setup.apply_setup(repo)
    data = json.loads(_settings(repo).read_text(encoding="utf-8"))
    hooks = data["hooks"]
    assert set(hooks) == {"PreToolUse", "PostToolUse", "Stop", "SessionEnd"}
    expected = {"PreToolUse": "pre-edit", "PostToolUse": "post-edit", "Stop": "turn-end", "SessionEnd": "session-end"}
    for event, name in expected.items():
        (entry,) = hooks[event]
        if event in ("PreToolUse", "PostToolUse"):
            assert entry["matcher"] == "Edit|MultiEdit|Write"
        else:
            assert "matcher" not in entry
        command = hook_command.build_hook_command([FAKE_PY, "-m", "aletheore.shield.hooks", name])
        assert entry["hooks"] == [{"type": "command", "command": command}]
    text = _hook(repo).read_text(encoding="utf-8")
    assert text.startswith("#!/bin/sh\n")
    assert MARK_START in text and MARK_END in text
    if sys.platform != "win32":
        assert os.stat(_hook(repo)).st_mode & 0o111
    assert (repo / ".aletheore" / "shield" / "config.json").is_file()


def test_apply_then_remove_leaves_nothing_in_a_repo_that_had_no_files(tmp_path):
    repo = _init(tmp_path / "repo")
    before = _snapshot(repo)
    setup.apply_setup(repo)
    removed = setup.remove_setup(repo)
    assert _settings(repo) in removed and _hook(repo) in removed
    assert _snapshot(repo) == before  # no settings file, no .claude/, no hook, no hooks/, no .aletheore/


def test_apply_then_remove_restores_original_bytes(tmp_path):
    repo = _init(tmp_path / "repo")
    _settings(repo).parent.mkdir()
    _settings(repo).write_bytes(USER_SETTINGS_BYTES)
    _hook(repo).parent.mkdir(parents=True)
    _hook(repo).write_bytes(USER_HOOK_BYTES)
    os.chmod(_hook(repo), 0o755)
    before = _snapshot(repo)

    plan = setup.plan_setup(repo)
    assert (_settings(repo), "edit") in _kinds(plan) and (_hook(repo), "edit") in _kinds(plan)
    setup.apply_setup(repo)
    merged = json.loads(_settings(repo).read_text(encoding="utf-8"))
    assert merged["permissions"] == USER_SETTINGS["permissions"] and merged["env"] == USER_SETTINGS["env"]
    assert merged["hooks"]["PreToolUse"][0] == USER_SETTINGS["hooks"]["PreToolUse"][0]
    assert merged["hooks"]["Stop"][0] == USER_SETTINGS["hooks"]["Stop"][0]
    assert len(merged["hooks"]["Stop"]) == 2
    hook_text = _hook(repo).read_text(encoding="utf-8")
    assert "echo user hook ran" in hook_text
    # our block runs before the user's script, so an `exit 0` in it cannot skip us
    assert hook_text.index(MARK_START) < hook_text.index("exit 0")

    setup.remove_setup(repo)
    assert _snapshot(repo) == before


@pytest.mark.parametrize(
    "content",
    [
        b'{\n    "hooks": {\n        "Stop": []\n    }\n}',  # empty list the user had, 4-space indent, no final newline
        b'{"hooks": {}, "model": "opus"}\n',  # compact, one line
        b'{\r\n\t"hooks": {\r\n\t\t"SessionEnd": []\r\n\t}\r\n}\r\n',  # tabs and CRLF
        b'\xef\xbb\xbf{\n  "a": "\xc3\xa9"\n}\n',  # BOM and a literal non-ASCII character
    ],
)
def test_remove_restores_bytes_of_unusual_but_valid_settings(tmp_path, content):
    repo = _init(tmp_path / "repo")
    _settings(repo).parent.mkdir()
    _settings(repo).write_bytes(content)
    setup.apply_setup(repo)
    assert len(json.loads(_settings(repo).read_bytes().decode("utf-8-sig"))["hooks"]) == 4
    setup.remove_setup(repo)
    assert _settings(repo).read_bytes() == content


def test_apply_is_idempotent(tmp_path):
    repo = _init(tmp_path / "repo")
    _settings(repo).parent.mkdir()
    _settings(repo).write_bytes(USER_SETTINGS_BYTES)
    setup.apply_setup(repo)
    after_first = _snapshot(tmp_path)
    assert setup.plan_setup(repo) == []
    setup.apply_setup(repo)
    assert _snapshot(tmp_path) == after_first


def test_reapplying_after_remove_works_and_remove_twice_is_harmless(tmp_path):
    repo = _init(tmp_path / "repo")
    before = _snapshot(repo)
    setup.apply_setup(repo)
    setup.remove_setup(repo)
    assert setup.remove_setup(repo) == []
    setup.apply_setup(repo)
    setup.remove_setup(repo)
    assert _snapshot(repo) == before


def test_remove_keeps_containers_the_user_added_entries_to_after_setup(tmp_path):
    repo = _init(tmp_path / "repo")
    setup.apply_setup(repo)
    data = json.loads(_settings(repo).read_text(encoding="utf-8"))
    data["hooks"]["Stop"].append({"hooks": [{"type": "command", "command": "say later"}]})
    data["model"] = "x"
    _settings(repo).write_text(json.dumps(data, indent=2), encoding="utf-8")
    setup.remove_setup(repo)
    left = json.loads(_settings(repo).read_text(encoding="utf-8"))
    assert left == {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "say later"}]}]}, "model": "x"}


def test_invalid_json_settings_file_is_not_overwritten(tmp_path):
    repo = _init(tmp_path / "repo")
    _settings(repo).parent.mkdir()
    broken = b'{"hooks": {,,}\n'
    _settings(repo).write_bytes(broken)
    plan = setup.plan_setup(repo)
    (change,) = [c for c in plan if c.path == _settings(repo)]
    assert change.kind == "snippet"
    assert "aletheore.shield.hooks" in change.detail
    assert json.loads(change.detail)["hooks"]["Stop"]
    setup.apply_setup(repo)
    assert _settings(repo).read_bytes() == broken
    assert MARK_START in _hook(repo).read_text(encoding="utf-8")  # the other changes still happen


@pytest.mark.parametrize("content", [b'["not", "an", "object"]\n', b'{"hooks": []}\n', b'{"hooks": {"Stop": {}}}\n'])
def test_unexpected_settings_shapes_get_a_snippet(tmp_path, content):
    repo = _init(tmp_path / "repo")
    _settings(repo).parent.mkdir()
    _settings(repo).write_bytes(content)
    (change,) = [c for c in setup.plan_setup(repo) if c.path == _settings(repo)]
    assert change.kind == "snippet"
    setup.apply_setup(repo)
    assert _settings(repo).read_bytes() == content


def test_non_sh_pre_commit_hook_is_left_alone_with_a_snippet(tmp_path):
    repo = _init(tmp_path / "repo")
    _hook(repo).parent.mkdir(parents=True)
    original = b"#!/usr/bin/env python3\nprint('mine')\n"
    _hook(repo).write_bytes(original)
    (change,) = [c for c in setup.plan_setup(repo) if c.path == _hook(repo)]
    assert change.kind == "snippet"
    assert MARK_START in change.detail and "aletheore.shield.hooks pre-commit" in change.detail
    setup.apply_setup(repo)
    assert _hook(repo).read_bytes() == original
    setup.remove_setup(repo)
    assert _hook(repo).read_bytes() == original


def test_bash_via_env_shebang_counts_as_sh(tmp_path):
    repo = _init(tmp_path / "repo")
    _hook(repo).parent.mkdir(parents=True)
    _hook(repo).write_bytes(b"#!/usr/bin/env bash\necho hi\n")
    os.chmod(_hook(repo), 0o755)
    (change,) = [c for c in setup.plan_setup(repo) if c.path == _hook(repo)]
    assert change.kind == "edit"


def test_core_hooks_path_is_respected_and_husky_style_setup_prints_snippet_instead(tmp_path):
    repo = _init(tmp_path / "repo")
    husky = repo / ".husky"
    husky.mkdir()
    (husky / "pre-commit").write_bytes(b"npx lint-staged\n")
    _git(repo, "config", "core.hooksPath", ".husky")
    before = _snapshot(repo / ".husky")
    plan = setup.plan_setup(repo)
    (change,) = [c for c in plan if c.kind == "snippet"]
    assert change.path == husky / "pre-commit"
    assert "core.hooksPath" in change.description and MARK_START in change.detail
    assert not any(c.path.name == "pre-commit" and c.kind != "snippet" for c in plan)
    setup.apply_setup(repo)
    assert _snapshot(repo / ".husky") == before
    assert not _hook(repo).exists()


def test_core_hooks_path_outside_the_repo_is_not_edited_either(tmp_path):
    repo = _init(tmp_path / "repo")
    shared = tmp_path / "global-hooks"
    shared.mkdir()
    _git(repo, "config", "core.hooksPath", str(shared))
    (change,) = [c for c in setup.plan_setup(repo) if c.path.name == "pre-commit"]
    assert change.kind == "snippet" and change.path == shared / "pre-commit"
    setup.apply_setup(repo)
    assert list(shared.iterdir()) == []


def test_core_hooks_path_inside_the_git_dir_is_edited(tmp_path):
    repo = _init(tmp_path / "repo")
    _git(repo, "config", "core.hooksPath", ".git/my-hooks")
    (change,) = [c for c in setup.plan_setup(repo) if c.path.name == "pre-commit"]
    assert change.kind == "create" and change.path == repo / ".git" / "my-hooks" / "pre-commit"
    setup.apply_setup(repo)
    assert MARK_START in (repo / ".git" / "my-hooks" / "pre-commit").read_text(encoding="utf-8")


def test_linked_worktree_uses_the_common_hooks_dir(tmp_path):
    repo = _init(tmp_path / "repo")
    linked = tmp_path / "linked"
    _git(repo, "worktree", "add", "-q", "-b", "side", str(linked))
    plan = setup.plan_setup(linked)
    assert (_hook(repo), "create") in _kinds(plan)
    assert (_settings(linked), "create") in _kinds(plan)
    setup.apply_setup(linked)
    assert MARK_START in _hook(repo).read_text(encoding="utf-8")
    assert _settings(linked).is_file() and not _settings(repo).exists()
    setup.remove_setup(linked)
    assert not _hook(repo).exists() and not _settings(linked).exists()


def test_remove_deletes_the_hook_file_only_if_it_held_just_our_block(tmp_path):
    repo = _init(tmp_path / "repo")
    setup.apply_setup(repo)
    # The user adds their own line to the hook Shield created: the file must survive removal, minus our block.
    with _hook(repo).open("a", encoding="utf-8") as handle:
        handle.write("echo added later\n")
    setup.remove_setup(repo)
    assert _hook(repo).read_text(encoding="utf-8") == "#!/bin/sh\necho added later\n"

    # A pre-existing hook that held only a shebang is not ours to delete.
    other = _init(tmp_path / "other")
    _hook(other).parent.mkdir(parents=True)
    _hook(other).write_bytes(b"#!/bin/sh\n")
    os.chmod(_hook(other), 0o755)
    setup.apply_setup(other)
    setup.remove_setup(other)
    assert _hook(other).read_bytes() == b"#!/bin/sh\n"


def test_shebang_without_a_newline_is_restored_exactly(tmp_path):
    repo = _init(tmp_path / "repo")
    _hook(repo).parent.mkdir(parents=True)
    _hook(repo).write_bytes(b"#!/bin/sh")
    os.chmod(_hook(repo), 0o755)
    setup.apply_setup(repo)
    assert MARK_START in _hook(repo).read_text(encoding="utf-8")
    setup.remove_setup(repo)
    assert _hook(repo).read_bytes() == b"#!/bin/sh"


def test_git_hook_block_quotes_the_interpreter_for_sh(tmp_path):
    block = setup.git_hook_block("C:\\Program Files\\Py's\\python.exe", platform="win32")
    assert "'C:/Program Files/Py'\\''s/python.exe' -m aletheore.shield.hooks pre-commit" in block
    assert "\\" not in block.replace("'\\''", "")
    assert block.startswith(MARK_START + "\n") and block.endswith(MARK_END + "\n")
    assert "|| true" in block
    posix = setup.git_hook_block("/a b/python", platform="linux")
    assert "'/a b/python' -m aletheore.shield.hooks pre-commit" in posix
    assert '--repo "$(git rev-parse --show-toplevel)"' in posix


@pytest.mark.skipif(sys.platform == "win32", reason="the stand-in interpreter is a sh script")
def test_git_hook_is_a_marked_block_and_runs_on_commit(tmp_path, monkeypatch):
    repo = _init(tmp_path / "repo")
    folder = tmp_path / "py dir"
    folder.mkdir()
    sentinel = tmp_path / "ran.txt"
    wrapper = folder / "python"
    # Fails on purpose: the hook must still let the commit through.
    wrapper.write_text(f'#!/bin/sh\necho "$@" > "{sentinel}"\npwd >> "{sentinel}"\nexit 3\n', encoding="utf-8")
    os.chmod(wrapper, 0o755)
    monkeypatch.setattr(setup, "_interpreter", lambda: str(wrapper))
    setup.apply_setup(repo)
    (repo / "sub").mkdir()
    (repo / "sub" / "f.txt").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "-A")
    subprocess.run(["git", "commit", "-q", "-m", "second"], cwd=repo / "sub", check=True, capture_output=True)
    args, cwd = sentinel.read_text(encoding="utf-8").splitlines()
    assert args.split()[:3] == ["-m", "aletheore.shield.hooks", "pre-commit"]
    assert os.path.realpath(args.split("--repo ", 1)[1]) == os.path.realpath(repo)
    assert _git(repo, "log", "-1", "--format=%s").stdout.decode().strip() == "second"


# --------------------------------------------------------------------------------------- checkpoints and purge


def _checkpoint(repo, session="s1"):
    (repo / "base.txt").write_text(f"changed {session}\n", encoding="utf-8")
    assert cp.take_checkpoint(repo, session, force=True) is not None


def _refs(repo):
    out = _git(repo, "for-each-ref", "--format=%(refname)", cp.REF_PREFIX).stdout.decode()
    return out.split()


def test_shield_off_without_purge_keeps_checkpoint_refs(tmp_path):
    repo = _init(tmp_path / "repo")
    setup.apply_setup(repo)
    _checkpoint(repo)
    receipts = repo / ".aletheore" / "shield" / "receipts"
    receipts.mkdir(parents=True)
    (receipts / "20261028T000000Z-s1.md").write_text("r\n", encoding="utf-8")
    refs = _refs(repo)
    assert refs
    setup.remove_setup(repo)
    assert _refs(repo) == refs
    assert (receipts / "20261028T000000Z-s1.md").is_file()
    assert not (repo / ".aletheore" / "shield" / "config.json").exists()


def test_purge_deletes_refs_but_never_runs_gc(tmp_path, monkeypatch):
    repo = _init(tmp_path / "repo")
    setup.apply_setup(repo)
    _checkpoint(repo, "s1")
    _checkpoint(repo, "s2")
    receipts = repo / ".aletheore" / "shield" / "receipts"
    receipts.mkdir(parents=True)
    (receipts / "20261028T000000Z-s1.md").write_text("r\n", encoding="utf-8")
    state_dir = repo / ".git" / "aletheore-shield"
    state_dir.mkdir(exist_ok=True)
    (state_dir / "hook-s1.json").write_text("{}", encoding="utf-8")
    calls = []
    real = cp.run_git

    def recording(repo_arg, *args, **kwargs):
        calls.append(args)
        return real(repo_arg, *args, **kwargs)

    monkeypatch.setattr(cp, "run_git", recording)
    monkeypatch.setattr(setup, "run_git", recording)
    removed = setup.remove_setup(repo, purge=True)
    assert _refs(repo) == []
    assert not receipts.exists() and not state_dir.exists()
    assert not (repo / ".aletheore").exists()
    assert state_dir in removed and any(path in removed for path in (receipts, receipts.parent))
    for args in calls:
        assert not {"gc", "prune", "repack", "reflog"} & set(args), args


def test_delete_all_checkpoints_counts_and_leaves_other_refs(tmp_path):
    repo = _init(tmp_path / "repo")
    _checkpoint(repo, "s1")
    _checkpoint(repo, "s2")
    _git(repo, "update-ref", "refs/aletheore/other", "HEAD")
    assert cp.delete_all_checkpoints(repo) == 2
    assert _refs(repo) == []
    assert _git(repo, "for-each-ref", "--format=%(refname)", "refs/aletheore/").stdout.decode().split() == [
        "refs/aletheore/other"
    ]


# ------------------------------------------------------------------------------------------------------ status


def test_shield_status_reports_enabled_and_missing_parts(tmp_path):
    repo = _init(tmp_path / "repo")
    status = setup.shield_status(repo)
    assert status.enabled is False and status.checkpoints == 0 and status.latest_receipt is None
    assert "agent hooks not registered" in status.notes and "git hook not installed" in status.notes

    setup.apply_setup(repo)
    _checkpoint(repo)
    receipts = repo / ".aletheore" / "shield" / "receipts"
    receipts.mkdir(parents=True)
    (receipts / "20261028T000000Z-s1.md").write_text("r\n", encoding="utf-8")
    status = setup.shield_status(repo)
    assert status.enabled is True and status.checkpoints == 1
    assert status.latest_receipt == receipts / "20261028T000000Z-s1.md"
    assert not any("not" in note for note in status.notes)

    _hook(repo).unlink()
    status = setup.shield_status(repo)
    assert status.enabled is True and "git hook not installed" in status.notes


def test_shield_status_mentions_skipped_files_and_repo_state(tmp_path, monkeypatch):
    repo = _init(tmp_path / "repo")
    monkeypatch.setattr(cp, "MAX_FILE_BYTES", 10)
    (repo / "big.bin").write_bytes(b"x" * 100)
    assert cp.take_checkpoint(repo, "s1", force=True) is not None
    assert any("1 file skipped" in note for note in setup.shield_status(repo).notes)
    head = _git(repo, "rev-parse", "HEAD").stdout.decode().strip()
    _git(repo, "checkout", "-q", head)
    assert any("detached" in note for note in setup.shield_status(repo).notes)


def test_setup_change_has_an_empty_detail_by_default(tmp_path):
    change = SetupChange(tmp_path, "create", "x")
    assert change.detail == ""
