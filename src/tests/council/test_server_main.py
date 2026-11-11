import json

import pytest

from aletheore.council import server_main


def test_entry_point_reads_settings_and_tokens_from_the_environment_and_runs(tmp_path, monkeypatch):
    tf = tmp_path / "tokens.json"
    tf.write_text(json.dumps({"alice": "tok-a"}))
    seen = {}
    monkeypatch.setattr(server_main, "run_server", lambda db, bundle_dir, tokens, retention, host, port, settings=None: seen.update(
        db=db, bundle_dir=bundle_dir, tokens=tokens, host=host, port=port, settings=settings))
    monkeypatch.setenv("COUNCIL_TOKEN_FILE", str(tf))
    monkeypatch.setenv("COUNCIL_HOST", "0.0.0.0")
    monkeypatch.setenv("COUNCIL_ALLOW_PUBLIC", "1")
    monkeypatch.setenv("COUNCIL_PORT", "9000")
    monkeypatch.setenv("COUNCIL_DB", str(tmp_path / "s.db"))
    monkeypatch.setenv("COUNCIL_BUNDLE_DIR", str(tmp_path / "b"))
    assert server_main.main() == 0
    assert seen["tokens"] == {"tok-a": "alice"} and seen["host"] == "0.0.0.0" and seen["port"] == 9000


def test_entry_point_refuses_a_public_bind_without_the_flag_and_says_why(tmp_path, monkeypatch, capsys):
    tf = tmp_path / "tokens.json"
    tf.write_text(json.dumps({"alice": "tok-a"}))
    monkeypatch.setattr(server_main, "run_server", lambda *a, **k: pytest.fail("must not start"))
    monkeypatch.setenv("COUNCIL_TOKEN_FILE", str(tf))
    monkeypatch.setenv("COUNCIL_HOST", "0.0.0.0")
    monkeypatch.delenv("COUNCIL_ALLOW_PUBLIC", raising=False)
    assert server_main.main() == 2
    assert "allow-public" in capsys.readouterr().err


def test_entry_point_fails_clearly_without_tokens_and_never_prints_them(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(server_main, "run_server", lambda *a, **k: pytest.fail("must not start"))
    monkeypatch.delenv("COUNCIL_TOKEN_FILE", raising=False)
    assert server_main.main() == 2
    assert "token" in capsys.readouterr().err.lower()
    tf = tmp_path / "t.json"
    tf.write_text(json.dumps({"alice": "super-secret-token-value"}))
    monkeypatch.setenv("COUNCIL_TOKEN_FILE", str(tf))
    monkeypatch.setenv("COUNCIL_STORE", "postgres")  # no DSN: a settings error
    monkeypatch.delenv("COUNCIL_PG_DSN", raising=False)
    assert server_main.main() == 2
    out = capsys.readouterr()
    assert "super-secret-token-value" not in out.out + out.err
