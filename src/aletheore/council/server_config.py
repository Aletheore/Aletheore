"""Server settings and the small pieces of operations code the room server needs: limits, metrics, client addresses.

Settings come from environment variables (prefix COUNCIL_) and, optionally, a JSON config file. A config file may not hold
secrets (tokens, database passwords): those come from the environment or a secrets file mounted into the container.
Nothing in here logs or prints a secret.
"""

from __future__ import annotations

import ipaddress
import json
import math
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Callable, Mapping


@dataclass
class ServerSettings:
    store: str = "sqlite"  # sqlite (LAN, development) or postgres (the hosted server)
    dsn: str = ""  # postgres connection string; environment only
    pg_pool_size: int = 10
    db: str = ""  # sqlite file
    bundle_dir: str = ""
    host: str = "127.0.0.1"
    port: int = 8765
    allow_public: bool = False
    accounts: bool = False  # per-person accounts with signed login (instead of, or beside, the stage 2 token file)
    retention_days: int = 7
    trusted_proxies: list[str] = field(default_factory=list)  # addresses/networks whose X-Forwarded-For is believed
    rate_limit_events_per_min: int = 300  # per participant
    rate_limit_events_burst: int = 60
    rate_limit_ip_per_min: int = 1200  # per client address, checked before anything else
    rate_limit_ip_burst: int = 200
    auth_fail_per_min: int = 30  # failed logins per client address
    auth_fail_burst: int = 10
    max_rooms_per_host: int = 50
    threads: int = 16  # worker threads for database and file work (more is not faster: see docs/loadtest.md)
    log_requests: str = "all"  # all, errors (only failed requests) or off
    closed_room_retention_days: int = 30  # a closed room is deleted this long after its last event; 0 keeps them forever
    sweep_interval_s: int = 3600  # how often the housekeeping runs
    max_members_per_room: int = 50
    archive_keep_events: int = 2000  # events kept live behind a room's newest snapshot; older ones move to cold storage (0 turns archiving off)
    archive_min_age_days: int = 30  # and an event must also be this old before it moves
    max_connections: int = 512  # open connections the server serves at once; beyond this new ones get a plain 503 instead of piling up
    file_transfer_cap_mb_per_day: int = 0  # file bytes (images, work bundles) this server moves in a rolling 24 hours; 0 = no cap. Messages are never capped.
    admin_token_hash: str = ""  # sha-256 (hex) of the admin token that unlocks /metrics; empty disables it


_ENV = {
    "COUNCIL_STORE": "store", "COUNCIL_PG_DSN": "dsn", "COUNCIL_PG_POOL": "pg_pool_size", "COUNCIL_DB": "db",
    "COUNCIL_BUNDLE_DIR": "bundle_dir", "COUNCIL_HOST": "host", "COUNCIL_PORT": "port", "COUNCIL_ALLOW_PUBLIC": "allow_public", "COUNCIL_ACCOUNTS": "accounts",
    "COUNCIL_RETENTION_DAYS": "retention_days", "COUNCIL_TRUSTED_PROXIES": "trusted_proxies",
    "COUNCIL_RATE_EVENTS_PER_MIN": "rate_limit_events_per_min", "COUNCIL_RATE_EVENTS_BURST": "rate_limit_events_burst",
    "COUNCIL_RATE_IP_PER_MIN": "rate_limit_ip_per_min", "COUNCIL_RATE_IP_BURST": "rate_limit_ip_burst",
    "COUNCIL_AUTH_FAIL_PER_MIN": "auth_fail_per_min", "COUNCIL_AUTH_FAIL_BURST": "auth_fail_burst",
    "COUNCIL_MAX_ROOMS_PER_HOST": "max_rooms_per_host", "COUNCIL_ADMIN_TOKEN_HASH": "admin_token_hash",
    "COUNCIL_FILE_CAP_MB_PER_DAY": "file_transfer_cap_mb_per_day", "COUNCIL_CLOSED_ROOM_RETENTION_DAYS": "closed_room_retention_days",
    "COUNCIL_SWEEP_INTERVAL_S": "sweep_interval_s", "COUNCIL_MAX_CONNECTIONS": "max_connections", "COUNCIL_ARCHIVE_KEEP_EVENTS": "archive_keep_events", "COUNCIL_ARCHIVE_MIN_AGE_DAYS": "archive_min_age_days", "COUNCIL_MAX_MEMBERS_PER_ROOM": "max_members_per_room", "COUNCIL_THREADS": "threads", "COUNCIL_LOG_REQUESTS": "log_requests",
}
_FORBIDDEN_IN_FILE = ("token", "dsn", "password", "secret")
_POSITIVE = ("max_connections", "sweep_interval_s", "max_members_per_room", "threads", "pg_pool_size", "retention_days", "rate_limit_events_per_min", "rate_limit_events_burst", "rate_limit_ip_per_min",
             "rate_limit_ip_burst", "auth_fail_per_min", "auth_fail_burst", "max_rooms_per_host")


