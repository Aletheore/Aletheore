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
