import io
import json
import sys

import httpx
import pytest
from typer.testing import CliRunner

from aletheore import cli, credentials, device_auth

runner = CliRunner()


@pytest.mark.parametrize("cmd", [["scan"], ["init"], ["watch"], ["mcp-install"], ["index"], ["dashboard"]])
def test_missing_path_is_a_clean_error(cmd, tmp_path):
    result = runner.invoke(cli.app, [*cmd, str(tmp_path / "nope")])
    assert result.exit_code == 1
    assert "does not exist" in result.output
    assert "Traceback" not in result.output


def test_file_path_is_rejected(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("x")
    result = runner.invoke(cli.app, ["scan", str(f)])
    assert result.exit_code == 1 and "not a directory" in result.output


def test_diff_directory_is_clean_error(tmp_path):
    assert cli._diff(str(tmp_path), str(tmp_path), False, False) == 1


@pytest.mark.parametrize("port", [0, 70000, -1])
def test_dashboard_rejects_bad_port(port, tmp_path):
    assert cli._dashboard(str(tmp_path), port) == 1


def test_port_check_survives_overflow():
    assert cli._port_is_available("127.0.0.1", 70000) is False


def test_k_and_debounce_validated(tmp_path):
    assert runner.invoke(cli.app, ["query", "secrets", "--k", "0"]).exit_code == 2
    assert runner.invoke(cli.app, ["watch", str(tmp_path), "--debounce", "-1"]).exit_code == 2


def test_jsonc_and_bom_configs_are_merged(tmp_path):
    cfg = tmp_path / "mcp.json"
    cfg.write_bytes(b'\xef\xbb\xbf{\n // c\n "servers": {"x": {"a": 1,},},\n /* b */\n}')
    msg = cli._write_json_mcp_client_config(cfg, "servers", {"command": "c"})
    assert msg.startswith("wrote")
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["servers"]["x"] == {"a": 1} and data["servers"]["aletheore"] == {"command": "c"}


def test_jsonc_keeps_slashes_inside_strings():
    assert cli._loads_jsonc('{"u": "http://x/*y*/"}') == {"u": "http://x/*y*/"}


def test_aletheore_command_finds_windows_exe(tmp_path, monkeypatch):
    exe = tmp_path / "aletheore.exe"
    exe.write_text("")
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python.exe"))
    assert cli._aletheore_command() == str(exe)


def test_confirm_treats_closed_stdin_as_no(monkeypatch):
    def boom(_):
        raise EOFError

    monkeypatch.setattr("builtins.input", boom)
    assert cli._confirm("? ") is False


def test_managed_audit_network_error_is_clean(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_scan", lambda *a: (0, {}, tmp_path / "air.json"))
    monkeypatch.setattr(cli, "infer_repo_full_name_from_cwd_git_remote", lambda cwd: None)

    def boom(*a, **k):
        raise httpx.ReadTimeout("slow")

    monkeypatch.setattr(cli, "run_managed_audit_request", boom)
    assert cli._managed_audit(str(tmp_path), "tok", None, None) == 1


def test_older_dev_build_is_not_an_update():
    class C:
        def get(self, *a, **k):
            return httpx.Response(200, json={"info": {"version": "0.9.1"}}, request=httpx.Request("GET", "http://x"))

    assert cli._check_for_update("0.9.2", C()) == "up to date"


def test_credentials_prompt_does_not_echo(tmp_path, monkeypatch):
    monkeypatch.delenv("K", raising=False)
    class Tty(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setattr(sys, "stdin", Tty("once\n"))
    monkeypatch.setattr(credentials.getpass, "getpass", lambda p: "secret")
    assert credentials.get_api_key("K", "p", tmp_path / "c.json") == "secret"


def test_corrupt_credentials_are_backed_up(tmp_path):
    path = tmp_path / "c.json"
    path.write_text("{not json")
    credentials._save_key("a", "1", path)
    assert json.loads(path.read_text())["a"] == "1"
    assert (tmp_path / "c.json.bak").read_text() == "{not json"


def test_shrinking_credentials_leave_valid_json(tmp_path):
    path = tmp_path / "c.json"
    credentials._save_key("a", "x" * 500, path)
    assert credentials.clear_api_key("a", path) is True
    assert json.loads(path.read_text()) == {}


def test_non_dict_credentials_ignored(tmp_path):
    path = tmp_path / "c.json"
    path.write_text("[1]")
    assert credentials.has_api_key("", "p", path) is False


@pytest.mark.parametrize(
    "url,expected",
    [
        ("ssh://git@github.com/o/r.git", "o/r"),
        ("https://user:tok@github.com/o/r.git", "o/r"),
        ("git@github.com:o/r.git", "o/r"),
        ("https://gitlab.com/o/r", None),
    ],
)
def test_remote_parsing(url, expected):
    class R:
        stdout = url + "\n"

    assert device_auth.infer_repo_full_name_from_cwd_git_remote(lambda *a, **k: R()) == expected


def test_watch_skips_ignored_dirs(tmp_path):
    from aletheore import watch

    for name in ("src", "node_modules", ".git", ".aletheore"):
        (tmp_path / name).mkdir()
    assert [p.name for p in watch._watchable_top_level_dirs(tmp_path)] == ["src"]


def test_windows_acl_helper_never_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("USERNAME", "u")
    monkeypatch.setattr(credentials.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
    credentials._restrict_windows_acl(tmp_path / "c.json")


def test_user_home_fallback_is_private_and_created(tmp_path, monkeypatch):
    from aletheore import user_paths

    def no_home():
        raise RuntimeError("no home")

    monkeypatch.setattr(user_paths.Path, "home", staticmethod(no_home))
    monkeypatch.setattr(user_paths.tempfile, "gettempdir", lambda: str(tmp_path))
    home = user_paths.user_home()
    assert home.is_dir() and home.parent == tmp_path


@pytest.mark.skipif(sys.platform == "win32", reason="symlink creation needs privileges on Windows")
def test_user_home_fallback_rejects_planted_symlink(tmp_path, monkeypatch):
    import os

    from aletheore import user_paths

    def no_home():
        raise RuntimeError("no home")

    target = tmp_path / "elsewhere"
    target.mkdir()
    os.symlink(target, tmp_path / f"aletheore-home-{os.getuid()}")
    monkeypatch.setattr(user_paths.Path, "home", staticmethod(no_home))
    monkeypatch.setattr(user_paths.tempfile, "gettempdir", lambda: str(tmp_path))
    assert user_paths.user_home() != tmp_path / f"aletheore-home-{os.getuid()}"
