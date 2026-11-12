"""A host's side of the registry: claim a slot, keep it alive, give it back, and look up where another host is."""

from __future__ import annotations

import time
from typing import Callable

from . import crypto
from .registry_server import signing_message
from .tunnel_registry import verify_answer


class RegistryClient:
    def __init__(self, http, identity, registry_vk: bytes, clock: Callable[[], float] = time.time) -> None:
        """`http` has .request(method, path, ...) returning a response with .status_code and .json() (httpx.Client or a TestClient)."""
        self.http, self.identity, self.registry_vk, self.clock = http, identity, registry_vk, clock
        self.offset = 0.0  # seconds to add to this machine's clock to match the registry's; learned from a refused request

    def now(self) -> float:
        return self.clock() + self.offset

    def _signed(self, method: str, path: str) -> dict:
        stamp, nonce = f"{self.now():.3f}", crypto.random_bytes(12).hex()
        pub = self.identity.public()
        sig = self.identity.sign(signing_message(method, path, stamp, nonce, b""))
        return {"x-council-key": f"{pub.sign_vk.hex()}.{pub.box_pk.hex()}", "x-council-time": stamp, "x-council-nonce": nonce,
                "x-council-sig": sig.hex()}

    def _call(self, method: str, path: str) -> dict:
        resp = self.http.request(method, path, headers=self._signed(method, path))
        if resp.status_code == 401 and "x-registry-time" in resp.headers:
            try:
                self.offset = float(resp.headers["x-registry-time"]) - self.clock()  # this machine's clock is off: learn by how much, try once more
            except ValueError:
                pass
            else:
                resp = self.http.request(method, path, headers=self._signed(method, path))
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail", "")
            except ValueError:
                detail = ""
            raise ConnectionError(detail or f"the registry answered {resp.status_code}")
        return resp.json()

    def claim(self) -> dict:
        return self._call("POST", "/v1/claim")

    def heartbeat(self) -> float:
        return float(self._call("POST", "/v1/heartbeat")["lease_expires"])

    def release(self) -> bool:
        return bool(self._call("POST", "/v1/release")["released"])

    def where_is(self, host_device_id: str) -> str:
        """The public address of a host, only if the registry's signed answer checks out for that exact host."""
        resp = self.http.request("GET", f"/v1/lookup/{host_device_id}")
        if resp.status_code != 200:
            raise ConnectionError("the registry has no public address for this host right now")
        return verify_answer(resp.json(), self.registry_vk, host_device_id, self.now())
