"""Stage 3 end to end: what a person types, through the real commands, against an in-process server, with the server's own
database inspected afterwards to show what it could and could not see."""

import json
import sqlite3
import subprocess

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from aletheore.council import cli, crypto
from aletheore.council.accounts import AccountService, SqliteAccountBackend
from aletheore.council.approval import decide_plan, set_autonomy
from aletheore.council.bundle_store import LocalBundleStore
from aletheore.council.conductor import Conductor
from aletheore.council.daemon import WorkerDaemon
from aletheore.council.dispatcher import Dispatcher, send_message
from aletheore.council.e2e_store import E2ERoomStore, EncryptedBundleStore
from aletheore.council.envelope import Keyring
from aletheore.council.identity import Keystore
from aletheore.council.remote_store import RemoteRoomStore
from aletheore.council.room import Room
from aletheore.council.runners import ScriptedRunner
from aletheore.council.server import create_app
from aletheore.council.sqlite_store import SqliteRoomStore
from aletheore.council.state import Area, load_state
from aletheore.council.store import read_all

runner = CliRunner()
SERVER = "http://council.test"


def git(repo, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


def make_repo(path):
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    for d in ("redis", "schema"):
        (path / d).mkdir()
        (path / d / "f.txt").write_text("base\n")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "init")
    return path


class CliWorld:
    """A server with accounts and one folder (COUNCIL_HOME) per person, so each person's commands run as them."""

    def __init__(self, tmp_path, monkeypatch, accounts=True, tokens=None):
        self.tmp, self.mp = tmp_path, monkeypatch
        self.db = tmp_path / "server.db"
        self.service = AccountService(SqliteAccountBackend(self.db)) if accounts else None
        app = create_app(SqliteRoomStore(self.db), tokens or {}, tmp_path / "server-bundles", accounts=self.service)
        self.client = TestClient(app, base_url=SERVER)
        monkeypatch.setattr(cli, "remote_store", lambda server, token: RemoteRoomStore(server, token, client=self.client, backoff_s=0))
        from aletheore.council import invites

        real_proof = invites.check_server_proves_host  # the host proof goes through the same in-process server as everything else
        monkeypatch.setattr(invites, "check_server_proves_host", lambda base, pin, http=None: real_proof(base, pin, http=http or self.client))
        monkeypatch.setenv("COUNCIL_SERVER", SERVER)
        monkeypatch.delenv("COUNCIL_TOKEN", raising=False)
        monkeypatch.setattr(cli.time, "sleep", lambda s: None)
        self.repo = make_repo(tmp_path / "host-repo")

    def as_person(self, name):
        self.mp.setenv("COUNCIL_HOME", str(self.tmp / f"home-{name}"))
        return self

    def run(self, *args, ok=True, **kw):
        res = runner.invoke(cli.app, [str(a) for a in args], **kw)
        if ok:
            assert res.exit_code == 0, res.output
        return res

    def register(self, name, handle, can_host=False):
        self.as_person(name)
        code = self.service.create_registration_code(handle, can_host=can_host)
        self.run("register", "--server", SERVER, "--code", code, "--handle", handle)

    def raw_events(self):
        with sqlite3.connect(self.db) as c:
            return c.execute("SELECT seq, type, thread_id, sender_agent, body FROM events ORDER BY seq").fetchall()

    def raw_dump(self):
        return repr(self.raw_events()) + repr(sqlite3.connect(self.db).execute("SELECT * FROM events").fetchall())


def link_from(output):
    return next(w for w in output.split() if w.startswith("http"))


@pytest.fixture
def cw(tmp_path, monkeypatch):
    return CliWorld(tmp_path, monkeypatch)


def build_room_with_a_guest(cw):
    """Host registers, creates a room, invites a guest, approves them; the guest has their own clone. Returns the room id."""
    cw.register("host", "hosty", can_host=True)
    room = cw.run("start", "--repo", cw.repo, "--host", "hosty", "--title", "Cache work").output.strip().split()[-1]
    cw.run("area", "redis", "--room", room, "--glob", "redis/**", "--owner", "hosty", "--agent", "hosty-codex")
    link = link_from(cw.run("invite-link", "--room", room, "--me", "hosty").output)
    cw.as_person("guest")
    guest_repo = cw.tmp / "guest-repo"
    git(cw.tmp, "clone", "-q", str(cw.repo), str(guest_repo))
    asked = cw.run("join-link", link, "--handle", "guesty", "--no-wait")
    assert "safety words" in asked.output
    cw.as_person("host")
    request_id = cw.run("requests", "--room", room, "--me", "hosty").output.split()[0]
    cw.run("approve-join", request_id, "--room", room, "--me", "hosty")
    cw.as_person("guest")
    cw.run("join", "--room", room, "--me", "guesty", "--repo", guest_repo)
    return room, guest_repo


