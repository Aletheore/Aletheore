import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from aletheore.cli import _ElapsedTicker, _make_progress_printer, app
from aletheore.device_auth import DeviceFlowError
from aletheore.report import (
    AmbiguousAdapterError,
    NoAdapterAvailableError,
    build_instruction,
    run_reasoning_phase,
    select_adapter,
)

runner = CliRunner()


def make_adapter(name: str, available: bool):
    adapter = MagicMock()
    adapter.name = name
    adapter.is_available.return_value = available
    return adapter


def test_select_adapter_returns_only_available_one():
    a = make_adapter("claude", True)
    b = make_adapter("cursor", False)
    with patch("builtins.input", return_value="1"):
        result = select_adapter([a, b], forced_name=None, interactive=True)
    assert result is a


def test_select_adapter_raises_when_none_available():
    a = make_adapter("claude", False)
    with pytest.raises(NoAdapterAvailableError):
        select_adapter([a], forced_name=None, interactive=False)


def test_select_adapter_raises_when_multiple_and_not_interactive_and_no_flag():
    a = make_adapter("claude", True)
    b = make_adapter("cursor", True)
    with pytest.raises(AmbiguousAdapterError):
        select_adapter([a, b], forced_name=None, interactive=False)


def test_select_adapter_always_prompts_interactively_even_with_one_available():
    a = make_adapter("claude", True)
    with patch("builtins.input", return_value="1") as mock_input:
        result = select_adapter([a], forced_name=None, interactive=True)
    assert result is a
    mock_input.assert_called_once()


def test_select_adapter_raises_when_not_interactive_even_with_one_available():
    a = make_adapter("claude", True)
    with pytest.raises(AmbiguousAdapterError):
        select_adapter([a], forced_name=None, interactive=False)


def test_select_adapter_honors_forced_name():
    a = make_adapter("claude", True)
    b = make_adapter("cursor", True)
    result = select_adapter([a, b], forced_name="cursor", interactive=False)
    assert result is b


def test_build_instruction_references_manual_and_evidence():
    instruction = build_instruction(manual_dir="manual")
    assert "manual" in instruction
    assert ".aletheore/evidence.toon" in instruction


def test_run_reasoning_phase_writes_report(tmp_path):
    repo = tmp_path
    (repo / ".aletheore").mkdir()
    (repo / ".aletheore" / "evidence.json").write_text("{}")

    adapter = MagicMock()
    adapter.invoke.return_value = "# Audit Report\n\nfindings here\n"

    report_path = run_reasoning_phase(adapter, repo_path=str(repo), manual_dir="manual")

    written = Path(report_path)
    assert written == repo / ".aletheore" / "audit-report.md"
    assert written.read_text() == "# Audit Report\n\nfindings here\n"
    adapter.invoke.assert_called_once()


def test_run_reasoning_phase_does_not_clobber_report_the_agent_wrote_itself(tmp_path):
    repo = tmp_path
    (repo / ".aletheore").mkdir()
    (repo / ".aletheore" / "evidence.json").write_text("{}")
    report_file = repo / ".aletheore" / "audit-report.md"

    def fake_invoke(instruction, cwd):
        # Simulate an agent (e.g. Claude Code with tool access) that writes
        # the report itself via its own file tools, per the instruction, and
        # only returns a short wrap-up message as its actual return value -
        # not the report content.
        report_file.write_text("# Real Audit Report\n\nreal findings here\n")
        return "I read the manual and evidence, then wrote the audit report."

    adapter = MagicMock()
    adapter.invoke.side_effect = fake_invoke

    report_path = run_reasoning_phase(adapter, repo_path=str(repo), manual_dir="manual")

    written = Path(report_path)
    assert written.read_text() == "# Real Audit Report\n\nreal findings here\n"


def test_main_with_no_command_shows_banner_and_exits_cleanly():
    result = runner.invoke(app, [])

    assert result.exit_code == 0
    assert "ALETHEORE" in result.output
    assert "scan" in result.output and "audit" in result.output


def test_main_unknown_command_still_errors():
    result = runner.invoke(app, ["bogus-command"])
    assert result.exit_code != 0


