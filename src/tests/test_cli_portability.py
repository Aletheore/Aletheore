"""CLI regressions that run on Linux, macOS and Windows."""

import importlib
import ast
import json
import os
import stat
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from aletheore import cli, subprocess_utils, token_profile as tp
from aletheore.adapters.base import AdapterInvocationError
from aletheore.hook_config import python_hook


def audit_stream(usage):
    return '\n'.join(json.dumps(item) for item in [
        [], None, "message", {"type": "system", "subtype": "init", "tools": ["Read"]},
        {"type": "result", "usage": usage},
    ])


def test_audit_ignores_nonobject_json_events():
    assert tp.parse_audit(audit_stream({"input_tokens": 4})).input_side_tokens == 4


@pytest.mark.parametrize("usage", [[1], "bad", {"input_tokens": -1}, {"input_tokens": True},
                                    {"input_tokens": "bad"}, {"input_tokens": 1.5}])
def test_invalid_audit_usage_is_a_profile_error(usage):
    with pytest.raises(tp.ProfileError):
        tp.parse_audit(audit_stream(usage))


@pytest.mark.parametrize("permissions", [[], "bad", {"deny": "Monitor"}, {"deny": [1]}])
def test_profile_remove_preserves_malformed_user_settings(tmp_path, permissions):
    folder = tmp_path / ".claude"
    folder.mkdir()
    settings = folder / "settings.json"
    settings.write_text(json.dumps({"permissions": permissions}), encoding="utf-8")
    (folder / tp.SIDECAR_NAME).write_text(json.dumps({"added": ["Monitor"], "created_file": True}), encoding="utf-8")
    before = settings.read_bytes()
    with pytest.raises(tp.ProfileError):
        tp.remove_deny(tmp_path)
    assert settings.read_bytes() == before


def test_profile_preserves_unicode_and_accepts_windows_utf8_bom(tmp_path):
    folder = tmp_path / ".claude"
    folder.mkdir()
    settings = folder / "settings.json"
    settings.write_text(json.dumps({"custom": "日本 é"}, ensure_ascii=False), encoding="utf-8-sig")
    tp.apply_deny(tmp_path, ["Monitor"])
    tp.remove_deny(tmp_path)
    assert json.loads(settings.read_text(encoding="utf-8")) == {"custom": "日本 é"}


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits; Windows inherits directory ACLs")
@pytest.mark.parametrize("existing", [False, True])
def test_settings_replacements_keep_private_permissions(tmp_path, existing):
    from aletheore import read_guard_setup
    directory = tmp_path / ".claude"
    directory.mkdir()
    settings = directory / "settings.json"
    local = directory / "settings.local.json"
    for path in (settings, local):
        if existing:
            path.write_text("{}", encoding="utf-8")
            path.chmod(0o600)
    tp.apply_deny(tmp_path, ["Monitor"])
    read_guard_setup.install(tmp_path)
    assert stat.S_IMODE(settings.stat().st_mode) == 0o600
    assert stat.S_IMODE(local.stat().st_mode) == 0o600
    tp.remove_deny(tmp_path)
    read_guard_setup.remove(tmp_path)
    if existing:
        assert stat.S_IMODE(settings.stat().st_mode) == 0o600
        assert stat.S_IMODE(local.stat().st_mode) == 0o600


def test_profile_write_failure_is_an_actionable_cli_error(monkeypatch, tmp_path):
    from aletheore.tokens_cli import tokens_app
    monkeypatch.setattr(tp, "apply_deny", Mock(side_effect=PermissionError("access denied")))
    result = CliRunner().invoke(tokens_app, ["profile", "core", "--yes", "--path", str(tmp_path)])
    assert result.exit_code == 1
    assert "error:" in result.output and "access denied" in result.output


@pytest.mark.parametrize("args", [["meter"], ["profile", "core"], ["audit"]])
@pytest.mark.parametrize("option", ["--path", "--project"])
def test_empty_token_project_is_rejected_before_any_action(args, option, monkeypatch, tmp_path):
    from aletheore.tokens_cli import tokens_app
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(tokens_app, [*args, option, "", "--yes"])
    assert result.exit_code == 2
    assert "project path must not be empty" in result.output
    assert not list(tmp_path.iterdir())


