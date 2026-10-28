import io
import os
import sys
from pathlib import Path

import pytest
from rich.cells import cell_len
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


# --- truncate and width-aware menus --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,width,unicode_ok,expected",
    [
        ("short", 20, True, "short"),
        ("  lots   of\n whitespace\t here ", 40, True, "lots of whitespace here"),
        ("abcdefghij", 10, True, "abcdefghij"),
        ("abcdefghijk", 10, True, "abcdefghi…"),
        ("abcdefghijk", 10, False, "abcdefg..."),
        ("a sentence. And more of it", 12, True, "a sentence…"),
        ("a sentence. And more of it", 14, False, "a sentence..."),
        ("anything", 0, True, ""),
        ("anything", 1, True, "…"),
        ("anything", 2, False, ""),
    ],
)
def test_truncate_fits_one_line_and_marks_the_cut(text, width, unicode_ok, expected):
    out = ui.truncate(text, width, unicode_ok=unicode_ok)
    assert out == expected
    assert "\n" not in out and cell_len(out) <= width


def test_truncate_never_leaves_a_dangling_stop_or_space_before_the_mark():
    text = "Reads files. Then writes files. And so on, for a long while"
    for width in range(6, 40):
        unicode_out = ui.truncate(text, width, unicode_ok=True)
        assert ".." not in unicode_out and ".…" not in unicode_out and " …" not in unicode_out
        ascii_out = ui.truncate(text, width, unicode_ok=False)
        assert "...." not in ascii_out and " ..." not in ascii_out


def test_truncate_counts_wide_characters_by_their_cell_width():
    out = ui.truncate("日本語のテキストです", 9, unicode_ok=True)
    assert cell_len(out) <= 9 and out.endswith("…")


WIDE_ITEMS = [
    ui.MenuItem("a", "A short label", "a perfectly ordinary hint"),
    ui.MenuItem("b", "testing/a-very-long-skill-identifier-that-goes-on  A Very Long Skill Title Indeed",
                "Use when you want to " + "do something quite involved " * 12),
    ui.MenuItem("c", "Back"),
]


def _render(items, selected, width, height=None, unicode_ok=True):
    console = Console(file=io.StringIO(), width=500, force_terminal=False, color_system=None)
    console.print(ui.render_menu(items, selected, unicode_ok=unicode_ok, width=width, height=height))
    return console.file.getvalue().splitlines()


@pytest.mark.parametrize("width", [40, 60, 80, 120, 200])
@pytest.mark.parametrize("selected", [0, 1, 2])
def test_render_menu_fits_every_row_to_the_width(width, selected):
    lines = _render(WIDE_ITEMS, selected, width)
    assert lines and all(cell_len(line) <= width for line in lines)
    assert any("A short label" in line for line in lines)


def test_render_menu_at_60_columns_cuts_the_long_label_and_hint_with_an_ellipsis():
    lines = _render(WIDE_ITEMS, 0, 60)
    long_row = next(line for line in lines if "testing/" in line)
    assert "…" in long_row and "A Very Long Skill Title Indeed" not in long_row
    assert len([line for line in lines if line.strip().startswith(("A short", "testing/", "Back", "❯"))]) == 3  # no wraps


def test_render_menu_at_200_columns_keeps_whole_labels_and_a_long_hint():
    lines = _render(WIDE_ITEMS, 0, 200)
    long_row = next(line for line in lines if "testing/" in line)
    assert "A Very Long Skill Title Indeed" in long_row and "Use when you want to" in long_row
    assert cell_len(long_row) <= 200


def test_render_menu_ascii_marks_the_cut_with_three_dots_and_no_double_dots():
    lines = _render(WIDE_ITEMS, 1, 60, unicode_ok=False)
    assert all(cell_len(line) <= 60 for line in lines)
    assert any(line.rstrip().endswith("...") for line in lines)
    assert not any(".." in line.replace("...", "") for line in lines)


def test_render_menu_without_a_width_is_unchanged():
    lines = _render(WIDE_ITEMS, 0, None)
    assert any("do something quite involved" in line and len(line) > 200 for line in lines)


def test_a_long_list_scrolls_inside_the_screen_height_and_keeps_the_selection_visible():
    items = [ui.MenuItem(str(i), f"skill-{i:03d}", "hint") for i in range(100)]
    for selected in (0, 40, 99):
        lines = _render(items, selected, 80, height=20)
        assert len(lines) <= 20
        assert any(f"skill-{selected:03d}" in line and line.lstrip().startswith("❯") for line in lines)
    middle = _render(items, 50, 80, height=20)
    assert any("more" in line and "↑" in line for line in middle) and any("more" in line and "↓" in line for line in middle)


