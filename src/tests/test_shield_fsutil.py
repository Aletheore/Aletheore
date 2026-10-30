import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from aletheore.shield import fsutil

SRC = Path(__file__).resolve().parents[1]


def _age(path: Path, seconds: float) -> None:
    then = time.time() - seconds
    os.utime(path, (then, then), follow_symlinks=False)


# ---- exclusive -----------------------------------------------------------------------------------------------------


def test_exclusive_acquires_and_releases(tmp_path):
    lock = tmp_path / "x.lock"
    with fsutil.exclusive(lock) as held:
        assert held is True and lock.is_file()
    assert not lock.exists()
    with fsutil.exclusive(lock) as held:  # free again
        assert held is True


def test_a_contended_lock_times_out_without_touching_the_holders_file(tmp_path):
    lock = tmp_path / "x.lock"
    with fsutil.exclusive(lock) as outer:
        assert outer
        token = lock.read_text(encoding="utf-8")
        started = time.monotonic()
        with fsutil.exclusive(lock, wait_s=0.1) as inner:
            assert inner is False
        assert 0.1 <= time.monotonic() - started < 5
        assert lock.read_text(encoding="utf-8") == token  # the loser never removes the holder's lock
    assert not lock.exists()


def test_a_stale_lock_is_taken_over(tmp_path):
    lock = tmp_path / "x.lock"
    lock.write_text("someone long gone", encoding="utf-8")
    _age(lock, 60)
    with fsutil.exclusive(lock, stale_s=10) as held:
        assert held is True
        assert lock.read_text(encoding="utf-8") != "someone long gone"
    assert not lock.exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == []  # the broken lock's renamed copy is gone too


def test_a_lock_left_by_a_killed_holder_is_taken_over_once_stale(tmp_path):
    lock = tmp_path / "x.lock"
    code = (
        "import os, sys; from pathlib import Path; from aletheore.shield import fsutil\n"
        "with fsutil.exclusive(Path(sys.argv[1])) as held:\n"
        "    os._exit(0 if held else 3)\n"
    )
    env = dict(os.environ, PYTHONPATH=str(SRC))
    done = subprocess.run([sys.executable, "-c", code, str(lock)], env=env, timeout=60, check=False)
    assert done.returncode == 0 and lock.is_file()  # killed while holding: the file stays behind
    with fsutil.exclusive(lock, wait_s=0.05, stale_s=10) as held:
        assert held is False  # still young: a live holder is assumed
    _age(lock, 60)
    with fsutil.exclusive(lock, wait_s=0.05, stale_s=10) as held:
        assert held is True


def test_breaking_a_stale_lock_never_steals_a_fresh_one(tmp_path, monkeypatch):
    lock = tmp_path / "x.lock"
    with fsutil.exclusive(lock) as outer:
        assert outer
        token = lock.read_text(encoding="utf-8")
        # Another caller's stat said "stale" (it saw an older file that has since been replaced by this fresh one).
        monkeypatch.setattr(fsutil, "_age", lambda path: 3600.0)
        with fsutil.exclusive(lock, wait_s=0.05, stale_s=10) as inner:
            assert inner is False
        assert lock.read_text(encoding="utf-8") == token
    assert not lock.exists()


def test_release_leaves_a_lock_that_is_no_longer_ours(tmp_path):
    lock = tmp_path / "x.lock"
    with fsutil.exclusive(lock) as held:
        assert held
        lock.write_text("another holder", encoding="utf-8")  # ours was broken as stale and someone else took it
    assert lock.read_text(encoding="utf-8") == "another holder"


def test_an_uncreatable_lock_yields_not_held_without_raising(tmp_path):
    with fsutil.exclusive(tmp_path / "missing" / "x.lock", wait_s=0.05) as held:
        assert held is False


def test_a_pending_delete_permission_error_is_retried_within_the_wait(tmp_path, monkeypatch):
    lock = tmp_path / "x.lock"
    real = fsutil._try_create
    calls = []

    def flaky(path, token):
        calls.append(1)
        if len(calls) <= 2:
            raise PermissionError("delete pending")  # what Windows raises for a lock file being deleted
        return real(path, token)

    monkeypatch.setattr(fsutil, "_try_create", flaky)
    with fsutil.exclusive(lock, wait_s=1.0) as held:
        assert held is True and len(calls) == 3


