"""A real server on a real port that can be stopped and started again over the same database, for the chaos tests."""

from __future__ import annotations

import socket
import threading
import time

import uvicorn

from aletheore.council.server import create_app
from aletheore.council.server_config import ServerSettings
from aletheore.council.sqlite_store import SqliteRoomStore


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LiveServer:
    def __init__(self, tmp_path, tokens=None, settings: ServerSettings | None = None, **app_kwargs) -> None:
        self.db, self.bundles = tmp_path / "live.db", tmp_path / "live-bundles"
        self.tokens = tokens or {"tok-a": "alice", "tok-b": "bob", "tok-c": "carol", "tok-d": "dave"}
        self.settings, self.app_kwargs = settings, app_kwargs
        self.port = free_port()
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> "LiveServer":
        store = SqliteRoomStore(self.db)
        app = create_app(store, self.tokens, self.bundles, settings=self.settings, **self.app_kwargs)
        self._server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="error", lifespan="off"))
        self._thread = threading.Thread(target=self._server.run, daemon=True, name="live-server")
        self._thread.start()
        for _ in range(100):
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.2):
                    return self
            except OSError:
                time.sleep(0.05)
        raise RuntimeError("the test server did not start")

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
            if self._thread:
                self._thread.join(timeout=10)
        self._server = None

    def restart(self) -> None:
        self.stop()
        self.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"