def test_progress_printer_prints_each_distinct_phase_on_its_own_line(capsys):
    report = _make_progress_printer(is_tty=False)
    report("Detecting languages, frameworks, and build tools")
    report("Building module dependency graph (parsing source with tree-sitter)")

    captured = capsys.readouterr()
    lines = [line for line in captured.out.split("\n") if line]
    assert len(lines) == 2
    assert "Detecting languages" in lines[0]
    assert "Building module dependency graph" in lines[1]


def test_progress_printer_overwrites_repeated_license_progress_on_a_tty(capsys):
    report = _make_progress_printer(is_tty=True)
    report("Checking dependency licenses: 1/3 (flask)")
    report("Checking dependency licenses: 2/3 (requests)")
    report("Done")

    captured = capsys.readouterr()
    # both license lines share one terminal line via \r, so only two real
    # newlines appear: one closing out the in-place license line, one from "Done"
    assert captured.out.count("\n") == 2
    assert "requests" in captured.out
    assert "Done" in captured.out


def test_progress_printer_prints_every_license_line_when_not_a_tty(capsys):
    report = _make_progress_printer(is_tty=False)
    report("Checking dependency licenses: 1/3 (flask)")
    report("Checking dependency licenses: 2/3 (requests)")
    report("Done")

    captured = capsys.readouterr()
    lines = [line for line in captured.out.split("\n") if line]
    assert len(lines) == 3
    assert "flask" in lines[0]
    assert "requests" in lines[1]
    assert "Done" in lines[2]


def test_elapsed_ticker_updates_in_place_on_a_tty(capsys):
    with _ElapsedTicker("Waiting", interval=0.05, is_tty=True):
        time.sleep(0.12)

    captured = capsys.readouterr()
    assert "Waiting" in captured.out
    assert "elapsed" in captured.out


def test_elapsed_ticker_prints_start_and_done_once_when_not_a_tty(capsys):
    with _ElapsedTicker("Waiting", is_tty=False):
        pass

    captured = capsys.readouterr()
    lines = [line for line in captured.out.split("\n") if line]
    assert len(lines) == 2
    assert "Waiting..." in lines[0]
    assert "done" in lines[1]


def test_main_audit_invokes_audit_flow(tmp_path):
    with patch("aletheore.cli._audit", return_value=0) as mock_audit:
        result = runner.invoke(app, ["audit", str(tmp_path), "--agent", "claude"])

    assert result.exit_code == 0
    mock_audit.assert_called_once_with(str(tmp_path), "claude", True, True, True, True)


def test_known_adapters_includes_every_provider():
    from aletheore.cli import KNOWN_ADAPTERS

    names = {a.name for a in KNOWN_ADAPTERS}
    assert names == {
        "claude",
        "anthropic",
        "opencode",
        "codex",
        "openai",
        "gemini-cli",
        "gemini",
        "mistral-vibe",
        "mistral",
        "grok-build",
        "grok",
        "ollama",
    }


def test_audit_shows_consent_prompt_for_api_based_adapter_and_proceeds_on_yes(tmp_path):
    repo = tmp_path
    (repo / "main.py").write_text("x = 1\n")

    fake_adapter = MagicMock()
    fake_adapter.name = "openai"
    fake_adapter.requires_consent = True
    fake_adapter.invoke.return_value = "## Summary\n\nreport text"

    with patch("aletheore.cli.select_adapter", return_value=fake_adapter):
        with patch("builtins.input", return_value="y") as mock_input:
            result = runner.invoke(app, ["audit", str(repo)])

    assert result.exit_code == 0
    assert any("Continue" in call.args[0] for call in mock_input.call_args_list)
    fake_adapter.invoke.assert_called_once()


def test_audit_cancels_cleanly_when_consent_is_declined(tmp_path):
    repo = tmp_path
    (repo / "main.py").write_text("x = 1\n")

    fake_adapter = MagicMock()
    fake_adapter.name = "openai"
    fake_adapter.requires_consent = True

    with patch("aletheore.cli.select_adapter", return_value=fake_adapter):
        with patch("builtins.input", return_value="n"):
            result = runner.invoke(app, ["audit", str(repo)])

    assert result.exit_code == 0
    fake_adapter.invoke.assert_not_called()
    assert "Cancelled" in result.output


