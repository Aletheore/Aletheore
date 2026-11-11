"""Capability inspector: says only what has been verified about each agent."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field

# (agent binary, headless driving verified in council/docs/spike-headless-agents.md)
_KNOWN = (("claude", True), ("codex", True), ("opencode", True), ("agy", True), ("gemini", False))


@dataclass
class AgentCapability:
    agent: str
    installed: bool
    level: str  # "worker" | "advisor" | "unavailable"
    notes: list[str] = field(default_factory=list)


def inspect_agents() -> list[AgentCapability]:
    caps = []
    for name, verified in _KNOWN:
        if shutil.which(name) is None:
            caps.append(AgentCapability(name, False, "unavailable", ["not installed or not on PATH"]))
        elif verified:
            note = "headless run verified on macOS and Windows" if name in ("opencode", "agy") else "headless run verified on macOS; Windows not tested"
            caps.append(AgentCapability(name, True, "worker", [note]))
        else:
            caps.append(AgentCapability(name, True, "advisor", ["headless driving not verified; can only join via MCP tools"]))
    return caps


def inspect_user_agents() -> list[AgentCapability]:
    """Agents the person described in agents.json: Council runs them but has not verified them."""
    from aletheore.council.agent_adapters import AdapterError, load_user_adapters

    try:
        adapters = load_user_adapters()
    except AdapterError as exc:
        return [AgentCapability("agents.json", False, "unavailable", [str(exc)])]
    return [AgentCapability(k, shutil.which(a.binary) is not None, "worker" if shutil.which(a.binary) else "unavailable",
                            ["described in agents.json; not verified by Council"] if shutil.which(a.binary) else ["not installed or not on PATH"])
            for k, a in adapters.items()]
