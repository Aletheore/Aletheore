import io
import os
import shutil
import stat
from types import SimpleNamespace

import pytest
from rich.console import Console
from typer.testing import CliRunner

from aletheore import ui
from aletheore.cli import app
from aletheore.demo import create_demo
from aletheore.demo_walkthrough import DemoWalkthrough

ENTER, DOWN = "\r", "\x1b[B"


@pytest.fixture(scope="module")
def sample():
    repo, evidence = create_demo()
    yield repo, evidence
    def remove_readonly(function, path, exc_info):
        if not isinstance(exc_info[1], PermissionError):
            raise exc_info[1]
        os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
        function(path)

    # Git objects carry the read-only attribute on Windows runners.
    shutil.rmtree(repo, onerror=remove_readonly)


def tour(monkeypatch, sample, keys, *, ask=""):
    import aletheore.demo_walkthrough as module

    monkeypatch.setattr(module, "create_demo", lambda: sample)
    pages, commands = [], []
    choose = ui.choose

    def observe(items, console, **kwargs):
        pages.append(kwargs["header"].plain)
        return choose(items, console, **kwargs)

    monkeypatch.setattr(ui, "choose", observe)
    stream = io.StringIO()
    console = Console(file=stream, width=80, height=24, color_system=None)
    sequence = iter(keys)
    walkthrough = DemoWalkthrough(console, run_command=lambda args: commands.append(args) or 0,
                                  ask=lambda _: ask, read_key=lambda: next(sequence))
    return walkthrough, pages, commands, stream


def test_tour_moves_one_page_at_a_time_without_running_commands(monkeypatch, sample):
    walkthrough, pages, commands, _ = tour(monkeypatch, sample, [ENTER] * 5 + ["q"])
    assert walkthrough.run() == 0
    assert len(pages) == 6
    assert "Scan once. Explore it. Give your agent evidence." in pages[0]
    assert "4 files" in pages[1]
    assert "return sum(items)" in pages[2]
    assert "3 dependent files" in pages[3].lower()
    assert "ACTUAL LOCAL TOOL CALL" in pages[4]
    assert "affected_total: 3" in pages[4]
    assert "affected_test_files: 1" in pages[4]
    assert "ACCESS THE SAME EVIDENCE" in pages[5]
    assert "return sum(items)" not in pages[1]
    assert commands == []


def test_previous_step_reuses_the_existing_sample(monkeypatch, sample):
    walkthrough, pages, _, _ = tour(monkeypatch, sample, [ENTER, ENTER, DOWN, ENTER, "q"])
    assert walkthrough.run() == 0
    assert pages[1] == pages[3]


def test_source_page_has_a_visible_hierarchy_without_ansi_styles(monkeypatch, sample):
    walkthrough, _, _, _ = tour(monkeypatch, sample, [])
    walkthrough._prepare()
    page = walkthrough.screen(2).plain
    assert page.index("STEP 2 OF 5  |  QUERY THE SOURCE") < page.index("+- SOURCE pricing.py:1-2")
    assert page.index("return sum(items)") < page.index("WHAT THIS MEANS") < page.index("ACTIONS")
    assert "=====" in page and "| " in page and "+-----" in page


def test_dashboard_action_is_scoped_to_the_sample(monkeypatch, sample):
    walkthrough, _, commands, _ = tour(monkeypatch, sample, [ENTER] * 6 + ["q"])
    assert walkthrough.run() == 0
    assert commands == [["dashboard", "--path", str(sample[0])]]


def test_own_repository_shows_network_plan_but_cancel_does_not_scan(monkeypatch, sample, tmp_path):
    keys = [ENTER] * 5 + [DOWN, ENTER, " ", DOWN, ENTER, "q"]
    walkthrough, _, commands, _ = tour(monkeypatch, sample, keys, ask=str(tmp_path))
    assert walkthrough.run() == 0
    assert commands == [["scan", str(tmp_path), "--explain-network"]]


def test_own_repository_scans_only_after_selection(monkeypatch, sample, tmp_path):
    keys = [ENTER] * 5 + [DOWN, ENTER, " ", ENTER]
    walkthrough, _, commands, _ = tour(monkeypatch, sample, keys, ask=str(tmp_path))
    assert walkthrough.run() == 0
    assert commands == [["scan", str(tmp_path), "--explain-network"], ["scan", str(tmp_path)]]


def test_quitting_welcome_does_not_create_a_sample(monkeypatch):
    def unexpected():
        raise AssertionError("No sample until Start is selected")

    monkeypatch.setattr("aletheore.demo_walkthrough.create_demo", unexpected)
    walkthrough = DemoWalkthrough(Console(file=io.StringIO()), run_command=unexpected, ask=unexpected,
                                  read_key=lambda: "q")
    assert walkthrough.run() == 0