# ---- remove_older_than --------------------------------------------------------------------------------------------


def test_remove_older_than_removes_only_old_matching_regular_files(tmp_path):
    old, young, other = tmp_path / "hook-a.json", tmp_path / "hook-b.json", tmp_path / "pins.json"
    folder = tmp_path / "hook-dir.json"
    for path in (old, young, other):
        path.write_text("{}", encoding="utf-8")
    folder.mkdir()
    for path in (old, other, folder):
        _age(path, 3600)
    links = []
    if os.name != "nt":
        target = tmp_path / "target.txt"
        target.write_text("t", encoding="utf-8")
        link = tmp_path / "hook-link.json"
        link.symlink_to(target)
        _age(link, 3600)
        links = [link]
    assert fsutil.remove_older_than(tmp_path, ("hook-",), (".json",), 60) == 1
    assert not old.exists()
    assert young.exists() and other.exists() and folder.is_dir()
    assert all(p.is_symlink() for p in links)


# ---- atomic_write_text --------------------------------------------------------------------------------------------


def test_atomic_write_replaces_and_keeps_the_existing_mode(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("old", encoding="utf-8")
    if os.name != "nt":
        os.chmod(path, 0o640)
    fsutil.atomic_write_text(path, "new")
    assert path.read_text(encoding="utf-8") == "new"
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o640
    assert [p.name for p in tmp_path.iterdir()] == ["config.json"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes")
def test_atomic_write_new_files_get_the_usual_mode_or_the_one_asked_for(tmp_path):
    umask = os.umask(0o022)
    try:
        fsutil.atomic_write_text(tmp_path / "a.json", "a")
        fsutil.atomic_write_text(tmp_path / "b.json", "b", mode=0o600)
    finally:
        os.umask(umask)
    assert stat.S_IMODE((tmp_path / "a.json").stat().st_mode) == 0o644
    assert stat.S_IMODE((tmp_path / "b.json").stat().st_mode) == 0o600


def test_atomic_write_failure_keeps_the_old_file_and_removes_the_temporary(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text("old", encoding="utf-8")

    def refuse(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(fsutil.os, "replace", refuse)
    with pytest.raises(OSError):
        fsutil.atomic_write_text(path, "new")
    assert path.read_text(encoding="utf-8") == "old"
    assert [p.name for p in tmp_path.iterdir()] == ["config.json"]


def test_atomic_write_retries_a_replace_refused_while_the_file_is_open(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    real = os.replace
    calls = []

    def busy(source, target):
        calls.append(1)
        if len(calls) <= 2:
            raise PermissionError("in use by another process")
        return real(source, target)

    monkeypatch.setattr(fsutil, "_RETRY_REPLACE", True)  # the Windows behaviour, on any platform
    monkeypatch.setattr(fsutil.os, "replace", busy)
    fsutil.atomic_write_text(path, "new")
    assert path.read_text(encoding="utf-8") == "new" and len(calls) == 3


def test_atomic_write_removes_temporaries_a_killed_writer_left_hours_ago(tmp_path):
    path = tmp_path / "config.json"
    stale, fresh = tmp_path / ".config.json.abc.tmp", tmp_path / ".config.json.def.tmp"
    unrelated = tmp_path / ".other.json.abc.tmp"
    for leftover in (stale, fresh, unrelated):
        leftover.write_text("partial", encoding="utf-8")
    _age(stale, 2 * 3600)
    _age(unrelated, 2 * 3600)
    fsutil.atomic_write_text(path, "new")
    assert not stale.exists() and fresh.exists() and unrelated.exists()


@pytest.mark.skipif(os.name == "nt", reason="symbolic links")
def test_atomic_write_replaces_a_symlink_itself_and_never_writes_through_it(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("theirs", encoding="utf-8")
    inside = tmp_path / "repo"
    inside.mkdir()
    link = inside / "config.json"
    link.symlink_to(outside)
    fsutil.atomic_write_text(link, "ours")
    assert not link.is_symlink() and link.read_text(encoding="utf-8") == "ours"
    assert outside.read_text(encoding="utf-8") == "theirs"
