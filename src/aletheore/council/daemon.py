"""Per-machine worker daemon: answers task requests and debate turns addressed to this machine's participant.

It only acts on events posted by the room host, and only on the two ops it knows
("request", "debate_turn"). It never runs anything on another machine and never
reads another machine's files; everything it sends back is an event or a bundle.
"""

from __future__ import annotations

import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from aletheore.council.bundle_store import BundleStore
from aletheore.council.attachments import fetch_attachment
from aletheore.council.bundle_transport import TransportError, create_bundle
from aletheore.council.protocol import Event, EventDraft, EventType, clip_text
from aletheore.council.room import WORKER_TIMEOUT_S, Room
from aletheore.council.runners import WorkerRunner
from aletheore.council.protocol import is_horizon
from aletheore.council.snapshots import SNAPSHOT_EVERY, latest_snapshot, maybe_snapshot
from aletheore.council.state import Projector, RoomState
from aletheore.council.store import RoomStore, read_all
from aletheore.council.worktrees import worker_branch

_FINAL = ("done", "failed", "blocked")


class WorkerDaemon:
    def __init__(
        self,
        store: RoomStore,
        room: Room,
        me: str,
        runners: dict[str, WorkerRunner],
        bundles: BundleStore | None = None,
        poll_interval_s: float = 1.0,
        claim_timeout_s: int = 3600,
        clock: Callable[[], float] = time.time,
        after_seq: int = 0,
        task_timeout_s: int = WORKER_TIMEOUT_S,
        parallel: bool = False,
        max_workers: int = 4,
        monitor=None,
    ) -> None:
        if claim_timeout_s <= task_timeout_s:
            raise ValueError("claim_timeout_s must be longer than task_timeout_s, or a running task could be claimed twice")
        self.store, self.room, self.me, self.runners, self.bundles = store, room, me, runners, bundles
        self.poll_interval_s, self.claim_timeout_s, self.clock, self.task_timeout_s = poll_interval_s, claim_timeout_s, clock, task_timeout_s
        self.after_seq = after_seq
        self.instance = uuid.uuid4().hex
        # The log is never kept in memory: events are folded into the small state and a few indexes as they arrive, so a worker's
        # memory stays flat however long the room has run.
        self._proj = Projector(host=room.host)
        self._my_claims: dict[str, list[Event]] = {}
        self._my_turns: set[str] = set()
        self._decided_threads: set[str] = set()
        self._snap_at = 0
        self._read_to = 0
        self._deferred: dict[str, Event] = {}
        # parallel mode: tasks and debate turns run on worker threads, one lane per agent (an agent's task and turns
        # share its worktree, so they take turns), while the main loop keeps reading the room
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="council-work") if parallel else None
        self._lanes: dict[str, threading.Lock] = {}
        self._lanes_guard = threading.Lock()
        self._inflight: set[str] = set()
        self._futures: list = []
        self.monitor = monitor  # a ConnectionMonitor: when the host has been away a while, stop starting new work
        self._held: list[tuple[str, str]] = []  # finished work whose upload waits for the host to come back

    # -- loop ----------------------------------------------------------------------
    def run_forever(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                self.run_once()
                if self.monitor is not None:
                    self.monitor.ok()
            except ConnectionError:
                if self.monitor is not None:
                    self.monitor.failed("host")  # server blip: try again next poll; the monitor decides how long we call it an absence
            stop.wait(self.poll_interval_s)

    def _lane(self, agent: str) -> threading.Lock:
        with self._lanes_guard:
            return self._lanes.setdefault(agent, threading.Lock())

    def _submit(self, key: str, agent: str, fn):
        """Run `fn` now (inline mode) or on a worker thread in this agent's lane. A key already in flight is skipped."""
        if self._pool is None:
            return fn()
        with self._lanes_guard:
            if key in self._inflight:
                return 0
            self._inflight.add(key)

        def job():
            try:
                with self._lane(agent):
                    return fn()
            except Exception:  # noqa: BLE001  a crashed job must not kill the pool; an unfinished claim is re-claimed later
                return 0
            finally:
                with self._lanes_guard:
                    self._inflight.discard(key)

        self._futures.append(self._pool.submit(job))
        self._futures = [f for f in self._futures if not f.done()]
        return 1

    def close(self) -> None:
        """Stop taking new work and wait for what is running to finish."""
        if self._pool is not None:
            self._pool.shutdown(wait=True, cancel_futures=True)
        self.room.close_brokers()  # the run is over: the agents' localhost tokens die with it

    def wait_idle(self, timeout: float = 30) -> None:
        for f in list(self._futures):
            f.result(timeout=timeout)

    def _down_s(self) -> float:
        return self.monitor.down_seconds() if self.monitor is not None else 0.0

    def _host_away(self) -> bool:
        return self.monitor is not None and self.monitor.state().kind == "away"

    def _retry_held(self) -> None:
        """Upload results that finished while the host was away, as soon as it answers again."""
        for task_id, agent in list(self._held):
            try:
                self._publish(task_id, agent)
            except ConnectionError:
                return  # still away: keep them, in order
            self._held.remove((task_id, agent))

    def run_once(self) -> int:
        if self._held:
            self._retry_held()
        fresh = read_all(self.store, self.room.room_id, self._read_to)
        for e in fresh:
            if is_horizon(e):  # older events are in cold storage: start the state from the room's snapshot instead
                snap = latest_snapshot(self.store, self.room.room_id, self.room.host)
                if snap is not None and self._proj.last_seq < snap[0]:
                    self._proj = Projector(host=self.room.host, state=snap[1], last_seq=snap[0])
                continue
            self._proj.apply(e)
            if e.sender_participant == self.me:
                if e.type is EventType.TASK and e.body.get("op") == "claimed" and isinstance(e.body.get("task_id"), str):
                    self._my_claims.setdefault(e.body["task_id"], []).append(e)
                elif e.client_event_id.startswith("turn:"):
                    self._my_turns.add(e.client_event_id)
            if e.type is EventType.DECISION:
                self._decided_threads.add(e.thread_id)
        if fresh:
            self._read_to = fresh[-1].seq
            if self.me == self.room.host and self._read_to - self._snap_at >= SNAPSHOT_EVERY:
                try:
                    self._snap_at = maybe_snapshot(self.store, self.room.room_id, self.me) or self._read_to  # the host keeps a snapshot current
                except (ConnectionError, OSError, ValueError):
                    pass  # a snapshot is an optimisation: try again at the next opportunity
        state = self._proj.state
        actions = 0
        todo = [e for e in fresh if e.seq > self.after_seq]
        for e in todo:
            if e.type is not EventType.TASK or e.sender_participant != state.host:
                continue
            op = e.body.get("op")
            if op == "request":
                self._deferred[e.body.get("task_id", "")] = e
            elif op == "debate_turn" and e.body.get("participant") == self.me:
                actions += self._submit(f"turn:{e.body.get('turn_id')}", str(e.body.get("agent")), lambda e=e: self._handle_turn(e))
        if todo:
            self.after_seq = todo[-1].seq
        for task_id, req in list(self._deferred.items()):
            try:
                outcome = self._handle_request(req, state)
            except (KeyError, ValueError, TypeError):
                outcome = 0  # a malformed request or claim is dropped, never allowed to stop the worker
            if outcome is None:
                continue  # keep waiting (someone else's claim may go stale)
            del self._deferred[task_id]
            actions += outcome
        return actions

    # -- task requests -------------------------------------------------------------
    def _claims(self, task_id: str) -> list[Event]:
        # only this participant's own claims count: nobody else can claim, or stall, their tasks
        return list(self._my_claims.get(task_id, []))

    def _handle_request(self, req: Event, state: RoomState) -> int | None:
        """Returns the number of actions taken, or None to keep the request pending."""
        body = req.body
        task_id, area = body.get("task_id"), state.areas.get(body.get("area", ""))
        if not task_id or area is None or area.owner_participant != self.me:
            return 0
        task = state.tasks.get(task_id)
        if task is not None and task.status in _FINAL:
            return 0
        claims = self._claims(task_id)
        attempt = len(claims)
        if claims:
            last = claims[-1]
            if last.body.get("instance") == self.instance:
                return 0
            # a lease pauses while the host is away: only time the host could be reached counts towards a claim going stale
            paused = max(0.0, self._down_s() - float(last.body.get("down", 0.0)))
            stale = self.clock() - float(last.body.get("at", 0.0)) - paused > self.claim_timeout_s
            if not (stale and last.sender_participant == self.me):
                return None
        if self._host_away():
            return None  # the host has been gone a while: do not start new work, keep the request pending for when it returns
        claim = self.store.append(EventDraft(
            self.room.room_id, f"claim:{task_id}:{attempt}", task_id, EventType.TASK, self.me, area.agent_id, None,
            {"op": "claimed", "task_id": task_id, "instance": self.instance, "at": self.clock(), "down": self._down_s()},
        ))
        if claim.body.get("instance") != self.instance:
            return 0  # another daemon for this participant claimed it first
        runner = self.runners.get(area.agent_id)
        if runner is None:
            self.store.append(EventDraft(self.room.room_id, f"{task_id}:failed", task_id, EventType.TASK, self.me, area.agent_id, None,
                                         {"op": "failed", "task_id": task_id, "note": f"no local runner for agent {area.agent_id}"}))
            return 1
        def work() -> int:
            sync_note = self.room.sync_integration(area.agent_id, self.me, self.bundles) if body.get("sync_integration") else ""
            images, notes = self._attachments(body)
            notes = " ".join(n for n in (sync_note, notes) if n)
            self.room.execute_task(task_id, area.name, body.get("instruction", ""), runner, self.task_timeout_s, images=images, notes=notes)
            try:
                self._publish(task_id, area.agent_id)
            except ConnectionError:
                self._held.append((task_id, area.agent_id))  # the work is done and kept; it is uploaded when the host answers
            return 1

        return self._submit(f"task:{task_id}", area.agent_id, work)

    def _attachments(self, body: dict) -> tuple[tuple[Path, ...], str]:
        """Download a request's images next to (not inside) the worktrees. One that cannot be loaded is reported to
        the agent instead of stopping the task."""
        refs = body.get("attachments")
        if not isinstance(refs, list) or self.bundles is None:
            return (), ""
        dest = self.room.state_dir / "attachments" / self.room.room_id
        paths, failed = [], 0
        for ref in refs[:4]:
            try:
                paths.append(fetch_attachment(self.bundles, self.room.room_id, ref, dest))
            except (ValueError, KeyError, ConnectionError, OSError):
                failed += 1
        return tuple(paths), (f"{failed} attached image(s) could not be loaded." if failed else "")

    def _publish(self, task_id: str, agent: str) -> None:
        if self.bundles is None or self.room.base_sha is None:
            return
        branch = worker_branch(self.room.room_id, agent)
        out = self.room.state_dir / "bundles-out" / self.room.room_id / f"{agent}.bundle"
        try:
            info = create_bundle(self.room.repo, branch, self.room.base_sha, out)
            self.bundles.put_bundle(self.room.room_id, info.sha256, out)
        except ConnectionError:
            raise  # an absence is not a failure: the caller holds the result and tries again
        except (TransportError, ValueError, OSError, PermissionError, KeyError) as exc:
            self.store.append(EventDraft(self.room.room_id, f"publish-failed:{task_id}", task_id, EventType.SYSTEM, self.me, agent, None,
                                         {"subtype": "publish_failed", "task_id": task_id, "note": clip_text(str(exc), 500)}))
            return
        self.store.append(EventDraft(
            self.room.room_id, f"published:{task_id}:{info.sha256[:16]}", task_id, EventType.SYSTEM, self.me, agent, None,
            {"subtype": "branch_published", "task_id": task_id, "participant": self.me, "branch": branch,
             "sha256": info.sha256, "size": info.size, "tip_sha": info.tip_sha, "base_sha": info.base_sha},
        ))

    # -- debate turns --------------------------------------------------------------
    def _handle_turn(self, e: Event) -> int:
        body = e.body
        turn_id, agent = body.get("turn_id"), body.get("agent")
        if not isinstance(turn_id, str) or not turn_id:
            return 0
        if f"turn:{turn_id}" in self._my_turns:
            return 0
        if e.thread_id in self._decided_threads:
            return 0  # the host already gave up on this debate and issued a verdict
        runner = self.runners.get(agent or "")
        if runner is None:
            self.store.append(EventDraft(self.room.room_id, f"turn:{turn_id}", e.thread_id, EventType.SYSTEM, self.me, agent, None,
                                         {"subtype": "turn_failed", "turn_id": turn_id, "note": f"no local runner for agent {agent}"}))
            return 1
        worktree, _ = self.room._worktree(agent)
        result = runner.run(
            clip_text(body.get("instruction", "")), worktree, self.room._mcp_config(self.me, agent),
            self.room._identity(self.me, agent), self.task_timeout_s, read_only=True,
        )
        if not result.ok:
            self.store.append(EventDraft(self.room.room_id, f"turn:{turn_id}", e.thread_id, EventType.SYSTEM, self.me, agent, None,
                                         {"subtype": "turn_failed", "turn_id": turn_id, "note": clip_text(result.final_text, 500)}))
            return 1
        etype = EventType.CLAIM if int(body.get("round", 1)) == 1 else EventType.CHALLENGE
        self.store.append(EventDraft(self.room.room_id, f"turn:{turn_id}", e.thread_id, etype, self.me, agent, None,
                                     {"text": clip_text(result.final_text), "round": body.get("round", 1), "turn_id": turn_id,
                                      **({"kind": "answer"} if body.get("post_kind") == "answer" else {})}))
        return 1
