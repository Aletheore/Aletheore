"""Council in the Aletheore menus: a home entry, a guided menu, and every Council command reachable from `aletheore council`."""

import builtins
import sys

import pytest
from typer.main import get_command

from aletheore import cli, ui
from aletheore.cli import app
from tests.test_ui import DOWN, ENTER, _keys

COUNCIL_AT = [row[0] for row in cli._MAIN_MENU].index("council")


def _open_council_menu():
    return (*((DOWN,) * COUNCIL_AT), ENTER)


def _row(key):
    return [k for k, *_ in cli._COUNCIL_MENU].index(key)


@pytest.fixture
def recorded(monkeypatch):
    ran = []
    monkeypatch.setattr(cli, "_run_cli", lambda args: ran.append(args) or 0)
    return ran


def test_council_is_in_the_home_menu_next_to_hallbook():
    keys = [row[0] for row in cli._MAIN_MENU]
    assert keys.index("council") == keys.index("skills") + 1


def test_every_guided_row_is_a_real_council_command():
    council = get_command(app).commands["council"]
    assert council.commands, "the council extra is needed for these tests"
    for key, label, description in cli._COUNCIL_MENU:
        assert key in council.commands, key
        assert label and description


def test_the_guided_menu_lists_the_steps_in_order_then_agent_all_and_back(monkeypatch):
    seen = []
    real_choose = ui.choose

    def recording(items, console, **kwargs):
        if kwargs.get("header") is not None and any(item.key == "setup" for item in items):
            seen[:] = [item.key for item in items]
        return real_choose(items, console, **kwargs)

    monkeypatch.setattr(ui, "choose", recording)
    cli._run_menu(read_key=_keys(*_open_council_menu(), "q", "q"), pause=lambda: None)
    assert seen == [key for key, *_ in cli._COUNCIL_MENU] + ["agent", "all", "back"]


def test_a_command_that_needs_nothing_runs_when_chosen(monkeypatch, recorded):
    cli._run_menu(read_key=_keys(*_open_council_menu(), *((DOWN,) * _row("agents")), ENTER, "q", "q"), pause=lambda: None)
    assert recorded == [["council", "agents"]]


def test_start_a_room_asks_for_what_it_needs_and_runs_it(monkeypatch, recorded):
    answers = iter(["/work/project", "alice"])
    monkeypatch.setattr(cli, "_ask", lambda question: next(answers, ""))
    cli._run_menu(read_key=_keys(*_open_council_menu(), *((DOWN,) * _row("start")), ENTER, "q", "q"), pause=lambda: None)
    assert recorded == [["council", "start", "--repo=/work/project", "--host=alice"]]


def test_who_you_are_and_the_room_are_remembered_between_steps(monkeypatch, recorded):
    prompts = []
    answers = iter(["alice", "room-1", "", ""])  # rooms asks for --me; invite-link then asks only what it has not heard

    def ask(question):
        prompts.append(question)
        return next(answers, "")

    monkeypatch.setattr(cli, "_ask", ask)
    keys = (*_open_council_menu(), *((DOWN,) * _row("rooms")), ENTER,
            *((DOWN,) * (_row("invite-link") - _row("rooms"))), ENTER, "q", "q")
    cli._run_menu(read_key=_keys(*keys), pause=lambda: None)
    assert recorded == [["council", "rooms", "--me=alice"], ["council", "invite-link", "--room=room-1", "--me=alice"]]
    assert "(enter for alice)" in prompts[-1]


def test_leaving_a_required_value_blank_cancels_without_running(monkeypatch, recorded):
    monkeypatch.setattr(cli, "_ask", lambda question: "")
    cli._run_menu(read_key=_keys(*_open_council_menu(), *((DOWN,) * _row("start")), ENTER, "q", "q"), pause=lambda: None)
    assert recorded == []


def test_the_agent_row_shows_how_to_add_the_control_tools(capsys, recorded):
    agent = len(cli._COUNCIL_MENU)
    cli._run_menu(read_key=_keys(*_open_council_menu(), *((DOWN,) * agent), ENTER, "q", "q"), pause=lambda: None)
    out = capsys.readouterr().out
    assert recorded == [] and "council-control" in out and "council mcp-control" in out and "cannot approve" in out


def test_all_council_commands_lists_every_command_with_the_steps_first(monkeypatch):
    shown = []

    def capture(items, console, **kwargs):
        if any(item.key == "all" for item in items):
            return next(i for i in items if i.key == "all") if not shown and not shown.append("main") else None
        shown.extend(item.key for item in items if item.key)
        return None

    monkeypatch.setattr(ui, "choose", capture)
    cli._council_flow({}, lambda: None)
    commands = list(get_command(app).commands["council"].commands)
    assert shown[1:] == commands
    assert commands[:3] == ["setup", "start", "rooms"]
    assert set(cli._COUNCIL_FIRST) <= set(commands)


def test_the_control_server_is_never_run_from_the_picker(monkeypatch, recorded):
    monkeypatch.setattr(ui, "choose", lambda items, console, **kw: next((i for i in items if i.key == "mcp-control"), None))
    cli._command_group_flow(["council"], get_command(app).commands["council"], {})
    assert recorded == [["council", "mcp-control", "--help"]]


def test_council_subcommands_work_through_aletheore(monkeypatch):
    from typer.testing import CliRunner

    result = CliRunner().invoke(app, ["council", "start", "--help"])
    assert result.exit_code == 0 and "Usage: aletheore council start" in result.output


def test_the_menu_says_how_to_install_the_extra_when_it_is_missing(monkeypatch, capsys):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("aletheore.council"):
            raise ModuleNotFoundError("No module named 'nacl'", name="nacl")
        return real_import(name, *args, **kwargs)

    for mod in [m for m in sys.modules if m.startswith("aletheore.council")]:
        monkeypatch.delitem(sys.modules, mod)
    monkeypatch.setattr(builtins, "__import__", fake_import)
    paused = []
    cli._council_flow({}, lambda: paused.append(True))
    assert "aletheore[council]" in "".join(capsys.readouterr().out.split()) and paused == [True]
