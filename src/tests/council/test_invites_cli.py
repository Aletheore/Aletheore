"""The invitation commands, end to end between two people's folders on one fake server."""

import base64

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from aletheore.council import cli, invites
from aletheore.council.accounts import AccountService, SqliteAccountBackend
from aletheore.council.remote_store import RemoteRoomStore
from aletheore.council.server import create_app
from aletheore.council.sqlite_store import SqliteRoomStore

runner = CliRunner()
SERVER = "http://council.test"


class World:
    def __init__(self, tmp_path, monkeypatch):
        self.tmp, self.mp = tmp_path, monkeypatch
        self.service = AccountService(SqliteAccountBackend(tmp_path / "s.db"))
        app = create_app(SqliteRoomStore(tmp_path / "s.db"), {}, tmp_path / "b", accounts=self.service)
        self.client = TestClient(app, base_url=SERVER)
        monkeypatch.setattr(cli, "remote_store", lambda server, token: RemoteRoomStore(server, token, client=self.client))
        real_proof = invites.check_server_proves_host  # the host proof goes through the same in-process server as everything else
        monkeypatch.setattr(invites, "check_server_proves_host", lambda base, pin, http=None: real_proof(base, pin, http=http or self.client))
        monkeypatch.setenv("COUNCIL_SERVER", SERVER)
        monkeypatch.delenv("COUNCIL_TOKEN", raising=False)

    def as_person(self, name):
        self.mp.setenv("COUNCIL_HOME", str(self.tmp / f"home-{name}"))

    def run(self, *args):
        return runner.invoke(cli.app, list(args))

    def make_host(self):
        self.as_person("host")
        code = self.service.create_registration_code("hosty", can_host=True)
        assert self.run("register", "--server", SERVER, "--code", code, "--handle", "hosty").exit_code == 0
        store = cli.open_store()  # signs in with this folder's device key: no token needed
        store.create_room("r1", "hosty", title="Cache work")
        return store


@pytest.fixture
def w(tmp_path, monkeypatch):
    return World(tmp_path, monkeypatch)


def secret_text(link):
    return base64.urlsafe_b64encode(invites.parse_link(link).secret).decode().rstrip("=")


def test_open_store_signs_in_with_this_devices_key_after_register(w):
    store = w.make_host()
    assert store.whoami() == "hosty"
    w.as_person("stranger")
    with pytest.raises(PermissionError):
        cli.open_store()  # a folder with no registered device and no token


def test_invite_join_approve_flow_over_the_commands(w):
    w.make_host()
    out = w.run("invite-link", "--room", "r1", "--me", "hosty")
    assert out.exit_code == 0, out.output
    link = next(word for word in out.output.split() if word.startswith("http"))
    assert "#" in link and "one person" in out.output

    w.as_person("guest")
    approvals = []

    def approve_during_wait(_seconds):  # the host acts while the guest is waiting
        if approvals:
            return
        approvals.append(1)
        w.as_person("host")
        pending = cli.open_store().list_join_requests("r1")
        assert [p["handle"] for p in pending] == ["guesty"]
        shown = w.run("requests", "--room", "r1", "--me", "hosty")
        assert "guesty" in shown.output and pending[0]["words"] in shown.output
        done = w.run("approve-join", "--room", "r1", "--me", "hosty", pending[0]["request_id"])
        assert done.exit_code == 0, done.output
        w.as_person("guest")

    w.mp.setattr(cli.time, "sleep", approve_during_wait)
    res = w.run("join-link", link, "--handle", "guesty")
    assert res.exit_code == 0, res.output
    assert secret_text(link) not in res.output and link not in res.output  # the secret part is never printed back
    assert "words" in res.output.lower() and "approved" in res.output.lower()
    assert "waiting for the host to approve" in res.output.lower()  # said once, before the silent wait, right after the words
    assert cli.open_store().whoami() == "guesty"
    assert "guesty" in cli.open_store().room_info("r1").members


def test_join_link_reads_the_link_from_standard_input(w):
    w.make_host()
    link = next(word for word in w.run("invite-link", "--room", "r1", "--me", "hosty").output.split() if word.startswith("http"))
    w.as_person("guest")
    res = runner.invoke(cli.app, ["join-link", "--handle", "guesty", "--no-wait"], input=link + "\n")
    assert res.exit_code == 0, res.output
    assert "waiting" in res.output.lower() or "asked" in res.output.lower()
    assert secret_text(link) not in res.output


