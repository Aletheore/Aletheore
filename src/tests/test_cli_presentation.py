"""Audit the complete command tree without executing network or mutation commands."""

import json

import click
import pytest
from typer.core import TyperArgument
from click.testing import CliRunner
from typer.main import get_command

from aletheore import cli, ui
from aletheore.cli_presentation import AletheoreGroup, SILENT_ROUTES


def leaf_paths(command, prefix=()):
    children = getattr(command, "commands", {})
    if not children:
        yield prefix
    for name, child in children.items():
        yield from leaf_paths(child, (*prefix, name))


COMMAND_PATHS = list(leaf_paths(get_command(cli.app)))


@pytest.mark.parametrize("path", COMMAND_PATHS, ids=lambda path: " ".join(path))
def test_every_command_help_uses_one_shared_heading(monkeypatch, path):
    monkeypatch.setattr(ui, "is_interactive", lambda: True)
    result = CliRunner().invoke(get_command(cli.app), [*path, "--help"])
    assert result.exit_code == 0, result.output
    assert result.output.count(ui.TAGLINE) == 1
    assert "Usage:" in result.output


def test_root_help_uses_the_shared_heading(monkeypatch):
    monkeypatch.setattr(ui, "is_interactive", lambda: True)
    result = CliRunner().invoke(get_command(cli.app), ["--help"])
    assert result.exit_code == 0
    assert result.output.count(ui.TAGLINE) == 1


def test_every_registered_root_command_is_reachable_in_all_commands():
    root = get_command(cli.app)
    listed = [name for _, names in cli._COMMAND_GROUPS for name in names]
    assert sorted(listed) == sorted(root.commands)


@pytest.mark.parametrize("args", [["diff"], ["tokens", "profile"]])
def test_interactive_heading_keeps_machine_stdout_intact(monkeypatch, args):
    monkeypatch.setattr(ui, "is_interactive", lambda: True)
    leaf = click.Command(args[-1], callback=lambda: click.echo('{"ok":true}'))
    root = AletheoreGroup(name="aletheore")
    if len(args) == 2:
        parent = click.Group(args[0])
        parent.add_command(leaf)
        root.add_command(parent)
    else:
        root.add_command(leaf)
    result = CliRunner().invoke(root, args)
    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"ok": True}
    assert ui.TAGLINE in result.stderr


@pytest.mark.parametrize("route", [("mcp",), ("hook", "pre-read"), ("hallbook", "suggest")])
def test_protocol_entry_points_stay_silent_even_in_a_terminal(monkeypatch, route):
    monkeypatch.setattr(ui, "is_interactive", lambda: True)
    root = AletheoreGroup(name="aletheore")
    parent = root
    for name in route[:-1]:
        child = click.Group(name)
        parent.add_command(child)
        parent = child
    parent.add_command(click.Command(route[-1], callback=lambda: click.echo('{"ok":true}')))
    result = CliRunner().invoke(root, route)
    assert result.stdout == '{"ok":true}\n'
    assert result.stderr == ""


def test_noninteractive_commands_keep_existing_output(monkeypatch):
    monkeypatch.setattr(ui, "is_interactive", lambda: False)
    root = AletheoreGroup(name="aletheore")
    root.add_command(click.Command("example", callback=lambda: click.echo("result")))
    result = CliRunner().invoke(root, ["example"])
    assert result.output == "result\n"


@pytest.mark.parametrize("plain", [False, True])
def test_meter_has_one_heading_or_one_plain_line(monkeypatch, tmp_path, plain):
    monkeypatch.setattr(ui, "is_interactive", lambda: True)
    args = ["tokens", "meter", "--path", str(tmp_path)]
    if plain:
        args.append("--plain")
    result = CliRunner().invoke(get_command(cli.app), args)
    assert result.exit_code == 0, result.output
    if plain:
        assert len(result.output.splitlines()) == 1
        assert ui.TAGLINE not in result.output
    else:
        assert result.output.count(ui.TAGLINE) == 1
        assert result.output.count("Token savings meter") == 1


@pytest.mark.parametrize("path", [p for p in COMMAND_PATHS if len(p) > 1 and p != ("tokens", "meter")], ids=lambda path: " ".join(path))
def test_nested_menu_commands_accept_required_arguments_and_protocols_show_help(monkeypatch, path):
    command = cli._click_command(list(path))
    parent = cli._click_command(list(path[:-1]))
    ran = []
    monkeypatch.setattr(ui, "choose", lambda items, console, **kwargs: next(item for item in items if item.key == path[-1]))
    monkeypatch.setattr(cli, "_run_cli", lambda args: ran.append(args) or 0)
    monkeypatch.setattr(cli, "_ask", lambda question: "value with spaces")
    assert cli._command_group_flow(list(path[:-1]), parent, {}) is True
    route = tuple("hallbook" if name == "skill-hub" else name for name in path)
    if route in SILENT_ROUTES:
        assert ran == [[*path, "--help"]]
    else:
        required = [p for p in command.params if p.required or (isinstance(p, TyperArgument) and p.default is None)]
        assert ran == [[*path, *("value with spaces" for _ in required)]]


@pytest.mark.parametrize("route", [["tokens"], ["hallbook"], ["skill-hub"], ["hook"], ["config"]])
def test_nested_group_help_has_exactly_one_heading(monkeypatch, route):
    monkeypatch.setattr(ui, "is_interactive", lambda: True)
    result = CliRunner().invoke(get_command(cli.app), [*route, "--help"])
    assert result.exit_code == 0, result.output
    assert result.output.count(ui.TAGLINE) == 1


def test_tokens_options_must_follow_the_leaf_command(monkeypatch):
    monkeypatch.setattr(ui, "is_interactive", lambda: True)
    result = CliRunner().invoke(get_command(cli.app), ["tokens", "--path", ".", "meter"])
    assert result.exit_code == 2
    assert "No such option" in result.output


@pytest.mark.parametrize("args", [["locate"], ["tokens", "bogus"], ["nonsense"]])
def test_usage_errors_are_not_preceded_by_the_banner(monkeypatch, args):
    monkeypatch.setattr(ui, "is_interactive", lambda: True)
    result = CliRunner().invoke(get_command(cli.app), args)
    assert result.exit_code == 2
    assert ui.TAGLINE not in result.output