def test_audit_skips_consent_prompt_for_cli_based_adapter(tmp_path):
    repo = tmp_path
    (repo / "main.py").write_text("x = 1\n")

    fake_adapter = MagicMock()
    fake_adapter.name = "claude"
    fake_adapter.requires_consent = False
    fake_adapter.invoke.return_value = "## Summary\n\nreport text"

    with patch("aletheore.cli.select_adapter", return_value=fake_adapter):
        with patch("builtins.input") as mock_input:
            result = runner.invoke(app, ["audit", str(repo)])

    assert result.exit_code == 0
    mock_input.assert_not_called()
    fake_adapter.invoke.assert_called_once()


def test_main_audit_threads_no_check_vulnerabilities_flag(tmp_path):
    repo = tmp_path
    (repo / "main.py").write_text("x = 1\n")

    runner.invoke(
        app,
        ["audit", str(repo), "--no-check-vulnerabilities", "--agent", "nonexistent"],
    )

    evidence = json.loads((repo / ".aletheore" / "evidence.json").read_text())
    assert evidence["security"]["dependency_vulnerabilities"]["checked"] is False
    assert (
        evidence["security"]["dependency_vulnerabilities"]["reason"]
        == "skipped (--no-check-vulnerabilities)"
    )


def test_main_audit_threads_no_check_licenses_flag(tmp_path):
    repo = tmp_path
    (repo / "main.py").write_text("x = 1\n")

    runner.invoke(app, ["audit", str(repo), "--no-check-licenses", "--agent", "nonexistent"])

    evidence = json.loads((repo / ".aletheore" / "evidence.json").read_text())
    assert evidence["security"]["dependency_licenses"]["checked"] is False
    assert evidence["security"]["dependency_licenses"]["reason"] == "skipped (--no-check-licenses)"


def test_main_scan_threads_no_check_licenses_flag(tmp_path):
    repo = tmp_path
    (repo / "main.py").write_text("x = 1\n")

    runner.invoke(app, ["scan", str(repo), "--no-check-licenses"])

    evidence = json.loads((repo / ".aletheore" / "evidence.json").read_text())
    assert evidence["security"]["dependency_licenses"]["checked"] is False


def test_main_scan_positive_check_licenses_flag_is_also_accepted(tmp_path):
    # Typer's boolean-pair syntax additively exposes the positive counterpart
    # of every existing --no-X flag (--check-licenses alongside
    # --no-check-licenses) - purely additive, but worth confirming it actually
    # does the right thing rather than silently being a no-op.
    repo = tmp_path
    (repo / "main.py").write_text("x = 1\n")

    result = runner.invoke(app, ["scan", str(repo), "--check-licenses", "--no-check-vulnerabilities"])

    assert result.exit_code == 0
    evidence = json.loads((repo / ".aletheore" / "evidence.json").read_text())
    assert evidence["security"]["dependency_licenses"]["checked"] is True


def test_every_subcommand_help_runs_cleanly():
    for command in ("audit", "scan", "query", "diff", "mcp", "dashboard", "healthcheck", "login"):
        result = runner.invoke(app, [command, "--help"])
        assert result.exit_code == 0, f"{command} --help failed: {result.output}"
        assert "Usage" in result.output


def test_main_scan_threads_no_map_endpoints_flag(tmp_path):
    repo = tmp_path
    (repo / "app.py").write_text('@app.route("/users")\ndef list_users():\n    pass\n')

    runner.invoke(app, ["scan", str(repo), "--no-map-endpoints"])

    evidence = json.loads((repo / ".aletheore" / "evidence.json").read_text())
    assert evidence["repository"]["api_endpoints"]["checked"] is False


def test_main_audit_threads_no_scan_git_history_flag(tmp_path):
    repo = tmp_path
    (repo / "main.py").write_text("x = 1\n")

    runner.invoke(
        app,
        [
            "audit",
            str(repo),
            "--no-check-vulnerabilities",
            "--no-scan-git-history",
            "--agent",
            "nonexistent",
        ],
    )

    evidence = json.loads((repo / ".aletheore" / "evidence.json").read_text())
    assert evidence["security"]["secrets"]["history_scanned_commits"] == 0
    assert evidence["security"]["secrets"]["history_findings"] == []


