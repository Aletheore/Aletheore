"""The `aletheore shield` command: setup, status, undo, off and check, through typer's CliRunner."""

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from aletheore import shield_cli
from aletheore.cli import app
from aletheore.shield import checkpoint as cp
from aletheore.shield import setup
from aletheore.shield.setup import SetupChange

runner = CliRunner()
EVENTS = ("SessionEnd", "PreToolUse", "PostToolUse", "Stop")


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch, tmp_path):
    empty = tmp_path / "empty-gitconfig"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("ALETHEORE_SHIELD_OFFLINE", "1")
    monkeypatch.setenv("ALETHEORE_NO_UPDATE_CHECK", "1")
    monkeypatch.setenv("ALETHEORE_CRASH_REPORTING", "0")
    monkeypatch.setattr(setup, "_interpreter", lambda: "/opt/py/bin/python")
    monkeypatch.setattr(shield_cli, "_interactive", lambda: True)
    from aletheore import cli

    scans = []

    def fake_scan(repo, *args, **kwargs):
        scans.append((repo, args, kwargs))
        out = Path(repo) / ".aletheore"
        out.mkdir(exist_ok=True)
        (out / "air.json").write_text("{}", encoding="utf-8")
        return 0, {}, out / "air.json"

    monkeypatch.setattr(cli, "_scan", fake_scan)
    return scans


@pytest.fixture
def scans(_hermetic):
    return _hermetic