def _coerce(name: str, raw, current):
    if isinstance(current, bool):
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    if isinstance(current, int):
        try:
            return int(raw)
        except (TypeError, ValueError):
            raise ValueError(f"{name} must be a whole number") from None
    if isinstance(current, list):
        return [p.strip() for p in raw.split(",") if p.strip()] if isinstance(raw, str) else list(raw)
    return str(raw)


def load_settings(config_file: str | Path | None = None, environ: Mapping[str, str] | None = None) -> ServerSettings:
    """Defaults, then the config file, then the environment. Raises ValueError with a plain message when something is wrong."""
    s = ServerSettings()
    known = {f.name for f in fields(ServerSettings)}
    if config_file:
        raw = json.loads(Path(config_file).read_text(encoding="utf-8"))
        for key, value in raw.items():
            if key != "admin_token_hash" and any(w in key.lower() for w in _FORBIDDEN_IN_FILE):
                raise ValueError(f"the config file may not hold secrets ({key}); use the environment or a secrets file")
            if key not in known:
                raise ValueError(f"unknown setting in the config file: {key}")
            setattr(s, key, _coerce(key, value, getattr(s, key)))
    env = environ if environ is not None else {}
    for var, name in _ENV.items():
        if var in env and env[var] != "":
            setattr(s, name, _coerce(name, env[var], getattr(s, name)))
    if s.store not in ("sqlite", "postgres"):
        raise ValueError("store must be sqlite or postgres")
    if s.log_requests not in ("all", "errors", "off"):
        raise ValueError("log_requests must be all, errors or off")
    if s.store == "postgres" and not s.dsn:
        raise ValueError("the postgres store needs COUNCIL_PG_DSN")
    if not 1 <= s.port <= 65535:
        raise ValueError("port must be between 1 and 65535")
    if s.closed_room_retention_days < 0:
        raise ValueError("closed_room_retention_days must be 0 (keep forever) or more")
    if s.file_transfer_cap_mb_per_day < 0:
        raise ValueError("file_transfer_cap_mb_per_day must be 0 (no cap) or more")
    for name in _POSITIVE:
        if getattr(s, name) < 1:
            raise ValueError(f"{name} must be at least 1")
    for net in s.trusted_proxies:
        try:
            ipaddress.ip_network(net, strict=False)
        except ValueError:
            raise ValueError(f"trusted proxy is not an address or network: {net}") from None
    return s


# Only the networks that are genuinely local. (Python's `is_private` also covers documentation and "this network" ranges.)
_LOCAL_NETWORKS = [ipaddress.ip_network(n) for n in
                   ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "fc00::/7", "fe80::/10")]


def check_bind(host: str, allow_public: bool) -> None:
    """Loopback and private-network addresses are fine. Anything that could be reached from the internet (all interfaces,
    a public address, a hostname) needs an explicit flag, and should sit behind a TLS reverse proxy."""
    if host == "localhost":
        return
    try:
        addr = ipaddress.ip_address(host)
        if addr.is_loopback or any(addr in net for net in _LOCAL_NETWORKS):
            return
    except ValueError:
        pass
    if not allow_public:
        raise ValueError(f"refusing to listen on {host}: it may be reachable from the internet. Use --allow-public only behind "
                         "a TLS reverse proxy (or bind to a private address).")


def client_ip(peer: str, forwarded_for: str | None, trusted_proxies: list[str]) -> str:
    """The address to rate-limit. X-Forwarded-For is believed only when the direct peer is a trusted proxy, and then only the
    part the trusted proxies appended: walking from the right, the first address that is not a trusted proxy is the client."""
    nets = [ipaddress.ip_network(t, strict=False) for t in trusted_proxies]

    def trusted(addr: str) -> bool:
        try:
            a = ipaddress.ip_address(addr)
        except ValueError:
            return False
        return any(a in n for n in nets)

    if not peer or not forwarded_for or not trusted(peer):
        return peer
    parts = [p.strip() for p in forwarded_for.split(",") if p.strip()]
    for addr in reversed(parts):
        try:
            ipaddress.ip_address(addr)
        except ValueError:
            return peer
        if not trusted(addr):
            return addr
    return parts[0] if parts else peer


