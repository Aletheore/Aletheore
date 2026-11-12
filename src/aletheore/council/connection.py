"""Tracks whether the room's server (and so its host) can be reached, and for how long, in the stages the design promises."""

from __future__ import annotations

import time
from typing import Callable

from .view_model import ConnectionState, connection_state


class ConnectionMonitor:
    def __init__(self, clock: Callable[[], float] = time.monotonic, retry_s: float = 2.0) -> None:
        self.clock, self.retry_s = clock, retry_s
        self.down_since: float | None = None
        self.reason = ""
        self.attempt_at = clock()
        self.recovered = False  # set when a long outage ends; the app shows a "returned" notice once and clears it
        self._down_total = 0.0  # seconds spent down in outages that have ended
        self.queued = 0  # messages waiting to be sent (the real outbox is stage 3 task 22; this drives the screen)

    def down_seconds(self) -> float:
        """Total time spent unable to reach the host, past outages and the current one. Task leases pause by this much."""
        return self._down_total + (self.clock() - self.down_since if self.down_since is not None else 0.0)

    def ok(self) -> None:
        if self.down_since is not None:
            self._down_total += self.clock() - self.down_since
            if self.clock() - self.down_since >= 30:
                self.recovered = True
        self.down_since, self.reason = None, ""
        self.attempt_at = self.clock()

    def failed(self, reason: str = "network") -> None:
        """`reason` is 'network' when this machine cannot reach the server, 'host' when the server answers but the host is away."""
        if self.down_since is None:
            self.down_since = self.clock()
        self.reason = reason
        self.attempt_at = self.clock()

    def state(self) -> ConnectionState:
        if self.down_since is None:
            return connection_state(0, 0, self.queued)
        since = max(1, int(self.clock() - self.down_since))
        next_try = max(0, round(self.retry_s - (self.clock() - self.attempt_at)))
        return connection_state(since, next_try, self.queued)