def test_main_scan_writes_evidence_without_invoking_an_agent(tmp_path):
    repo = tmp_path
    (repo / "main.py").write_text("x = 1\n")

    result = runner.invoke(app, ["scan", str(repo)])

    assert result.exit_code == 0
    assert (repo / ".aletheore" / "evidence.json").exists()
    assert "audit-report.md" not in result.output
    assert "Running audit with" not in result.output


def test_index_command_builds_index_from_existing_evidence(tmp_path):
    repo = tmp_path
    (repo / "app.py").write_text("def greet():\n    return 'hi'\n")
    result = runner.invoke(app, ["scan", str(repo)])
    assert result.exit_code == 0

    with patch("aletheore.cli.build_index", return_value=3) as mock_build:
        result = runner.invoke(app, ["index", str(repo)])

    assert result.exit_code == 0
    assert "3" in result.output
    mock_build.assert_called_once()


def test_index_command_fails_clearly_without_prior_scan(tmp_path):
    result = runner.invoke(app, ["index", str(tmp_path)])
    assert result.exit_code == 1
    assert "scan" in result.output.lower()


def test_query_search_codebase_prints_toon_results(tmp_path):
    with patch(
        "aletheore.cli.search_index",
        return_value=[
            {
                "module_path": "auth.py",
                "symbol_name": "login",
                "start_line": 1,
                "end_line": 2,
                "score": 0.1,
            }
        ],
    ):
        result = runner.invoke(
            app, ["query", "search-codebase", "how does auth work", "--path", str(tmp_path)]
        )
    assert result.exit_code == 0
    assert "auth.py" in result.output


def test_query_answer_reuses_selected_adapter(tmp_path):
    fake_adapter = MagicMock()
    fake_adapter.name = "ollama"
    fake_adapter.requires_consent = False
    with patch("aletheore.cli.select_adapter", return_value=fake_adapter):
        with patch(
            "aletheore.cli.answer_question",
            return_value={
                "answer": "Login uses auth.py::login.",
                "cited_chunks": ["auth.py::login"],
                "confidence_gated": False,
            },
        ) as mock_answer:
            result = runner.invoke(
                app,
                [
                    "query",
                    "answer",
                    "how does auth work",
                    "--path",
                    str(tmp_path),
                    "--agent",
                    "ollama",
                ],
            )

    assert result.exit_code == 0
    assert "Login uses auth.py::login" in result.output
    mock_answer.assert_called_once()


def test_main_mcp_invokes_mcp_flow(tmp_path):
    with patch("aletheore.cli._mcp", return_value=0) as mock_mcp:
        result = runner.invoke(app, ["mcp", str(tmp_path)])

    assert result.exit_code == 0
    mock_mcp.assert_called_once_with(str(tmp_path), None)


def test_main_mcp_threads_answer_agent(tmp_path):
    with patch("aletheore.cli._mcp", return_value=0) as mock_mcp:
        result = runner.invoke(app, ["mcp", str(tmp_path), "--agent", "ollama"])

    assert result.exit_code == 0
    mock_mcp.assert_called_once_with(str(tmp_path), "ollama")


def test_main_dashboard_invokes_dashboard_flow(tmp_path):
    with patch("aletheore.cli._dashboard", return_value=0) as mock_dashboard:
        result = runner.invoke(app, ["dashboard", str(tmp_path)])

    assert result.exit_code == 0
    mock_dashboard.assert_called_once_with(str(tmp_path), 8420)


def test_main_dashboard_threads_custom_port(tmp_path):
    with patch("aletheore.cli._dashboard", return_value=0) as mock_dashboard:
        result = runner.invoke(app, ["dashboard", str(tmp_path), "--port", "9000"])

    assert result.exit_code == 0
    mock_dashboard.assert_called_once_with(str(tmp_path), 9000)


