"""Container entry point for the tunnel registry: `python -m aletheore.council.registry_main`.

Everything comes from the environment, and every secret from a file (mounted as a Docker secret), never from a value in the
environment or on a command line:
  COUNCIL_REGISTRY_DOMAIN        aletheorecouncil.com
  COUNCIL_REGISTRY_ACCOUNT_ID    Cloudflare account id (not secret)
  COUNCIL_REGISTRY_ZONE_ID       Cloudflare zone id (not secret)
  COUNCIL_CF_TOKEN_FILE          file holding the scoped Cloudflare API token
  COUNCIL_REGISTRY_ADMIN_TOKEN_FILE   file holding the admin token that may block a host (optional)
  COUNCIL_REGISTRY_DATA          directory for the database and the signing key (a volume; back it up)
  COUNCIL_REGISTRY_SLOTS         how many free slots to keep (0 = create none; slots are only ever added, never removed under a holder)
  COUNCIL_REGISTRY_HOST_PORT     the local port each host's tunnel forwards to (8765)
  COUNCIL_TRUSTED_PROXIES        addresses whose client-address header is believed (the tunnel sidecar's network)
The signing key is made on first start and kept in the data directory; its PUBLIC half is printed so it can be pinned in clients.
Losing the key means every client pin stops matching, so it is part of the backup."""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

from . import crypto
from .cloudflare_provider import CloudflareProvider
from .registry_server import create_registry_app
from .tunnel_registry import Registry

SWEEP_EVERY_S = 300


def load_signing_key(data: Path) -> tuple[bytes, bytes]:
    """The registry's signing key pair, created once. The file is readable only by its owner."""
    path = data / "registry-signing.key"
    if path.exists():
        raw = bytes.fromhex(path.read_text().strip())
        if len(raw) != 64:
            raise ValueError("the registry signing key file is damaged; restore it from the backup, do not make a new one")
        return raw[:32], raw[32:]
    sk, vk = crypto.generate_signing_keypair()
    data.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write((sk + vk).hex())
    return sk, vk


def read_secret(path_var: str, environ=os.environ, required: bool = True) -> str:
    path = environ.get(path_var, "")
    if not path:
        if required:
            raise ValueError(f"{path_var} must name a file holding the secret (mount it as a Docker secret)")
        return ""
    return Path(path).read_text(encoding="utf-8").strip()


def sweep_loop(registry: Registry, stop: threading.Event, every: float = SWEEP_EVERY_S, on_error=lambda e: print(f"sweep failed: {e}", file=sys.stderr)) -> None:
    """Free leases whose grace ran out even when nobody is asking, so the public counter stays true."""
    while not stop.wait(every):
        try:
            registry.sweep()
        except Exception as exc:  # noqa: BLE001 - the loop must outlive any single failure
            on_error(exc)


def grow_pool(registry: Registry, wanted: int, stop: threading.Event, pause: float = 0.5, retry_s: float = 60.0,
              say=lambda m: print(m, flush=True)) -> None:
    """Make slots one at a time until there are `wanted`, while the service already answers for the ones that exist. A failure is
    reported and tried again after a pause; a slot is only counted once it is fully made, and making one twice is harmless."""
    import sqlite3
    from contextlib import closing

    while not stop.is_set():
        with closing(sqlite3.connect(registry.path)) as c:
            have = c.execute("SELECT COUNT(*) FROM slots").fetchone()[0]
        if have >= wanted:
            say(f"pool complete: {have} slots")
            return
        try:
            registry.ensure_slots(have + 1)
            say(f"slots: {have + 1}/{wanted}")
        except Exception as exc:  # noqa: BLE001 - report, wait, try again
            say(f"could not make slot {have + 1}: {exc}; trying again in {int(retry_s)} s")
            if stop.wait(retry_s):
                return
            continue
        if stop.wait(pause):  # a polite pace towards the provider's API
            return


def main(environ=os.environ) -> int:
    import uvicorn

    try:
        domain = environ["COUNCIL_REGISTRY_DOMAIN"]
        provider = CloudflareProvider(read_secret("COUNCIL_CF_TOKEN_FILE", environ), environ["COUNCIL_REGISTRY_ACCOUNT_ID"],
                                      environ["COUNCIL_REGISTRY_ZONE_ID"], domain, int(environ.get("COUNCIL_REGISTRY_HOST_PORT", "8765")))
        data = Path(environ.get("COUNCIL_REGISTRY_DATA", "/data"))
        sk, vk = load_signing_key(data)
        slots = int(environ.get("COUNCIL_REGISTRY_SLOTS", "0"))
        admin = read_secret("COUNCIL_REGISTRY_ADMIN_TOKEN_FILE", environ, required=False)
        trusted = [t.strip() for t in environ.get("COUNCIL_TRUSTED_PROXIES", "127.0.0.1").split(",") if t.strip()]
    except (KeyError, ValueError, OSError) as exc:
        print(f"cannot start: {exc}", file=sys.stderr)
        return 2
    registry = Registry(data / "registry.db", provider, domain, (sk, vk), slots=0)  # slots are made in the background below
    print(f"REGISTRY PUBLIC KEY (pin this in clients): {vk.hex()}", flush=True)
    stop = threading.Event()
    threading.Thread(target=sweep_loop, args=(registry, stop), daemon=True, name="registry-sweep").start()
    if slots > 0:
        threading.Thread(target=grow_pool, args=(registry, slots, stop), daemon=True, name="registry-pool").start()
    app = create_registry_app(registry, admin_token=admin, trusted_proxies=trusted)
    uvicorn.run(app, host=environ.get("COUNCIL_REGISTRY_HOST", "0.0.0.0"), port=int(environ.get("COUNCIL_REGISTRY_PORT", "18700")),  # nosec B104 - runs in a container behind a tunnel
                log_level="warning", access_log=False, proxy_headers=False, server_header=False, timeout_graceful_shutdown=20)
    stop.set()
    return 0


if __name__ == "__main__":
    sys.exit(main())
