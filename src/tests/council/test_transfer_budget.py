"""The file transfer cap: files only, every room together, a rolling day; messages are never touched."""

import hashlib

import pytest
from fastapi.testclient import TestClient

from aletheore.council.protocol import EventDraft, EventType
from aletheore.council.remote_store import RemoteRoomStore, TransferBudgetError
from aletheore.council.server import create_app
from aletheore.council.server_config import ServerSettings, TransferBudget, load_settings
from aletheore.council.sqlite_store import SqliteRoomStore

MB = 1024 * 1024


class Clock:
    def __init__(self):
        self.t = 10_000_000.0

    def __call__(self):
        return self.t


def test_budget_counts_a_rolling_day_and_frees_up_gradually():
    c = Clock()
    b = TransferBudget(100, c)
    assert b.allows(100) and not b.allows(101)
    b.add(60)
    c.t += 2 * 3600
    b.add(30)
    assert b.used() == 90 and b.allows(10) and not b.allows(11)
    c.t += 23 * 3600  # the first 60 bytes are now more than a day old
    assert b.used() == 30 and b.allows(70)
    c.t += 48 * 3600
    assert b.used() == 0 and b.resets_in_s() == 0
    assert TransferBudget(0, c).allows(10**12)  # 0 means no cap


def test_resets_in_names_when_the_oldest_bytes_age_out():
    c = Clock()
    b = TransferBudget(10, c)
    b.add(10)
    assert 23 * 3600 <= b.resets_in_s() <= 24 * 3600 + 3600
    c.t += 12 * 3600
    assert b.resets_in_s() < 13 * 3600


def test_the_setting_reads_from_the_environment_and_rejects_negatives():
    assert load_settings(environ={"COUNCIL_FILE_CAP_MB_PER_DAY": "2048"}).file_transfer_cap_mb_per_day == 2048
    assert load_settings(environ={}).file_transfer_cap_mb_per_day == 0
    with pytest.raises(ValueError, match="0 \\(no cap\\) or more"):
        load_settings(environ={"COUNCIL_FILE_CAP_MB_PER_DAY": "-1"})


@pytest.fixture
def world(tmp_path):
    clock = Clock()
    store = SqliteRoomStore(tmp_path / "r.db")
    store.create_room("r1", "alice")
    store.create_room("r2", "alice")
    store.add_member("r1", "bob")
    settings = ServerSettings(file_transfer_cap_mb_per_day=3)
    app = create_app(store, {"tok-a": "alice", "tok-b": "bob"}, tmp_path / "b", clock=clock, settings=settings)
    http = TestClient(app, base_url="http://council.test")
    remote = RemoteRoomStore("http://council.test", "tok-a", client=http)
    return store, remote, clock, tmp_path


def bundle(tmp_path, size, name):
    data = (name.encode() * (size // len(name) + 1))[:size]
    path = tmp_path / f"{name}.bundle"
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest(), path


def test_files_count_across_all_rooms_not_per_room_and_messages_never_do(world):
    store, remote, clock, tmp = world
    sha1, p1 = bundle(tmp, 2 * MB, "a")
    sha2, p2 = bundle(tmp, 2 * MB, "b")
    remote.put_bundle("r1", sha1, p1)  # 2 of 3 MB used, in room r1
    with pytest.raises(TransferBudgetError, match="would go over"):
        remote.put_bundle("r2", sha2, p2)  # a different room, same host allowance
    for i in range(50):  # messages are untouched by the cap
        remote.append(EventDraft("r1", f"m{i}", "t", EventType.CLAIM, "alice", None, None, {"text": "x" * 500}))
    assert len(remote.read_after("r1", 0)) >= 50


def test_downloads_count_too_and_the_message_says_what_to_do(world):
    store, remote, clock, tmp = world
    sha1, p1 = bundle(tmp, 2 * MB, "a")
    remote.put_bundle("r1", sha1, p1)
    with pytest.raises(TransferBudgetError, match="would go over"):
        remote.get_bundle("r1", sha1, tmp / "got1")  # the download would make 4 MB of a 3 MB allowance
    assert not (tmp / "got1").exists()


def test_the_budget_frees_up_after_a_day_and_the_snapshot_is_readable_by_members(world):
    store, remote, clock, tmp = world
    sha1, p1 = bundle(tmp, 2 * MB, "a")
    remote.put_bundle("r1", sha1, p1)
    snap = remote.transfer_budget()
    assert snap["cap_bytes"] == 3 * MB and snap["used_bytes"] == 2 * MB and snap["resets_in_s"] > 0
    sha2, p2 = bundle(tmp, 2 * MB, "b")
    with pytest.raises(TransferBudgetError) as exc:
        remote.put_bundle("r1", sha2, p2)
    assert "2.0 of 3.0 MB used" in str(exc.value) and "Messages still work" in str(exc.value) and "Tailscale" in str(exc.value) and "hour" in str(exc.value)
    clock.t += 25 * 3600
    remote.put_bundle("r1", sha2, p2)  # a day later it fits
    assert remote.transfer_budget()["used_bytes"] == 2 * MB


def test_no_cap_means_no_limit(tmp_path):
    clock = Clock()
    store = SqliteRoomStore(tmp_path / "r.db")
    store.create_room("r1", "alice")
    app = create_app(store, {"tok-a": "alice"}, tmp_path / "b", clock=clock, settings=ServerSettings())
    remote = RemoteRoomStore("http://council.test", "tok-a", client=TestClient(app, base_url="http://council.test"))
    for name in ("a", "b", "c"):
        sha, p = bundle(tmp_path, 2 * MB, name)
        remote.put_bundle("r1", sha, p)
    assert remote.transfer_budget()["cap_bytes"] == 0
