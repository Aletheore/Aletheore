"""Retention, quotas and the admin tool: what is removed and when, clear errors at the limits, and an audit trail without secrets."""

import json

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from aletheore.council import cli
from aletheore.council.accounts import AccountService, SqliteAccountBackend
from aletheore.council.admin import AdminError, AdminTool, token_hash
from aletheore.council.housekeeping import run_sweep
from aletheore.council.identity import Keystore
from aletheore.council.protocol import EventDraft, EventType
from aletheore.council.server import create_app
from aletheore.council.server_config import ServerSettings
from aletheore.council.sqlite_store import SqliteRoomStore

DAY = 86400.0


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


def iso(t):
    from datetime import datetime, timezone

    return datetime.fromtimestamp(t, timezone.utc).isoformat()


@pytest.fixture
def world(tmp_path):
    clock = Clock()
    store = SqliteRoomStore(tmp_path / "s.db")
    accounts = AccountService(SqliteAccountBackend(tmp_path / "s.db"), clock=clock)
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    return store, accounts, bundles, clock, tmp_path


def make_room(store, rid, host="alice", events=2, close=False):
    store.create_room(rid, host, title=rid)
    store.append(EventDraft(rid, "c0", "room", EventType.SYSTEM, host, None, None, {"subtype": "room_created", "host": host}))
    for i in range(events - 1):
        store.append(EventDraft(rid, f"m{i}", "t", EventType.CLAIM, host, None, None, {"text": "hello"}))
    if close:
        store.close_room(rid)


def test_closed_rooms_and_their_objects_are_purged_after_the_retention_period(world):
    store, accounts, bundles, clock, tmp = world
    clock.t = 0.0  # events are stamped with real time by the store; judge age from "now" instead
    import time as _t

    real_now = _t.time()
    make_room(store, "old-closed", close=True)
    make_room(store, "open-room")
    (bundles / "old-closed").mkdir()
    (bundles / "old-closed" / "x.bundle").write_bytes(b"1")
    (bundles / "open-room").mkdir()
    (bundles / "open-room" / "y.bundle").write_bytes(b"2")
    settings = ServerSettings(closed_room_retention_days=30)
    assert run_sweep(store, accounts, bundles, settings, lambda: real_now + 5 * DAY)["rooms_deleted"] == []  # too recent
    done = run_sweep(store, accounts, bundles, settings, lambda: real_now + 31 * DAY)
    assert done["rooms_deleted"] == ["old-closed"]  # past retention, and closed
    assert [r.room_id for r in store.all_rooms()] == ["open-room"] and not (bundles / "old-closed").exists() and (bundles / "open-room" / "y.bundle").exists()
    assert run_sweep(store, accounts, bundles, ServerSettings(closed_room_retention_days=0), lambda: real_now + 900 * DAY)["rooms_deleted"] == []  # 0 keeps forever


def test_unclaimed_invites_and_old_challenges_expire_and_are_removed(world):
    store, accounts, bundles, clock, tmp = world
    ident = Keystore(tmp / "k").load_or_create("a")
    accounts.redeem_registration_code(accounts.create_registration_code("alice", can_host=True), ident.public(), "alice")
    accounts.login_challenge(ident.device_id)  # a challenge nobody answers
    invite_id, _ = accounts.create_invite("r1", ident.device_id, b"wrapped", ttl=3600, title="T")
    assert accounts.get_invite(invite_id).status == "open"
    clock.t += 2 * 3600
    assert accounts.get_invite(invite_id).status == "expired"  # expired but still explainable
    first = accounts.purge_expired()
    assert first["invites"] == 0 and first["nonces"] >= 1  # the unanswered challenge is already gone; the invite is still explainable
    clock.t += 25 * 3600  # past the one day grace
    removed = accounts.purge_expired()
    assert removed["invites"] == 1
    assert accounts.get_invite(invite_id) is None


def test_per_room_and_per_host_quotas_return_clear_errors(tmp_path):
    store = SqliteRoomStore(tmp_path / "s.db")
    settings = ServerSettings(max_rooms_per_host=1, max_members_per_room=2)
    app = create_app(store, {"ta": "alice", "tb": "bob", "tc": "carol"}, tmp_path / "b", settings=settings, max_events_per_room=3)
    c = TestClient(app, base_url="http://x.test")
    A = {"Authorization": "Bearer ta"}
    assert c.post("/v1/rooms/r1", json={"host": "alice"}, headers=A).status_code == 200
    second = c.post("/v1/rooms/r2", json={"host": "alice"}, headers=A)
    assert second.status_code == 422 and "already host 1 open rooms" in second.json()["detail"] and "close one" in second.json()["detail"]
    assert c.post("/v1/rooms/r1/members", json={"participant": "bob"}, headers=A).status_code == 200
    third = c.post("/v1/rooms/r1/members", json={"participant": "carol"}, headers=A)
    assert third.status_code == 422 and "already has 2 members" in third.json()["detail"]
    assert c.post("/v1/rooms/r1/members", json={"participant": "bob"}, headers=A).status_code == 200  # re-adding a member is not a new one
    body = {"client_event_id": "e", "thread_id": "t", "type": "claim", "sender_participant": "alice", "sender_agent": None, "reply_to": None, "body": {"text": "x"}}
    codes = [c.post("/v1/rooms/r1/events", json={**body, "client_event_id": f"e{i}"}, headers=A).status_code for i in range(5)]
    assert codes[:3] == [200, 200, 200] and codes[3] == 429
    limit = c.post("/v1/rooms/r1/events", json={**body, "client_event_id": "e9"}, headers=A)
    assert "event limit" in limit.json()["detail"]


