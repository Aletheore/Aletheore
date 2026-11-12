"""Load test for the Council room server.

Simulates many rooms of people at once and measures what the server does about it. Nothing here starts real agents: each
simulated person posts chat events at a steady rate with a realistic mix of sizes, and polls their room once a second for
new events, exactly like the real client does. It records latency (percentiles), how long a posted event takes to reach
the other members ("delivery delay"), errors by kind (429 rate limits are kept apart from real failures), the server's CPU and
memory when it can see them, and how busy the test tool itself was (so a saturated test tool is never mistaken for a slow server).

    python -m tests.council.loadtest.run gen-tokens --users 200 --out tokens.json
    COUNCIL_MAX_ROOMS_PER_HOST=100000 COUNCIL_RATE_EVENTS_PER_MIN=100000 ... council serve --token-file tokens.json ...
    python -m tests.council.loadtest.run run --server http://127.0.0.1:8765 --tokens tokens.json --rooms 50 --participants 4 \
        --events-per-min 2 --duration 120 --out result.json

Results describe the machine and settings the test ran on; they are not a promise about any other machine.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import random
import secrets
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx

# (probability, size in characters): mostly short chat, some agent answers, rarely a big paste. The server's body cap is 64 KiB.
SIZE_MIX = [(0.85, 120), (0.14, 2048), (0.01, 60_000)]


def pick_size(rng: random.Random) -> int:
    r = rng.random()
    acc = 0.0
    for p, size in SIZE_MIX:
        acc += p
        if r < acc:
            return max(1, int(size * rng.uniform(0.5, 1.0)))
    return SIZE_MIX[0][1]


def make_tokens(users: int) -> dict[str, str]:
    """{user name: token}. Tokens are random and used only for the test server."""
    return {f"lt-user-{i}": secrets.token_urlsafe(32) for i in range(users)}


class LatencyStats:
    """Percentiles from a log-spaced histogram (about 2.5% error, constant memory however long the test runs)."""

    _BASE, _MIN = 1.05, 1e-4  # from 0.1 ms upward in 5% steps

    def __init__(self) -> None:
        self.counts: dict[int, int] = {}
        self.n = 0
        self.max = 0.0

    def add(self, seconds: float) -> None:
        idx = max(0, int(math.log(max(seconds, self._MIN) / self._MIN) / math.log(self._BASE)))
        self.counts[idx] = self.counts.get(idx, 0) + 1
        self.n += 1
        self.max = max(self.max, seconds)

    def percentile(self, p: float) -> float:
        if not self.n:
            return 0.0
        target, seen = p / 100.0 * self.n, 0
        for idx in sorted(self.counts):
            seen += self.counts[idx]
            if seen >= target:
                return self._MIN * self._BASE ** (idx + 0.5)
        return self.max

    @classmethod
    def from_counts(cls, counts: dict, max_seconds: float = 0.0) -> "LatencyStats":
        s = cls()
        s.counts = {int(k): int(v) for k, v in counts.items()}
        s.n = sum(s.counts.values())
        s.max = max_seconds
        return s

    def merged_with(self, other: "LatencyStats") -> "LatencyStats":
        out = LatencyStats.from_counts(self.counts, max(self.max, other.max))
        for k, v in other.counts.items():
            out.counts[k] = out.counts.get(k, 0) + v
        out.n = self.n + other.n
        return out

    def to_dict(self) -> dict:
        ms = lambda s: round(s * 1000.0, 2)  # noqa: E731
        return {"count": self.n, "p50_ms": ms(self.percentile(50)), "p95_ms": ms(self.percentile(95)),
                "p99_ms": ms(self.percentile(99)), "max_ms": ms(self.max)}


@dataclass
class LoadConfig:
    server: str
    rooms: int = 10
    participants: int = 4
    events_per_min: float = 2.0  # per participant
    duration_s: float = 60.0
    poll_s: float = 1.0  # how often each participant asks for new events (the real client polls once a second)
    tokens: dict[str, str] = field(default_factory=dict)
    users: int = 0  # size of the pool of simulated people (0: all tokens)
    warmup_s: float = 0.0
    seed: int = 1
    max_connections: int = 500
    timeout_s: float = 30.0
    pid: int | None = None  # server process to sample with `ps`
    container: str | None = None  # or a Docker container to sample with `docker stats`
    sample_interval_s: float = 5.0
    driver: str = "auto"  # httpx (works in-process, heavy) or aiohttp (light: use it for real runs); auto picks aiohttp when it can


@dataclass
class PlannedRoom:
    room_id: str
    host: str
    members: list[str]


def plan_rooms(rooms: int, participants: int, users: list[str], run_id: str) -> list[PlannedRoom]:
    """Each room gets `participants` different people from the pool; people appear in several rooms, as in real life."""
    if participants > len(users):
        raise ValueError(f"a room of {participants} needs at least that many users in the pool (have {len(users)})")
    return [PlannedRoom(f"lt-{run_id}-{i}", users[(i * participants) % len(users)],
                        [users[(i * participants + j) % len(users)] for j in range(participants)]) for i in range(rooms)]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class Resp:
    """What the harness needs from a response, whichever HTTP library produced it."""

    __slots__ = ("status_code", "_body")

    def __init__(self, status_code: int, body: bytes = b"") -> None:
        self.status_code, self._body = status_code, body

    def json(self):
        return json.loads(self._body)


def _pick_driver(cfg: "LoadConfig", transport) -> str:
    if cfg.driver != "auto":
        return cfg.driver
    if transport is None:
        try:
            import aiohttp  # noqa: F401

            return "aiohttp"
        except ImportError:
            pass
    return "httpx"


_PROCS: dict[int, object] = {}


async def sample_resources(pid: int | None, container: str | None) -> dict | None:
    """CPU percent (over the time since the previous sample, so a burst is not averaged away) and resident memory of the server,
    if the test can see it. The first sample of a process only primes the measurement. Never raises."""
    def look() -> dict | None:
        try:
            if pid and not container:
                import psutil

                # The process and everything it started: on Windows a virtual environment's python.exe is only a launcher
                # that runs the real interpreter as a child, and a server may run workers. CPU is the sum over the tree.
                known = _PROCS.get(pid)
                tree = {pid: (known or {}).get(pid) or psutil.Process(pid)}
                for child in tree[pid].children(recursive=True):
                    tree[child.pid] = (known or {}).get(child.pid) or child
                cpu, rss = 0.0, 0
                for p_id, proc in list(tree.items()):
                    try:
                        if known is not None and p_id in known:
                            cpu += proc.cpu_percent(None)
                        else:
                            proc.cpu_percent(None)  # a process seen for the first time only primes its measurement
                        rss += proc.memory_info().rss
                    except psutil.Error:
                        if p_id == pid:
                            raise  # the process we were asked about is gone
                        tree.pop(p_id, None)  # a child that exited between listing and measuring
                _PROCS[pid] = tree
                out = {"cpu_pct": round(cpu, 1) if known is not None else 0.0, "rss_mb": round(rss / 1048576, 1)}
                if known is None:
                    out["primed"] = True
                return out
            if container:
                out = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{.CPUPerc}}|{{.MemUsage}}", container],
                                     capture_output=True, text=True, timeout=20).stdout.strip()
                cpu, mem = out.split("|")
                used = mem.split("/")[0].strip()
                num = float("".join(c for c in used if c.isdigit() or c == "."))
                unit = "".join(c for c in used if c.isalpha()).lower()
                mb = num * {"kib": 1 / 1024, "mib": 1, "gib": 1024, "kb": 1 / 1000, "mb": 1, "gb": 1000}.get(unit, 1)
                return {"cpu_pct": float(cpu.strip("%")), "rss_mb": round(mb, 1)}
            if pid:
                out = subprocess.run(["ps", "-o", "%cpu=,rss=", "-p", str(pid)], capture_output=True, text=True, timeout=10).stdout.split()
                return {"cpu_pct": float(out[0]), "rss_mb": round(int(out[1]) / 1024, 1)}
        except Exception:  # noqa: BLE001  sampling is best effort (no psutil, no such process, no docker)
            _PROCS.pop(pid or -1, None)
            return None
        return None

    return await asyncio.to_thread(look)


async def run_load(cfg: LoadConfig, transport: httpx.AsyncBaseTransport | None = None, include_hist: bool = False) -> dict:
    rng = random.Random(cfg.seed)
    names = sorted(cfg.tokens)[: (cfg.users or len(cfg.tokens))]
    run_id = secrets.token_hex(3)
    rooms = plan_rooms(cfg.rooms, cfg.participants, names, run_id)
    append, read, delivery = LatencyStats(), LatencyStats(), LatencyStats()
    errors = {"429": 0, "5xx": 0, "4xx_other": 0, "transport": 0}
    counts = {"posted": 0, "seen": 0, "requests": 0, "append_errors": 0, "read_errors": 0, "setup_errors": 0, "rooms_created": 0}
    sent_at: dict[tuple[str, str], float] = {}
    samples: list[dict] = []
    notes: list[str] = []
    stop = asyncio.Event()
    t_start = time.monotonic()

    driver = _pick_driver(cfg, transport)
    if driver == "aiohttp":
        import aiohttp

        session = aiohttp.ClientSession(base_url=cfg.server, timeout=aiohttp.ClientTimeout(total=cfg.timeout_s),
                                        connector=aiohttp.TCPConnector(limit=cfg.max_connections, ttl_dns_cache=300))

        class Client:
            async def request(self, method: str, path: str, headers: dict, json_body=None) -> Resp:
                async with session.request(method, path, headers=headers, json=json_body) as r:
                    return Resp(r.status, await r.read())

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                await session.close()

        client_cm = Client()
    else:
        limits = httpx.Limits(max_connections=cfg.max_connections, max_keepalive_connections=cfg.max_connections)
        hx = httpx.AsyncClient(base_url=cfg.server, transport=transport, timeout=cfg.timeout_s, limits=limits)

        class Client:  # type: ignore[no-redef]
            async def request(self, method: str, path: str, headers: dict, json_body=None) -> Resp:
                r = await hx.request(method, path, headers=headers, json=json_body)
                return Resp(r.status_code, r.content)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                await hx.aclose()

        client_cm = Client()
    async with client_cm as client:

        def count_error(status: int | None) -> str:
            kind = "transport" if status is None else "429" if status == 429 else "5xx" if status >= 500 else "4xx_other"
            errors[kind] += 1
            return kind

        async def call(method: str, path: str, user: str, json: dict | None = None) -> Resp | None:
            counts["requests"] += 1
            try:
                return await client.request(method, path, _auth(cfg.tokens[user]), json)
            except Exception:  # noqa: BLE001  any connection-level failure (refused, reset, timeout) is a "transport" error
                return None

        # -- setup: create each room and let the others in (not measured) ---------------------------------------------
        sem = asyncio.Semaphore(50)

        async def set_up(room: PlannedRoom) -> bool:
            """True only when this room itself was created and fully staffed."""
            async with sem:
                r = await call("POST", f"/v1/rooms/{room.room_id}", room.host, json={"host": room.host, "title": "load test"})
                if r is None or r.status_code != 200:
                    counts["setup_errors"] += 1
                    return False
                for member in room.members[1:]:
                    m = await call("POST", f"/v1/rooms/{room.room_id}/members", room.host, json={"participant": member})
                    if m is None or m.status_code != 200:
                        counts["setup_errors"] += 1
                        return False
                counts["rooms_created"] += 1
                return True

        created: list[PlannedRoom] = []

        async def set_up_and_track(room: PlannedRoom) -> None:
            if await set_up(room):  # not "the counter went up": other rooms are being set up at the same time
                created.append(room)

        await asyncio.gather(*(set_up_and_track(r) for r in rooms))
        ready = created  # only rooms that really exist get traffic

        recording = lambda: time.monotonic() - t_start >= cfg.warmup_s  # noqa: E731

        async def writer(room: PlannedRoom, user: str, seed: int) -> None:
            local = random.Random(seed)
            n = 0
            mean_gap = 60.0 / cfg.events_per_min
            await asyncio.sleep(local.uniform(0, mean_gap))
            while not stop.is_set():
                n += 1
                cid = f"lt-{user}-{n}-{secrets.token_hex(3)}"
                text = "x" * pick_size(local)
                payload = {"type": "question", "client_event_id": cid, "thread_id": "room", "reply_to": None,
                           "sender_participant": user, "sender_agent": None, "body": {"text": text, "kind": "chat"}}
                started = time.monotonic()
                sent_at[(room.room_id, cid)] = started
                r = await call("POST", f"/v1/rooms/{room.room_id}/events", user, json=payload)
                took = time.monotonic() - started
                if r is not None and r.status_code == 200:
                    if recording():
                        append.add(took)
                    counts["posted"] += 1
                else:
                    counts["append_errors"] += 1
                    count_error(None if r is None else r.status_code)
                    sent_at.pop((room.room_id, cid), None)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=local.expovariate(1.0 / mean_gap))
                except asyncio.TimeoutError:
                    pass

        async def reader(room: PlannedRoom, user: str, seed: int) -> None:
            local = random.Random(seed)
            after = 0
            await asyncio.sleep(local.uniform(0, cfg.poll_s))
            while not stop.is_set():
                while True:  # like the real client: keep reading until a page comes back empty
                    started = time.monotonic()
                    r = await call("GET", f"/v1/rooms/{room.room_id}/events?after_seq={after}&limit=500", user)
                    took = time.monotonic() - started
                    if r is None or r.status_code != 200:
                        counts["read_errors"] += 1
                        count_error(None if r is None else r.status_code)
                        break
                    if recording():
                        read.add(took)
                    page = r.json()["events"]
                    if not page:
                        break
                    now = time.monotonic()
                    for e in page:
                        after = max(after, e["seq"])
                        if e["sender_participant"] != user:
                            t0 = sent_at.get((room.room_id, e["client_event_id"]))
                            counts["seen"] += 1
                            if t0 is not None and recording():
                                delivery.add(now - t0)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=cfg.poll_s)
                except asyncio.TimeoutError:
                    pass

        async def sampler() -> None:
            while not stop.is_set():
                s = await sample_resources(cfg.pid, cfg.container)
                if s and not s.pop("primed", False):
                    samples.append({"t": round(time.monotonic() - t_start, 1), **s})
                try:
                    await asyncio.wait_for(stop.wait(), timeout=cfg.sample_interval_s)
                except asyncio.TimeoutError:
                    pass

        cpu0 = time.process_time()
        wall0 = time.monotonic()
        tasks = []
        for i, room in enumerate(ready):
            for j, user in enumerate(room.members):
                tasks.append(asyncio.create_task(writer(room, user, cfg.seed * 1_000_003 + i * 101 + j)))
                tasks.append(asyncio.create_task(reader(room, user, cfg.seed * 7_000_001 + i * 103 + j)))
        if cfg.pid or cfg.container:
            tasks.append(asyncio.create_task(sampler()))
        await asyncio.sleep(cfg.duration_s)
        stop.set()
        done, pending = await asyncio.wait(tasks, timeout=cfg.timeout_s + 5) if tasks else (set(), set())
        for t in pending:
            t.cancel()
        wall = time.monotonic() - wall0
        busy = (time.process_time() - cpu0) / wall * 100 if wall > 0 else 0.0

    if busy > 85:
        notes.append(f"the test tool itself was {busy:.0f}% busy: results may reflect the tool, not the server; run more tool processes")
    if counts["setup_errors"]:
        notes.append(f"{counts['setup_errors']} setup calls failed (for example a per-host room limit); fewer rooms ran than asked")
    config = {k: v for k, v in asdict(cfg).items() if k != "tokens"}
    config["users"] = len(names)
    config["driver"] = driver
    return {
        "config": config, "started_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "duration_s": round(wall, 1),
        "rooms_created": counts["rooms_created"], "setup_errors": counts["setup_errors"],
        "events_posted": counts["posted"], "events_seen": counts["seen"],
        "append": {**append.to_dict(), "errors": counts["append_errors"]}, "read": {**read.to_dict(), "errors": counts["read_errors"]},
        "delivery": delivery.to_dict(), "errors": errors, "rate_limited": errors["429"],
        "requests_per_s": round(counts["requests"] / wall, 1) if wall > 0 else 0.0, "harness_busy_pct": round(busy, 1),
        "resource_samples": samples, "notes": notes,
        **({"_hist": {"append": append.counts, "read": read.counts, "delivery": delivery.counts},
            "_max": {"append": append.max, "read": read.max, "delivery": delivery.max}} if include_hist else {}),
    }


def split_rooms(total: int, procs: int) -> list[int]:
    """How many rooms each test process runs: as even as possible, never an empty process."""
    procs = max(1, min(procs, total))
    base, extra = divmod(total, procs)
    return [base + (1 if i < extra else 0) for i in range(procs)]


def merge_results(parts: list[dict]) -> dict:
    """Combine the results of several test processes. Percentiles are recomputed from the combined histograms (never averaged)."""
    out = {k: v for k, v in parts[0].items() if k not in ("_hist", "_max")}
    for section in ("append", "read", "delivery"):
        total = LatencyStats()
        for p in parts:
            total = total.merged_with(LatencyStats.from_counts(p["_hist"][section], p.get("_max", {}).get(section, 0.0)))
        out[section] = {**total.to_dict(), **({"errors": sum(p[section].get("errors", 0) for p in parts)} if "errors" in parts[0][section] else {})}
    for key in ("rooms_created", "setup_errors", "events_posted", "events_seen", "rate_limited"):
        out[key] = sum(p[key] for p in parts)
    out["errors"] = {k: sum(p["errors"][k] for p in parts) for k in parts[0]["errors"]}
    out["requests_per_s"] = round(sum(p["requests_per_s"] for p in parts), 1)
    out["duration_s"] = max(p["duration_s"] for p in parts)
    out["harness_busy_pct"] = max(p["harness_busy_pct"] for p in parts)  # the busiest process is the one that limits the test
    out["harness_procs"] = len(parts)
    out["resource_samples"] = max((p["resource_samples"] for p in parts), key=len)
    out["notes"] = list(dict.fromkeys(n for p in parts for n in p["notes"]))
    out["config"] = {**parts[0]["config"], "rooms": sum(p["config"]["rooms"] for p in parts)}
    return out


def _child(cfg_dict: dict, queue) -> None:
    cfg = LoadConfig(**cfg_dict)
    queue.put(asyncio.run(run_load(cfg, include_hist=True)))


def run_load_multi(cfg: LoadConfig, procs: int) -> dict:
    """Run the load from several processes so the test tool is not the bottleneck."""
    import multiprocessing as mp

    shares = split_rooms(cfg.rooms, procs)
    ctx = mp.get_context("spawn")
    queue = ctx.Queue()
    children = []
    for i, rooms in enumerate(shares):
        child_cfg = {**asdict(cfg), "rooms": rooms, "seed": cfg.seed + i * 9973,
                     "pid": cfg.pid if i == 0 else None, "container": cfg.container if i == 0 else None}  # one process samples the server
        p = ctx.Process(target=_child, args=(child_cfg, queue))
        p.start()
        children.append(p)
    parts = [queue.get(timeout=cfg.duration_s + cfg.timeout_s + 120) for _ in children]
    for p in children:
        p.join(30)
    return merge_results(parts)


def write_results(results: dict, path: str | Path) -> None:
    Path(path).write_text(json.dumps(results, indent=2), encoding="utf-8")


def _summary(r: dict) -> str:
    row = lambda name, d: f"  {name:<9} n={d['count']:<8} p50={d['p50_ms']:<8} p95={d['p95_ms']:<8} p99={d['p99_ms']:<8} max={d['max_ms']} ms"  # noqa: E731
    lines = [f"rooms {r['rooms_created']} | events posted {r['events_posted']} | requests/s {r['requests_per_s']} | harness busy {r['harness_busy_pct']}%",
             row("append", r["append"]), row("read", r["read"]), row("delivery", r["delivery"]),
             f"  errors {r['errors']}"]
    if r["resource_samples"]:
        peak_cpu = max(s["cpu_pct"] for s in r["resource_samples"])
        peak_mem = max(s["rss_mb"] for s in r["resource_samples"])
        lines.append(f"  server peak cpu {peak_cpu}% | peak memory {peak_mem} MB")
    lines += [f"  NOTE: {n}" for n in r["notes"]]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m tests.council.loadtest.run")
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gen-tokens", help="write a token file for the test server")
    g.add_argument("--users", type=int, default=200)
    g.add_argument("--out", required=True)
    r = sub.add_parser("run", help="drive the load")
    r.add_argument("--server", required=True)
    r.add_argument("--tokens", required=True)
    r.add_argument("--rooms", type=int, default=10)
    r.add_argument("--participants", type=int, default=4)
    r.add_argument("--events-per-min", type=float, default=2.0)
    r.add_argument("--duration", type=float, default=60.0)
    r.add_argument("--poll", type=float, default=1.0)
    r.add_argument("--users", type=int, default=0)
    r.add_argument("--warmup", type=float, default=0.0)
    r.add_argument("--seed", type=int, default=1)
    r.add_argument("--procs", type=int, default=1, help="test processes (use several for thousands of clients)")
    r.add_argument("--driver", default="auto", choices=("auto", "httpx", "aiohttp"))
    r.add_argument("--pid", type=int)
    r.add_argument("--container")
    r.add_argument("--out")
    a = p.parse_args(argv)
    if a.cmd == "gen-tokens":
        path = Path(a.out)
        path.write_text(json.dumps(make_tokens(a.users)), encoding="utf-8")
        os.chmod(path, 0o600)
        print(f"wrote {a.users} tokens to {path}")
        return 0
    tokens = json.loads(Path(a.tokens).read_text(encoding="utf-8"))
    cfg = LoadConfig(server=a.server, rooms=a.rooms, participants=a.participants, events_per_min=a.events_per_min, duration_s=a.duration,
                     poll_s=a.poll, tokens=tokens, users=a.users, warmup_s=a.warmup, seed=a.seed, pid=a.pid, container=a.container, driver=a.driver)
    result = run_load_multi(cfg, a.procs) if a.procs > 1 else asyncio.run(run_load(cfg))
    print(_summary(result))
    if a.out:
        write_results(result, a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