def test_full_flow_register_create_invite_join_approve_ask_and_discuss(cw):
    room, _ = build_room_with_a_guest(cw)
    assert cli.open_store().room_info(room).crypto == "e2e-v1"  # rooms on an accounts server are sealed by default
    cw.run("say", "the secret launch plan is blue", "--room", room, "--me", "guesty")
    cw.run("ask", "please add the session cache", "--room", room, "--me", "guesty")
    cw.run("discuss", "@all what do you think about caching?", "--room", room, "--me", "guesty")
    guest_view = cw.run("status", "--room", room).output
    assert "redis" in guest_view  # the guest opened the host's events: the keys arrived with the approval
    cw.as_person("host")
    host_view = cw.run("status", "--room", room).output
    assert "the secret launch plan is blue" in host_view and "please add the session cache" in host_view
    assert "Cache work" in cw.run("rooms", "--me", "hosty").output
    # what the server could see: nothing but envelopes, and none of the words
    rows = cw.raw_events()
    assert rows and {r[1] for r in rows} == {"envelope"} and all(r[3] is None for r in rows)
    dump = cw.raw_dump()
    for secret in ("launch plan", "session cache", "caching", "redis", "hosty-codex", "room_created", "Cache work".lower()):
        assert secret not in dump, dump[max(0, dump.find(secret) - 100):dump.find(secret) + 100]


def test_a_guest_who_is_not_approved_sees_nothing(cw):
    cw.register("host", "hosty", can_host=True)
    room = cw.run("start", "--repo", cw.repo, "--host", "hosty").output.strip().split()[-1]
    link = link_from(cw.run("invite-link", "--room", room, "--me", "hosty").output)
    cw.as_person("guest")
    cw.run("join-link", link, "--handle", "guesty", "--no-wait")
    res = cw.run("status", "--room", room, ok=False)
    assert res.exit_code != 0 and "Traceback" not in res.output  # a clean refusal, not a crash


def test_remove_member_then_room_continues_for_the_rest(cw):
    room, _ = build_room_with_a_guest(cw)
    cw.run("say", "before", "--room", room, "--me", "guesty")
    cw.as_person("host")
    removed = cw.run("remove", "guesty", "--room", room, "--me", "hosty")
    assert "guesty" in removed.output
    cw.run("say", "after the removal, only the host reads this", "--room", room, "--me", "hosty")
    assert "after the removal" in cw.run("status", "--room", room).output
    assert "after the removal" not in cw.raw_dump()
    cw.as_person("guest")
    refused = cw.run("say", "let me back in", "--room", room, "--me", "guesty", ok=False)
    assert refused.exit_code != 0 and "Traceback" not in refused.output
    assert cw.run("status", "--room", room, ok=False).exit_code != 0
    epochs = {json.loads(r[4])["epoch"] for r in cw.raw_events()}
    assert epochs == {1, 2}  # the room moved to a new key epoch


def test_only_the_host_can_remove_people(cw):
    room, _ = build_room_with_a_guest(cw)
    cw.as_person("guest")
    res = cw.run("remove", "hosty", "--room", room, "--me", "guesty", ok=False)
    assert res.exit_code != 0 and "host" in res.output


def test_a_plain_room_can_still_be_requested_on_an_accounts_server(cw):
    cw.register("host", "hosty", can_host=True)
    room = cw.run("start", "--repo", cw.repo, "--host", "hosty", "--crypto", "none").output.strip().split()[-1]
    cw.run("say", "readable by the server", "--room", room, "--me", "hosty")
    assert "readable by the server" in cw.raw_dump()
    assert {r[1] for r in cw.raw_events()} == {"system", "question"}
    bad = cw.run("start", "--repo", cw.repo, "--host", "hosty", "--crypto", "rot13", ok=False)
    assert bad.exit_code != 0


def test_stage_2_lan_flow_with_a_token_file_still_passes_unchanged(tmp_path, monkeypatch):
    cw = CliWorld(tmp_path, monkeypatch, accounts=False, tokens={"tok-alice": "alice", "tok-bob": "bob"})
    monkeypatch.setenv("COUNCIL_HOME", str(tmp_path / "home-alice"))
    monkeypatch.setenv("COUNCIL_TOKEN", "tok-alice")
    room = cw.run("start", "--repo", cw.repo, "--host", "alice").output.strip().split()[-1]
    cw.run("area", "redis", "--room", room, "--glob", "redis/**", "--owner", "alice", "--agent", "alice-codex")
    cw.run("invite", "bob", "--room", room, "--me", "alice")
    bob_repo = tmp_path / "bob-repo"
    git(tmp_path, "clone", "-q", str(cw.repo), str(bob_repo))
    monkeypatch.setenv("COUNCIL_HOME", str(tmp_path / "home-bob"))
    monkeypatch.setenv("COUNCIL_TOKEN", "tok-bob")
    cw.run("join", "--room", room, "--me", "bob", "--repo", bob_repo)
    cw.run("say", "hello over the LAN", "--room", room, "--me", "bob")
    assert "hello over the LAN" in cw.run("status", "--room", room).output
    assert "hello over the LAN" in cw.raw_dump() and "envelope" not in {r[1] for r in cw.raw_events()}  # a plain room, as before
    asking = cw.run("start", "--repo", cw.repo, "--host", "alice", "--crypto", "e2e", ok=False)
    assert asking.exit_code != 0 and "account" in asking.output.lower()  # a token server cannot do end-to-end rooms