def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _init(path):
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "--template=")
    _git(path, "config", "user.name", "T")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "commit.gpgsign", "false")
    (path / "base.txt").write_text("base\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "init")
    return path


def _snapshot(root: Path) -> dict:
    found = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            p = Path(dirpath) / name
            found[p] = None if p.is_dir() else p.read_bytes()
    return found


@pytest.fixture
def repo(tmp_path):
    return _init(tmp_path / "repo")


def _setup_on(repo):
    r = runner.invoke(app, ["shield", "--path", str(repo), "--yes"])
    assert r.exit_code == 0, r.output
    return r


def test_first_run_lists_changes_and_no_changes_on_no(repo):
    before = _snapshot(repo)
    r = runner.invoke(app, ["shield", "--path", str(repo)], input="no\n")
    assert r.exit_code == 0, r.output
    assert "settings.local.json" in r.output
    assert "Nothing changed." in r.output
    assert _snapshot(repo) == before


def test_first_run_states_the_network_disclosure(repo, monkeypatch):
    monkeypatch.delenv("ALETHEORE_SHIELD_OFFLINE")
    r = runner.invoke(app, ["shield", "--path", str(repo)], input="no\n")
    assert "package NAME" in r.output
    assert "pypi.org" in r.output and "registry.npmjs.org" in r.output
    assert "ALETHEORE_SHIELD_OFFLINE=1" in r.output
    assert "registry_lookups" in r.output


def test_first_run_yes_installs_and_runs_first_scan_when_missing(repo, scans):
    r = runner.invoke(app, ["shield", "--path", str(repo)], input="yes\n")
    assert r.exit_code == 0, r.output
    settings = json.loads((repo / ".claude" / "settings.local.json").read_text(encoding="utf-8"))
    assert set(settings["hooks"]) >= set(EVENTS)
    assert (repo / ".git" / "hooks").is_dir() and any((repo / ".git" / "hooks").iterdir())
    assert "aletheore shield" in (repo / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert len(scans) == 1
    vulns, history, licenses = scans[0][1][:3]
    assert (vulns, history, licenses) == (False, False, False)
    assert "refs/aletheore/checkpoints/" in r.output
    assert "aletheore shield undo" in r.output and "aletheore shield off" in r.output


def test_yes_flag_skips_prompt_and_existing_evidence_is_not_rescanned(repo, scans):
    (repo / ".aletheore").mkdir()
    (repo / ".aletheore" / "air.json").write_text("{}", encoding="utf-8")
    r = runner.invoke(app, ["shield", "--path", str(repo), "--yes"])
    assert r.exit_code == 0, r.output
    assert scans == []
    assert shield_cli.shield_status(repo).enabled


def test_non_tty_without_yes_exits_1_and_writes_nothing(repo, monkeypatch):
    monkeypatch.setattr(shield_cli, "_interactive", lambda: False)
    before = _snapshot(repo)
    r = runner.invoke(app, ["shield", "--path", str(repo)])
    assert r.exit_code == 1
    assert "Run in a terminal or pass --yes" in r.output
    assert _snapshot(repo) == before


def test_second_run_shows_status(repo):
    _setup_on(repo)
    cp.take_checkpoint(repo, "s1", force=True)
    r = runner.invoke(app, ["shield", "--path", str(repo)])
    assert r.exit_code == 0, r.output
    assert "Shield is on" in r.output
    assert "checkpoints: 1" in r.output
    assert "shield check" in r.output and "shield undo" in r.output and "shield off" in r.output


def test_value_error_from_apply_says_nothing_written(repo, monkeypatch):
    def boom(*a, **k):
        raise ValueError("setup plan changed since it was shown")

    monkeypatch.setattr(shield_cli, "apply_setup", boom)
    before = _snapshot(repo)
    r = runner.invoke(app, ["shield", "--path", str(repo), "--yes"])
    assert r.exit_code == 1
    assert "nothing was written" in r.output.lower()
    assert "aletheore shield" in r.output
    assert "Traceback" not in r.output
    after = _snapshot(repo)
    after.pop(repo / ".aletheore", None)
    after.pop(repo / ".aletheore" / "air.json", None)  # the first scan is allowed; setup files are not
    assert after == before


def test_other_apply_error_gives_reason(repo, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk is full")

    monkeypatch.setattr(shield_cli, "apply_setup", boom)
    r = runner.invoke(app, ["shield", "--path", str(repo), "--yes"])
    assert r.exit_code == 1
    assert "disk is full" in r.output


def test_non_git_directory_says_so_and_stops(tmp_path):
    folder = tmp_path / "plain"
    folder.mkdir()
    r = runner.invoke(app, ["shield", "--path", str(folder), "--yes"])
    assert r.exit_code == 1
    assert "needs a git repository" in r.output
    assert "Traceback" not in r.output
    assert list(folder.iterdir()) == []


def _two_checkpoints(repo):
    (repo / "a.txt").write_text("one\n", encoding="utf-8")
    first = cp.take_checkpoint(repo, "s1", force=True)
    (repo / "a.txt").write_text("two\n", encoding="utf-8")
    second = cp.take_checkpoint(repo, "s1", force=True, now=datetime.now(timezone.utc))
    assert first and second and first.ref != second.ref
    return first, second


def test_undo_lists_checkpoints_confirms_then_restores_and_names_safety_ref(repo):
    first, second = _two_checkpoints(repo)
    (repo / "a.txt").write_text("overwritten\n", encoding="utf-8")
    (repo / "new.txt").write_text("keep me\n", encoding="utf-8")
    r = runner.invoke(app, ["shield", "undo", "--path", str(repo)], input="2\nyes\n")
    assert r.exit_code == 0, r.output
    assert "Checkpoints (newest first)" in r.output
    assert "left in place" in r.output
    assert (repo / "a.txt").read_text(encoding="utf-8") == "one\n"
    assert (repo / "new.txt").exists()
    assert "Undo this undo with: aletheore shield undo " in r.output


def test_undo_no_answers_changes_nothing(repo):
    _two_checkpoints(repo)
    (repo / "a.txt").write_text("overwritten\n", encoding="utf-8")
    r = runner.invoke(app, ["shield", "undo", "--path", str(repo)], input="1\nno\n")
    assert r.exit_code == 0
    assert "Nothing changed." in r.output
    assert (repo / "a.txt").read_text(encoding="utf-8") == "overwritten\n"


def test_undo_yes_with_ref_suffix_is_scriptable(repo, monkeypatch):
    first, second = _two_checkpoints(repo)
    (repo / "a.txt").write_text("overwritten\n", encoding="utf-8")
    monkeypatch.setattr(shield_cli, "_interactive", lambda: False)
    r = runner.invoke(app, ["shield", "undo", first.ref, "--yes", "--path", str(repo)])
    assert r.exit_code == 0, r.output
    assert (repo / "a.txt").read_text(encoding="utf-8") == "one\n"
    short = first.ref.rsplit("/", 1)[-1]
    (repo / "a.txt").write_text("again\n", encoding="utf-8")
    r = runner.invoke(app, ["shield", "undo", short, "--yes", "--path", str(repo)])
    assert r.exit_code == 0, r.output
    assert (repo / "a.txt").read_text(encoding="utf-8") == "one\n"


def test_undo_unknown_ref_and_non_tty_without_yes(repo, monkeypatch):
    _two_checkpoints(repo)
    r = runner.invoke(app, ["shield", "undo", "nope-not-here", "--yes", "--path", str(repo)])
    assert r.exit_code == 1 and "No checkpoint matches" in r.output
    monkeypatch.setattr(shield_cli, "_interactive", lambda: False)
    (repo / "a.txt").write_text("overwritten\n", encoding="utf-8")
    r = runner.invoke(app, ["shield", "undo", "--path", str(repo)])
    assert r.exit_code == 1
    assert (repo / "a.txt").read_text(encoding="utf-8") == "overwritten\n"


def test_undo_with_no_checkpoints(repo):
    r = runner.invoke(app, ["shield", "undo", "--path", str(repo)])
    assert r.exit_code == 0
    assert "No checkpoints" in r.output


def test_undo_checkpoint_error_is_a_message(repo, monkeypatch):
    _two_checkpoints(repo)
    (repo / "a.txt").write_text("x\n", encoding="utf-8")

    def boom(*a, **k):
        raise cp.CheckpointError("nothing was written; could not save a safety checkpoint")

    monkeypatch.setattr(shield_cli, "restore_checkpoint", boom)
    r = runner.invoke(app, ["shield", "undo", "--yes", "--path", str(repo)])
    assert r.exit_code == 1
    assert "nothing was written" in r.output
    assert "Traceback" not in r.output


def test_off_removes_everything_and_names_it(repo):
    _setup_on(repo)
    r = runner.invoke(app, ["shield", "off", "--path", str(repo)], input="yes\n")
    assert r.exit_code == 0, r.output
    assert "Removed:" in r.output
    assert "settings.local.json" in r.output or "config.json" in r.output
    assert not shield_cli.shield_status(repo).enabled
    assert not (repo / ".aletheore" / "shield" / "config.json").exists()


def test_off_no_changes_nothing(repo):
    _setup_on(repo)
    before = _snapshot(repo)
    r = runner.invoke(app, ["shield", "off", "--path", str(repo)], input="no\n")
    assert "Nothing changed." in r.output
    assert _snapshot(repo) == before


def test_off_keeps_checkpoint_refs_and_purge_deletes_them(repo):
    _setup_on(repo)
    cp.take_checkpoint(repo, "s1", force=True)
    r = runner.invoke(app, ["shield", "off", "--yes", "--path", str(repo)])
    assert r.exit_code == 0, r.output
    assert "kept unless --purge" in r.output
    assert len(cp.list_checkpoints(repo, 10)) == 1
    _setup_on(repo)
    r = runner.invoke(app, ["shield", "off", "--purge", "--yes", "--path", str(repo)])
    assert r.exit_code == 0, r.output
    assert cp.list_checkpoints(repo, 10) == []


def test_check_clean_repo_prints_all_clear(repo, monkeypatch):
    monkeypatch.setattr(shield_cli, "render_report", lambda report, max_lines=12: "")
    r = runner.invoke(app, ["shield", "check", "--path", str(repo)])
    assert r.exit_code == 0, r.output
    assert "No findings; every check ran." in r.output


def test_check_prints_the_report_when_there_is_one(repo):
    r = runner.invoke(app, ["shield", "check", "--path", str(repo)])
    assert r.exit_code == 0
    assert "not checked" in r.output
    assert "No findings; every check ran." not in r.output


def test_check_no_lookups_reports_not_checked(repo):
    (repo / "requirements.txt").write_text("brandnewpkg==1.0\n", encoding="utf-8")
    r = runner.invoke(app, ["shield", "check", "--no-lookups", "--path", str(repo)])
    assert r.exit_code == 0, r.output
    assert "not checked" in r.output
    assert "No findings; every check ran." not in r.output


def test_check_never_traces_back(repo, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(shield_cli, "run_guard", boom)
    r = runner.invoke(app, ["shield", "check", "--path", str(repo)])
    assert r.exit_code == 0
    assert "could not run" in r.output and "Traceback" not in r.output


def test_check_in_non_git_directory(tmp_path):
    r = runner.invoke(app, ["shield", "check", "--path", str(tmp_path)])
    assert r.exit_code == 1 and "needs a git repository" in r.output


def test_hostile_path_and_description_never_appear_raw(repo, monkeypatch):
    hostile = Path(repo / ".claude" / "evil‮\x1b[31mred\x1b]0;title\x07.json")
    changes = [
        SetupChange(hostile, "edit", "bad \x1b[2J desc ‮ [bold]x[/bold]\nnext", "", "+\x1b[31mline‮\n-ok\n"),
        SetupChange(hostile, "snippet", "paste\x1b[0m", "add\x1b[1m this‮", ""),
    ]
    monkeypatch.setattr(shield_cli, "plan_setup", lambda r: changes)
    r = runner.invoke(app, ["shield", "--path", str(repo)], input="no\n")
    assert r.exit_code == 0, r.output
    assert "\x1b" not in r.output and "‮" not in r.output and "\x07" not in r.output
    assert "Shield will not write this file; add it yourself" in r.output
    assert "[bold]x[/bold]" in r.output  # printed literally, not interpreted


def test_blocked_setup_prints_reason_escaped(repo, monkeypatch):
    monkeypatch.setattr(shield_cli, "plan_setup", lambda r: shield_cli.SetupBlocked("bad\x1b[31m‮ reason"))
    r = runner.invoke(app, ["shield", "--path", str(repo)])
    assert r.exit_code == 1
    assert "bad" in r.output and "\x1b" not in r.output and "‮" not in r.output


def test_help_lists_shield():
    r = runner.invoke(app, ["--help"])
    assert "shield" in r.output
    from aletheore import cli

    assert "shield" in dict(cli._COMMAND_SUMMARIES)
    assert "shield" in dict(cli._COMMAND_GROUPS)["Agents and skills"]


# ----------------------------------------------------------------------------- review fixes


def test_consent_screen_announces_the_first_scan_and_that_it_stays(repo):
    r = runner.invoke(app, ["shield", "--path", str(repo)], input="no\n")
    assert "first local scan (no network)" in r.output
    assert "stays after `shield off`" in r.output and "delete .aletheore/ yourself" in r.output


def test_no_scan_sentence_when_evidence_exists(repo):
    (repo / ".aletheore").mkdir()
    (repo / ".aletheore" / "air.json").write_text("{}", encoding="utf-8")
    r = runner.invoke(app, ["shield", "--path", str(repo)], input="no\n")
    assert "first local scan" not in r.output


def test_first_scan_is_quiet_and_never_edits_gitignore(repo, scans):
    r = runner.invoke(app, ["shield", "--path", str(repo), "--yes"])
    assert r.exit_code == 0, r.output
    assert scans[0][2]["edit_gitignore"] is False
    assert "Scanning done." in r.output


def test_real_scan_leaves_git_status_clean(repo, monkeypatch):
    from aletheore import cli
    monkeypatch.undo()  # drop every patch, including the fake scan
    for key, value in (("GIT_CONFIG_NOSYSTEM", "1"), ("ALETHEORE_SHIELD_OFFLINE", "1"),
                       ("ALETHEORE_NO_UPDATE_CHECK", "1"), ("ALETHEORE_CRASH_REPORTING", "0")):
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(shield_cli, "_interactive", lambda: True)
    r = runner.invoke(app, ["shield", "--path", str(repo), "--yes"])
    assert r.exit_code == 0, r.output
    assert (repo / ".aletheore" / "air.json").exists()
    assert not (repo / ".gitignore").exists()
    status = _git(repo, "status", "--porcelain").stdout.decode()
    assert status.strip() == "", status


@pytest.mark.parametrize("exc", [RuntimeError("boom"), KeyboardInterrupt()])
def test_first_scan_failure_is_a_warning_not_a_traceback(repo, monkeypatch, exc):
    from aletheore import cli

    def boom(*a, **k):
        raise exc

    monkeypatch.setattr(cli, "_scan", boom)
    r = runner.invoke(app, ["shield", "--path", str(repo), "--yes"])
    assert r.exit_code == 0, r.output
    assert "first scan did not finish" in r.output
    assert "Shield is on." in r.output
    assert shield_cli.shield_status(repo).enabled


def test_group_path_and_yes_reach_subcommands(tmp_path, monkeypatch):
    a, b = _init(tmp_path / "a"), _init(tmp_path / "b")
    _setup_on(b)
    monkeypatch.chdir(a)
    r = runner.invoke(app, ["shield", "--path", str(b), "off", "--yes"])
    assert r.exit_code == 0, r.output
    assert not shield_cli.shield_status(b).enabled
    # `check` and `undo` honour the group --path too
    cp.take_checkpoint(b, "s1", force=True)
    r = runner.invoke(app, ["shield", "--path", str(b), "undo", "--yes"])
    assert "Checkpoint " in r.output or "Nothing to restore" in r.output
    (b / "requirements.txt").write_text("brandnewpkg==1.0\n", encoding="utf-8")
    r = runner.invoke(app, ["shield", "--path", str(b), "check", "--no-lookups"])
    assert "brandnewpkg" in r.output or "not checked" in r.output


def test_off_output_is_relative_and_never_calls_a_restored_file_removed(repo):
    settings = repo / ".claude" / "settings.local.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"theme": "dark"}), encoding="utf-8")
    _setup_on(repo)
    r = runner.invoke(app, ["shield", "off", "--yes", "--path", str(repo)])
    assert r.exit_code == 0, r.output
    restored, _, removed = r.output.partition("Removed:")
    assert "Restored to how they were:" in r.output
    assert ".claude/settings.local.json" in restored
    assert ".claude/settings.local.json" not in removed
    assert str(repo) not in r.output.replace("Shield is", "")
    assert "aletheore-shield/setup" not in r.output
    assert "private records" in r.output
    assert "delete .aletheore/ yourself" in r.output


def test_plan_hides_private_records_and_uses_relative_paths(repo):
    r = runner.invoke(app, ["shield", "--path", str(repo)], input="no\n")
    assert str(repo) not in r.output.split("Network:")[0].split("\n", 1)[1]
    assert "setup-record" not in r.output and "private records" in r.output


@pytest.mark.parametrize("answer", ["y\n", "Y\n", "ye\n"])
def test_a_y_answer_does_not_apply(repo, answer):
    r = runner.invoke(app, ["shield", "--path", str(repo)], input=answer)
    assert "Only 'yes' sets it up" in r.output
    assert not shield_cli.shield_status(repo).enabled


@pytest.mark.parametrize("answer", ["YES\n", " Yes \n"])
def test_yes_is_case_insensitive_and_trimmed(repo, answer):
    r = runner.invoke(app, ["shield", "--path", str(repo)], input=answer)
    assert r.exit_code == 0, r.output
    assert shield_cli.shield_status(repo).enabled


def test_no_lookups_flag_reaches_the_guard(repo, monkeypatch):
    monkeypatch.delenv("ALETHEORE_SHIELD_OFFLINE")
    seen = []
    real = shield_cli.run_guard

    def spy(repo_, **kw):
        seen.append(kw["lookups"])
        return real(repo_, **kw)

    monkeypatch.setattr(shield_cli, "run_guard", spy)
    runner.invoke(app, ["shield", "check", "--no-lookups", "--path", str(repo)])
    runner.invoke(app, ["shield", "check", "--path", str(repo)])
    assert seen == [False, None]


def test_status_notes_are_shown_with_what_to_do(repo, monkeypatch):
    _setup_on(repo)
    status = shield_cli.shield_status(repo)
    monkeypatch.setattr(
        shield_cli, "shield_status",
        lambda r: type(status)(True, 0, None, ("git hook not installed", "weird\x1b[31m note")),
    )
    r = runner.invoke(app, ["shield", "--path", str(repo)])
    assert "git hook not installed" in r.output and "add the block shown during setup" in r.output
    assert "\x1b" not in r.output


def test_status_prints_receipt_relative(repo, monkeypatch):
    _setup_on(repo)
    receipt = repo / ".aletheore" / "shield" / "receipts" / "r.md"
    status = shield_cli.shield_status(repo)
    monkeypatch.setattr(shield_cli, "shield_status", lambda r: type(status)(True, 1, receipt, ()))
    r = runner.invoke(app, ["shield", "--path", str(repo)])
    assert "receipt: .aletheore/shield/receipts/r.md" in r.output


def test_undo_shows_blocked_and_left_in_place_lists(repo, monkeypatch):
    first, _ = _two_checkpoints(repo)
    (repo / "a.txt").write_text("x\n", encoding="utf-8")
    plan = cp.RestorePlan(("a.txt",), tuple(f"new{i}.txt" for i in range(12)), ("locked\x1b[31m.txt",))
    monkeypatch.setattr(shield_cli, "plan_restore", lambda r, c: plan)
    r = runner.invoke(app, ["shield", "undo", "--path", str(repo)], input="1\nno\n")
    assert "12 files left in place" in r.output and "new9.txt" in r.output and "... and 2 more" in r.output
    assert "1 file left untouched" in r.output and "locked?[31m.txt" in r.output
    assert "\x1b" not in r.output


def test_off_and_undo_refuse_without_a_terminal_or_yes(repo, monkeypatch):
    _setup_on(repo)
    _two_checkpoints(repo)
    (repo / "a.txt").write_text("keep\n", encoding="utf-8")
    monkeypatch.setattr(shield_cli, "_interactive", lambda: False)
    before = {k: v for k, v in _snapshot(repo).items() if ".git/objects" not in k.as_posix()}
    ref = cp.list_checkpoints(repo, 5)[0].ref
    for args in (["off"], ["undo", ref]):
        r = runner.invoke(app, ["shield", *args, "--path", str(repo)])
        assert r.exit_code == 1, (args, r.output)
        assert "Nothing was changed" in r.output
    assert {k: v for k, v in _snapshot(repo).items() if ".git/objects" not in k.as_posix()} == before


def test_undo_picks_by_unique_suffix_and_rejects_ambiguous(repo, monkeypatch):
    first, second = _two_checkpoints(repo)
    (repo / "a.txt").write_text("over\n", encoding="utf-8")
    suffix = first.ref.rsplit("-", 1)[-1]
    r = runner.invoke(app, ["shield", "undo", suffix, "--yes", "--path", str(repo)])
    assert r.exit_code == 0, r.output
    assert (repo / "a.txt").read_text(encoding="utf-8") == "one\n"
    import dataclasses

    twins = [dataclasses.replace(first, ref="refs/aletheore/checkpoints/x/1-same"),
             dataclasses.replace(second, ref="refs/aletheore/checkpoints/y/2-same")]
    monkeypatch.setattr(shield_cli, "list_checkpoints", lambda r, n: twins)
    r = runner.invoke(app, ["shield", "undo", "same", "--yes", "--path", str(repo)])
    assert r.exit_code == 1 and "more than one" in r.output


def test_off_says_when_the_shared_hook_was_kept(repo, monkeypatch):
    _setup_on(repo)
    monkeypatch.setattr(shield_cli, "shared_blocks_kept", lambda r: True)
    r = runner.invoke(app, ["shield", "off", "--yes", "--path", str(repo)])
    assert "Another worktree still uses Shield" in r.output


def test_plan_changed_and_other_value_errors_differ(repo, monkeypatch):
    def changed(*a, **k):
        raise ValueError(shield_cli.PLAN_CHANGED)

    def other(*a, **k):
        raise ValueError("settings.local.json is not valid")

    monkeypatch.setattr(shield_cli, "apply_setup", changed)
    r = runner.invoke(app, ["shield", "--path", str(repo), "--yes"])
    assert "nothing was written" in r.output.lower()
    monkeypatch.setattr(shield_cli, "apply_setup", other)
    r = runner.invoke(app, ["shield", "--path", str(repo), "--yes"])
    assert "not valid" in r.output and "nothing was written" not in r.output.lower()


def test_success_message_names_receipts_config_and_check(repo):
    r = runner.invoke(app, ["shield", "--path", str(repo), "--yes"])
    assert ".aletheore/shield/receipts/" in r.output and ".aletheore/shield/config.json" in r.output
    assert "aletheore shield check" in r.output