def test_dashboard_refuses_to_start_when_port_already_bound(tmp_path, capsys):
    import socket

    from aletheore.cli import _dashboard

    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    taken_port = blocker.getsockname()[1]

    try:
        with patch("aletheore.cli.webbrowser.open") as mock_open:
            exit_code = _dashboard(str(tmp_path), taken_port)
    finally:
        blocker.close()

    assert exit_code == 1
    mock_open.assert_not_called()
    captured = capsys.readouterr()
    assert "already in use" in captured.out
    assert "Dashboard running at" not in captured.out


def test_main_healthcheck_reports_results(tmp_path):
    repo = tmp_path
    (repo / "app.py").write_text('@app.route("/health")\ndef health():\n    pass\n')
    runner.invoke(app, ["scan", str(repo)])

    response = MagicMock()
    response.status = 200
    response.__enter__.return_value = response
    response.__exit__.return_value = False

    with patch("aletheore.healthcheck.urllib.request.urlopen", return_value=response):
        result = runner.invoke(
            app, ["healthcheck", str(repo), "--base-url", "http://localhost:5000"]
        )

    assert result.exit_code == 0
    assert "/health" in result.output
    assert "200" in result.output


def test_main_healthcheck_without_evidence_errors_clearly(tmp_path):
    result = runner.invoke(
        app, ["healthcheck", str(tmp_path), "--base-url", "http://localhost:5000"]
    )

    assert result.exit_code == 1
    assert "aletheore scan" in result.output


def test_login_saves_token_when_installation_auto_resolved(tmp_path, monkeypatch):
    import aletheore.credentials as credentials

    creds_path = tmp_path / "creds.json"
    monkeypatch.setattr(credentials, "DEFAULT_CREDENTIALS_PATH", creds_path)

    with patch("aletheore.device_auth.request_device_code") as mock_request_code, \
         patch("aletheore.device_auth.poll_for_access_token") as mock_poll, \
         patch("aletheore.device_auth.resolve_installation") as mock_resolve, \
         patch("aletheore.device_auth.mint_cli_token") as mock_mint:
        mock_request_code.return_value = MagicMock(
            verification_uri="https://github.com/login/device",
            user_code="ABCD-1234",
        )
        mock_poll.return_value = "gho_faketoken"
        mock_resolve.return_value = {"installation_id": 100, "account_login": "acme"}
        mock_mint.return_value = "aletheore-tok-xyz"

        result = runner.invoke(app, ["login"])

    assert result.exit_code == 0
    assert "acme" in result.output
    saved = json.loads(creds_path.read_text())
    assert saved["aletheore-managed-audit"] == "aletheore-tok-xyz"


def test_login_prompts_when_installation_ambiguous(tmp_path, monkeypatch):
    import aletheore.credentials as credentials

    creds_path = tmp_path / "creds.json"
    monkeypatch.setattr(credentials, "DEFAULT_CREDENTIALS_PATH", creds_path)

    with patch("aletheore.device_auth.request_device_code") as mock_request_code, \
         patch("aletheore.device_auth.poll_for_access_token") as mock_poll, \
         patch("aletheore.device_auth.resolve_installation") as mock_resolve, \
         patch("aletheore.device_auth.mint_cli_token") as mock_mint:
        mock_request_code.return_value = MagicMock(
            verification_uri="https://github.com/login/device",
            user_code="ABCD-1234",
        )
        mock_poll.return_value = "gho_faketoken"
        mock_resolve.return_value = [
            {"installation_id": 100, "account_login": "acme"},
            {"installation_id": 200, "account_login": "other"},
        ]
        mock_mint.return_value = "aletheore-tok-xyz"

        result = runner.invoke(app, ["login"], input="2\n")

    assert result.exit_code == 0
    called_installation_id = mock_mint.call_args[0][1]
    assert called_installation_id == 200


def test_login_prints_error_and_exits_nonzero_on_device_flow_error():
    with patch("aletheore.device_auth.request_device_code") as mock_request_code, \
         patch("aletheore.device_auth.poll_for_access_token") as mock_poll:
        mock_request_code.return_value = MagicMock(
            verification_uri="https://github.com/login/device",
            user_code="ABCD-1234",
        )
        mock_poll.side_effect = DeviceFlowError("authorization was denied")

        result = runner.invoke(app, ["login"])

    assert result.exit_code == 1
    assert "denied" in result.output


