"""Host side of `council serve`: consent, the tunnel supervisor, the wake lock, the leaving notice, the heartbeat."""

import threading

from aletheore.council.host_runtime import BACKOFF, STABLE_S, Consent, TunnelSupervisor, WakeLock, announce_leaving, heartbeat_loop
from aletheore.council.protocol import EventDraft, EventType
from aletheore.council.sqlite_store import SqliteRoomStore


def test_first_run_consent_is_remembered_and_absent_means_ask(tmp_path):
    c = Consent(tmp_path)
    assert c.get() is None
    c.set(False)
    assert Consent(tmp_path).get() is False
    c.set(True)
    assert Consent(tmp_path).get() is True
    (tmp_path / "consent.json").write_text("garbage")
    assert Consent(tmp_path).get() is None  # a damaged file means ask again, not "yes"


class FakeProc:
    def __init__(self, lives_for):
        self.lives, self.terminated = lives_for, False

    def poll(self):
        return None if self.lives > 0 and not self.terminated else 1

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 1

    def kill(self):
        self.terminated = True


class Sim:
    """A fake world for the supervisor: processes live a given number of polls; time only moves when the supervisor sleeps."""

    def __init__(self, lives):
        self.lives, self.t, self.sleeps, self.spawned, self.envs, self.argv = list(lives), 0.0, [], 0, [], []
        self.sup = None

    def popen(self, cmd, env=None, **kw):
        self.spawned += 1
        self.envs.append(env)
        self.argv.append(cmd)
        return FakeProc(self.lives.pop(0) if self.lives else 10**9)

    def sleep(self, s):
        self.t += s
        self.sleeps.append(s)
        for p in [self.sup.process] if self.sup and self.sup.process else []:
            p.lives -= 1  # a poll cycle passes
        if len(self.sleeps) > 700:
            self.sup.stop()

    def clock(self):
        return self.t


def test_supervisor_restarts_a_killed_tunnel_with_backoff_and_hides_the_token():
    sim = Sim([2, 2, 2, 2])
    sup = TunnelSupervisor("RUN-TOKEN", sim.popen, sim.sleep, sim.clock)
    sim.sup = sup
    sup.run()
    assert sim.spawned >= 4 and sup.restarts >= 3
    pauses = [s for s in sim.sleeps if s >= 1]
    assert pauses[:4] == [BACKOFF[1], BACKOFF[2], BACKOFF[3], BACKOFF[4]]  # 2, 4, 8, 16: it grows
    assert all("RUN-TOKEN" not in " ".join(c) for c in sim.argv)  # never on the command line
    assert all(e["TUNNEL_TOKEN"] == "RUN-TOKEN" for e in sim.envs)  # only in the child's environment


def test_a_tunnel_that_stayed_up_starts_its_backoff_over():
    sim = Sim([int(STABLE_S / 0.2) + 5, 1, 1])
    sup = TunnelSupervisor("T", sim.popen, sim.sleep, sim.clock)
    sim.sup = sup
    sup.run()
    first_pause = next(s for s in sim.sleeps if s >= 1)
    assert first_pause == BACKOFF[0]


def test_a_missing_program_does_not_crash_the_supervisor_and_stop_ends_it():
    calls = []

    def popen(cmd, **kw):
        calls.append(cmd)
        raise FileNotFoundError("cloudflared")

    ticks = {"n": 0}
    sup = TunnelSupervisor("T", popen, lambda s: ticks.__setitem__("n", ticks["n"] + 1) or (sup.stop() if ticks["n"] > 3 else None), lambda: 0.0)
    sup.run()
    assert len(calls) >= 2 and sup.process is None


def test_stop_terminates_the_child_cleanly():
    proc = FakeProc(10**9)
    sup = TunnelSupervisor("T", lambda *a, **k: proc, lambda s: None, lambda: 0.0)
    sup.process = proc
    sup.stop()
    assert proc.terminated


def test_wake_lock_uses_the_right_tool_per_system_and_reports_when_unavailable():
    started = []

    class P:
        def poll(self):
            return None

        def terminate(self):
            started.append("terminated")

    mac = WakeLock("darwin", lambda cmd, **k: started.append(cmd[0]) or P())
    assert mac.acquire() and started[0] == "caffeinate" and mac.active
    mac.release()
    assert "terminated" in started and not mac.active
    linux = WakeLock("linux", lambda cmd, **k: started.append(cmd[0]) or P())
    assert linux.acquire() and "systemd-inhibit" in started

    class K32:
        calls = []

        class windll:
            class kernel32:
                @staticmethod
                def SetThreadExecutionState(flags):
                    K32.calls.append(flags)

    win = WakeLock("win32", ctypes_module=K32)
    assert win.acquire() and K32.calls == [0x80000001]
    win.release()
    assert K32.calls[-1] == 0x80000000

    def broken(*a, **k):
        raise FileNotFoundError("caffeinate")

    assert WakeLock("darwin", broken).acquire() is False


