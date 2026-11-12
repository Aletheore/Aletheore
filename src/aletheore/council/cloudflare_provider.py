"""The real tunnel provider: Cloudflare's API. NOT yet run against the real service.

Written from Cloudflare's documentation and tested only against a recorded, fake transport that checks the order of calls. Before any
slot is handed out for real, the sequence in `rotate` (change the secret, drop connections, old token refused) must be tried on a
throwaway tunnel with the owner's go-ahead, because the docs do not promise that changing the secret alone disconnects a running tunnel.

The API token is read from the environment or a file outside the repository (never an argument, never logged)."""

from __future__ import annotations

import base64
import os

import httpx

API = "https://api.cloudflare.com/client/v4"


class CloudflareError(Exception):
    pass


class CloudflareProvider:
    def __init__(self, token: str, account_id: str, zone_id: str, domain: str, local_port: int = 8765,
                 client: httpx.Client | None = None) -> None:
        if not token:
            raise CloudflareError("no Cloudflare API token: set COUNCIL_CF_TOKEN (or COUNCIL_CF_TOKEN_FILE); never put it on a command line")
        self.account, self.zone, self.domain, self.port = account_id, zone_id, domain, local_port
        self.http = client or httpx.Client(base_url=API, timeout=20)
        self.http.headers["Authorization"] = f"Bearer {token}"  # kept off the repr and out of logs

    @classmethod
    def from_env(cls, account_id: str, zone_id: str, domain: str, local_port: int = 8765) -> "CloudflareProvider":
        token = os.environ.get("COUNCIL_CF_TOKEN", "")
        path = os.environ.get("COUNCIL_CF_TOKEN_FILE")
        if not token and path:
            token = open(path, encoding="utf-8").read().strip()
        return cls(token, account_id, zone_id, domain, local_port)

    def _call(self, method: str, path: str, **kw) -> dict:
        resp = self.http.request(method, path, **kw)
        try:
            data = resp.json()
        except ValueError:
            raise CloudflareError(f"Cloudflare answered {resp.status_code} with something that is not JSON") from None
        if resp.status_code >= 400 or not data.get("success", False):
            errors = "; ".join(str(e.get("message", e)) for e in data.get("errors", [])) or f"status {resp.status_code}"
            raise CloudflareError(f"Cloudflare refused: {errors}")
        return data

    def _existing_tunnel(self, name: str) -> str | None:
        found = self._call("GET", f"/accounts/{self.account}/cfd_tunnel", params={"name": name, "is_deleted": "false"}).get("result") or []
        return found[0]["id"] if found else None

    def _dns_exists(self, hostname: str) -> bool:
        return bool(self._call("GET", f"/zones/{self.zone}/dns_records", params={"name": hostname, "type": "CNAME"}).get("result"))

    def create_tunnel(self, slot: str) -> str:
        """A tunnel, its fixed ingress (only the local Council port: a host cannot publish anything else under our domain) and its DNS name.
        Safe to repeat: a slot whose tunnel or DNS name already exists (a run that stopped half way) is completed, not duplicated."""
        name, hostname = f"council-{slot}", f"{slot}.{self.domain}"
        tid = self._existing_tunnel(name)
        if tid is None:
            secret = base64.b64encode(os.urandom(32)).decode()
            tid = self._call("POST", f"/accounts/{self.account}/cfd_tunnel",
                             json={"name": name, "tunnel_secret": secret, "config_src": "cloudflare"})["result"]["id"]
        self._call("PUT", f"/accounts/{self.account}/cfd_tunnel/{tid}/configurations",
                   json={"config": {"ingress": [{"hostname": hostname, "service": f"http://localhost:{self.port}"},
                                                {"service": "http_status:404"}]}})
        if not self._dns_exists(hostname):
            self._call("POST", f"/zones/{self.zone}/dns_records",
                       json={"type": "CNAME", "name": hostname, "content": f"{tid}.cfargotunnel.com", "proxied": True})
        return tid

    def create_service_tunnel(self, name: str, hostname: str, service_url: str) -> tuple[str, str]:
        """A tunnel for one of our own services (the registry), at `hostname`, forwarding only to `service_url`. Returns (id, run token)."""
        secret = base64.b64encode(os.urandom(32)).decode()
        tid = self._call("POST", f"/accounts/{self.account}/cfd_tunnel",
                         json={"name": name, "tunnel_secret": secret, "config_src": "cloudflare"})["result"]["id"]
        self._call("PUT", f"/accounts/{self.account}/cfd_tunnel/{tid}/configurations",
                   json={"config": {"ingress": [{"hostname": hostname, "service": service_url}, {"service": "http_status:404"}]}})
        self._call("POST", f"/zones/{self.zone}/dns_records",
                   json={"type": "CNAME", "name": hostname, "content": f"{tid}.cfargotunnel.com", "proxied": True})
        return tid, str(self._call("GET", f"/accounts/{self.account}/cfd_tunnel/{tid}/token")["result"])

    def rotate(self, tunnel_id: str) -> str:
        """A new secret, every connection dropped, then the new run token. In this order so the old token cannot reconnect."""
        secret = base64.b64encode(os.urandom(32)).decode()
        self._call("PATCH", f"/accounts/{self.account}/cfd_tunnel/{tunnel_id}", json={"tunnel_secret": secret})
        self._call("DELETE", f"/accounts/{self.account}/cfd_tunnel/{tunnel_id}/connections")
        return str(self._call("GET", f"/accounts/{self.account}/cfd_tunnel/{tunnel_id}/token")["result"])

    def connections(self, tunnel_id: str) -> list[dict]:
        """The connectors currently attached to the tunnel (empty when nothing is running)."""
        return list(self._call("GET", f"/accounts/{self.account}/cfd_tunnel/{tunnel_id}/connections").get("result") or [])

    def delete_slot(self, slot: str, tunnel_id: str) -> None:
        """Remove a slot completely: its DNS name, its connections and the tunnel itself (used to tidy up a test or shrink the pool)."""
        name = f"{slot}.{self.domain}"
        found = self._call("GET", f"/zones/{self.zone}/dns_records", params={"name": name, "type": "CNAME"}).get("result") or []
        for record in found:
            self._call("DELETE", f"/zones/{self.zone}/dns_records/{record['id']}")
        self._call("DELETE", f"/accounts/{self.account}/cfd_tunnel/{tunnel_id}/connections")
        self._call("DELETE", f"/accounts/{self.account}/cfd_tunnel/{tunnel_id}")

    def __repr__(self) -> str:
        return f"CloudflareProvider(zone={self.zone!r}, domain={self.domain!r})"