class TransferBudget:
    """How many file bytes this server may move in any rolling 24 hours (all rooms together; there is no per-room limit).

    Only files count: images and work bundles, uploaded or downloaded. Messages never do. Hourly buckets keep it cheap and the window
    smooth. `cap_bytes` of 0 means no cap. The server asks `allows(n)` before moving a file and `add(n)` after it did."""

    HOUR = 3600

    def __init__(self, cap_bytes: int, clock: Callable[[], float] = time.time) -> None:
        self.cap, self.clock = max(0, int(cap_bytes)), clock
        self._buckets: dict[int, int] = {}
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        oldest = int(now // self.HOUR) - 23
        for hour in [h for h in self._buckets if h < oldest]:
            del self._buckets[hour]

    def used(self) -> int:
        with self._lock:
            self._prune(self.clock())
            return sum(self._buckets.values())

    def allows(self, n: int) -> bool:
        return self.cap == 0 or self.used() + n <= self.cap

    def add(self, n: int) -> None:
        with self._lock:
            now = self.clock()
            self._prune(now)
            hour = int(now // self.HOUR)
            self._buckets[hour] = self._buckets.get(hour, 0) + int(n)

    def resets_in_s(self) -> int:
        """Seconds until enough of the window has passed for the oldest bytes to stop counting (0 when nothing is held)."""
        with self._lock:
            now = self.clock()
            self._prune(now)
            if not self._buckets:
                return 0
            return max(0, int((min(self._buckets) + 24) * self.HOUR - now))

    def snapshot(self) -> dict:
        return {"cap_bytes": self.cap, "used_bytes": self.used(), "resets_in_s": self.resets_in_s()}


class RateLimiter:
    """A token bucket per key (a participant, a client address): `burst` at once, refilled at `rate_per_min`.
    One instance's memory is bounded; with several server instances the limits apply per instance (the load test records this)."""

    def __init__(self, rate_per_min: float, burst: int, clock: Callable[[], float] = time.monotonic, max_keys: int = 100_000) -> None:
        self.per_sec, self.burst, self.clock, self.max_keys = rate_per_min / 60.0, float(burst), clock, max_keys
        self._buckets: OrderedDict[str, list[float]] = OrderedDict()  # key -> [tokens, last_seen]
        self._lock = threading.Lock()

    def _bucket(self, key: str) -> list[float]:
        now = self.clock()
        b = self._buckets.get(key)
        if b is None:
            b = [self.burst, now]
            self._buckets[key] = b
            while len(self._buckets) > self.max_keys:
                self._buckets.popitem(last=False)  # forget the least recently used key
        else:
            b[0] = min(self.burst, b[0] + (now - b[1]) * self.per_sec)
            b[1] = now
            self._buckets.move_to_end(key)
        return b

    def allow(self, key: str) -> bool:
        with self._lock:
            b = self._bucket(key)
            if b[0] >= 1.0:
                b[0] -= 1.0
                return True
            return False

    def blocked(self, key: str) -> bool:
        """True when the key has no budget, without spending any."""
        with self._lock:
            return self._bucket(key)[0] < 1.0

    def take(self, key: str) -> None:
        with self._lock:
            b = self._bucket(key)
            b[0] = max(0.0, b[0] - 1.0)

    def retry_after(self, key: str) -> int:
        with self._lock:
            b = self._bucket(key)
            return max(1, math.ceil((1.0 - b[0]) / self.per_sec)) if self.per_sec > 0 else 60

    def size(self) -> int:
        with self._lock:
            return len(self._buckets)


_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)


def _label_text(labels: dict[str, str] | None) -> str:
    if not labels:
        return ""
    esc = lambda v: str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")  # noqa: E731
    return "{" + ",".join(f'{k}="{esc(v)}"' for k, v in sorted(labels.items())) + "}"


class Metrics:
    """Counters and one latency histogram, rendered in the Prometheus text format. It holds counts and timings only:
    never a token, a participant name or message content."""

    def __init__(self) -> None:
        self._counters: dict[tuple[str, tuple], float] = {}
        self._hist: dict[str, list[float]] = {}  # name -> bucket counts..., sum, count
        self._started = time.time()
        self._lock = threading.Lock()

    def inc(self, name: str, labels: dict[str, str] | None = None, n: float = 1) -> None:
        key = (name, tuple(sorted((labels or {}).items())))
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + n

    def observe(self, name: str, value: float) -> None:
        with self._lock:
            h = self._hist.setdefault(name, [0.0] * (len(_BUCKETS) + 2))
            for i, edge in enumerate(_BUCKETS):
                if value <= edge:
                    h[i] += 1
            h[-2] += value
            h[-1] += 1

    def render(self) -> str:
        lines: list[str] = []
        with self._lock:
            for (name, labels), value in sorted(self._counters.items()):
                v = int(value) if float(value).is_integer() else value
                lines.append(f"{name}{_label_text(dict(labels))} {v}")
            for name, h in sorted(self._hist.items()):
                for i, edge in enumerate(_BUCKETS):
                    lines.append(f'{name}_bucket{{le="{edge}"}} {int(h[i])}')
                lines.append(f'{name}_bucket{{le="+Inf"}} {int(h[-1])}')
                lines.append(f"{name}_sum {h[-2]:.6f}")
                lines.append(f"{name}_count {int(h[-1])}")
            lines.append(f"council_uptime_seconds {time.time() - self._started:.0f}")
        return "\n".join(lines) + "\n"
