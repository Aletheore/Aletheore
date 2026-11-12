"""Client side of accounts: RemoteRoomStore.login, and the register / identity / admin commands."""

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from aletheore.council import cli
from aletheore.council.accounts import AccountService, SqliteAccountBackend
from aletheore.council.identity import Keystore
from aletheore.council.remote_store import RemoteRoomStore
from aletheore.council.server import create_app, parse_tokens
from aletheore.council.sqlite_store import SqliteRoomStore

runner = CliRunner()


class Clock:
    now = 1_000_000.0

    def __call__(self):
        return self.now


@pytest.fixture
def world(tmp_path, monkeypatch):
    clock = Clock()
    monkeypatch.setenv("COUNCIL_HOME", str(tmp_path / "home"))
    service = AccountService(SqliteAccountBackend(tmp_path / "server.db"), clock=clock)
    app = create_app(SqliteRoomStore(tmp_path / "server.db"), {}, tmp_path / "bundles", clock=clock, accounts=service)
    client = TestClient(app)
    monkeypatch.setattr(cli, "remote_store", lambda server, token: RemoteRoomStore(server, token, client=client))
    return service, client, clock, tmp_path


def test_remote_store_registers_logs_in_and_signs_back_in_when_the_session_expires(world):
    service, client, clock, tmp_path = world
    ident = Keystore(tmp_path / "k").load_or_create("device")
    store = RemoteRoomStore("http://x", "", client=client)
    code = service.create_registration_code("alice", can_host=True)
    assert store.register(code, "alice", ident)["handle"] == "alice"
    assert store.login(ident) == "alice"
    assert store.whoami() == "alice"
    clock.now += 13 * 3600  # the 12 hour session is over
    assert store.whoami() == "alice"  # one quiet re-login, then the request goes through
    store.create_room("r1", "alice")
    assert store.room_info("r1").host == "alice"


def test_a_revoked_device_cannot_sign_back_in(world):
    service, client, clock, tmp_path = world
    ident = Keystore(tmp_path / "k").load_or_create("device")
    store = RemoteRoomStore("http://x", "", client=client)
    store.register(service.create_registration_code("alice"), "alice", ident)
    store.login(ident)
    service.revoke_device(ident.device_id)
    with pytest.raises(PermissionError):
        store.whoami()


def test_serve_needs_tokens_unless_accounts_are_on():
    with pytest.raises(ValueError):
        parse_tokens([], None)
    assert parse_tokens([], None, allow_empty=True) == {}


def test_identity_show_prints_the_public_name_and_safety_words_never_a_key(world):
    res = runner.invoke(cli.app, ["identity", "show"])
    assert res.exit_code == 0, res.output
    assert "dev_" in res.output and len(res.output.split("words:")[1].split()[0].split("-")) == 4
    ident = Keystore(world[3] / "home" / "keys").load_or_create("device")
    assert ident._sign_sk.hex() not in res.output and ident._box_sk.hex() not in res.output  # noqa: SLF001
    assert runner.invoke(cli.app, ["identity", "show"]).output == res.output  # the same device every time


def test_admin_add_host_prints_a_working_one_time_code(world):
    service, client, clock, tmp_path = world
    res = runner.invoke(cli.app, ["admin", "add-host", "alice", "--db", str(tmp_path / "server.db")])
    assert res.exit_code == 0, res.output
    code = next(w for w in res.output.split() if w.startswith("cr_"))
    ident = Keystore(tmp_path / "k").load_or_create("device")
    assert service.redeem_registration_code(code, ident.public(), "alice").can_host is True
    guest = runner.invoke(cli.app, ["admin", "add-host", "bobby", "--guest", "--db", str(tmp_path / "server.db")])
    gcode = next(w for w in guest.output.split() if w.startswith("cr_"))
    assert service.redeem_registration_code(gcode, Keystore(tmp_path / "k2").load_or_create("d").public(), "bobby").can_host is False
    bad = runner.invoke(cli.app, ["admin", "add-host", "Not Valid", "--db", str(tmp_path / "server.db")])
    assert bad.exit_code == 1 and "handle" in bad.output


def test_register_command_registers_this_device(world):
    service, client, clock, tmp_path = world
    code = service.create_registration_code("alice", can_host=True)
    res = runner.invoke(cli.app, ["register", "--server", "http://x", "--code", code, "--handle", "alice"])
    assert res.exit_code == 0, res.output
    assert "alice" in res.output
    ident = Keystore(tmp_path / "home" / "keys").load_or_create("device")
    assert service.get_device(ident.device_id).handle == "alice"
    again = runner.invoke(cli.app, ["register", "--server", "http://x", "--code", code, "--handle", "alice"])
    assert again.exit_code == 1  # a used code is refused with one clean line, not a traceback
    assert "Traceback" not in again.output


def test_identity_export_then_import_moves_the_device_key_with_a_passphrase(tmp_path, monkeypatch):
    monkeypatch.setenv("COUNCIL_HOME", str(tmp_path / "home1"))
    shown = runner.invoke(cli.app, ["identity", "show"]).output
    out = tmp_path / "device.council-key"
    res = runner.invoke(cli.app, ["identity", "export", str(out)], input="correct horse\ncorrect horse\n")
    assert res.exit_code == 0, res.output
    assert out.exists() and "correct horse" not in res.output
    assert "sign_sk" not in out.read_text(errors="ignore")  # encrypted, not readable
    monkeypatch.setenv("COUNCIL_HOME", str(tmp_path / "home2"))
    res = runner.invoke(cli.app, ["identity", "import", str(out)], input="correct horse\n")
    assert res.exit_code == 0, res.output
    assert runner.invoke(cli.app, ["identity", "show"]).output == shown  # the same device on the new machine


def test_identity_export_refuses_mismatched_passphrases_and_existing_files(tmp_path, monkeypatch):
    monkeypatch.setenv("COUNCIL_HOME", str(tmp_path / "home"))
    runner.invoke(cli.app, ["identity", "show"])
    out = tmp_path / "k"
    assert runner.invoke(cli.app, ["identity", "export", str(out)], input="a\nb\n").exit_code != 0
    assert not out.exists()
    out.write_bytes(b"precious")
    res = runner.invoke(cli.app, ["identity", "export", str(out)], input="a\na\n")
    assert res.exit_code == 1 and out.read_bytes() == b"precious"


def test_identity_import_wrong_passphrase_and_existing_key_are_clean_refusals(tmp_path, monkeypatch):
    monkeypatch.setenv("COUNCIL_HOME", str(tmp_path / "home1"))
    runner.invoke(cli.app, ["identity", "show"])
    out = tmp_path / "k"
    runner.invoke(cli.app, ["identity", "export", str(out)], input="pw\npw\n")
    monkeypatch.setenv("COUNCIL_HOME", str(tmp_path / "home2"))
    bad = runner.invoke(cli.app, ["identity", "import", str(out)], input="nope\n")
    assert bad.exit_code == 1 and "Traceback" not in bad.output and "passphrase" in bad.output
    assert runner.invoke(cli.app, ["identity", "import", str(out)], input="pw\n").exit_code == 0
    again = runner.invoke(cli.app, ["identity", "import", str(out)], input="pw\n")  # would overwrite
    assert again.exit_code == 1 and "Traceback" not in again.output
    assert runner.invoke(cli.app, ["identity", "import", str(tmp_path / "missing")], input="pw\n").exit_code == 1
