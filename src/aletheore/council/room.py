"""A room on one machine: defines areas, runs workers in worktrees, raises conflicts."""

from __future__ import annotations

import json
import sys
import threading
import time
import uuid
from pathlib import Path

from aletheore.council.agent_posts import WORKER_POST_TYPES
from aletheore.council.approval import apply_verdict, maybe_auto_apply
from aletheore.council.broker import Broker
from aletheore.council.debate import DEFAULT_MAX_ROUNDS, DebateOutcome, DebateSide, run_debate
from aletheore.council.protocol import Event, EventDraft, EventType, clip_text
from aletheore.council.remote_turns import RemoteTurnRunner
from aletheore.council.runners import WorkerRunner
import tempfile

from aletheore.council.bundle_store import BundleStore
from aletheore.council.bundle_transport import TransportError, create_bundle, ensure_integration_branch, import_bundle
from aletheore.council.state import Area, load_state
from aletheore.council.store import RoomStore, read_all
from aletheore.council.worktrees import (
    GitError, MergeResult, _git, branch_changed_files, changed_files, commit_all, create_worktree, files_outside_area, integration_branch,
    merge_branch, worker_branch,
)

WORKER_TIMEOUT_S = 1800


class Room:
    def __init__(self, store: RoomStore, repo: Path, room_id: str, host: str, state_dir: Path, base_sha: str | None = None) -> None:
        self.store, self.repo, self.room_id, self.host, self.state_dir = store, Path(repo), room_id, host, Path(state_dir)
        self.base_sha = base_sha
        self._worktree_lock = threading.RLock()
        self._brokers: dict[str, Broker] = {}  # one localhost broker per participant whose agents run here, started when first needed
        self._broker_tokens: dict[tuple[str, str], tuple[str, float]] = {}
        self._broker_lock = threading.Lock()

    @classmethod
    def create(cls, store: RoomStore, repo: Path, host: str, state_dir: Path, title: str = "", mode: str = "interactive",
               crypto: str = "none") -> "Room":
        room_id = f"room-{uuid.uuid4().hex[:8]}"
        repo = Path(repo).resolve()
        base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
        ensure_integration_branch(repo, room_id, base_sha)
        store.create_room(room_id, host, title=title, mode=mode, crypto=crypto)
        store.append(EventDraft(room_id, "room-created", "room", EventType.SYSTEM, host, None, None,
                                {"subtype": "room_created", "host": host, "repo": str(repo), "base_sha": base_sha}))
        return cls(store, repo, room_id, host, state_dir, base_sha)

    @classmethod
    def open(cls, store: RoomStore, room_id: str, state_dir: Path) -> "Room":
        for e in store.read_after(room_id, 0, limit=50):
            if e.body.get("subtype") == "room_created" and e.body.get("repo"):
                return cls(store, Path(e.body["repo"]), room_id, store.host_of(room_id), state_dir, e.body.get("base_sha"))
        raise KeyError(f"room {room_id} has no recorded repository on this machine")

    @classmethod
    def open_remote(cls, store: RoomStore, room_id: str, repo: Path, state_dir: Path) -> "Room":
        """Join a room hosted on another machine, using this machine's own clone of the project repo.
        Refuses (creating nothing) unless the clone has the room's starting commit."""
        for e in store.read_after(room_id, 0, limit=50):
            if e.body.get("subtype") == "room_created" and e.body.get("base_sha"):
                repo = Path(repo).resolve()
                ensure_integration_branch(repo, room_id, e.body["base_sha"])
                return cls(store, repo, room_id, store.host_of(room_id), state_dir, e.body["base_sha"])
        raise KeyError(f"room {room_id} has no recorded starting commit")

    # -- helpers -----------------------------------------------------------------
    def _post(self, etype: EventType, cid: str, body: dict, sender: str, agent: str | None = None, thread: str = "room") -> Event:
        return self.store.append(EventDraft(self.room_id, cid, thread, etype, sender, agent, None, body))

    def _worktree(self, agent_id: str) -> tuple[Path, str]:
        with self._worktree_lock:  # several questions or tasks for one agent can arrive together; create its worktree once
            if self.base_sha:
                ensure_integration_branch(self.repo, self.room_id, self.base_sha)
            dest = self.state_dir / "worktrees" / self.room_id / agent_id
            if dest.is_dir():
                return dest, worker_branch(self.room_id, agent_id)
            return create_worktree(self.repo, self.room_id, agent_id, self.state_dir / "worktrees")

    @property
    def _via_broker(self) -> bool:
        """A store an agent's tool process cannot open itself (a room server, an encrypted room) is reached through a broker."""
        return not getattr(self.store, "supports_agent_tools", True)

    def _identity(self, participant: str, agent_id: str) -> dict[str, str]:
        base = {"COUNCIL_ROOM": self.room_id, "COUNCIL_PARTICIPANT": participant, "COUNCIL_AGENT": agent_id}
        if not self._via_broker:
            return {"COUNCIL_STORE": str(self.state_dir / "council.db"), **base}
        with self._broker_lock:
            broker = self._brokers.get(participant)
            if broker is None:
                broker = self._brokers[participant] = Broker(self.store, self.room_id, participant)
                broker.start()
            cached = self._broker_tokens.get((participant, agent_id))
            if cached is None or cached[1] < time.monotonic() + 120:  # one token per run; a fresh one when it is nearly over
                token = broker.mint(agent_id, kinds=tuple(sorted(WORKER_POST_TYPES)), ttl_s=WORKER_TIMEOUT_S + 600)
                cached = self._broker_tokens[(participant, agent_id)] = (token, time.monotonic() + WORKER_TIMEOUT_S + 600)
            return {"COUNCIL_BROKER_URL": broker.url, "COUNCIL_BROKER_TOKEN": cached[0], **base}

    def close_brokers(self) -> None:
        with self._broker_lock:
            brokers, self._brokers, self._broker_tokens = list(self._brokers.values()), {}, {}
        for b in brokers:
            b.stop()

    @property
    def agent_tools(self) -> bool:
        return True  # directly for local rooms, through the broker for the rest

    def _mcp_config(self, participant: str, agent_id: str) -> Path:
        path = self.state_dir / "mcp" / self.room_id / f"{agent_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        servers = {"council": {
            "command": sys.executable, "args": ["-m", "aletheore.council.mcp_tools"],
            "env": self._identity(participant, agent_id)}} if self.agent_tools else {}
        path.write_text(json.dumps({"mcpServers": servers}))
        return path

    # -- API ---------------------------------------------------------------------
    def define_area(self, area: Area) -> Event:
        for other in load_state(self.store, self.room_id).areas.values():
            if other.agent_id == area.agent_id and other.owner_participant != area.owner_participant and other.name != area.name:
                raise ValueError(
                    f"agent id '{area.agent_id}' already belongs to {other.owner_participant}; give each person's agent a distinct id "
                    f"(for example '{area.owner_participant}-{area.agent_id}')"
                )
        return self._post(EventType.SYSTEM, f"area:{area.name}",
                          {"subtype": "area_defined", "name": area.name, "globs": area.globs,
                           "owner": area.owner_participant, "agent_id": area.agent_id}, self.host)

    def request_task(self, area_name: str, instruction: str, task_id: str | None = None, attachments: list[dict] | None = None,
                     sync_integration: bool = False) -> str:
        """Ask the owner of an area to do some work. Only posts the request; the owner's machine runs it.
        A caller-chosen `task_id` makes the request idempotent (posting it twice creates one task)."""
        load_state(self.store, self.room_id).areas[area_name]  # KeyError if the area is unknown
        task_id = task_id or f"task-{uuid.uuid4().hex[:8]}"
        self._post(EventType.TASK, f"{task_id}:request",
                   {"op": "request", "task_id": task_id, "area": area_name, "instruction": clip_text(instruction),
                    **({"attachments": list(attachments)} if attachments else {}),
                    **({"sync_integration": True} if sync_integration else {})},
                   self.host, thread=task_id)
        return task_id

    def execute_task(self, task_id: str, area_name: str, instruction: str, runner: WorkerRunner, timeout_s: int = WORKER_TIMEOUT_S,
                     images: tuple[Path, ...] = (), notes: str = "") -> None:
        area = load_state(self.store, self.room_id).areas[area_name]
        worktree, branch = self._worktree(area.agent_id)
        owner, agent = area.owner_participant, area.agent_id
        self._post(EventType.TASK, f"{task_id}:started", {"op": "started", "task_id": task_id, "branch": branch}, owner, agent, thread=task_id)
        prompt = (
            f"You are {agent}, working in the '{area_name}' area of a shared project, inside this git worktree. "
            f"Only create or change files matching: {', '.join(area.globs)}. "
            + ("Use the council_post tool to report claims, questions or challenges for the other agents." if self.agent_tools
               else "Finish by replying with a short summary of what you did and anything the other agents should know.")
            + f"\n\nTask: {instruction}"
            + ("\n\nAttached images (open them with your file reading tool, do not copy them into the project):\n"
               + "\n".join(f"- {i}" for i in images) if images else "")
            + (f"\n\nNote: {notes}" if notes else "")
        )
        result = runner.run(prompt, worktree, self._mcp_config(owner, agent), self._identity(owner, agent), timeout_s, images=tuple(images))
        self._post(EventType.RESULT, f"{task_id}:result", {"text": clip_text(result.final_text), "ok": result.ok, "task_id": task_id}, owner, agent, thread=task_id)
        if not result.ok:
            self._post(EventType.TASK, f"{task_id}:failed", {"op": "failed", "task_id": task_id}, owner, agent, thread=task_id)
            return
        commit_all(worktree, f"Council: {agent} work on {area_name}")
        outside = files_outside_area(changed_files(worktree, integration_branch(self.room_id)), area.globs)
        if outside:
            self._post(EventType.TASK, f"{task_id}:blocked", {"op": "blocked", "task_id": task_id, "files": outside}, owner, agent, thread=task_id)
        else:
            self._post(EventType.TASK, f"{task_id}:finished", {"op": "finished", "task_id": task_id}, owner, agent, thread=task_id)

    def assign(self, area_name: str, instruction: str, runner: WorkerRunner, timeout_s: int = WORKER_TIMEOUT_S) -> str:
        """Single-machine convenience: request the task and run it here."""
        task_id = self.request_task(area_name, instruction)
        self.execute_task(task_id, area_name, clip_text(instruction), runner, timeout_s)
        return task_id

    def acknowledge_out_of_area(self, task_id: str, participant: str) -> Event:
        state = load_state(self.store, self.room_id)
        task = state.tasks[task_id]
        owner = state.areas[task.area].owner_participant
        if participant not in (owner, state.host):
            raise PermissionError(f"{participant} may not acknowledge out-of-area files for {task_id}")
        return self._post(EventType.SYSTEM, f"ack:{task_id}", {"subtype": "out_of_area_acknowledged", "task_id": task_id}, participant)

    def raise_conflict(
        self, thread_id: str, subject: str, area_a: str, area_b: str, runners: dict[str, WorkerRunner],
        affected_area: str | None = None, max_rounds: int = DEFAULT_MAX_ROUNDS, remote_timeout_s: float = 600,
        bundles: BundleStore | None = None,
    ) -> DebateOutcome:
        state = load_state(self.store, self.room_id)
        planner_dir = self.state_dir / "planner" / self.room_id
        planner_dir.mkdir(parents=True, exist_ok=True)
        sides = []
        for name in (area_a, area_b):
            a = state.areas[name]
            if a.owner_participant != self.host and a.agent_id not in runners:
                # the owner's own machine takes this turn; we only ask and wait for the reply
                runner = RemoteTurnRunner(self.store, self.room_id, self.host, a.owner_participant, a.agent_id, thread_id, timeout_s=remote_timeout_s)
                sides.append(DebateSide(a.agent_id, a.owner_participant, runner, planner_dir, planner_dir / "unused.json", {}, remote=True))
                continue
            wt, _ = self._worktree(a.agent_id)
            sides.append(DebateSide(a.agent_id, a.owner_participant, runners[a.agent_id], wt,
                                    self._mcp_config(a.owner_participant, a.agent_id), self._identity(a.owner_participant, a.agent_id)))
        planner = DebateSide("planner", self.host, runners["planner"], planner_dir,
                             self._mcp_config(self.host, "planner"), self._identity(self.host, "planner"))
        affected = affected_area or area_b
        outcome = run_debate(
            self.store, self.room_id, thread_id, subject, sides[0], sides[1], planner, max_rounds=max_rounds,
            affected_area=affected, branches={s.agent_id: worker_branch(self.room_id, s.agent_id) for s in sides},
            affected_by_winner={sides[0].agent_id: area_b, sides[1].agent_id: area_a},
        )
        body = outcome.verdict_event.body
        self._auto_apply(body["verdict_id"], bundles)
        return outcome

    def _auto_apply(self, verdict_id: str, bundles: BundleStore | None) -> None:
        """Autonomy mode: approve a verdict that has a winner and merge it, fetching the winner's bundle if it lives on
        another machine. Never raises: a failure is posted as an `apply_blocked` notice for a person to look at."""
        state = load_state(self.store, self.room_id)
        verdict = state.verdicts.get(verdict_id)
        if not state.autonomy or verdict is None or not verdict.winning_branch:
            return
        if verdict.status == "pending":
            self._post(EventType.SYSTEM, f"auto-approve:{verdict_id}",
                       {"subtype": "verdict_approved", "verdict_id": verdict_id, "by": state.host, "auto": True}, state.host)
        try:
            self.apply(verdict_id, bundles)
        except (ValueError, PermissionError, TransportError, KeyError) as exc:
            self._post(EventType.SYSTEM, f"apply-failed:{verdict_id}",
                       {"subtype": "apply_blocked", "verdict_id": verdict_id, "reason": clip_text(str(exc), 300), "files": []}, state.host)

    def _branch_ref(self, branch: str, bundles: BundleStore | None) -> str | None:
        """None when `branch` exists in this repository; otherwise the ref of the owner's published bundle, imported
        and verified (see bundle_transport). ValueError when it was never published."""
        if _git(self.repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", check=False).returncode == 0:
            return None
        state = load_state(self.store, self.room_id)
        owner_area = next((a for a in state.areas.values() if worker_branch(self.room_id, a.agent_id) == branch), None)
        published = [] if owner_area is None else [
            e for e in read_all(self.store, self.room_id)
            if e.body.get("subtype") == "branch_published" and e.body.get("branch") == branch
            and e.sender_participant == owner_area.owner_participant  # only the area's owner can publish its branch
        ]
        if not published or bundles is None:
            raise ValueError(f"branch {branch} was never published to this room by its owner (or no bundle store is configured)")
        pub = published[-1]
        with tempfile.TemporaryDirectory(prefix="council-bundle-") as tmp:
            path = Path(tmp) / "in.bundle"
            bundles.get_bundle(self.room_id, pub.body["sha256"], path)
            return import_bundle(self.repo, pub.sender_participant, branch, path, pub.body["sha256"])

    def apply(self, verdict_id: str, bundles: BundleStore | None = None) -> MergeResult:
        state = load_state(self.store, self.room_id)
        v = state.verdicts[verdict_id]
        branch = v.winning_branch
        merge_ref = self._branch_ref(branch, bundles) if branch else None
        result = apply_verdict(self.store, self.room_id, self.repo, verdict_id, branch, v.affected_area, merge_ref=merge_ref)
        if result.ok:
            self._publish_integration(bundles)
        return result

    def integrate(self, area_name: str, bundles: BundleStore | None = None) -> MergeResult:
        """Merge an area's finished branch into the room's integration branch (never into anyone's own branch), after
        checking again, from the branch's real contents, that it only touches files inside the area."""
        area = load_state(self.store, self.room_id).areas[area_name]
        branch = worker_branch(self.room_id, area.agent_id)
        ref = self._branch_ref(branch, bundles) or branch
        outside = files_outside_area(branch_changed_files(self.repo, self.room_id, ref), area.globs)
        if outside:
            return MergeResult(False, [], f"blocked: files outside the area {area_name}: {', '.join(sorted(outside))}")
        result = merge_branch(self.repo, self.room_id, ref)
        if result.ok:
            self._publish_integration(bundles)
        return result

    def _publish_integration(self, bundles: BundleStore | None) -> None:
        """After a merge, make the integration branch available to the other machines (best effort)."""
        if bundles is None or not self.base_sha:
            return
        branch = integration_branch(self.room_id)
        out = self.state_dir / "bundles-out" / self.room_id / "integration.bundle"
        try:
            info = create_bundle(self.repo, branch, self.base_sha, out)
            bundles.put_bundle(self.room_id, info.sha256, out)
            self._post(EventType.SYSTEM, f"integration-published:{info.sha256[:16]}",
                       {"subtype": "branch_published", "task_id": "integration", "participant": self.host, "branch": branch,
                        "sha256": info.sha256, "size": info.size, "tip_sha": info.tip_sha, "base_sha": info.base_sha}, self.host)
        except (TransportError, ValueError, OSError, ConnectionError, PermissionError, KeyError):
            pass

    def sync_integration(self, agent_id: str, me: str, bundles: BundleStore | None) -> str:
        """Bring the room's merged work into an agent's own branch, so its next task builds on what has been decided.
        Returns "" on success, or a short note about what could not be done (the task still runs, told about it)."""
        branch = integration_branch(self.room_id)
        try:
            if me == self.host:
                ref = f"refs/heads/{branch}"
            else:
                published = [e for e in read_all(self.store, self.room_id)
                             if e.body.get("subtype") == "branch_published" and e.body.get("branch") == branch
                             and e.sender_participant == self.host]
                if not published or bundles is None:
                    return "the host has not published the integration branch yet, so this agent starts from the original code"
                pub = published[-1]
                with tempfile.TemporaryDirectory(prefix="council-bundle-") as tmp:
                    path = Path(tmp) / "in.bundle"
                    bundles.get_bundle(self.room_id, pub.body["sha256"], path)
                    ref = import_bundle(self.repo, self.host, branch, path, pub.body["sha256"])
            if me != self.host:
                # This machine never commits to the integration branch, so it can simply follow the host's: otherwise the
                # host's merged work would look like this agent's own changes when its branch is checked against it.
                tip = _git(self.repo, "rev-parse", ref).stdout.strip()
                if _git(self.repo, "merge-base", "--is-ancestor", f"refs/heads/{branch}", tip, check=False).returncode == 0:
                    _git(self.repo, "update-ref", f"refs/heads/{branch}", tip)
            worktree, _ = self._worktree(agent_id)
            res = _git(worktree, "merge", "--no-edit", ref, check=False)
            if res.returncode != 0:
                _git(worktree, "merge", "--abort", check=False)
                first = (res.stderr or res.stdout or "").strip().splitlines()[:1]
                return f"could not bring the merged work into this branch ({first[0] if first else 'merge failed'})"
            return ""
        except (TransportError, GitError, KeyError, OSError, ConnectionError, ValueError) as exc:
            return f"could not fetch the merged work: {exc}"
