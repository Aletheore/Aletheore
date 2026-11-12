"""A small TCP proxy for breaking connections on purpose. Used only by the chaos tests.

    proxy = FaultProxy(("127.0.0.1", upstream_port)); port = proxy.start()
    proxy.cut_after_bytes(200, direction="down", once=True)   # the next connection dies after 200 bytes of the server's reply
    proxy.blackhole() / proxy.reset() / proxy.delay(ms) / proxy.loss(p) / proxy.throttle(bytes_per_s)
    proxy.cut_all()   # close every open connection now
    proxy.heal()      # back to passing everything through
"""

from __future__ import annotations

import asyncio
import random
import socket
import struct
import threading


class FaultProxy:
    def __init__(self, upstream: tuple[str, int], seed: int = 0) -> None:
        self.upstream, self.rng = upstream, random.Random(seed)
        self.mode: dict = {"kind": "pass"}
        self._loop = asyncio.new_event_loop()
        self._server: asyncio.AbstractServer | None = None
        self._writers: set[asyncio.StreamWriter] = set()
        self._thread: threading.Thread | None = None
        self.port = 0
        self.connections = 0

    # -- control (callable from any thread) -------------------------------------------------------------------------------
    def _set(self, **mode) -> None:
        self.mode = mode

    def heal(self) -> None:
        self._set(kind="pass")

    def cut_after_bytes(self, n: int, direction: str = "down", once: bool = True) -> None:
        self._set(kind="cut_after", n=n, direction=direction, once=once)

    def reset(self) -> None:
        self._set(kind="reset")

    def blackhole(self) -> None:
        self._set(kind="blackhole")

    def delay(self, ms: float) -> None:
        self._set(kind="delay", ms=ms)

    def loss(self, p: float) -> None:
        self._set(kind="loss", p=p)

    def throttle(self, bytes_per_s: int) -> None:
        self._set(kind="throttle", rate=bytes_per_s)

    def cut_all(self) -> None:
        asyncio.run_coroutine_threadsafe(self._close_all(), self._loop).result(5)

    async def _close_all(self) -> None:
        for w in list(self._writers):
            self._abort(w)

    @staticmethod
    def _abort(w: asyncio.StreamWriter) -> None:
        try:
            sock = w.get_extra_info("socket")
            if sock is not None:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))  # a real reset, not a polite close
        except OSError:
            pass
        try:
            w.close()
        except Exception:  # noqa: BLE001
            pass

    # -- the proxy -------------------------------------------------------------------------------------------------------
    def start(self) -> int:
        ready = threading.Event()

        def run() -> None:
            asyncio.set_event_loop(self._loop)
            self._server = self._loop.run_until_complete(asyncio.start_server(self._handle, "127.0.0.1", 0))
            self.port = self._server.sockets[0].getsockname()[1]
            ready.set()
            self._loop.run_forever()

        self._thread = threading.Thread(target=run, daemon=True, name="fault-proxy")
        self._thread.start()
        ready.wait(5)
        return self.port

    def stop(self) -> None:
        async def shut() -> None:
            if self._server:
                self._server.close()
            await self._close_all()
            for task in [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]:
                task.cancel()  # no pump is left running when the loop stops
            await asyncio.sleep(0.05)

        try:
            asyncio.run_coroutine_threadsafe(shut(), self._loop).result(5)
        except Exception:  # noqa: BLE001
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)

    async def _handle(self, client_r: asyncio.StreamReader, client_w: asyncio.StreamWriter) -> None:
        self.connections += 1
        mode = dict(self.mode)
        self._writers.add(client_w)
        if mode["kind"] == "reset" or (mode["kind"] == "loss" and self.rng.random() < mode["p"]):
            self._abort(client_w)
            self._writers.discard(client_w)
            return
        if mode["kind"] == "blackhole":
            try:
                while await client_r.read(65536):  # accept, read, never answer
                    pass
            finally:
                self._writers.discard(client_w)
            return
        try:
            up_r, up_w = await asyncio.open_connection(*self.upstream)
        except OSError:
            self._abort(client_w)
            self._writers.discard(client_w)
            return
        self._writers.add(up_w)
        if mode["kind"] == "cut_after" and mode.get("once"):
            self.heal()  # only this connection is cut
        budget = {"up": None, "down": None}
        if mode["kind"] == "cut_after":
            budget[mode["direction"]] = mode["n"]

        async def pump(src: asyncio.StreamReader, dst: asyncio.StreamWriter, name: str) -> None:
            try:
                while True:
                    chunk = await src.read(16384)
                    if not chunk:
                        break
                    if mode["kind"] == "delay":
                        await asyncio.sleep(mode["ms"] / 1000)
                    if mode["kind"] == "throttle":
                        for i in range(0, len(chunk), max(1, mode["rate"] // 10)):
                            dst.write(chunk[i:i + max(1, mode["rate"] // 10)])
                            await dst.drain()
                            await asyncio.sleep(0.1)
                        continue
                    left = budget[name]
                    if left is not None:
                        if len(chunk) >= left:
                            dst.write(chunk[:left])
                            await dst.drain()
                            self._abort(client_w)
                            self._abort(up_w)
                            return
                        budget[name] = left - len(chunk)
                    dst.write(chunk)
                    await dst.drain()
            except (ConnectionError, OSError, asyncio.CancelledError):
                pass
            finally:
                for w in (client_w, up_w):
                    try:
                        w.close()
                    except Exception:  # noqa: BLE001
                        pass

        await asyncio.gather(pump(client_r, up_w, "up"), pump(up_r, client_w, "down"))
        self._writers.discard(client_w)
        self._writers.discard(up_w)
