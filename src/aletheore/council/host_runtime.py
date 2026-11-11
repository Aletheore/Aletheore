"""What `council serve` does around the room server on a host's own machine: consent, the tunnel child process, keeping the
machine awake, the slot heartbeat, and a leaving notice. Every outside effect (starting a process, sleeping, the clock, the
registry) is passed in, so each rule is tested without a network or a real tunnel."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

from .protocol import server_notice

BACKOFF = (1, 2, 4, 8, 16, 32, 60)  # seconds between restarts of the tunnel process; the last value repeats
STABLE_S = 60  # a tunnel that stayed up this long starts its backoff over
HEARTBEAT_S = 10 * 60


class Consent:
    """The one question `council serve` asks, remembered: make this room reachable from the internet?"""

    def __init__(self, home: Path) -> None:
        self.path = Path(home) / "consent.json"

    def get(self) -> bool | None:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8")).get("internet")
        except (OSError, ValueError, AttributeError):
            return None
        return value if isinstance(value, bool) else None

    def set(self, answer: bool) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"internet": bool(answer)}), encoding="utf-8")


class TunnelSupervisor:
    """Runs the tunnel program (cloudflared) as a child and keeps it running: restarts with a growing pause, stops it cleanly.

    The run token goes to the child in the TUNNEL_TOKEN environment variable, never on its command line, where any user of the
    machine could read it in the process list."""

    def __init__(self, token: str, popen: Callable = subprocess.Popen, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic, binary: str = "cloudflared", url: str = "http://localhost:8765") -> None:
        self.token, self.popen, self.sleep, self.clock, self.binary, self.url = token, popen, sleep, clock, binary, url
        self.restarts = 0
        self.process = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def command(self) -> list[str]:
        return [self.binary, "tunnel", "--no-autoupdate", "run"]  # the token is in the environment, not here

    def _start(self):
        env = {**os.environ, "TUNNEL_TOKEN": self.token}
        return self.popen(self.command(), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def run(self) -> None:
        """Blocks until stop(): start, wait, and on an exit restart after the backoff."""
        step = 0
        while not self._stop.is_set():
            started = self.clock()
            try:
                self.process = self._start()
            except OSError:
                self.process = None  # the program is missing: keep trying slowly so installing it fixes things without a restart
            else:
                while self.process.poll() is None and not self._stop.is_set():
                    self.sleep(0.2)
            if self._stop.is_set():
                break
            step = 0 if self.clock() - started >= STABLE_S else min(step + 1, len(BACKOFF) - 1)
            self.restarts += 1
            self.sleep(BACKOFF[step])

    def start_in_background(self) -> None:
        self._thread = threading.Thread(target=self.run, daemon=True, name="council-tunnel")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        proc = self.process
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
        if self._thread is not None:
            self._thread.join(timeout=5)


class WakeLock:
    """Keeps the machine awake while rooms are live (a sleeping laptop is an offline host). Opt out with --no-wake-lock."""

    def __init__(self, platform: str = sys.platform, popen: Callable = subprocess.Popen, ctypes_module=None) -> None:
        self.platform, self.popen, self._ctypes, self._proc, self.active = platform, popen, ctypes_module, None, False

    def acquire(self) -> bool:
        try:
            if self.platform == "darwin":
                self._proc = self.popen(["caffeinate", "-i", "-w", str(os.getpid())], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            elif self.platform.startswith("win"):
                ct = self._ctypes
                if ct is None:
                    import ctypes as ct  # type: ignore[no-redef]
                ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
                ct.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
            else:
                self._proc = self.popen(["systemd-inhibit", "--what=sleep", "--who=Aletheore Council", "--why=hosting a room", "sleep", "infinity"],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, AttributeError):
            self.active = False  # not available here: say so, do not pretend
            return False
        self.active = True
        return True

    def release(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
        if self.platform.startswith("win") and self.active:
            ct = self._ctypes
            if ct is None:
                import ctypes as ct  # type: ignore[no-redef]
            ct.windll.kernel32.SetThreadExecutionState(0x80000000)
        self.active = False


def announce_leaving(store, host: str = "", note: str = "") -> int:
    """Tell everyone connected the host is leaving, in every open room this host runs (every open room in a host-run database when
    `host` is empty). Returns how many rooms were told. The notice is the server's own (see protocol.server_notice)."""
    told = 0
    rooms = store.all_rooms() if (not host and hasattr(store, "all_rooms")) else store.list_rooms(host)
    for room in rooms:
        if (host and room.host != host) or room.status != "open":
            continue
        extra = {"note": note.strip()[:200]} if note.strip() else {}
        store.append(server_notice(room.room_id, "host_leaving", f"host-leaving-{uuid.uuid4().hex}", **extra))
        told += 1
    return told


def heartbeat_loop(beat: Callable[[], object], stop: threading.Event, every: float = HEARTBEAT_S, on_error: Callable[[Exception], None] = lambda e: None) -> None:
    """Keeps the slot lease alive until `stop` is set. A failed beat is reported and tried again next time, never fatal."""
    while not stop.wait(every):
        try:
            beat()
        except Exception as exc:  # noqa: BLE001 - the loop must survive any single failure
            on_error(exc)


def announce_returned(store) -> int:
    """The host's server is up again: write "the host is back" into every open room that has any history, so guests who were
    waiting see why things move again. Returns how many rooms were told."""
    told = 0
    for room in store.all_rooms():
        if room.status != "open" or store.last_seq(room.room_id) == 0:
            continue
        store.append(server_notice(room.room_id, "host_returned", f"host-returned-{uuid.uuid4().hex}"))
        told += 1
    return told
