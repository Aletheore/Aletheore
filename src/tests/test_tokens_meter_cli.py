import json
import io
from datetime import datetime, timezone
from pathlib import Path

import pytest
from rich.cells import cell_len
from rich.console import Console
from typer.testing import CliRunner

from aletheore.read_guard_setup import SETTINGS_RELATIVE_PATH, SIDECAR_NAME
from aletheore.token_meter import record_mcp_trim, record_read_guard
from aletheore.tokens_cli import tokens_app

runner = CliRunner()


@pytest.mark.parametrize("width,height", [(40, 20), (80, 24), (140, 45)])
def test_meter_fits_terminal_width_without_losing_status(tmp_path, width, height):
    from aletheore.meter_ui import render_meter
    from aletheore.token_meter import summarize

    output = io.StringIO()
    console = Console(file=output, width=width, height=height, color_system=None)
    console.print(render_meter(summarize(tmp_path), tmp_path, guard_configured=False,
                               profile_status="not active", width=width, height=height))
    text = output.getvalue()
    assert all(cell_len(line) <= width for line in text.splitlines())
    assert "No savings recorded yet" in text
    assert "Today" in text and "All time" in text
    assert "\x1b" not in text


def test_plain_meter_is_one_line_and_labels_unmeasured_sources(tmp_path: Path):
    result = runner.invoke(tokens_app, ["meter", "--path", str(tmp_path), "--plain"])
    assert result.exit_code == 0, result.output
    assert len(result.output.splitlines()) == 1
    assert "Aletheore meter" in result.output
    assert "unmeasured" in result.output
    assert "enable with --enable-read-guard" in result.output
    assert "Lean profile: not active; token savings unmeasured" in result.output
    assert "Aletheore guide" not in result.output
    assert not (tmp_path / ".aletheore").exists()


def test_default_meter_uses_the_package_style_and_explains_empty_state(tmp_path: Path):
    result = runner.invoke(tokens_app, ["meter", "--path", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "Token savings meter" in result.output
    assert "Evidence-grounded code intelligence" in result.output
    assert "No savings recorded yet" in result.output
    assert result.output.count("read savings unmeasured") == 1
    assert "settings.local.json" in result.output
    assert ".----." not in result.output
    assert "| o  o |" not in result.output
    assert (
        "Today" in result.output
        and "This week" in result.output
        and "All time" in result.output
    )
    assert "file bytes ÷ 4 or omitted characters ÷ 4" in result.output


def test_styled_meter_keeps_observed_counts_separate_from_estimates(tmp_path: Path):
    (tmp_path / ".aletheore").mkdir()
    record_read_guard(tmp_path, 9)
    record_mcp_trim(tmp_path, 120, 101)
    result = runner.invoke(tokens_app, ["meter", "--path", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "~3" in result.output and "~5" in result.output
    assert "9 file bytes" in result.output and "19 characters omitted" in result.output
    assert "No savings recorded yet" not in result.output


def test_meter_shows_measured_characters_separately_from_estimated_tokens(
    tmp_path: Path,
):
    (tmp_path / ".aletheore").mkdir()
    now = datetime.now(timezone.utc)
    assert record_read_guard(tmp_path, 9, occurred_at=now)
    assert record_mcp_trim(tmp_path, 120, 101, occurred_at=now)
    result = runner.invoke(tokens_app, ["meter", "--path", str(tmp_path), "--plain"])
    assert result.exit_code == 0, result.output
    assert "19 serialized characters omitted (measured)" in result.output
    assert "~5 tokens estimated (19 characters ÷ 4)" in result.output
    assert "~3 tokens estimated (9 file bytes ÷ 4)" in result.output


def test_regular_meter_does_not_write_settings_or_call_the_agent(
    tmp_path: Path, monkeypatch
):
    from aletheore import token_profile

    monkeypatch.setattr(
        token_profile,
        "run_audit",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("model call")),
    )
    result = runner.invoke(tokens_app, ["meter", "--path", str(tmp_path), "--plain"])
    assert result.exit_code == 0, result.output
    assert not (tmp_path / ".claude").exists()


def test_enable_read_guard_requires_confirmation_and_shows_exact_entries(
    tmp_path: Path,
):
    result = runner.invoke(
        tokens_app,
        ["meter", "--path", str(tmp_path), "--enable-read-guard"],
        input="no\n",
    )
    assert result.exit_code == 1
    assert "settings.local.json" in result.output
    assert '"PreToolUse"' in result.output and '"PostToolUse"' in result.output
    assert "--disable-read-guard" in result.output
    assert "cancelled; nothing was changed" in result.output
    assert not (tmp_path / SETTINGS_RELATIVE_PATH).exists()
    assert not (tmp_path / ".claude" / SIDECAR_NAME).exists()


def test_explicit_read_guard_setup_and_removal_are_reversible(tmp_path: Path):
    config = tmp_path / SETTINGS_RELATIVE_PATH
    config.parent.mkdir(parents=True)
    original = {
        "model": "sonnet",
        "hooks": {"PreToolUse": [{"matcher": "Edit", "hooks": []}]},
    }
    config.write_text(json.dumps(original))

    enabled = runner.invoke(
        tokens_app, ["meter", "--path", str(tmp_path), "--enable-read-guard", "--yes"]
    )
    assert enabled.exit_code == 0, enabled.output
    merged = json.loads(config.read_text())
    assert merged["model"] == "sonnet"
    assert len(merged["hooks"]["PreToolUse"]) == 2
    assert len(merged["hooks"]["PostToolUse"]) == 1

    disabled = runner.invoke(
        tokens_app, ["meter", "--path", str(tmp_path), "--disable-read-guard"]
    )
    assert disabled.exit_code == 0, disabled.output
    assert json.loads(config.read_text()) == original


def test_meter_reports_profile_configured_without_claiming_savings(tmp_path: Path):
    d = tmp_path / ".claude"
    d.mkdir()
    (d / "aletheore-tokens.json").write_text(
        json.dumps({"added": ["Monitor"], "created_file": True})
    )
    result = runner.invoke(tokens_app, ["meter", "--path", str(tmp_path), "--plain"])
    assert result.exit_code == 0, result.output
    assert "Lean profile: active; token savings unmeasured" in result.output