def test_main_query_imports_prints_result(tmp_path):
    repo = tmp_path
    (repo / "app").mkdir()
    (repo / "app" / "config.py").write_text("SETTING = 1\n")
    (repo / "app" / "auth.py").write_text("from app import config\n")
    runner.invoke(app, ["scan", str(repo)])

    result = runner.invoke(app, ["query", "imports", "app/auth.py", "--path", str(repo)])

    assert result.exit_code == 0
    assert "app/config.py" in result.output


def test_main_query_symbol_source_prints_toon_result(tmp_path):
    repo = tmp_path
    (repo / "app.py").write_text("x = 1\n\ndef greet():\n    return 'hi'\n")
    runner.invoke(app, ["scan", str(repo)])

    result = runner.invoke(app, ["query", "symbol-source", "app.py", "greet", "--path", str(repo)])

    assert result.exit_code == 0
    assert "def greet" in result.output
    assert "return 'hi'" in result.output


def test_main_query_ownership_does_not_require_a_target(tmp_path):
    repo = tmp_path
    (repo / "main.py").write_text("x = 1\n")
    runner.invoke(app, ["scan", str(repo)])

    result = runner.invoke(app, ["query", "ownership", "--path", str(repo)])

    assert result.exit_code == 0


def test_main_query_missing_target_errors_clearly(tmp_path):
    repo = tmp_path
    (repo / "main.py").write_text("x = 1\n")
    runner.invoke(app, ["scan", str(repo)])

    result = runner.invoke(app, ["query", "imports", "--path", str(repo)])

    assert result.exit_code == 1
    assert "requires a target" in result.output


def test_main_query_without_evidence_errors_clearly(tmp_path):
    repo = tmp_path

    result = runner.invoke(app, ["query", "imports", "app/auth.py", "--path", str(repo)])

    assert result.exit_code == 1
    assert "aletheore scan" in result.output


def test_main_query_unknown_module_errors_clearly(tmp_path):
    repo = tmp_path
    (repo / "main.py").write_text("x = 1\n")
    runner.invoke(app, ["scan", str(repo)])

    result = runner.invoke(
        app, ["query", "imports", "does/not/exist.py", "--path", str(repo)]
    )

    assert result.exit_code == 1
    assert "not present in evidence" in result.output


def test_main_query_unknown_kind_errors_clearly(tmp_path):
    result = runner.invoke(app, ["query", "bogus-kind", "--path", str(tmp_path)])

    assert result.exit_code == 1
    assert "not a valid query kind" in result.output


def make_evidence_file(
    path: Path,
    findings: list[dict] | None = None,
    vulnerabilities: list[dict] | None = None,
    layer_violations: list[dict] | None = None,
) -> Path:
    evidence = {
        "repository": {"modules": [], "dependency_graph": {"nodes": [], "edges": []}},
        "git": {"total_commits": 0},
        "security": {
            "secrets": {
                "findings": findings or [],
                "history_scanned_commits": 0,
                "history_findings": [],
            },
            "dependency_vulnerabilities": {
                "checked": True,
                "reason": None,
                "findings": vulnerabilities or [],
            },
        },
        "architecture": {"layer_violations": {"violations": layer_violations or []}},
    }
    path.write_text(json.dumps(evidence))
    return path


def test_main_diff_shows_curated_diff_between_two_files(tmp_path):
    old_path = make_evidence_file(tmp_path / "old.json")
    new_path = make_evidence_file(
        tmp_path / "new.json",
        findings=[
            {
                "path": "a.py",
                "pattern": "aws_access_key_id",
                "match_preview": "AKIA...MNOP",
                "likely_placeholder": False,
            }
        ],
    )

    result = runner.invoke(app, ["diff", str(old_path), str(new_path)])

    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert len(parsed["secrets"]["new"]) == 1