def test_reject_tells_the_guest_plainly(w):
    w.make_host()
    link = next(word for word in w.run("invite-link", "--room", "r1", "--me", "hosty").output.split() if word.startswith("http"))
    w.as_person("guest")

    def reject_during_wait(_seconds):
        w.as_person("host")
        req = cli.open_store().list_join_requests("r1")[0]
        assert w.run("reject-join", "--room", "r1", "--me", "hosty", req["request_id"]).exit_code == 0
        w.as_person("guest")

    w.mp.setattr(cli.time, "sleep", reject_during_wait)
    res = w.run("join-link", link, "--handle", "guesty")
    assert res.exit_code == 1 and "declined" in res.output.lower()
    assert not w.service.handle_exists("guesty")


def test_a_link_for_a_different_host_is_refused_before_anything_is_sent(w):
    w.make_host()
    link = next(word for word in w.run("invite-link", "--room", "r1", "--me", "hosty").output.split() if word.startswith("http"))
    parsed = invites.parse_link(link)
    forged = invites.build_link(parsed.server, parsed.invite_id, parsed.secret, "dev_" + "0" * 24)
    w.as_person("guest")
    res = w.run("join-link", forged, "--handle", "guesty", "--no-wait")
    assert res.exit_code == 1 and "not the host" in res.output.lower()
    w.as_person("host")
    assert cli.open_store().list_join_requests("r1") == []


def test_bad_links_give_one_clean_line(w):
    w.as_person("guest")
    res = w.run("join-link", "not a link", "--handle", "guesty")
    assert res.exit_code == 1 and "invitation link" in res.output and "Traceback" not in res.output


def test_only_the_host_can_make_links_and_decide(w):
    w.make_host()
    w.as_person("other")
    code = w.service.create_registration_code("otherp", can_host=True)
    assert w.run("register", "--server", SERVER, "--code", code, "--handle", "otherp").exit_code == 0
    assert w.run("invite-link", "--room", "r1", "--me", "otherp").exit_code == 1
    assert w.run("requests", "--room", "r1", "--me", "otherp").exit_code == 1


# ---- the link's address is out of date: the registry knows where the host is now ---------------------------------------------
def test_join_falls_back_to_the_registry_when_the_links_address_is_dead_and_never_to_the_wrong_host(monkeypatch, tmp_path):
    from aletheore.council import cli, invites
    from aletheore.council.invites import ParsedLink

    parsed = ParsedLink("https://old.example", "inv1", b"s" * 32, "dev_host")
    calls = []

    def proof(addr, host_id, http=None):
        calls.append(("proof", addr))
        if addr == "https://old.example":
            raise ValueError("could not reach the server to check it is the host's; try again")
        if addr == "https://impostor.example":
            raise ValueError("this server is not the host's")
        return True

    class Store:
        def __init__(self, addr):
            self.addr = addr

        def get_invite(self, inv):
            if self.addr == "https://old.example":
                raise ConnectionError("down")
            return {"host": {}}

    monkeypatch.setattr(invites, "check_server_proves_host", proof)
    monkeypatch.setattr(invites, "check_host_pin", lambda pinned, host: None)  # the pin itself is covered by its own tests
    monkeypatch.setattr(cli, "remote_store", lambda addr, token: Store(addr))
    monkeypatch.setenv("COUNCIL_HOME", str(tmp_path))
    monkeypatch.setenv("COUNCIL_REGISTRY", "https://registry.test")
    monkeypatch.setenv("COUNCIL_REGISTRY_KEY", "00" * 32)

    import aletheore.council.registry_client as rc

    answers = {"v": "https://new.example"}
    monkeypatch.setattr(rc.RegistryClient, "where_is", lambda self, host: answers["v"])
    assert cli._find_host(parsed) == "https://new.example"  # the link's address is dead; the registry's is verified and used
    answers["v"] = "https://impostor.example"
    with pytest.raises(ConnectionError, match="down"):
        cli._find_host(parsed)  # the registry's address fails the host-key proof: the ORIGINAL reason is raised, no one is joined
    answers["v"] = "https://old.example"
    with pytest.raises(ConnectionError):
        cli._find_host(parsed)  # the registry says the same dead place: nothing new to try
    monkeypatch.setenv("COUNCIL_REGISTRY", "off")
    with pytest.raises(ConnectionError):
        cli._find_host(parsed)  # no registry configured: just the honest failure