def test_deleting_a_room_removes_events_members_grants_invites_and_blobs(world):
    store, accounts, bundles, clock, tmp = world
    ident = Keystore(tmp / "k").load_or_create("a")
    accounts.redeem_registration_code(accounts.create_registration_code("alice", can_host=True), ident.public(), "alice")
    make_room(store, "r1")
    store.add_member("r1", "bob")
    accounts.create_invite("r1", ident.device_id, b"w", ttl=3600, title="T")
    accounts.put_grant("r1", "alice", 1, ident.device_id, b"grant")
    (bundles / "r1").mkdir()
    (bundles / "r1" / "a.bundle").write_bytes(b"1")
    (bundles / "r1" / "b.blob").write_bytes(b"2")
    make_room(store, "keep")
    audit = tmp / "audit.log"
    tool = AdminTool(store, accounts, bundles, audit)
    done = tool.delete_room("r1")
    assert done["files"] == 2 and done["invites"] == 1 and done["grants"] == 1
    assert [r.room_id for r in store.all_rooms()] == ["keep"] and store.read_after("r1", 0) == [] and not (bundles / "r1").exists()
    assert accounts.my_grants("r1", ident.device_id) == []
    with pytest.raises(AdminError, match="no room called"):
        tool.delete_room("r1")


def test_rooms_and_usage_report_real_numbers(world):
    store, accounts, bundles, clock, tmp = world
    make_room(store, "r1", events=3)
    make_room(store, "r2", host="bob", events=1, close=True)
    (bundles / "r1").mkdir()
    (bundles / "r1" / "a.bundle").write_bytes(b"x" * 100)
    tool = AdminTool(store, accounts, bundles, tmp / "audit.log")
    rooms = {r["room_id"]: r for r in tool.rooms()}
    assert rooms["r1"]["events"] == 3 and rooms["r2"]["status"] == "closed" and rooms["r1"]["members"] == 1
    u = tool.usage()
    assert u["rooms"] == 2 and u["open_rooms"] == 1 and u["events"] == 4 and u["files"] == 1 and u["file_bytes"] == 100
    assert u["rooms_per_host"] == {"alice": 1, "bob": 1}


def test_admin_commands_need_the_admin_token_and_are_written_to_an_audit_log_without_secrets(world):
    store, accounts, bundles, clock, tmp = world
    audit = tmp / "audit.log"
    tool = AdminTool(store, accounts, bundles, audit, admin_hash=token_hash("right-token"))
    with pytest.raises(AdminError, match="missing or wrong"):
        tool.authorize("wrong")
    with pytest.raises(AdminError):
        tool.authorize("")
    tool.authorize("right-token")  # fine
    make_room(store, "r1")
    tool.delete_room("r1")
    token = tool.rotate_admin_token(tmp / "hash")
    assert (tmp / "hash").read_text() == token_hash(token) and token not in (tmp / "hash").read_text()
    tool._audit("custom", {"room": "r9", "admin_token": "SHOULD-NOT-APPEAR", "reg_code": "ALSO-NOT"}, True)
    text = audit.read_text()
    lines = [json.loads(l) for l in text.splitlines()]
    assert [l["command"] for l in lines][:1] == ["authorize"] and lines[0]["ok"] is False  # the refused attempt is on record
    assert "SHOULD-NOT-APPEAR" not in text and "ALSO-NOT" not in text and "right-token" not in text and token not in text
    assert any(l["command"] == "delete-room" and l["args"] == {"room": "r1"} for l in lines)
    import os

    if os.name != "nt":
        assert oct(audit.stat().st_mode & 0o777) == "0o600"


def test_the_cli_admin_commands_work_end_to_end_and_refuse_a_wrong_token(tmp_path, monkeypatch):
    monkeypatch.setenv("COUNCIL_HOME", str(tmp_path))
    monkeypatch.delenv("COUNCIL_ADMIN_TOKEN", raising=False)
    db = tmp_path / "server.db"
    store = SqliteRoomStore(db)
    make_room(store, "r1", events=2)
    runner = CliRunner()
    out = runner.invoke(cli.app, ["admin", "rooms", "--db", str(db)])
    assert out.exit_code == 0 and "r1" in out.output and "2 events" in out.output
    assert "1 rooms" in runner.invoke(cli.app, ["admin", "usage", "--db", str(db)]).output
    rotated = runner.invoke(cli.app, ["admin", "rotate-admin-token", "--db", str(db)])
    token = rotated.output.strip().splitlines()[-1]
    assert rotated.exit_code == 0 and len(token) > 20
    refused = runner.invoke(cli.app, ["admin", "rooms", "--db", str(db)])  # a hash exists now: no token, no entry
    assert refused.exit_code == 1 and "admin token" in refused.output
    monkeypatch.setenv("COUNCIL_ADMIN_TOKEN", token)
    assert runner.invoke(cli.app, ["admin", "rooms", "--db", str(db)]).exit_code == 0
    gone = runner.invoke(cli.app, ["admin", "delete-room", "r1", "--yes", "--db", str(db)])
    assert gone.exit_code == 0 and "deleted r1" in gone.output and SqliteRoomStore(db).all_rooms() == []
    audit = (tmp_path / "admin-audit.log").read_text()
    assert token not in audit and "delete-room" in audit