def test_main_diff_full_flag_returns_raw_diff(tmp_path):
    old_path = make_evidence_file(tmp_path / "old.json")
    new_path = make_evidence_file(tmp_path / "new.json")

    result = runner.invoke(app, ["diff", str(old_path), str(new_path), "--full"])

    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert set(parsed.keys()) == {"added", "removed", "changed"}


def test_main_diff_fail_on_new_secrets_exits_1_for_a_real_secret(tmp_path):
    old_path = make_evidence_file(tmp_path / "old.json")
    new_path = make_evidence_file(
        tmp_path / "new.json",
        findings=[
            {
                "path": "a.py",
                "pattern": "aws_access_key_id",
                "match_preview": "AKIA...MNOP",
                "likely_placeholder": False,
            }
        ],
    )

    result = runner.invoke(
        app, ["diff", str(old_path), str(new_path), "--fail-on-new-secrets"]
    )

    assert result.exit_code == 1


def test_main_diff_fail_on_new_secrets_exits_0_for_a_placeholder_only(tmp_path):
    old_path = make_evidence_file(tmp_path / "old.json")
    new_path = make_evidence_file(
        tmp_path / "new.json",
        findings=[
            {
                "path": "tests/fixture.py",
                "pattern": "generic_credential_assignment",
                "match_preview": "test****...cret",
                "likely_placeholder": True,
            }
        ],
    )

    result = runner.invoke(
        app, ["diff", str(old_path), str(new_path), "--fail-on-new-secrets"]
    )

    assert result.exit_code == 0


def test_main_diff_fail_on_new_secrets_exits_0_for_an_accepted_baseline_secret(tmp_path):
    old_path = make_evidence_file(tmp_path / "old.json")
    new_path = make_evidence_file(
        tmp_path / "new.json",
        findings=[
            {
                "path": "app/aws_client.py",
                "pattern": "aws_access_key_id",
                "match_preview": "AKIA...MNOP",
                "likely_placeholder": False,
                "accepted": True,
            }
        ],
    )

    result = runner.invoke(
        app, ["diff", str(old_path), str(new_path), "--fail-on-new-secrets"]
    )

    assert result.exit_code == 0


def test_main_diff_fail_on_new_secrets_works_even_with_full_flag(tmp_path):
    old_path = make_evidence_file(tmp_path / "old.json")
    new_path = make_evidence_file(
        tmp_path / "new.json",
        findings=[
            {
                "path": "a.py",
                "pattern": "aws_access_key_id",
                "match_preview": "AKIA...MNOP",
                "likely_placeholder": False,
            }
        ],
    )

    result = runner.invoke(
        app, ["diff", str(old_path), str(new_path), "--full", "--fail-on-new-secrets"]
    )

    assert result.exit_code == 1
    parsed = json.loads(result.output)
    assert set(parsed.keys()) == {"added", "removed", "changed"}


def test_main_diff_fail_on_new_vulnerabilities_exits_1_for_a_new_vulnerability(tmp_path):
    old_path = make_evidence_file(tmp_path / "old.json")
    new_path = make_evidence_file(
        tmp_path / "new.json",
        vulnerabilities=[
            {
                "ecosystem": "PyPI",
                "package": "requests",
                "installed_version": "2.25.0",
                "advisory_id": "GHSA-xxxx",
                "summary": "...",
                "severity": [],
            }
        ],
    )

    result = runner.invoke(
        app, ["diff", str(old_path), str(new_path), "--fail-on-new-vulnerabilities"]
    )

    assert result.exit_code == 1


def test_main_diff_fail_on_new_vulnerabilities_exits_0_with_no_new_vulnerabilities(tmp_path):
    old_path = make_evidence_file(tmp_path / "old.json")
    new_path = make_evidence_file(tmp_path / "new.json")

    result = runner.invoke(
        app, ["diff", str(old_path), str(new_path), "--fail-on-new-vulnerabilities"]
    )

    assert result.exit_code == 0


def test_main_diff_fail_on_new_layer_violations_exits_1_for_a_new_violation(tmp_path):
    old_path = make_evidence_file(tmp_path / "old.json")
    new_path = make_evidence_file(
        tmp_path / "new.json",
        layer_violations=[
            {
                "from": "app/routes.py",
                "to": "app/db.py",
                "reason": "inner layer 'routes' imports outer layer 'db'",
            }
        ],
    )

    result = runner.invoke(
        app, ["diff", str(old_path), str(new_path), "--fail-on-new-layer-violations"]
    )

    assert result.exit_code == 1