def test_an_invitation_carries_the_public_address_the_server_published_not_the_local_one(w, tmp_path, monkeypatch):
    """The host's own commands talk to its local server; the invitation has to name the address friends can reach."""
    w.make_host()
    (cli.home() / "public-address.txt").write_text("https://slot-042.aletheorecouncil.com")
    out = w.run("invite-link", "--room", "r1", "--me", "hosty").output
    link = next(word for word in out.split() if word.startswith("http"))
    assert link.startswith("https://slot-042.aletheorecouncil.com/j/")
    monkeypatch.setenv("COUNCIL_PUBLIC_ADDRESS", "https://override.example")
    assert next(word for word in w.run("invite-link", "--room", "r1", "--me", "hosty").output.split() if word.startswith("http")).startswith("https://override.example/j/")


def test_an_invitation_from_a_registered_host_device_pins_the_hosts_device(w):
    """Without the pin the guest cannot ask the registry where the host moved, so a recycled address would be a dead end."""
    w.make_host()
    out = w.run("invite-link", "--room", "r1", "--me", "hosty").output
    link = next(word for word in out.split() if word.startswith("http"))
    parsed = cli.invites.parse_link(link)
    assert parsed.host_device_id == cli.device_identity().device_id


def test_invite_link_on_a_token_mode_server_says_it_needs_accounts_not_a_bare_not_found(tmp_path, monkeypatch):
    app = create_app(SqliteRoomStore(tmp_path / "t.db"), {"tok": "hosty"}, tmp_path / "b")  # token mode: no accounts
    client = TestClient(app, base_url=SERVER)
    monkeypatch.setattr(cli, "remote_store", lambda server, token: RemoteRoomStore(server, token, client=client))
    monkeypatch.setenv("COUNCIL_SERVER", SERVER)
    monkeypatch.setenv("COUNCIL_TOKEN", "tok")
    monkeypatch.setenv("COUNCIL_HOME", str(tmp_path / "home-host"))
    RemoteRoomStore(SERVER, "tok", client=client).create_room("r1", "hosty", title="Cache work")
    result = runner.invoke(cli.app, ["invite-link", "--room", "r1", "--me", "hosty"])
    assert result.exit_code == 1
    assert "--accounts" in result.output
    assert "council invite" in result.output
    assert "not found: Not Found" not in result.output


def test_invite_link_warns_when_the_server_runs_as_a_different_device_than_the_host(w, tmp_path):
    from aletheore.council.identity import Keystore

    w.make_host()
    other = Keystore(tmp_path / "other-keys").load_or_create("other")
    app = create_app(SqliteRoomStore(tmp_path / "s.db"), {}, tmp_path / "b2", accounts=w.service, host_identity=other)
    w.client = TestClient(app, base_url=SERVER)  # same rooms and accounts, but the server signs as someone else
    res = w.run("invite-link", "--room", "r1", "--me", "hosty")
    assert res.exit_code == 0, res.output
    assert "different device" in res.output and "COUNCIL_HOME" in res.output
    assert "Invitation link for room r1" in res.output  # still made: the warning does not block the host


def test_invite_link_is_quiet_when_the_server_runs_as_the_host(w, tmp_path):
    w.make_host()
    app = create_app(SqliteRoomStore(tmp_path / "s.db"), {}, tmp_path / "b2", accounts=w.service, host_identity=cli.device_identity())
    w.client = TestClient(app, base_url=SERVER)
    res = w.run("invite-link", "--room", "r1", "--me", "hosty")
    assert res.exit_code == 0, res.output
    assert "different device" not in res.output


def test_rooms_works_without_me_when_this_device_already_knows_who_you_are(w):
    w.make_host()
    res = w.run("rooms")
    assert res.exit_code == 0, res.output
    assert "r1" in res.output and "hosty" in res.output
