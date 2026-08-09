import threading
import time
from pathlib import Path

DEFAULT_HEARTBEAT_PATH = Path("/tmp/heartbeat")


def touch_heartbeat(path: Path = DEFAULT_HEARTBEAT_PATH) -> None:
    """Writes the current time to path, creating it if absent. Called once
    per iteration of a long-running loop (scheduler.py's own work loop) or
    periodically by a background thread (RQ workers - see
    start_heartbeat_thread) as the liveness signal a Docker HEALTHCHECK
    reads via the file's mtime. A background thread only proves the process
    itself hasn't fully deadlocked (Python schedules threads independently
    of what the main thread is blocked on for typical I/O waits) - it does
    not prove the specific work loop (e.g. RQ actually dequeuing jobs) is
    making progress. That stronger guarantee needs a separate check, not
    this file.
    """
    path.write_text(str(time.time()))


def start_heartbeat_thread(
    path: Path = DEFAULT_HEARTBEAT_PATH, interval_seconds: float = 30.0
) -> threading.Thread:
    """Starts a daemon thread that touches the heartbeat file every
    interval_seconds, for processes (RQ workers) whose main loop has no
    natural per-iteration hook to touch it from directly. Daemon so it
    never blocks process exit."""

    def _loop() -> None:
        while True:
            touch_heartbeat(path)
            time.sleep(interval_seconds)

    thread = threading.Thread(target=_loop, daemon=True)
    thread.start()
    return thread
