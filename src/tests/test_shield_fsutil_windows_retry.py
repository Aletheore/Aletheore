"""Windows-only races, simulated on any OS: a busy target and a locked lock-file byte must not lose a write."""

import os

from aletheore.shield import fsutil, locking


def test_replace_keeps_trying_within_its_budget_when_the_target_is_busy(tmp_path, monkeypatch):
    source, target = tmp_path / "a.tmp", tmp_path / "a.json"
    source.write_text("new", encoding="utf-8")
    real = os.replace
    calls = {"n": 0}

    def busy_then_free(src, dst):
        calls["n"] += 1
        if calls["n"] <= 12:  # far more than the old fixed five tries
            raise PermissionError(13, "in use")
        return real(src, dst)

    monkeypatch.setattr(fsutil, "_RETRY_REPLACE", True)
    monkeypatch.setattr(fsutil.os, "replace", busy_then_free)
    fsutil._replace(str(source), target)
    assert target.read_text(encoding="utf-8") == "new"
    assert calls["n"] == 13


def test_replace_gives_up_once_the_budget_is_spent(tmp_path, monkeypatch):
    source, target = tmp_path / "a.tmp", tmp_path / "a.json"
    source.write_text("new", encoding="utf-8")

    def always_busy(src, dst):
        raise PermissionError(13, "in use")

    monkeypatch.setattr(fsutil, "_RETRY_REPLACE", True)
    monkeypatch.setattr(fsutil, "_REPLACE_BUDGET_S", 0.05)
    monkeypatch.setattr(fsutil.os, "replace", always_busy)
    try:
        fsutil._replace(str(source), target)
    except PermissionError:
        return
    raise AssertionError("expected PermissionError")


def test_replace_does_not_retry_off_windows(tmp_path, monkeypatch):
    calls = {"n": 0}

    def busy(src, dst):
        calls["n"] += 1
        raise PermissionError(13, "denied")

    monkeypatch.setattr(fsutil, "_RETRY_REPLACE", False)
    monkeypatch.setattr(fsutil.os, "replace", busy)
    try:
        fsutil._replace("x", tmp_path / "y")
    except PermissionError:
        pass
    assert calls["n"] == 1


def test_file_lock_still_works_on_a_fresh_path(tmp_path):
    with locking.file_lock(tmp_path / "d" / ".x.lock") as acquired:
        assert acquired


def _as_windows(monkeypatch):
    locked = type("M", (), {"LK_NBLCK": 1, "LK_UNLCK": 0, "locking": staticmethod(lambda *a: None)})()
    monkeypatch.setattr(locking.os, "name", "nt")
    monkeypatch.setitem(__import__("sys").modules, "msvcrt", locked)


def test_file_lock_init_write_error_is_raised_when_the_file_stays_empty(tmp_path, monkeypatch):
    import pytest

    def refused(descriptor, data):
        raise PermissionError(13, "read-only")

    _as_windows(monkeypatch)
    monkeypatch.setattr(locking.os, "write", refused)
    with pytest.raises(PermissionError):
        with locking.file_lock(tmp_path / ".x.lock", timeout=0.05):
            pass


def test_lock_open_keeps_trying_within_its_budget_when_creation_races(tmp_path, monkeypatch):
    # Windows: openers creating the same lock file together can get a sharing violation for a moment. That used to
    # raise out of file_lock, and every caller treats a raise as "skip the write", so a write was silently lost.
    real = os.open
    calls = {"n": 0}

    def busy_then_free(path, flags, mode=0o777):
        calls["n"] += 1
        if calls["n"] <= 6:
            raise PermissionError(13, "sharing violation")
        return real(path, flags, mode)

    monkeypatch.setattr(locking, "_RETRY_OPEN", True)
    monkeypatch.setattr(locking.os, "open", busy_then_free)
    with locking.file_lock(tmp_path / "x.lock", timeout=5.0) as acquired:
        assert acquired is True
    assert calls["n"] == 7


def test_lock_open_raises_once_its_budget_is_spent(tmp_path, monkeypatch):
    def always_refused(path, flags, mode=0o777):
        raise PermissionError(13, "denied")

    monkeypatch.setattr(locking, "_RETRY_OPEN", True)
    monkeypatch.setattr(locking.os, "open", always_refused)
    try:
        with locking.file_lock(tmp_path / "x.lock", timeout=0.05):
            pass
    except PermissionError:
        return
    raise AssertionError("expected PermissionError")


def test_lock_open_does_not_retry_off_windows(tmp_path, monkeypatch):
    calls = {"n": 0}

    def refused(path, flags, mode=0o777):
        calls["n"] += 1
        raise PermissionError(13, "denied")

    monkeypatch.setattr(locking, "_RETRY_OPEN", False)
    monkeypatch.setattr(locking.os, "open", refused)
    try:
        with locking.file_lock(tmp_path / "x.lock", timeout=5.0):
            pass
    except PermissionError:
        assert calls["n"] == 1
        return
    raise AssertionError("expected PermissionError")


def test_lock_byte_write_keeps_trying_while_the_file_is_still_empty(tmp_path, monkeypatch):
    # The same creation race one step later: the byte write is refused while the file is still empty, then works.
    real = os.write
    calls = {"n": 0}

    def busy_then_free(descriptor, data):
        calls["n"] += 1
        if calls["n"] <= 4:
            raise PermissionError(13, "locked")
        return real(descriptor, data)

    _as_windows(monkeypatch)
    monkeypatch.setattr(locking.os, "write", busy_then_free)
    with locking.file_lock(tmp_path / ".x.lock", timeout=5.0) as acquired:
        assert acquired is True
    assert calls["n"] == 5


def test_lock_byte_write_stops_retrying_once_the_byte_exists(tmp_path, monkeypatch):
    # Another opener won the race and locked the byte it wrote: our refused write is then benign.
    real_write = os.write

    def lost_race(descriptor, data):
        real_write(descriptor, data)  # the winner's byte lands...
        raise PermissionError(13, "locked")  # ...and ours is refused

    _as_windows(monkeypatch)
    monkeypatch.setattr(locking.os, "write", lost_race)
    with locking.file_lock(tmp_path / ".x.lock", timeout=5.0) as acquired:
        assert acquired is True