def test_setup_guides_a_first_run(cw):
    cw.as_person("host")
    first = cw.run("setup", "--server", SERVER)
    assert "Device:" in first.output and "not registered" in first.output.lower() and "add-host" in first.output
    code = cw.service.create_registration_code("hosty", can_host=True)
    done = cw.run("setup", "--server", SERVER, "--code", code, "--handle", "hosty")
    assert "registered" in done.output.lower()
    again = cw.run("setup", "--server", SERVER)
    assert "signed in as hosty" in again.output.lower()


# ---- a room run by the planner and the conductor, with the server's store sealed end to end ------------------------------
@pytest.fixture
def e2e_world(tmp_path):
    a = make_repo(tmp_path / "mac repo")
    b = tmp_path / "win repo"
    git(tmp_path, "clone", "-q", str(a), str(b))
    keys = Keystore(tmp_path / "ids")
    alice_id, bob_id = keys.load_or_create("alice"), keys.load_or_create("bob")
    members = {"alice": [alice_id.public()], "bob": [bob_id.public()]}
    key = crypto.random_bytes(32)
    inner = SqliteRoomStore(tmp_path / "room.db")
    ring_a, ring_b = Keyring({1: key}), Keyring({1: key})
    store_a = E2ERoomStore(inner, alice_id, ring_a, lambda room: members)
    store_b = E2ERoomStore(inner, bob_id, ring_b, lambda room: members)
    alice_room = Room.create(store_a, a, "alice", tmp_path / "state_alice", mode="autonomous", crypto="e2e-v1")
    alice_room.define_area(Area("redis", ["redis/**"], "alice", "codex"))
    alice_room.define_area(Area("schema", ["schema/**"], "bob", "claude"))
    store_a.add_member(alice_room.room_id, "bob")
    bob_room = Room.open_remote(store_b, alice_room.room_id, b, tmp_path / "state_bob")
    raw = LocalBundleStore(tmp_path / "bundles", opaque=True)
    vault_a = EncryptedBundleStore(raw, ring_a, alice_room.room_id)
    vault_b = EncryptedBundleStore(raw, ring_b, alice_room.room_id)
    return dict(inner=inner, alice_store=store_a, bob_store=store_b, alice=alice_room, bob=bob_room, a=a, b=b, vault_a=vault_a,
                vault_b=vault_b, tmp=tmp_path, db=tmp_path / "room.db")


def plan_json(reply, *items):
    return json.dumps({"reply": reply, "tasks": list(items)})


def task(area, text):
    return {"kind": "task", "area": area, "instruction": text}


def sealed_only(w):
    with sqlite3.connect(w["db"]) as c:
        return {r[0] for r in c.execute("SELECT type FROM events")}


def test_autonomous_room_runs_over_an_encrypted_server_with_scripted_agents(e2e_world):
    w = e2e_world
    rid = w["alice"].room_id
    set_autonomy(w["alice_store"], rid, "alice", True)
    send_message(w["alice_store"], rid, "alice", "build the cache and the table", goal=True)
    planner = ScriptedRunner(texts=[plan_json("two tasks", task("redis", "write the cache"), task("schema", "write the table")),
                                    json.dumps({"done": True, "reply": "both written"})])
    dispatcher = Dispatcher(w["alice_store"], w["alice"], "alice", planner, blobs=w["vault_a"], agent_runner=lambda a: ScriptedRunner(final_text="ok"))
    conductor = Conductor(dispatcher, max_rounds=4, max_steps=16)
    alice_worker = WorkerDaemon(w["alice_store"], w["alice"], "alice", {"codex": ScriptedRunner(final_text="cache built", writes={"redis/c.py": "1\n"})},
                                bundles=w["vault_a"])
    bob_worker = WorkerDaemon(w["bob_store"], w["bob"], "bob", {"claude": ScriptedRunner(final_text="table built", writes={"schema/t.sql": "1\n"})},
                              bundles=w["vault_b"])
    for _ in range(8):
        conductor.run_once()
        alice_worker.run_once()
        bob_worker.run_once()
        dispatcher.wait_idle()
    state = load_state(w["alice_store"], rid)
    assert state.finished == "done" and "both written" in state.finish_note
    assert {t.status for t in state.tasks.values()} == {"done"} and len(state.tasks) == 2
    assert sealed_only(w) == {"envelope"}
    assert load_state(w["bob_store"], rid).finished == "done"  # bob's own reading agrees
    # the work really travelled: both branches were published as sealed bundles
    published = [e for e in read_all(w["alice_store"], rid) if e.body.get("subtype") == "branch_published"]
    assert {e.body["participant"] for e in published} == {"alice", "bob"}
    sealed = list((w["tmp"] / "bundles" / rid).glob("*.bundle"))
    assert len(sealed) >= 2 and all(b"PACK" not in f.read_bytes() for f in sealed)  # the workers' branches and the host's integration branch


