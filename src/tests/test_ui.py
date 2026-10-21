import io
import os
import sys

import pytest
from rich.console import Console
from typer.testing import CliRunner

from aletheore import cli, ui
from aletheore.cli import app

runner = CliRunner()

DOWN, UP, ENTER = "\x1b[B", "\x1b[A", "\r"


def _keys(*keys):
    it = iter(keys)
    return lambda: next(it)


def _console():
    return Console(file=io.StringIO(), width=100, force_terminal=False, color_system=None)


ITEMS = [ui.MenuItem("a", "First"), ui.MenuItem("b", "Second", "a hint"), ui.MenuItem("c", "Third")]


# --- wordmark and menu --------------------------------------------------------------------------------------------


def test_wordmark_fits_a_normal_terminal_and_degrades_when_narrow():
    wide = ui.wordmark_lines(80)
    assert len(wide) == 6 and max(len(line) for line in wide) <= 78
    assert ui.wordmark_lines(40) == ["ALETHEORE", "=" * 9]


@pytest.mark.parametrize(
    "keys,expected",
    [
        ((ENTER,), "a"),
        ((DOWN, ENTER), "b"),
        ((DOWN, DOWN, ENTER), "c"),
        ((UP, ENTER), "c"),  # wraps to the end
        ((DOWN, DOWN, DOWN, ENTER), "a"),  # wraps to the start
        (("j", "j", "\n"), "c"),
        (("k", "\r"), "c"),
        (("\xe0P", "\r"), "b"),  # Windows arrow keys
        (("\x00H", "\r"), "c"),
        (("2",), "b"),  # number shortcut
    ],
)
def test_choose_follows_the_keys(keys, expected):
    picked = ui.choose(ITEMS, _console(), read_key=_keys(*keys))
    assert picked.key == expected


@pytest.mark.parametrize("quit_key", ["q", "\x1b", "\x03", "\x04"])
def test_choose_returns_none_when_the_person_quits(quit_key):
    assert ui.choose(ITEMS, _console(), read_key=_keys(quit_key)) is None


def test_choose_treats_ctrl_c_and_eof_as_quit():
    def boom():
        raise KeyboardInterrupt

    assert ui.choose(ITEMS, _console(), read_key=boom) is None

    def eof():
        raise EOFError

    assert ui.choose(ITEMS, _console(), read_key=eof) is None


def test_render_menu_marks_the_selected_row_in_plain_text_too():
    console = _console()
    console.print(ui.render_menu(ITEMS, 1, unicode_ok=False))
    out = console.file.getvalue()
    assert "> Second" in out and "  First" in out and "a hint" in out
    assert "❯" not in out and "↑" not in out  # ASCII only when the terminal cannot show Unicode


def test_render_menu_uses_the_arrow_pointer_when_unicode_works():
    console = _console()
    console.print(ui.render_menu(ITEMS, 0, unicode_ok=True))
    assert "❯ First" in console.file.getvalue()


# --- when is the terminal interactive -----------------------------------------------------------------------------


