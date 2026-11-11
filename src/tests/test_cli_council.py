"""`aletheore council` hands the rest of the command line to Council's own CLI, and says how to install it when the extra is missing."""

import builtins
import sys

from typer.testing import CliRunner

from aletheore.cli import app

runner = CliRunner()


def test_council_command_shows_up_in_the_main_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0 and "council" in result.output


def test_council_command_without_the_extra_says_how_to_install_it(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("aletheore.council"):
            raise ModuleNotFoundError("No module named 'nacl'", name="nacl")
        return real_import(name, *args, **kwargs)

    for mod in [m for m in sys.modules if m.startswith("aletheore.council")]:
        monkeypatch.delitem(sys.modules, mod)
    monkeypatch.setattr(builtins, "__import__", fake_import)
    result = runner.invoke(app, ["council", "setup"])
    assert result.exit_code == 1
    assert "aletheore[council]" in "".join(result.output.split())  # the brackets survive rich markup (the terminal may wrap the line)


def test_a_broken_aletheore_council_module_is_not_hidden_behind_the_install_hint(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("aletheore.council"):
            raise ModuleNotFoundError("No module named 'aletheore.council.missing'", name="aletheore.council.missing")
        return real_import(name, *args, **kwargs)

    for mod in [m for m in sys.modules if m.startswith("aletheore.council")]:
        monkeypatch.delitem(sys.modules, mod)
    monkeypatch.setattr(builtins, "__import__", fake_import)
    result = runner.invoke(app, ["council", "setup"])
    assert result.exit_code != 0 and isinstance(result.exception, ModuleNotFoundError)


def test_council_command_passes_its_arguments_on():
    result = runner.invoke(app, ["council", "--help"])
    assert result.exit_code == 0
    assert "Usage: aletheore council" in result.output
