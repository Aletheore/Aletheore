"""`council serve`: the short command, the alias, and that nothing is opened to the internet without consent."""

from typer.testing import CliRunner

from aletheore.council import cli, host_runtime


def test_serve_and_the_server_alias_are_both_commands():
    names = {c.name for c in cli.app.registered_commands}
    assert "serve" in names and "server" in names
    hidden = [c for c in cli.app.registered_commands if c.name == "server"]
    assert hidden and hidden[0].hidden


def run_serve(tmp_path, monkeypatch, args, answer=None, tty=False):
    monkeypatch.setenv("COUNCIL_HOME", str(tmp_path))
    started, opened = [], []
    monkeypatch.setattr(cli, "run_server", lambda *a, **k: started.append((a, k)))
    monkeypatch.setattr(cli, "_open_tunnel", lambda port, stops: opened.append(port) or "https://slot-001.aletheorecouncil.com")
    monkeypatch.setattr(host_runtime.WakeLock, "acquire", lambda self: False)
    monkeypatch.setattr(cli, "_ask_consent", lambda: answer)
    result = CliRunner().invoke(cli.app, ["serve", *args])
    return result, started, opened


def test_no_public_listener_without_consent_and_the_answer_is_remembered(tmp_path, monkeypatch):
    result, started, opened = run_serve(tmp_path, monkeypatch, [], answer=False)
    assert result.exit_code == 0 and started and opened == []  # said no: nothing opened
    assert host_runtime.Consent(tmp_path).get() is False
    result, started, opened = run_serve(tmp_path, monkeypatch, [])  # asked once; not again
    assert opened == []


def test_a_yes_opens_the_tunnel_prints_the_address_and_the_sponsor_line_once(tmp_path, monkeypatch):
    from aletheore.council.sponsor import SPONSOR

    result, started, opened = run_serve(tmp_path, monkeypatch, [], answer=True)
    assert opened and "https://slot-001.aletheorecouncil.com" in result.output.replace("\n", "")
    assert result.output.replace("\n", "").count("student developed open source project") == 1 and "sponsors/ArihantK15" in result.output.replace("\n", "")
    assert (tmp_path / "public-address.txt").read_text() == "https://slot-001.aletheorecouncil.com"
    assert host_runtime.Consent(tmp_path).get() is True


def test_local_never_asks_and_never_opens_a_tunnel(tmp_path, monkeypatch):
    result, started, opened = run_serve(tmp_path, monkeypatch, ["--local"], answer=True)
    assert opened == [] and started and host_runtime.Consent(tmp_path).get() is None
    assert "Make this room reachable" not in result.output


def test_a_non_local_bind_is_never_tunnelled(tmp_path, monkeypatch):
    host_runtime.Consent(tmp_path).set(True)
    result, started, opened = run_serve(tmp_path, monkeypatch, ["--host", "127.0.0.1", "--port", "9000", "--local"])
    assert opened == []


def test_no_tunnel_service_configured_says_so_plainly(monkeypatch, tmp_path):
    monkeypatch.setenv("COUNCIL_HOME", str(tmp_path))
    monkeypatch.setenv("COUNCIL_REGISTRY", "off")  # switched off: the plain "not open yet" answer
    monkeypatch.delenv("COUNCIL_REGISTRY_KEY", raising=False)
    out = []
    monkeypatch.setattr(cli.console, "print", lambda *a, **k: out.append(str(a[0])))
    assert cli._open_tunnel(8765, []) == ""
    assert "not open yet" in out[0] and "--local" in out[0]


def test_a_public_room_gets_the_default_file_cap_and_a_local_one_does_not(tmp_path, monkeypatch):
    monkeypatch.setenv("COUNCIL_HOME", str(tmp_path))
    seen = []
    monkeypatch.setattr(cli, "run_server", lambda db, bd, tokens, ret, host, port, settings=None, host_identity=None: seen.append(settings.file_transfer_cap_mb_per_day))
    monkeypatch.setattr(cli, "_open_tunnel", lambda port, stops: "https://slot-001.aletheorecouncil.com")
    monkeypatch.setattr(cli, "_ask_consent", lambda: True)
    monkeypatch.setattr(host_runtime.WakeLock, "acquire", lambda self: False)
    result = CliRunner().invoke(cli.app, ["serve"])
    assert seen == [5 * 1024] and "capped at 5 GB a day" in result.output.replace("\n", " ") and "Messages are not" in result.output.replace("\n", " ")
    local = CliRunner().invoke(cli.app, ["serve", "--local"])
    assert seen[-1] == 0 and "capped" not in local.output
    monkeypatch.setenv("COUNCIL_FILE_CAP_MB_PER_DAY", "100")
    CliRunner().invoke(cli.app, ["serve"])
    assert seen[-1] == 100  # an explicit choice is kept


def test_no_tunnel_program_and_no_yes_means_no_slot_is_taken(monkeypatch, tmp_path):
    monkeypatch.setenv("COUNCIL_HOME", str(tmp_path))
    monkeypatch.setenv("COUNCIL_REGISTRY", "https://registry.example")
    monkeypatch.setenv("COUNCIL_REGISTRY_KEY", "00" * 32)
    monkeypatch.setattr("aletheore.council.cloudflared_helper.find_cloudflared", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_ask_download", lambda q: False)
    claimed = []
    import aletheore.council.registry_client as rc

    monkeypatch.setattr(rc.RegistryClient, "claim", lambda self: claimed.append(1) or {})
    out = []
    monkeypatch.setattr(cli.console, "print", lambda *a, **k: out.append(str(a[0])))
    assert cli._open_tunnel(8765, []) == "" and claimed == []  # nothing was claimed from the pool
    assert any("not installed" in m for m in out)


def test_the_default_registry_is_the_real_one_and_its_key_is_pinned():
    assert cli.DEFAULT_REGISTRY_URL == "https://registry.aletheorecouncil.com"
    assert len(bytes.fromhex(cli.DEFAULT_REGISTRY_KEY)) == 32
    # a custom registry URL never inherits the default key: it must bring its own