def test_shutdown_tells_every_open_room_the_host_is_leaving_with_the_note(tmp_path):
    s = SqliteRoomStore(tmp_path / "r.db")
    for rid, host, status in (("r1", "alice", "open"), ("r2", "alice", "open"), ("r3", "bob", "open")):
        s.create_room(rid, host, title=rid)
        s.append(EventDraft(rid, "c0", "room", EventType.SYSTEM, host, None, None, {"subtype": "room_created", "host": host}))
    s.add_member("r3", "alice")
    s.close_room("r2")
    assert announce_leaving(s, "alice", "back tomorrow 10:00") == 1  # r1 only: r2 is closed, r3 is bob's
    leaving = [e for e in s.read_after("r1", 0) if e.body.get("subtype") == "host_leaving"]
    assert len(leaving) == 1 and leaving[0].body["note"] == "back tomorrow 10:00"
    assert not [e for e in s.read_after("r3", 0) if e.body.get("subtype") == "host_leaving"]


def test_heartbeat_survives_a_failed_beat_and_stops_when_told():
    stop, seen, errors = threading.Event(), [], []

    def beat():
        seen.append(1)
        if len(seen) == 2:
            raise ConnectionError("registry down")
        if len(seen) >= 4:
            stop.set()

    heartbeat_loop(beat, stop, every=0.01, on_error=errors.append)
    assert len(seen) >= 4 and len(errors) == 1


def test_a_host_run_database_announces_in_every_open_room_and_the_stream_shows_it(tmp_path):
    from datetime import datetime, timezone

    from aletheore.council.state import project
    from aletheore.council.view_model import build_stream

    s = SqliteRoomStore(tmp_path / "r.db")
    s.create_room("r1", "alice", title="T")
    s.append(EventDraft("r1", "c0", "room", EventType.SYSTEM, "alice", None, None, {"subtype": "room_created", "host": "alice"}))
    assert announce_leaving(s, "", "back tomorrow 10:00") == 1
    events = s.read_after("r1", 0)
    items = build_stream(events, project(events, host="alice"), datetime.now(timezone.utc))
    assert any("the host's server is leaving: back tomorrow 10:00" in i.text for i in items)
    assert announce_leaving(s, "", "") == 1
    events = s.read_after("r1", 0)
    assert any("You can keep reading" in i.text for i in build_stream(events, project(events, host="alice"), datetime.now(timezone.utc)))


def test_a_returning_host_writes_that_it_is_back_in_rooms_with_history(tmp_path):
    from datetime import datetime, timezone

    from aletheore.council.host_runtime import announce_returned
    from aletheore.council.state import project
    from aletheore.council.view_model import build_stream

    s = SqliteRoomStore(tmp_path / "r.db")
    s.create_room("r1", "alice", title="used")
    s.append(EventDraft("r1", "c0", "room", EventType.SYSTEM, "alice", None, None, {"subtype": "room_created", "host": "alice"}))
    s.create_room("r2", "alice", title="brand new and empty")
    assert announce_returned(s) == 1  # only the room that has history
    events = s.read_after("r1", 0)
    items = build_stream(events, project(events, host="alice"), datetime.now(timezone.utc))
    assert any("the host's server is back" in i.text for i in items)


def test_server_notices_show_up_in_a_sealed_room_and_are_not_counted_as_junk(tmp_path):
    """Regression for a real bug found while planning the acceptance test: the server cannot seal an event (it holds no room keys), and a
    sealed room's reader used to call every unsealed event malformed, hiding the notice and raising a false 'ignored' alarm."""
    from aletheore.council import crypto
    from aletheore.council.e2e_store import E2ERoomStore
    from aletheore.council.envelope import Keyring
    from aletheore.council.host_runtime import announce_leaving, announce_returned
    from aletheore.council.identity import Keystore
    from aletheore.council.protocol import is_server_notice

    ident = Keystore(tmp_path / "k").load_or_create("host")
    inner = SqliteRoomStore(tmp_path / "s.db")
    inner.create_room("r1", "alice", title="sealed", crypto="e2e-v1")
    sealed = E2ERoomStore(inner, ident, Keyring({1: crypto.random_bytes(32)}), lambda room: {"alice": [ident.public()]})
    sealed.append(EventDraft("r1", "m1", "t", EventType.CLAIM, "alice", None, None, {"text": "before"}))
    assert announce_leaving(inner, "", "back at ten") == 1 and announce_returned(inner) == 1
    sealed.append(EventDraft("r1", "m2", "t", EventType.CLAIM, "alice", None, None, {"text": "after"}))
    seen = sealed.read_after("r1", 0)
    assert [e.body.get("text") for e in seen if not is_server_notice(e)] == ["before", "after"]
    assert [e.body["subtype"] for e in seen if is_server_notice(e)] == ["host_leaving", "host_returned"]
    assert sealed.rejected == 0 and sealed.unreadable == 0 and sealed.gaps == []  # no false alarm


def test_the_server_notice_sender_cannot_be_registered_as_a_handle(tmp_path):
    import pytest

    from aletheore.council.accounts import AccountError, AccountService, SqliteAccountBackend

    service = AccountService(SqliteAccountBackend(tmp_path / "a.db"))
    with pytest.raises(AccountError, match="taken"):
        service.create_registration_code("council-server", can_host=True)