def _fake_tty(monkeypatch, stdin=True, stdout=True):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: stdin, raising=False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: stdout, raising=False)
    for var in ("CI", "ALETHEORE_NO_MENU"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")


def test_interactive_only_with_a_real_terminal_on_both_ends(monkeypatch):
    _fake_tty(monkeypatch)
    assert ui.is_interactive()
    _fake_tty(monkeypatch, stdout=False)
    assert not ui.is_interactive()
    _fake_tty(monkeypatch, stdin=False)
    assert not ui.is_interactive()


@pytest.mark.parametrize("var,value", [("CI", "true"), ("ALETHEORE_NO_MENU", "1"), ("TERM", "dumb")])
def test_interactive_is_off_for_ci_dumb_terminals_and_the_opt_out(monkeypatch, var, value):
    _fake_tty(monkeypatch)
    monkeypatch.setenv(var, value)
    assert not ui.is_interactive()


def test_supports_unicode_follows_the_stream_encoding():
    class S:
        encoding = "utf-8"

    class L:
        encoding = "cp1252"

    assert ui.supports_unicode(S()) and not ui.supports_unicode(L())


# --- the "is this really a project" guard -------------------------------------------------------------------------


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    for name in ("Desktop", "Documents", "Downloads", "code"):
        (home / name).mkdir(parents=True)
    monkeypatch.setattr("aletheore.user_paths.user_home", lambda: home)
    return home


def test_home_and_personal_folders_are_flagged_when_they_hold_no_project(fake_home):
    assert ui.broad_directory_reason(fake_home) == "your home folder"
    assert ui.broad_directory_reason(fake_home / "Desktop") == "your Desktop folder"
    assert ui.broad_directory_reason(fake_home / "Documents") == "your Documents folder"
    assert ui.broad_directory_reason(fake_home / "Downloads") == "your Downloads folder"


def test_the_file_system_root_is_flagged():
    assert ui.broad_directory_reason(os.path.abspath(os.sep)) == "the top of your file system"


def test_ordinary_project_locations_are_never_flagged(fake_home):
    assert ui.broad_directory_reason(fake_home / "code") is None
    assert ui.broad_directory_reason(fake_home / "Documents" / "my-project") is None


@pytest.mark.parametrize("marker", [".git", "pyproject.toml", "package.json", "go.mod", ".aletheore.json", "App.sln"])
def test_a_folder_with_project_files_is_not_flagged_even_if_it_is_a_personal_folder(fake_home, marker):
    desktop = fake_home / "Desktop"
    (desktop / marker).mkdir() if marker == ".git" else (desktop / marker).write_text("x")
    assert ui.broad_directory_reason(desktop) is None


def test_scan_in_the_desktop_without_a_terminal_refuses_with_a_hint(fake_home, monkeypatch):
    monkeypatch.setattr(ui, "is_interactive", lambda: False)
    result = runner.invoke(app, ["scan", str(fake_home / "Desktop")])
    assert result.exit_code == 2
    assert "your Desktop folder" in result.output
    assert "--yes" in result.output
    assert "aletheore scan ~/code/my-project" in result.output


def test_yes_flag_and_env_var_skip_the_question(fake_home, monkeypatch):
    monkeypatch.setattr(ui, "is_interactive", lambda: False)
    monkeypatch.setattr(cli, "_scan", lambda *a, **k: (0, None, None))
    assert runner.invoke(app, ["scan", str(fake_home / "Desktop"), "--yes"]).exit_code == 0
    assert runner.invoke(app, ["scan", str(fake_home / "Desktop"), "-y"]).exit_code == 0
    monkeypatch.setenv("ALETHEORE_ASSUME_YES", "1")
    assert runner.invoke(app, ["scan", str(fake_home / "Desktop")]).exit_code == 0


def test_in_a_terminal_it_asks_and_no_cancels(fake_home, monkeypatch):
    monkeypatch.setattr(ui, "is_interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    result = runner.invoke(app, ["scan", str(fake_home / "Desktop")])
    assert result.exit_code == 1
    assert "Cancelled" in result.output


def test_in_a_terminal_yes_proceeds(fake_home, monkeypatch):
    monkeypatch.setattr(ui, "is_interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    monkeypatch.setattr(cli, "_scan", lambda *a, **k: (0, None, None))
    assert runner.invoke(app, ["scan", str(fake_home / "Desktop")]).exit_code == 0


@pytest.mark.parametrize("command", ["scan", "audit", "watch", "init", "mcp", "mcp-install", "dashboard"])
def test_every_command_that_reads_or_writes_a_folder_has_the_guard(fake_home, monkeypatch, command):
    monkeypatch.setattr(ui, "is_interactive", lambda: False)
    result = runner.invoke(app, [command, str(fake_home / "Desktop")])
    assert result.exit_code == 2, (command, result.output)
    assert "your Desktop folder" in result.output


def test_a_real_project_folder_is_not_questioned(tmp_path, monkeypatch):
    monkeypatch.setattr(ui, "is_interactive", lambda: False)
    monkeypatch.setattr(cli, "_scan", lambda *a, **k: (0, None, None))
    (tmp_path / "pyproject.toml").write_text("x")
    assert runner.invoke(app, ["scan", str(tmp_path)]).exit_code == 0


# --- the home screen ----------------------------------------------------------------------------------------------


def test_a_bare_aletheore_in_a_terminal_opens_the_menu(monkeypatch):
    called = []
    monkeypatch.setattr(ui, "is_interactive", lambda: True)
    monkeypatch.setattr(cli, "_run_menu", lambda: called.append(True))
    monkeypatch.setattr(cli, "_print_update_notice_if_available", lambda: None)
    result = runner.invoke(app, [])
    assert result.exit_code == 0 and called == [True]


def test_a_bare_aletheore_without_a_terminal_prints_the_plain_command_list(monkeypatch):
    monkeypatch.setattr(ui, "is_interactive", lambda: False)
    monkeypatch.setattr(cli, "_print_update_notice_if_available", lambda: None)
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "ALETHEORE" in result.output and "scan" in result.output and "mcp-install" in result.output


def test_menu_choices_run_the_matching_commands(monkeypatch):
    ran = []
    monkeypatch.setattr(cli, "_run_cli", lambda args: ran.append(args) or 0)
    # scan, then audit, then quit
    cli._run_menu(read_key=_keys(ENTER, DOWN, DOWN, ENTER, "q"), pause=lambda: None)
    assert ran == [["scan", "."], ["audit", "."]]


def test_the_menu_remembers_where_you_were(monkeypatch):
    ran = []
    monkeypatch.setattr(cli, "_run_cli", lambda args: ran.append(args) or 0)
    # pick "Explore the evidence" (second), come back: the highlight is still on it, enter picks it again
    cli._run_menu(read_key=_keys(DOWN, ENTER, ENTER, "q"), pause=lambda: None)
    assert ran == [["query"], ["query"]]


def test_connect_submenu_passes_the_chosen_agent_and_all_means_no_target(monkeypatch):
    ran = []
    monkeypatch.setattr(cli, "_run_cli", lambda args: ran.append(args) or 0)
    connect = [i[0] for i in cli._MAIN_MENU].index("connect")
    to_connect = (DOWN,) * connect
    # "All of them" is first in the submenu; then the second entry is claude-code
    # (the menu remembers its place, so the second visit starts on "Connect" already)
    cli._run_menu(read_key=_keys(*to_connect, ENTER, ENTER, ENTER, DOWN, ENTER, "q"), pause=lambda: None)
    assert ran[0] == ["mcp-install", "."]
    assert ran[1][:3] == ["mcp-install", ".", "--target"]


def test_account_submenu_runs_status_login_or_logout(monkeypatch):
    ran = []
    monkeypatch.setattr(cli, "_run_cli", lambda args: ran.append(args) or 0)
    account = [i[0] for i in cli._MAIN_MENU].index("account")
    cli._run_menu(read_key=_keys(*((DOWN,) * account), ENTER, ENTER, "q"), pause=lambda: None)
    assert ran == [["status"]]


# --- black and white ----------------------------------------------------------------------------------------------


def test_output_consoles_carry_no_colour():
    assert cli.console.color_system is None
    assert cli._stderr_console.color_system is None


def test_help_output_has_no_colour_codes():
    result = runner.invoke(app, ["--help"], color=True)
    assert "\x1b[3" not in result.output and "\x1b[9" not in result.output  # no foreground colour sequences


def test_the_menu_header_drops_the_block_letters_on_a_short_window(monkeypatch):
    monkeypatch.setattr(cli, "console", Console(file=io.StringIO(), width=100, height=20, color_system=None))
    assert "██" not in cli._menu_header().plain
    assert "ALETHEORE" in cli._menu_header().plain
    monkeypatch.setattr(cli, "console", Console(file=io.StringIO(), width=100, height=40, color_system=None))
    assert "██" in cli._menu_header().plain
