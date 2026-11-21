"""Waking an agent from a room message: who is mentioned, how often an agent may be woken, how deep a chain may go.

A wake is deliberately small. It reads the room, may answer in it, and can never approve, remove, close, merge or change who may
wake what: the daemon that uses these helpers only ever writes plain messages and notices.
"""

from __future__ import annotations

import re
from collections import deque

MAX_WAKE_DEPTH = 2        # a human message is depth 0; an agent's answer is one deeper; an agent message at this depth wakes nobody
MAX_WAKES = 6             # wakes of one agent inside one window
WAKE_WINDOW_S = 600.0
WAKE_COOLDOWN_S = 3.0     # the least gap between two wakes of one agent (the CLI uses it; library users choose)
RECENT_MESSAGES = 40        # how many earlier room messages a worker keeps for the wake prompt (a planner with six executors needs more than a dozen)
OWN_MESSAGE_CHARS = 12000    # the longest message-to-you shown in full
RECENT_MESSAGE_CHARS = 6000  # how much of each earlier room message a woken agent is shown (a report from another agent is often a page long)
WAKE_PROMPT_CHARS = 40000    # the most one wake prompt carries: 12 earlier messages, the standing job and the message itself
STALE_REPORT_S = 6 * 3600.0  # a mention older than the age limit but newer than this is reported as missed; older is history
MAX_WAKE_AGE_S = 900.0    # a message older than this is history, not a request: a restarted worker does not replay it

_CODE = re.compile(r"```.*?```|`[^`\n]*`", re.S)
_MENTION = re.compile(r"(?<![\w@.])@([\w][\w-]*)", re.I)


def strip_code(text: str) -> str:
    """A name inside code is a name, not a call: `@codex` in backticks or a fence wakes nobody."""
    return _CODE.sub(" ", text or "")


def mentions(text: str, names: set[str]) -> set[str]:
    """Which of `names` the text addresses with @name. Case-insensitive; the name must be a whole word, so @codexx or
    @claude-2 does not address codex or claude, and an address like a@codex.com is not a mention."""
    wanted = {n.lower() for n in names}
    found: set[str] = set()
    for m in _MENTION.finditer(strip_code(text)):
        word = m.group(1).rstrip("-").lower()
        if word in wanted:
            found.add(word)
    return found


_CHAT_TASK = re.compile(r"\A\s*@([\w][\w-]*)[,:]?\s+/task(?:\s+(.*))?\Z", re.S | re.I)


def parse_chat_task(text: str) -> tuple[str, str] | None:
    """'@agent /task <what to do>' at the start of a message: (agent id lower-cased, the instruction), else None. Read from the raw text, so a command
    inside backticks or a code fence (which begins with a backtick) is not one, while backticks inside the instruction are kept."""
    m = _CHAT_TASK.match(text or "")
    if not m or not (m.group(2) or "").strip():
        return None
    return m.group(1).rstrip("-").lower(), m.group(2).strip()


def wake_depth(body: dict) -> int:
    d = body.get("wake_depth", 0)
    return d if isinstance(d, int) and not isinstance(d, bool) and d >= 0 else 0


class WakeGate:
    """Per-agent limits on how often it may be woken. The budget is the hard stop: even a sender that forges the depth of its
    messages cannot make an agent run more than `max_wakes` times in a window."""

    def __init__(self, max_wakes: int = MAX_WAKES, window_s: float = WAKE_WINDOW_S, cooldown_s: float = 0.0) -> None:
        self.max_wakes, self.window_s, self.cooldown_s = max_wakes, window_s, cooldown_s
        self._times: dict[str, deque[float]] = {}

    def _recent(self, agent: str, now: float) -> deque[float]:
        q = self._times.setdefault(agent, deque())
        while q and now - q[0] >= self.window_s:
            q.popleft()
        return q

    def check(self, agent: str, now: float) -> str:
        """'ok', 'budget' (wait for the window to move on; drop the request) or 'cooldown' (try again shortly)."""
        q = self._recent(agent, now)
        if len(q) >= self.max_wakes:
            return "budget"
        if q and now - q[-1] < self.cooldown_s:
            return "cooldown"
        return "ok"

    def record(self, agent: str, now: float) -> None:
        self._recent(agent, now).append(now)