def test_a_short_list_is_not_windowed():
    lines = _render(ITEMS, 0, 80, height=20)
    assert not any("more" in line for line in lines)


def test_choose_reaches_a_row_far_down_a_long_list_and_can_turn_off_digit_picks():
    items = [ui.MenuItem(str(i), f"row {i}") for i in range(30)]
    console = Console(file=io.StringIO(), width=80, height=12, force_terminal=False, color_system=None)
    picked = ui.choose(items, console, read_key=_keys(*([DOWN] * 25), ENTER), numbers=False)
    assert picked.key == "25"
    assert ui.choose(items, console, read_key=_keys("5", ENTER), numbers=False).key == "0"
    assert ui.choose(items, console, read_key=_keys("5")).key == "4"


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
    output = " ".join(result.output.split())  # the hint wraps at the terminal width, which differs by OS
    assert "your Desktop folder" in output
    assert "--yes" in output
    assert "aletheore scan ~/code/my-project" in output


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


def test_token_meter_has_a_direct_home_menu_item(monkeypatch):
    ran = []
    monkeypatch.setattr(cli, "_run_cli", lambda args: ran.append(args) or 0)
    position = [row[0] for row in cli._MAIN_MENU].index("meter")
    cli._run_menu(read_key=_keys(*([DOWN] * position), ENTER, "q", "q"), pause=lambda: None)
    assert ran == [["tokens", "meter", "--path", str(Path.cwd())]]


def test_meter_menu_opens_setup_only_after_user_selects_enable(monkeypatch, tmp_path):
    ran = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "_run_cli", lambda args: ran.append(args) or 0)
    cli._meter_flow({"read_key": _keys(DOWN, ENTER, "q")}, pause=lambda: None)
    assert ran == [
        ["tokens", "meter", "--path", str(tmp_path)],
        ["tokens", "meter", "--path", str(tmp_path), "--enable-read-guard"],
        ["tokens", "meter", "--path", str(tmp_path)],
    ]


def test_the_menu_remembers_where_you_were(monkeypatch):
    ran = []
    monkeypatch.setattr(cli, "_run_cli", lambda args: ran.append(args) or 0)
    monkeypatch.setattr(cli, "_query_flow", lambda kwargs: ran.append(["query"]) or True)
    # pick "Explore the evidence" (second), come back: the highlight is still on it, enter picks it again
    cli._run_menu(read_key=_keys(DOWN, ENTER, ENTER, "q"), pause=lambda: None)
    assert ran == [["query"], ["query"]]


def test_guided_demo_is_available_from_home_and_all_commands(monkeypatch):
    ran = []
    monkeypatch.setattr(cli, "_run_cli", lambda args: ran.append(args) or 0)
    position = [row[0] for row in cli._MAIN_MENU].index("demo")
    cli._run_menu(read_key=_keys(*([DOWN] * position), ENTER, "q"),
                  pause=lambda: pytest.fail("The tour returns directly to the home menu"))
    assert ran == [["demo"]]
    assert "demo" in dict(cli._COMMAND_GROUPS)["Scan and explore"]
    assert cli._run_command("demo", {}) is True
    assert ran == [["demo"], ["demo"]]


def test_connect_submenu_passes_the_chosen_agent_and_all_means_no_target(monkeypatch):
    ran = []
    monkeypatch.setattr(cli, "_run_cli", lambda args: ran.append(args) or 0)
    connect = [i[0] for i in cli._MAIN_MENU].index("connect")
    agents = len(cli._MCP_CLIENT_CONFIGS) + 2  # plus codex-cli and claude-desktop
    # Enter on the first row must pick ONE agent (claude-code), never fan out to all of them. "All of them" sits
    # below the agents. The menu remembers its place, so the second visit starts on "Connect" already.
    cli._run_menu(
        read_key=_keys(*((DOWN,) * connect), ENTER, ENTER, ENTER, *((DOWN,) * agents), ENTER, "q"), pause=lambda: None
    )
    assert ran[0] == ["mcp-install", ".", "--target", "claude-code"]
    assert ran[1] == ["mcp-install", "."]


def test_account_submenu_runs_status_login_or_logout(monkeypatch):
    ran = []
    monkeypatch.setattr(cli, "_run_cli", lambda args: ran.append(args) or 0)
    account = [i[0] for i in cli._MAIN_MENU].index("account")
    cli._run_menu(read_key=_keys(*((DOWN,) * account), ENTER, ENTER, "q"), pause=lambda: None)
    assert ran == [["status"]]


# --- the "All commands" picker ------------------------------------------------------------------------------------


