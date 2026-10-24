"""Regression tests for the review findings on the evidence hook and router PR that were real."""

import io
import json
import sys

import pytest
from typer.testing import CliRunner

from aletheore import cli, evidence_hook, query, tool_router

runner = CliRunner()


def test_snippet_rejects_an_unknown_mode_instead_of_putting_it_in_a_shell_command():
    result = runner.invoke(cli.app, ["hook", "evidence-snippet", "--mode", "inject && evil"])
    assert result.exit_code == 2
    assert '"hooks"' not in result.stdout  # no settings block is printed for a mode that was refused


def test_snippet_quotes_an_interpreter_path_with_spaces(monkeypatch):
    # pipx venvs live under "Application Support" on macOS; unquoted, the hook command breaks at the space
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sys, "executable", "/Users/a/Library/Application Support/venv/bin/python")
    result = runner.invoke(cli.app, ["hook", "evidence-snippet"])
    assert result.exit_code == 0
    command = json.loads(result.stdout)["hooks"]["PostToolUse"][0]["hooks"][0]["command"]
    assert command.startswith("'/Users/a/Library/Application Support/venv/bin/python' -m aletheore.evidence_hook")
    assert command.endswith("--mode inject")


@pytest.mark.parametrize(
    "question,intent",
    [
        ("explain the layer violations in this repo", "layers"),
        ("summarize the secrets found", "secrets"),
        ("summarize the project structure", "overview"),
    ],
)
def test_read_only_requests_phrased_with_explain_or_summarize_are_answered(question, intent):
    decision = tool_router.route(question, None, ["a.py", "b.py"])
    assert (decision.confidence, decision.intent) == ("high", intent)


@pytest.mark.parametrize("question", ["deploy this", "restart the server", "write a changelog"])
def test_imperative_requests_to_act_are_still_refused(question):
    assert tool_router.route(question, None, ["a.py"]).confidence == "none"


def test_a_failing_query_comes_back_as_an_error_result_not_an_exception(monkeypatch):
    def boom(*args):
        raise RuntimeError("bad evidence")

    monkeypatch.setitem(tool_router._RUNNERS, "secrets", boom)
    evidence = {"repository": {"modules": [{"path": "a.py", "imports": [], "imported_by": []}]}}
    result = tool_router.answer("are there any leaked secrets", None, evidence, None)
    assert "RuntimeError: bad evidence" in result["error"]


def test_a_pasted_test_definition_alone_does_not_make_test_importers_not_count():
    evidence = {
        "repository": {
            "modules": [
                {"path": "lib/a.py", "language": "python", "imports": [], "imported_by": ["tests/test_a.py"]},
                {"path": "tests/test_a.py", "language": "python", "imports": ["lib/a.py"], "imported_by": []},
            ],
            "dead_code": {"unreachable_modules": []},
        }
    }
    definition = "Test files are any path containing a tests/ directory."
    kept = tool_router.answer(f"List modules in lib that no other module imports. {definition}", None, evidence, None)
    assert kept["answer"]["importers_counted"] == "all"
    assert kept["answer"]["imported_only_by_tests"] == ["lib/a.py"]
    ignored = tool_router.answer(
        f"List the NON-TEST modules in lib that no other module imports. {definition}", None, evidence, None
    )
    assert ignored["answer"]["importers_counted"] == "non-test only"
    assert ignored["answer"]["unimported"] == ["lib/a.py"]


def test_scan_unreachable_total_uses_the_same_language_filter_and_tolerates_a_missing_path():
    evidence = {
        "repository": {
            "modules": [
                {"path": "x/a.py", "language": "python", "imports": [], "imported_by": []},
                {"path": "x/b.go", "language": "go", "imports": [], "imported_by": []},
            ],
            "dead_code": {"unreachable_modules": [{"path": "x/a.py"}, {"path": "x/b.go"}, {"reason": "no path"}]},
        }
    }
    python_only = query.find_unimported_modules(evidence, "x", ["python"])
    assert python_only["unimported_total"] == 1
    assert python_only["scan_unreachable_total"] == 1  # the go entry and the entry without a path are not counted


def test_the_router_profile_does_not_tell_the_agent_to_call_a_tool_it_does_not_have(tmp_path, monkeypatch):
    import asyncio

    from aletheore.mcp_server import build_server
    from tests.test_mcp_server import make_repo_with_evidence

    repo = make_repo_with_evidence(tmp_path)
    monkeypatch.setattr(tool_router, "ANSWER_MAX_CHARS", 40)
    router = asyncio.run(build_server(repo, profile="router").call_tool("aletheore_ask", {"question": "what breaks if I change b.py"}))
    assert "narrower question" in str(router)
    assert "call aletheore_get_blast_radius directly" not in str(router)
    full = asyncio.run(build_server(repo, profile="full").call_tool("aletheore_ask", {"question": "what breaks if I change b.py"}))
    assert "call aletheore_get_blast_radius directly" in str(full)


@pytest.mark.parametrize(
    "question,scope",
    [
        ("which modules under `web` does nothing import", "web"),
        ("in the web folder, which modules does nobody import", "web"),
        ("list the modules of the `web` package that nothing imports", "web"),
        ("which modules inside web does nobody import", "web"),
        # an identifier or an ordinary word is not a place, even when a directory of that name exists
        ("is `web` unused, nobody imports it", None),
        ("what changed in web, anything nobody imports", None),
        ("how does `web` work, any modules nothing imports", None),
    ],
)
def test_a_one_word_directory_needs_a_place_cue_to_become_the_scope(question, scope):
    paths = ["web/a.py", "web/b.py", "lib/c.py"]
    assert tool_router.resolve_scope(question, None, paths) == scope


def test_parallel_hook_calls_do_not_lose_each_others_shown_files(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    state = tmp_path / "s.shown"
    names = [f"src/file{i}.py" for i in range(200)]
    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(lambda name: evidence_hook._mark_shown(state, name), names))
    assert all(evidence_hook._already_shown(state, name) for name in names)
    assert not evidence_hook._already_shown(state, "src/other.py")


def test_hook_failures_are_visible_on_stderr_only_when_debugging_is_asked_for(monkeypatch, capsys):
    stdout = io.StringIO()
    monkeypatch.delenv("ALETHEORE_HOOK_DEBUG", raising=False)
    assert evidence_hook.main([], stdin=io.StringIO("{not json"), stdout=stdout) == 0
    assert capsys.readouterr().err == ""
    monkeypatch.setenv("ALETHEORE_HOOK_DEBUG", "1")
    assert evidence_hook.main([], stdin=io.StringIO("{not json"), stdout=stdout) == 0
    assert "Traceback" in capsys.readouterr().err
    assert stdout.getvalue() == ""