def test_profile_rejects_directory_junctions(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "is_junction", lambda path: path.name == ".claude", raising=False)
    with pytest.raises(tp.ProfileError, match="linked settings"):
        tp.apply_deny(tmp_path, ["Monitor"])
    assert not (tmp_path / ".claude").exists()


def test_python311_junction_fallback(monkeypatch):
    from aletheore import user_paths
    path = SimpleNamespace(is_symlink=lambda: False, lstat=lambda: SimpleNamespace(st_reparse_tag=0xA0000003))
    monkeypatch.setattr(user_paths.os, "name", "nt")
    assert user_paths.is_link_or_junction(path)


def test_audit_runner_resolves_binary_and_validates_exit_status(monkeypatch, tmp_path):
    monkeypatch.setattr(tp.shutil, "which", lambda name: str(tmp_path / "claude"))
    run = Mock(return_value=subprocess.CompletedProcess([], 7, "", "not signed in"))
    monkeypatch.setattr(tp.subprocess, "run", run)
    with pytest.raises(tp.ProfileError, match="exit 7"):
        tp._default_runner(["claude", "-p", "test"], tmp_path)
    args, kwargs = run.call_args
    assert Path(args[0][0]).is_absolute()
    assert kwargs["encoding"] == "utf-8" and kwargs["stdin"] == subprocess.DEVNULL


@pytest.mark.parametrize("name,cls", [("claude_code", "ClaudeCodeAdapter"), ("codex_cli", "CodexCliAdapter"),
    ("gemini_cli", "GeminiCliAdapter"), ("grok_build", "GrokBuildAdapter"),
    ("mistral_vibe", "MistralVibeAdapter"), ("opencode", "OpenCodeAdapter")])
def test_agent_start_errors_are_adapter_errors(monkeypatch, name, cls):
    module = importlib.import_module(f"aletheore.adapters.{name}")
    monkeypatch.setattr(module.shutil, "which", lambda _: sys.executable)
    monkeypatch.setattr(module.subprocess, "run", Mock(side_effect=PermissionError("access denied")))
    with pytest.raises(AdapterInvocationError, match="could not start"):
        getattr(module, cls)().invoke("audit", ".")


def test_npm_shim_runs_node_without_shell_interpreting_prompt(monkeypatch, tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed")
    folder = tmp_path / "agent install & 日本"
    folder.mkdir()
    script = folder / "entry.js"
    script.write_text("console.log(JSON.stringify(process.argv.slice(2)))", encoding="utf-8")
    shim = folder / "agent.cmd"
    shim.write_text('@SET "_prog=node"\n@"%_prog%" "%dp0%\\entry.js" %*', encoding="utf-8")
    prompt = 'first line\n日本 & echo unsafe | more %PATH% "quoted" $HOME'
    with monkeypatch.context() as patch:
        patch.setattr(subprocess_utils.sys, "platform", "win32")
        patch.setattr(subprocess_utils.shutil, "which", lambda _: node)
        argv = subprocess_utils.command_argv(str(shim), prompt)
    result = subprocess.run(argv, capture_output=True, encoding="utf-8", check=True)
    assert json.loads(result.stdout) == [prompt]
    assert argv[0] == str(Path(node).absolute())


def test_unknown_batch_wrapper_fails_without_invoking_a_shell(monkeypatch, tmp_path):
    shim = tmp_path / "agent.cmd"
    shim.write_text("@echo %*", encoding="utf-8")
    monkeypatch.setattr(subprocess_utils.sys, "platform", "win32")
    with pytest.raises(OSError, match="unsupported batch launcher"):
        subprocess_utils.command_argv(str(shim), "& injected")


def test_generated_hooks_use_utf8_exec_arguments(monkeypatch):
    executable = r"C:\Program Files\日本 & tools\python.exe"
    monkeypatch.setattr(sys, "executable", executable)
    hook = python_hook("aletheore.read_guard", "pre")
    assert hook == {"type": "command", "command": executable,
                    "args": ["-X", "utf8", "-m", "aletheore.read_guard", "pre"]}


def test_windows_system_python_finds_scripts_console_entrypoint(monkeypatch, tmp_path):
    python = tmp_path / "python.exe"
    python.touch()
    scripts = tmp_path / "Scripts"
    scripts.mkdir()
    entry = scripts / "aletheore.exe"
    entry.touch()
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "executable", str(python))
    assert cli._aletheore_command() == str(entry)