def _commands_menu_position():
    return [i[0] for i in cli._MAIN_MENU].index("commands")


def _pick_command(group, row):
    """Keys that open "All commands", go to a group and a row in it, and press enter on that row."""
    return (*((DOWN,) * _commands_menu_position()), ENTER, *((DOWN,) * group), ENTER, *((DOWN,) * row), ENTER)


def _group_row(name):
    for group, (_, names) in enumerate(cli._COMMAND_GROUPS):
        if name in names:
            return group, names.index(name)
    raise AssertionError(name)


def test_every_banner_command_is_in_the_picker_exactly_once_with_the_banner_description():
    grouped = [name for _, names in cli._COMMAND_GROUPS for name in names]
    assert sorted(grouped) == sorted([n for n, _ in cli._COMMAND_SUMMARIES] + [n for n, _ in cli._PICKER_ONLY_SUMMARIES])
    assert len(grouped) == len(set(grouped))
    for name, description in cli._COMMAND_SUMMARIES:
        assert cli._COMMAND_DESCRIPTIONS[name] == description
    assert [title for title, _ in cli._COMMAND_GROUPS] == [
        "Scan and explore", "Agents and skills", "Dashboard and watch", "Account"]


def test_every_picker_command_has_an_action():
    for _, names in cli._COMMAND_GROUPS:
        for name in names:
            group = cli._click_command([name])
            assert (name in cli._COMMAND_RUNS or name in cli._COMMAND_PROMPTS or name in cli._COMMAND_HELP
                    or name == "mcp-install" or bool(getattr(group, "commands", None))), name


@pytest.mark.parametrize(
    "name,expected",
    [
        ("scan", ["scan", "."]),
        ("query", ["query"]),
        ("audit", ["audit", "."]),
        ("dashboard", ["dashboard", "."]),
        ("init", ["init", "."]),
        ("index", ["index", "."]),
        ("watch", ["watch", "."]),
        ("login", ["login"]),
        ("logout", ["logout"]),
        ("status", ["status"]),
    ],
)
def test_enter_on_a_command_that_needs_nothing_runs_it_directly(monkeypatch, name, expected):
    ran = []
    monkeypatch.setattr(cli, "_run_cli", lambda args: ran.append(args) or 0)
    monkeypatch.setattr(cli, "_query_flow", lambda kwargs: ran.append(["query"]) or True)
    cli._run_menu(read_key=_keys(*_pick_command(*_group_row(name)), "q"), pause=lambda: None)
    assert ran == [expected]


@pytest.mark.parametrize(
    "name,answers,expected",
    [
        ("diff", ["old.json", "new.json"], ["diff", "old.json", "new.json"]),
        ("verify", ["report.md"], ["verify", "report.md"]),
        ("healthcheck", ["http://localhost:8000"], ["healthcheck", ".", "--base-url", "http://localhost:8000"]),
        ("locate", ["parse_config"], ["locate", "parse_config"]),
        ("usages", ["parse_config"], ["usages", "parse_config"]),
        ("dependents", ["src/example.py"], ["dependents", "src/example.py"]),
        ("tests-for", ["src/example.py"], ["tests-for", "src/example.py"]),
    ],
)
def test_commands_with_one_required_argument_ask_for_it_and_pass_it_on(monkeypatch, name, answers, expected):
    ran, asked = [], []
    typed = iter(answers)
    monkeypatch.setattr(cli, "_run_cli", lambda args: ran.append(args) or 0)
    monkeypatch.setattr(cli, "_ask", lambda question: asked.append(question) or next(typed))
    cli._run_menu(read_key=_keys(*_pick_command(*_group_row(name)), "q"), pause=lambda: None)
    assert ran == [expected] and len(asked) == len(answers)


def test_an_empty_answer_cancels_the_command_instead_of_running_it(monkeypatch):
    ran = []
    monkeypatch.setattr(cli, "_run_cli", lambda args: ran.append(args) or 0)
    monkeypatch.setattr(cli, "_ask", lambda question: "")
    # the empty answer drops back to the group's list, where q goes back to the groups and q again to the menu
    cli._run_menu(read_key=_keys(*_pick_command(*_group_row("diff")), "q", "q", "q"), pause=lambda: None)
    assert ran == []


@pytest.mark.parametrize("name", ["mcp"])
def test_the_remaining_commands_show_their_own_help(monkeypatch, name):
    ran = []
    monkeypatch.setattr(cli, "_run_cli", lambda args: ran.append(args) or 0)
    cli._run_menu(read_key=_keys(*_pick_command(*_group_row(name)), "q"), pause=lambda: None)
    assert ran == [[name, "--help"]]