@pytest.mark.parametrize("width,height", [(80, 24), (100, 32), (60, 24)])
def test_pages_fit_normal_terminal_sizes(monkeypatch, sample, width, height):
    walkthrough, _, _, _ = tour(monkeypatch, sample, [])
    walkthrough.console = Console(file=io.StringIO(), width=width, height=height)
    walkthrough._prepare()
    for step in range(6):
        console = Console(file=io.StringIO(), width=width, height=height)
        menu = ui.render_menu([ui.MenuItem("next", "Continue"), ui.MenuItem("back", "Previous step"),
                               ui.MenuItem("quit", "Exit")], 0, unicode_ok=True,
                              header=walkthrough.screen(step), width=width, height=height)
        console.print(menu)
        assert len(console.file.getvalue().splitlines()) <= height, step


def test_preparation_failure_has_a_readable_error(monkeypatch):
    def fail():
        raise OSError("Git unavailable")

    monkeypatch.setattr("aletheore.demo_walkthrough.create_demo", fail)
    stream = io.StringIO()
    walkthrough = DemoWalkthrough(Console(file=stream), run_command=lambda _: 0, ask=lambda _: "",
                                  read_key=lambda: ENTER)
    assert walkthrough.run() == 1
    assert "Demo could not prepare its sample: Git unavailable" in stream.getvalue()


def test_plain_flag_bypasses_the_tour_even_in_a_terminal(monkeypatch):
    monkeypatch.setattr(ui, "is_interactive", lambda: True)
    monkeypatch.setattr(DemoWalkthrough, "run", lambda _: pytest.fail("--plain must not open the tour"))
    result = CliRunner().invoke(app, ["demo", "--plain"])
    assert result.exit_code == 0, result.output
    assert "3 files depend on pricing.py" in result.output


@pytest.mark.parametrize("text", [
    '{"result":{"affected_total":3,"affected_test_files":1}}',
    'result:\n  affected_total: 3\n  affected_test_files: 1',
])
def test_mcp_demo_accepts_real_serializer_formats(monkeypatch, tmp_path, text):
    import asyncio

    async def call_tool(name, arguments):
        assert name == "aletheore_get_blast_radius"
        assert arguments == {"target": "pricing.py", "symbol": "total"}
        return SimpleNamespace(is_error=False, content=[SimpleNamespace(text=text)])

    def build_server(repo, **kwargs):
        assert repo == tmp_path
        assert kwargs == {"allow": frozenset(), "watch": False, "profile": "lean"}
        return SimpleNamespace(call_tool=call_tool)

    monkeypatch.setattr("aletheore.mcp_server.build_server", build_server)
    walkthrough = DemoWalkthrough(Console(file=io.StringIO()), run_command=lambda _: 0, ask=lambda _: "")
    walkthrough.repo = tmp_path
    assert asyncio.run(walkthrough._agent_call()) == {"affected_total": 3, "affected_test_files": 1}


@pytest.mark.parametrize("content", [
    [], [SimpleNamespace(text='{}')], [SimpleNamespace(text='not: [valid')],
    [SimpleNamespace(text='{"result":{"affected_total":3,"affected_test_files":[]}}')],
    [SimpleNamespace(text='{"result":{"affected_total":true,"affected_test_files":1}}')],
])
def test_unexpected_mcp_payload_has_a_readable_failure(monkeypatch, tmp_path, content):
    import asyncio

    async def call_tool(*args):
        return SimpleNamespace(is_error=False, content=content)

    monkeypatch.setattr("aletheore.mcp_server.build_server", lambda *a, **k: SimpleNamespace(call_tool=call_tool))
    walkthrough = DemoWalkthrough(Console(file=io.StringIO()), run_command=lambda _: 0, ask=lambda _: "")
    walkthrough.repo = tmp_path
    with pytest.raises(OSError, match="unreadable impact response"):
        asyncio.run(walkthrough._agent_call())


@pytest.mark.parametrize("stage", ["plan", "scan"])
def test_repository_action_errors_are_readable(tmp_path, stage):
    def run_command(args):
        if stage == "plan" or "--explain-network" not in args:
            raise OSError("repository unavailable")
        return 0

    keys = iter([" ", ENTER])
    stream = io.StringIO()
    walkthrough = DemoWalkthrough(Console(file=stream), run_command=run_command, ask=lambda _: str(tmp_path),
                                  read_key=lambda: next(keys))
    assert walkthrough._own_repository() == (None if stage == "plan" else 1)
    assert "Could not complete this action: repository unavailable" in stream.getvalue()


def test_welcome_falls_back_to_ascii_on_non_unicode_terminals(monkeypatch):
    monkeypatch.setattr(ui, "supports_unicode", lambda stream: False)
    console = Console(file=io.StringIO(), width=100, height=32)
    walkthrough = DemoWalkthrough(console, run_command=lambda args: 0, ask=lambda _: "")
    page = walkthrough.screen(0).plain
    assert page.startswith("ALETHEORE\n")
    assert page.isascii()
