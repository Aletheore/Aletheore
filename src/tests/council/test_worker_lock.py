import subprocess
import sys
import textwrap

import pytest

from aletheore.council.worker_lock import WorkerAlreadyRunning, WorkerLock


def test_second_lock_for_same_worker_is_refused_with_pid(tmp_path):
    first = WorkerLock(tmp_path, "room-1", "bob")
    first.acquire()
    try:
        with pytest.raises(WorkerAlreadyRunning, match="already running"):
            WorkerLock(tmp_path, "room-1", "bob").acquire()
    finally:
        first.release()


def test_different_room_or_person_do_not_conflict(tmp_path):
    a, b, c = WorkerLock(tmp_path, "room-1", "bob"), WorkerLock(tmp_path, "room-2", "bob"), WorkerLock(tmp_path, "room-1", "alice")
    for lock in (a, b, c):
        lock.acquire()
    for lock in (a, b, c):
        lock.release()


def test_lock_is_freed_when_holder_process_dies(tmp_path):
    code = textwrap.dedent(f"""
        import sys, time
        from pathlib import Path
        from aletheore.council.worker_lock import WorkerLock
        lock = WorkerLock(Path({str(tmp_path)!r}), "room-1", "bob")
        lock.acquire()
        print("held", flush=True)
        time.sleep(60)
    """)
    proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == "held"
        with pytest.raises(WorkerAlreadyRunning):
            WorkerLock(tmp_path, "room-1", "bob").acquire()
    finally:
        proc.kill()
        proc.wait()
    again = WorkerLock(tmp_path, "room-1", "bob")
    again.acquire()
    again.release()


def test_release_then_reacquire(tmp_path):
    lock = WorkerLock(tmp_path, "room-1", "bob")
    lock.acquire()
    lock.release()
    lock.acquire()
    lock.release()


def test_unsafe_names_cannot_escape_the_lock_directory(tmp_path):
    lock = WorkerLock(tmp_path, "../../evil", "..\\x")
    lock.acquire()
    lock.release()
    assert not (tmp_path.parent / "evil").exists()