def test_tokens_meter_is_reachable_from_all_commands_and_hallbook_browse_stays_available(monkeypatch):
    ran, skills = [], []
    monkeypatch.setattr(cli, "_run_cli", lambda args: ran.append(args) or 0)
    monkeypatch.setattr(cli, "_skills_flow", lambda kwargs: skills.append(True))
    group, row = _group_row("mcp-install")
    # the first agent in the Connect list is chosen: one agent, never "all of them"
    cli._run_menu(read_key=_keys(*_pick_command(group, row), ENTER, "q"), pause=lambda: None)
    assert ran == [["mcp-install", ".", "--target", "claude-code"]]
    group, row = _group_row("tokens")
    cli._run_menu(read_key=_keys(*_pick_command(group, row), *((DOWN,) * 2), ENTER, "q", "q", "q", "q", "q"), pause=lambda: None)
    assert ran[-1] == ["tokens", "meter", "--path", str(Path.cwd())]
    skills_position = [i[0] for i in cli._MAIN_MENU].index("skills")
    cli._run_menu(read_key=_keys(*((DOWN,) * skills_position), ENTER, "q"), pause=lambda: None)
    assert skills == [True]


def test_all_nested_click_commands_are_listed_in_the_picker(monkeypatch):
    from typer.main import get_command

    root = get_command(app)
    for name, command in root.commands.items():
        if not getattr(command, "commands", None):
            continue
        shown = []
        def capture_rows(items, console, **kwargs):
            shown.extend(item.key for item in items if item.key)
            return None
        monkeypatch.setattr(ui, "choose", capture_rows)
        cli._run_command(name, {})
        assert shown == list(command.commands)


def test_back_leaves_the_picker_and_the_menu_comes_back_without_running_anything(monkeypatch):
    ran = []
    monkeypatch.setattr(cli, "_run_cli", lambda args: ran.append(args) or 0)
    back = len(cli._COMMAND_GROUPS)  # the Back row sits under the groups
    cli._run_menu(read_key=_keys(*((DOWN,) * _commands_menu_position()), ENTER, *((DOWN,) * back), ENTER, "q"),
                  pause=lambda: None)
    assert ran == []


def test_the_picker_rows_carry_the_banner_descriptions(capsys):
    console = Console(file=io.StringIO(), width=120, height=40, force_terminal=False, color_system=None)
    names = cli._COMMAND_GROUPS[0][1]
    rows = [ui.MenuItem(n, n, cli._COMMAND_DESCRIPTIONS[n]) for n in names]
    console.print(ui.render_menu(rows, 0, unicode_ok=True, width=120))
    out = console.file.getvalue()
    for name in names:
        assert name in out and cli._COMMAND_DESCRIPTIONS[name] in out


# --- clearing the screen --------------------------------------------------------------------------------------------


def test_the_screen_is_cleared_only_in_a_real_interactive_terminal(monkeypatch):
    monkeypatch.setenv("TERM", "xterm-256color")
    live = Console(file=io.StringIO(), width=80, force_terminal=True, color_system=None)
    monkeypatch.setattr(cli, "console", live)
    monkeypatch.setattr(ui, "is_interactive", lambda: True)
    cli._clear_screen()
    assert "\x1b[2J" in live.file.getvalue()

    plain = Console(file=io.StringIO(), width=80, force_terminal=False, color_system=None)
    monkeypatch.setattr(cli, "console", plain)
    cli._clear_screen()
    assert plain.file.getvalue() == ""

    monkeypatch.setattr(cli, "console", live)
    live.file.truncate(0)
    live.file.seek(0)
    monkeypatch.setattr(ui, "is_interactive", lambda: False)
    cli._clear_screen()
    assert live.file.getvalue() == ""


def test_the_menu_clears_before_it_is_redrawn_but_not_the_first_time(monkeypatch):
    events = []
    monkeypatch.setattr(cli, "_run_cli", lambda args: events.append("run") or 0)
    monkeypatch.setattr(cli, "_clear_screen", lambda: events.append("clear"))
    cli._run_menu(read_key=_keys(ENTER, "q"), pause=lambda: events.append("pause"))
    assert events == ["run", "pause", "clear"]


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


def test_a_window_taller_than_a_very_short_menu_never_starts_below_the_first_row():
    from rich.text import Text

    items = [ui.MenuItem("a", "A"), ui.MenuItem("b", "B")]
    group = ui.render_menu(items, 1, unicode_ok=True, header=Text("h"), width=40, height=5)
    shown = [str(r).strip() for r in group.renderables]
    assert shown.count("A") == 1
    assert shown.count("\u276f B") == 1  # the selected row, drawn once
