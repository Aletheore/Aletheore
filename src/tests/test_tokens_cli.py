import json

import typer
from typer.testing import CliRunner

from aletheore import token_profile as tp
from aletheore.tokens_cli import tokens_app

app = typer.Typer()
app.add_typer(tokens_app, name="tokens")
runner = CliRunner()


def test_profile_shows_the_change_and_cancels_without_a_yes(tmp_path):
    r = runner.invoke(app, ["tokens", "profile", "core", "--project", str(tmp_path)], input="no\n")
    assert r.exit_code == 1 and "Monitor" in r.output and "cancelled" in r.output
    assert not (tmp_path / ".claude").exists()


def test_profile_applies_after_yes_and_off_undoes_it(tmp_path):
    r = runner.invoke(app, ["tokens", "profile", "core", "--project", str(tmp_path)], input="yes\n")
    assert r.exit_code == 0
    deny = json.loads((tmp_path / ".claude" / "settings.json").read_text())["permissions"]["deny"]
    assert "Monitor" in deny and "Task" not in deny and "ToolSearch" not in deny
    r = runner.invoke(app, ["tokens", "profile", "off", "--project", str(tmp_path)])
    assert r.exit_code == 0 and not (tmp_path / ".claude" / "settings.json").exists()


def test_profile_reports_a_broken_settings_file_and_leaves_it_alone(tmp_path):
    d = tmp_path / ".claude"
    d.mkdir()
    (d / "settings.json").write_text("{oops")
    r = runner.invoke(app, ["tokens", "profile", "core", "--project", str(tmp_path), "--yes"])
    assert r.exit_code == 1 and "not valid JSON" in r.output and (d / "settings.json").read_text() == "{oops"


def test_audit_reports_floor_and_comparison(tmp_path, monkeypatch):
    calls = []

    def fake_run_audit(project, deny=None, runner=None):
        calls.append(deny)
        return tp.Audit(tools=["Bash", "Monitor", "Task"], input_side_tokens=1000 if deny is None else 400, cost_usd=0.005)

    monkeypatch.setattr(tp, "run_audit", fake_run_audit)
    r = runner.invoke(app, ["tokens", "audit", "--project", str(tmp_path), "--compare", "core", "--yes"])
    assert r.exit_code == 0 and "1,000" in r.output and "400" in r.output and "60% lower" in r.output
    assert calls == [None, ["Monitor"]]


def test_audit_rejects_an_unknown_compare_profile_before_spending_anything(tmp_path, monkeypatch):
    # Flash Review finding: --compare was validated only after the first billable request had already run.
    monkeypatch.setattr(tp, "run_audit", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not run")))
    r = runner.invoke(app, ["tokens", "audit", "--project", str(tmp_path), "--compare", "turbo", "--yes"])
    assert r.exit_code == 1 and "unknown profile" in r.output


def test_audit_asks_before_spending(tmp_path, monkeypatch):
    monkeypatch.setattr(tp, "run_audit", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not run")))
    r = runner.invoke(app, ["tokens", "audit", "--project", str(tmp_path)], input="no\n")
    assert r.exit_code == 1


def test_audit_failure_is_a_clear_error(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise tp.ProfileError("`claude` was not found on PATH")

    monkeypatch.setattr(tp, "run_audit", boom)
    r = runner.invoke(app, ["tokens", "audit", "--project", str(tmp_path), "--yes"])
    assert r.exit_code == 1 and "not found" in r.output


def test_tokens_is_registered_on_the_main_cli():
    from aletheore.cli import app as main_app

    r = runner.invoke(main_app, ["tokens", "--help"])
    assert r.exit_code == 0 and "audit" in r.output and "profile" in r.output
