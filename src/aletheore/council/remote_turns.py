"""A debate side that lives on another machine.

The planner posts a `debate_turn` request addressed to the other participant; that
machine's worker daemon runs its own agent and posts the reply as an event. This
module never starts anything on the other machine: it only writes a request to the
shared log and waits for the answer to appear there.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

from aletheore.council.protocol import EventDraft, EventType, clip_text
from aletheore.council.runners import RunResult
from aletheore.council.store import RoomStore, read_all


class RemoteTurnRunner:
    name = "remote"

    def __init__(
        self,
        store: RoomStore,
        room_id: str,
        planner_participant: str,
        target_participant: str,
        target_agent: str,
        thread_id: str,
        timeout_s: float = 600,
        poll_interval_s: float = 0.5,
        post_kind: str | None = None,
    ) -> None:
        self.store, self.room_id = store, room_id
        self.planner, self.target, self.agent, self.thread_id = planner_participant, target_participant, target_agent, thread_id
        self.timeout_s, self.poll_interval_s = timeout_s, poll_interval_s
        self.post_kind = post_kind

    def run(self, instruction: str, worktree: Path, mcp_config: Path, identity_env: dict[str, str], timeout_s: float, images: tuple = (), read_only: bool = False) -> RunResult:
        turn_id = uuid.uuid4().hex[:12]
        request = None
        for attempt in range(3):  # the request id is fixed, so retrying after a blip cannot duplicate the turn
            try:
                prior = [e for e in read_all(self.store, self.room_id)
                         if e.thread_id == self.thread_id and e.sender_participant == self.target and e.sender_agent == self.agent
                         and e.type in (EventType.CLAIM, EventType.CHALLENGE)]
                request = self.store.append(EventDraft(
                    self.room_id, f"turn-request:{turn_id}", self.thread_id, EventType.TASK, self.planner, None, None,
                    {"op": "debate_turn", "turn_id": turn_id, "participant": self.target, "agent": self.agent,
                     "instruction": clip_text(instruction), "round": len(prior) + 1,
                     **({"post_kind": self.post_kind} if self.post_kind else {})},
                ))
                break
            except ConnectionError as exc:
                last_error = exc
                time.sleep(min(self.poll_interval_s, 0.2))
        if request is None:
            return RunResult(False, f"cannot reach the room server to ask {self.target}: {last_error}", -1, [])
        deadline = time.monotonic() + self.timeout_s
        after = request.seq
        while True:
            try:
                for e in self.store.read_after(self.room_id, after, limit=500):
                    after = e.seq
                    if e.client_event_id != f"turn:{turn_id}" or e.sender_participant != self.target:
                        continue  # only the addressed participant can answer
                    if e.type is EventType.SYSTEM:
                        return RunResult(False, str(e.body.get("note", "the remote agent could not take its turn")), 1, [])
                    return RunResult(True, str(e.body.get("text", "")), 0, [])
            except ConnectionError:
                pass  # a network blip: keep waiting until the deadline
            if time.monotonic() >= deadline:
                return RunResult(False, f"no reply from {self.target} within {self.timeout_s}s", -1, [])
            time.sleep(self.poll_interval_s)
