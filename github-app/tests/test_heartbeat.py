import time

from app_server.heartbeat import start_heartbeat_thread, touch_heartbeat


def test_touch_heartbeat_creates_file(tmp_path):
    path = tmp_path / "heartbeat"
    assert not path.exists()

    touch_heartbeat(path)

    assert path.exists()


def test_touch_heartbeat_updates_mtime_on_repeat_calls(tmp_path):
    path = tmp_path / "heartbeat"
    touch_heartbeat(path)
    first_mtime = path.stat().st_mtime
    time.sleep(0.01)

    touch_heartbeat(path)

    assert path.stat().st_mtime >= first_mtime


def test_start_heartbeat_thread_touches_file_periodically(tmp_path):
    path = tmp_path / "heartbeat"

    start_heartbeat_thread(path, interval_seconds=0.05)
    time.sleep(0.2)

    assert path.exists()
    # Written at least a couple of times in 0.2s at a 0.05s interval -
    # confirms the loop is actually repeating, not a one-shot touch.
    first_mtime = path.stat().st_mtime
    time.sleep(0.15)
    assert path.stat().st_mtime > first_mtime


def test_start_heartbeat_thread_is_a_daemon_thread(tmp_path):
    path = tmp_path / "heartbeat"

    thread = start_heartbeat_thread(path, interval_seconds=10)

    assert thread.daemon is True
    assert thread.is_alive()