def test_quoted_home_paths_are_expanded(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert Path(cli._resolve_path("~", str(tmp_path))) == tmp_path


def test_menu_positional_input_cannot_become_an_option():
    assert cli._command_args("locate", ["--help"]) == ["locate", "--", "--help"]


@pytest.mark.parametrize("args,code", [
    (["query", "[/]"], 1),
    (["config", "crash-reporting", "[/]"], 1),
    (["hook", "evidence-snippet", "--mode", "[/]"], 2),
    (["mcp", "--profile", "[/]"], 2),
    (["audit", "--agent", "[/]"], 2),
])
def test_user_input_is_not_interpreted_as_rich_markup(args, code):
    result = CliRunner().invoke(cli.app, args)
    assert result.exit_code == code
    assert isinstance(result.exception, SystemExit)
    assert "[/]" in result.output


def test_existing_config_path_with_brackets_is_printed_literally(tmp_path):
    project = tmp_path / "[/]"
    project.mkdir(parents=True)
    (project / ".aletheore.json").write_text("{}", encoding="utf-8")
    result = CliRunner().invoke(cli.app, ["init", str(project), "--yes"])
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert str(project) in result.output.replace("\n", "")


def test_menu_cli_returns_usage_failure_and_command_exit_codes(tmp_path):
    assert cli._run_cli(["tokens", "profile"]) == 2
    assert cli._run_cli(["tokens", "profile", "invalid", "--path", str(tmp_path)]) == 1
    assert cli._run_cli(["tokens", "meter", "--plain", "--path", str(tmp_path)]) == 0


def test_menu_prompt_cancellation_uses_typers_runtime(monkeypatch):
    import typer
    monkeypatch.setattr(typer, "prompt", Mock(side_effect=typer.Abort()))
    assert cli._ask("cancel") == ""


def test_expected_filesystem_failure_is_not_reported_as_a_crash(monkeypatch, capsys):
    monkeypatch.setattr(cli, "init_cli_sentry", lambda: None)
    monkeypatch.setattr(cli, "is_crash_reporting_enabled", lambda: True)
    monkeypatch.setattr(cli, "has_shown_crash_reporting_notice", lambda: True)
    report = Mock()
    monkeypatch.setattr(cli.sentry_sdk, "capture_exception", report)
    monkeypatch.setattr(cli, "app", Mock(side_effect=PermissionError("access denied")))
    with pytest.raises(SystemExit) as exited:
        cli.main()
    assert exited.value.code == 1
    assert "error: access denied" in capsys.readouterr().err
    report.assert_not_called()


def test_closed_output_pipe_exits_cleanly(monkeypatch):
    monkeypatch.setattr(cli, "init_cli_sentry", lambda: None)
    monkeypatch.setattr(cli, "is_crash_reporting_enabled", lambda: False)
    monkeypatch.setattr(cli, "app", Mock(side_effect=BrokenPipeError()))
    # Register the original stream for restoration after main replaces it.
    original = sys.stdout
    monkeypatch.setattr(sys, "stdout", original)
    with pytest.raises(SystemExit) as exited:
        cli.main()
    stream = sys.stdout
    try:
        assert exited.value.code == 0
        assert Path(stream.name).name == Path(os.devnull).name
    finally:
        sys.stdout = original
        stream.close()


@pytest.mark.parametrize("kind", cli.QUERY_KIND_CHOICES)
def test_query_menu_can_run_every_kind(monkeypatch, kind):
    from aletheore import ui
    monkeypatch.setattr(cli, "_clear_screen", lambda: None)
    monkeypatch.setattr(ui, "choose", lambda *a, **kw: ui.MenuItem(kind, kind))
    monkeypatch.setattr(cli, "_ask", lambda _: "--literal")
    run = Mock()
    monkeypatch.setattr(cli, "_run_cli", run)
    assert cli._query_flow({})
    argv = run.call_args.args[0]
    command = cli._click_command(["query"])
    with command.make_context("query", argv[1:]) as context:
        assert context.params["kind"] == kind
        expected = None if kind in ("schema", "changes") else "--literal"
        assert context.params["target"] == expected
        if kind == "symbol-source":
            assert context.params["symbol"] == "--literal"
        if kind == "ast-pattern":
            assert context.params["language"] == "--literal"
        if kind == "answer":
            assert context.params["agent"] == "--literal"


def test_query_menu_cancel_does_not_execute(monkeypatch):
    from aletheore import ui
    monkeypatch.setattr(cli, "_clear_screen", lambda: None)
    choices = iter([ui.MenuItem("symbols", "symbols"), None])
    monkeypatch.setattr(ui, "choose", lambda *a, **kw: next(choices))
    monkeypatch.setattr(cli, "_ask", lambda _: None)
    run = Mock()
    monkeypatch.setattr(cli, "_run_cli", run)
    assert not cli._query_flow({})
    run.assert_not_called()


def test_meter_menu_rejects_invalid_path_without_losing_current_project(monkeypatch):
    from aletheore import ui, read_guard_setup
    monkeypatch.setattr(cli, "_clear_screen", lambda: None)
    choices = iter([ui.MenuItem("project", "project"), None])
    monkeypatch.setattr(ui, "choose", lambda *a, **kw: next(choices))
    monkeypatch.setattr(cli, "_ask", lambda _: "bad\x00path")
    monkeypatch.setattr(read_guard_setup, "is_installed", lambda _: False)
    run, pause = Mock(), Mock()
    monkeypatch.setattr(cli, "_run_cli", run)
    cli._meter_flow({}, pause)
    assert run.call_count == 2
    assert run.call_args_list[0] == run.call_args_list[1]
    pause.assert_called_once()


def test_meter_status_handles_linked_settings_and_stale_profile(monkeypatch, tmp_path):
    from aletheore.tokens_cli import _meter_profile_status
    tp.apply_deny(tmp_path, ["Monitor"])
    settings = tmp_path / ".claude" / "settings.json"
    settings.write_text("{}", encoding="utf-8")
    assert _meter_profile_status(tmp_path) == "not active"
    monkeypatch.setattr(tp, "_paths", Mock(side_effect=tp.ProfileError("linked settings")))
    assert _meter_profile_status(tmp_path).startswith("unknown")


def test_text_subprocesses_never_depend_on_the_host_locale():
    import aletheore
    root = Path(aletheore.__file__).parent
    for path in root.rglob("*.py"):
        if "skills" in path.relative_to(root).parts:
            continue
        for call in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                    and isinstance(call.func.value, ast.Name) and call.func.value.id == "subprocess"
                    and call.func.attr in ("run", "Popen")):
                continue
            keywords = {item.arg: item.value for item in call.keywords}
            text = keywords.get("text")
            if isinstance(text, ast.Constant) and text.value is True:
                assert "encoding" in keywords, f"{path}:{call.lineno} uses the host locale"


def test_cli_markup_interpolations_use_literal_text():
    tree = ast.parse(Path(cli.__file__).read_text(encoding="utf-8"))
    for call in ast.walk(tree):
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                and call.func.attr == "print" and isinstance(call.func.value, ast.Name)
                and call.func.value.id in ("console", "_stderr_console")
                and call.args and isinstance(call.args[0], ast.JoinedStr)):
            continue
        if any(kw.arg == "markup" and isinstance(kw.value, ast.Constant) and kw.value.value is False
               for kw in call.keywords):
            continue
        assert not any(isinstance(value, ast.FormattedValue) for value in call.args[0].values), call.lineno


@pytest.mark.parametrize("value", [r"C:\folder\[\]", r"C:\folder\[2026]", r"C:\folder\[file]",
                                   "C:\\folder\\", "[/]", "[bold]literal[/bold]", ":warning: 日本"])
def test_markup_inserts_exact_literal_values_with_template_styles(value):
    from aletheore import ui
    result = ui.markup("[bold]Before {} after[/bold]", value)
    assert result.plain == f"Before {value} after"
    start = len("Before ")
    assert any(span.start == start and span.end == start + len(value) and span.style == "bold"
               for span in result.spans)
