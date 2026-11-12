"""The analysis budget, counted in interpreter steps so a scan gives the same answer on any machine.

Every interpreter reports each step it takes to one global tally. A language pass starts a `Budget`, which
measures the steps taken since it began and stops the pass when they pass its limit. A slow or loaded
machine therefore analyses exactly as much as a fast one. A wall-clock cap, several times the nominal
time, stays as a last resort so a pathological input still ends.
"""

from __future__ import annotations

import os
import time

STEPS = [0]  # steps taken by every interpreter in this process

# Calibration: interpreter steps per second of work on a typical development machine, measured across the
# benchmark repositories (see DEFECT-STATUS.md, Pass 11). The nominal budget in seconds becomes a step limit.
STEPS_PER_SECOND = int(os.environ.get("ALETHEORE_FLOW_STEPS_PER_SECOND", "0") or 0) or 250000
WALL_FACTOR = 2.5  # the wall-clock backstop is this many times the nominal budget


class Budget:
    def __init__(self):
        self.base = STEPS[0]
        self.started = time.monotonic()

    def spent(self) -> int:
        return STEPS[0] - self.base

    def over(self, seconds: float) -> bool:
        """True once the pass has used `seconds` of nominal budget in steps, or `WALL_FACTOR` times that in wall-clock time."""
        if self.spent() > seconds * STEPS_PER_SECOND:
            return True
        return time.monotonic() - self.started > seconds * WALL_FACTOR
