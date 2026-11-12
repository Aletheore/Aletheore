"""A machine-wide cap on how many agents run at once, shared by every Council process on this machine.

Several rooms (and a worker plus a planner per room) can all want to start Claude or Codex at the same time; on one
laptop that would swamp the machine and the accounts. Slots are lock files in a shared folder: an agent run takes one for
its duration, and the operating system frees a slot automatically if the process holding it dies.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from aletheore.council.runners import RunResult, WorkerRunner
from aletheore.council.worker_lock import try_lock, unlock

DEFAULT_LIMIT = 3
DEFAULT_WAIT_S = 3600


class AgentSlots:
    def __init__(self, state_dir: Path, limit: int = DEFAULT_LIMIT) -> None:
        self.dir = Path(state_dir) / "locks" / "slots"
        self.limit = max(1, int(limit))

    def acquire(self, timeout_s: float | None = None):
        self.dir.mkdir(parents=True, exist_ok=True)
        deadline = None if timeout_s is None else time.monotonic() + timeout_s
        while True:
            for i in range(self.limit):
                fh = open(self.dir / f"slot-{i}.lock", "a+b")
                if try_lock(fh):
                    return fh
                fh.close()
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("no free agent slot")
            time.sleep(0.2)

    def release(self, handle) -> None:
        try:
            unlock(handle)
        finally:
            handle.close()


def slot_limit() -> int:
    try:
        return max(1, int(os.environ.get("COUNCIL_MAX_AGENTS", DEFAULT_LIMIT)))
    except ValueError:
        return DEFAULT_LIMIT


class LimitedRunner:
    """Wraps a runner so each run waits for a free slot first. Waiting too long is a failed run, not a hang."""

    def __init__(self, inner: WorkerRunner, slots: AgentSlots, wait_s: float = DEFAULT_WAIT_S) -> None:
        self.inner, self.slots, self.wait_s = inner, slots, wait_s
        self.name = getattr(inner, "name", "agent")

    def __getattr__(self, name):  # anything else (model, ...) is the wrapped runner's
        if name == "inner":
            raise AttributeError(name)
        return getattr(self.inner, name)

    def run(self, instruction, worktree, mcp_config, identity_env, timeout_s, images=(), read_only=False) -> RunResult:
        try:
            handle = self.slots.acquire(timeout_s=self.wait_s)
        except TimeoutError:
            return RunResult(False, "waited too long for a free agent slot (other agents are busy on this machine)", -1, [])
        try:
            return self.inner.run(instruction, worktree, mcp_config, identity_env, timeout_s, images=images, read_only=read_only)
        finally:
            self.slots.release(handle)