def test_main_diff_fail_on_new_layer_violations_exits_0_with_no_new_violations(tmp_path):
    old_path = make_evidence_file(tmp_path / "old.json")
    new_path = make_evidence_file(tmp_path / "new.json")

    result = runner.invoke(
        app, ["diff", str(old_path), str(new_path), "--fail-on-new-layer-violations"]
    )

    assert result.exit_code == 0


def test_main_diff_fail_flags_combine_any_one_triggering_causes_exit_1(tmp_path):
    old_path = make_evidence_file(tmp_path / "old.json")
    new_path = make_evidence_file(
        tmp_path / "new.json",
        layer_violations=[
            {
                "from": "app/routes.py",
                "to": "app/db.py",
                "reason": "inner layer 'routes' imports outer layer 'db'",
            }
        ],
    )

    result = runner.invoke(
        app,
        [
            "diff",
            str(old_path),
            str(new_path),
            "--fail-on-new-secrets",
            "--fail-on-new-vulnerabilities",
            "--fail-on-new-layer-violations",
        ],
    )

    assert result.exit_code == 1


def test_main_diff_missing_file_errors_cleanly(tmp_path):
    old_path = make_evidence_file(tmp_path / "old.json")
    missing_path = tmp_path / "does_not_exist.json"

    result = runner.invoke(app, ["diff", str(old_path), str(missing_path)])

    assert result.exit_code == 1
    assert "not found" in result.output


def test_main_scan_saves_a_history_snapshot(tmp_path):
    repo = tmp_path
    (repo / "main.py").write_text("x = 1\n")

    runner.invoke(app, ["scan", str(repo), "--no-check-vulnerabilities"])

    history_files = list((repo / ".aletheore" / "history").glob("*.json"))
    assert len(history_files) == 1


def test_main_query_changes_reports_no_prior_snapshot_on_first_scan(tmp_path):
    repo = tmp_path
    (repo / "main.py").write_text("x = 1\n")
    runner.invoke(app, ["scan", str(repo), "--no-check-vulnerabilities"])

    result = runner.invoke(app, ["query", "changes", "--path", str(repo)])

    assert result.exit_code == 0
    assert "no prior snapshot" in result.output


def test_main_query_changes_reports_corrupt_snapshot(tmp_path):
    repo = tmp_path
    (repo / "main.py").write_text("x = 1\n")
    runner.invoke(app, ["scan", str(repo), "--no-check-vulnerabilities"])
    runner.invoke(app, ["scan", str(repo), "--no-check-vulnerabilities"])

    history_dir = repo / ".aletheore" / "history"
    oldest = sorted(history_dir.glob("*.json"))[0]
    oldest.write_text("{not valid json")

    result = runner.invoke(app, ["query", "changes", "--path", str(repo)])

    assert result.exit_code == 1
    assert "unreadable" in result.output


def test_main_query_changes_shows_a_real_diff_between_two_scans(tmp_path):
    repo = tmp_path
    (repo / "main.py").write_text("x = 1\n")
    runner.invoke(app, ["scan", str(repo), "--no-check-vulnerabilities"])

    (repo / "second.py").write_text("y = 2\n")
    runner.invoke(app, ["scan", str(repo), "--no-check-vulnerabilities"])

    result = runner.invoke(app, ["query", "changes", "--path", str(repo)])

    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed["aggregate_deltas"]["module_count"] == 1


def test_main_query_changes_full_flag_returns_raw_diff(tmp_path):
    repo = tmp_path
    (repo / "main.py").write_text("x = 1\n")
    runner.invoke(app, ["scan", str(repo), "--no-check-vulnerabilities"])
    runner.invoke(app, ["scan", str(repo), "--no-check-vulnerabilities"])

    result = runner.invoke(app, ["query", "changes", "--path", str(repo), "--full"])

    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert set(parsed.keys()) == {"added", "removed", "changed"}