def test_planner_plan_approval_and_dispatch_over_e2e(e2e_world):
    w = e2e_world
    rid = w["alice"].room_id
    with w["inner"]._connect() as conn:  # noqa: SLF001  this room is interactive: the host approves plans by hand
        conn.execute("UPDATE rooms SET mode='interactive' WHERE room_id=?", (rid,))
    thread = send_message(w["bob_store"], rid, "bob", "add user accounts")
    planner = ScriptedRunner(texts=[plan_json("splitting it", task("schema", "users table"), task("redis", "session cache"))])
    dispatcher = Dispatcher(w["alice_store"], w["alice"], "alice", planner, blobs=w["vault_a"], agent_runner=lambda a: ScriptedRunner(final_text="ok"))
    dispatcher.run_once()
    dispatcher.wait_idle()
    state = load_state(w["bob_store"], rid)  # bob, a different identity, sees the host's plan
    (plan_id,) = state.plans
    plan = state.plans[plan_id]
    assert plan.status == "pending" and plan.requested_by == "bob" and plan.thread_id == thread
    decide_plan(w["alice_store"], rid, plan_id, "alice", True)
    dispatcher.run_once()
    dispatcher.wait_idle()
    alice_worker = WorkerDaemon(w["alice_store"], w["alice"], "alice", {"codex": ScriptedRunner(final_text="cache", writes={"redis/c.py": "1\n"})}, bundles=w["vault_a"])
    bob_worker = WorkerDaemon(w["bob_store"], w["bob"], "bob", {"claude": ScriptedRunner(final_text="table", writes={"schema/t.sql": "1\n"})}, bundles=w["vault_b"])
    for _ in range(3):
        alice_worker.run_once()
        bob_worker.run_once()
    final = load_state(w["bob_store"], rid)
    assert final.plans[plan_id].status == "dispatched" and {t.status for t in final.tasks.values()} == {"done"}
    assert sealed_only(w) == {"envelope"}


def test_agent_tools_work_in_an_encrypted_room_through_the_broker(e2e_world):
    """The agent's tool process holds only a localhost address and a short token; the broker holds the sealing store, so what
    the agent posts is sealed on the way to the server and the agent never sees a key."""
    import httpx

    w = e2e_world
    rid = w["alice"].room_id
    assert w["bob"].agent_tools and w["bob"]._via_broker  # noqa: SLF001  (a sealed store is reached through a broker)
    try:
        env = json.loads(w["bob"]._mcp_config("bob", "bob-claude").read_text())["mcpServers"]["council"]["env"]  # noqa: SLF001
        assert "COUNCIL_BROKER_URL" in env and not any("key" in k.lower() and "BROKER" not in k for k in env)
        posted = httpx.post(env["COUNCIL_BROKER_URL"] + "/post", json={"type": "claim", "thread_id": "t1", "text": "the cache is stale"},
                            headers={"Authorization": "Bearer " + env["COUNCIL_BROKER_TOKEN"]})
        assert posted.status_code == 200
        mine = [e for e in read_all(w["alice_store"], rid) if e.body.get("text") == "the cache is stale"]
        assert [(e.sender_participant, e.sender_agent) for e in mine] == [("bob", "bob-claude")]  # alice opened what bob's agent said
        assert "the cache is stale" not in repr(sqlite3.connect(w["db"]).execute("SELECT * FROM events").fetchall())
        read = httpx.get(env["COUNCIL_BROKER_URL"] + "/read", params={"after": 0}, headers={"Authorization": "Bearer " + env["COUNCIL_BROKER_TOKEN"]})
        assert "the cache is stale" in read.json()["text"] and "<<untrusted-" in read.json()["text"]  # reads come back opened, and framed
    finally:
        w["bob"].close_brokers()
