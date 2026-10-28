"""Lean tool profile: keep the built-in tools a coding task needs, deny the rest in the project's settings."""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess  # nosec B404 - intentional fixed CLI invocation, never a shell
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from aletheore.subprocess_utils import command_argv
from aletheore.user_paths import is_link_or_junction, set_replacement_mode

PROFILES = ("core", "core+web", "off")

# Built-in tools a coding agent rarely uses. Anything not on this list is never denied, so tools we do not
# recognise and every MCP tool stay available. ToolSearch is deliberately absent: deferred tools load through it.
NON_CORE_TOOLS = frozenset({
    "Artifact", "ArtifactComments", "ArtifactData", "CronCreate", "CronDelete", "CronList", "DesignSync",
    "EnterWorktree", "ExitWorktree", "ListAgents", "Monitor", "NotebookEdit", "PushNotification",
    "RemoteTrigger", "ReportFindings", "ScheduleWakeup", "SendMessage", "TaskStop", "WebFetch", "WebSearch",
})
WEB_TOOLS = frozenset({"WebFetch", "WebSearch"})

SIDECAR_NAME = "aletheore-tokens.json"
AUDIT_PROMPT = "Say ok."
AUDIT_BUDGET_USD = "0.05"


class ProfileError(Exception):
    pass


def build_deny_list(seen_tools: Iterable[str] | None, profile: str) -> list[str]:
    """Tools to deny for a profile: the non-core built-ins that were seen (all known ones when seen is None)."""
    if profile not in PROFILES:
        raise ProfileError(f"unknown profile {profile!r}; choose one of {', '.join(PROFILES)}")
    if profile == "off":
        return []
    candidates = NON_CORE_TOOLS - (WEB_TOOLS if profile == "core+web" else frozenset())
    if seen_tools is not None:
        candidates = candidates & set(seen_tools)
    return sorted(candidates)


def _paths(project: Path) -> tuple[Path, Path]:
    d = Path(project) / ".claude"
    try:
        for path in (d, d / "settings.json", d / SIDECAR_NAME):
            if is_link_or_junction(path):
                raise ProfileError(f"{path}: linked settings are not modified; use a project-local settings file")
    except (OSError, RuntimeError) as exc:
        raise ProfileError(f"could not inspect project settings: {exc}") from exc
    return d / "settings.json", d / SIDECAR_NAME


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, UnicodeError) as e:
        raise ProfileError(f"{path} is not valid JSON ({e}); fix it first, nothing was changed") from e
    if not isinstance(data, dict):
        raise ProfileError(f"{path} is not a JSON object; nothing was changed")
    return data


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp.open("x", encoding="utf-8", newline="\n") as stream:
            set_replacement_mode(path, stream.fileno())
            json.dump(data, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _validated_added(sidecar: dict, sidecar_path: Path) -> list[str]:
    """The sidecar's own record of what it added - never trusted blindly, since a hand-edited sidecar with
    `added` as a bare string would otherwise be iterated character by character (`list("WebFetch")`), silently
    corrupting both which entries get removed and the file this call reports having removed."""
    added = sidecar.get("added", [])
    if "created_file" in sidecar and not isinstance(sidecar["created_file"], bool):
        raise ProfileError(f"{sidecar_path}: 'created_file' is not a boolean; nothing was changed")
    if not isinstance(added, list) or not all(isinstance(t, str) for t in added):
        raise ProfileError(f"{sidecar_path}: 'added' is not a list of strings; nothing was changed")
    return added


def apply_deny(project: Path, deny: list[str]) -> list[str]:
    """Merge deny entries into the project's settings. Returns the entries this call added."""
    settings_path, sidecar_path = _paths(project)
    # Recorded so `remove_deny` only ever unlinks a settings.json this tool itself created - never one that
    # already existed, even if its content happens to become empty once this tool's own entries are removed.
    settings_existed = settings_path.exists()
    settings = _load(settings_path)
    sidecar = _load(sidecar_path)
    perms = settings.setdefault("permissions", {})
    if not isinstance(perms, dict):
        raise ProfileError(f"{settings_path}: 'permissions' is not an object; nothing was changed")
    existing = perms.get("deny", [])
    if not isinstance(existing, list) or not all(isinstance(item, str) for item in existing):
        raise ProfileError(f"{settings_path}: 'permissions.deny' is not a list; nothing was changed")
    previously_added = _validated_added(sidecar, sidecar_path)
    added = [t for t in deny if t not in existing]
    perms["deny"] = existing + added
    # The sidecar is the safety record, so it goes first: a crash between the two writes then leaves a record of
    # entries that may not have landed (removing an absent entry is a no-op) rather than a tool-created
    # settings.json that nothing remembers creating.
    _write(sidecar_path, {
        "added": sorted(set(previously_added) | set(added)),
        "created_file": sidecar.get("created_file", not settings_existed),
    })
    _write(settings_path, settings)
    return added


def remove_deny(project: Path) -> list[str]:
    """Remove exactly the entries a previous apply_deny added. Returns what was removed."""
    settings_path, sidecar_path = _paths(project)
    if not sidecar_path.exists():
        return []
    sidecar = _load(sidecar_path)
    added = _validated_added(sidecar, sidecar_path)
    # A sidecar from before `created_file` existed has no way to say who created settings.json, so it is treated as
    # the user's: left in place (written back empty) rather than deleted.
    created_file = sidecar.get("created_file", False)
    settings = _load(settings_path)
    perms = settings.get("permissions", {})
    if not isinstance(perms, dict):
        raise ProfileError(f"{settings_path}: 'permissions' is not an object; nothing was changed")
    existing = perms.get("deny", [])
    if not isinstance(existing, list) or not all(isinstance(item, str) for item in existing):
        raise ProfileError(f"{settings_path}: 'permissions.deny' is not a list of strings; nothing was changed")
    deny = [t for t in existing if t not in added]
    if deny:
        perms["deny"] = deny
    else:
        perms.pop("deny", None)
    if not perms:
        settings.pop("permissions", None)
    if settings:
        _write(settings_path, settings)
    elif created_file and settings_path.exists():
        # Only delete a settings.json this tool created itself - one that pre-existed (even as `{}`) is the
        # user's file to keep, so it gets written back empty instead of removed.
        settings_path.unlink()
    elif settings_path.exists():
        _write(settings_path, settings)
    sidecar_path.unlink()
    return added


@dataclass
class Audit:
    tools: list[str]
    input_side_tokens: int
    cost_usd: float | None


def parse_audit(stream: str) -> Audit:
    tools: list[str] = []
    usage: dict = {}
    cost = None
    for line in stream.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict):
            continue
        if ev.get("type") == "system" and ev.get("subtype") == "init":
            received = ev.get("tools")
            if isinstance(received, list) and all(isinstance(t, str) for t in received):
                tools = received
        elif ev.get("type") == "result":
            if ev.get("is_error"):
                raise ProfileError("the audit request reported a failure")
            usage = ev.get("usage") or {}
            cost = ev.get("total_cost_usd")
    if not isinstance(usage, dict):
        raise ProfileError("the audit request returned invalid token usage")
    counts = [0 if usage.get(k) is None else usage[k] for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")]
    if any(isinstance(n, bool) or not isinstance(n, int) or n < 0 for n in counts):
        raise ProfileError("the audit request returned invalid token usage")
    total = sum(counts)
    if cost is not None and (isinstance(cost, bool) or not isinstance(cost, (int, float)) or not math.isfinite(cost) or cost < 0):
        cost = None
    if not tools or total == 0:
        raise ProfileError("the audit request returned no tool list or no token usage; is `claude` installed and signed in?")
    return Audit(tools=tools, input_side_tokens=total, cost_usd=cost)


def _default_runner(cmd: list[str], cwd: Path) -> str:
    executable = shutil.which(cmd[0])
    if not executable:
        raise ProfileError("`claude` was not found on PATH")
    try:
        resolved = command_argv(os.path.abspath(executable), *cmd[1:])
        p = subprocess.run(resolved,  # nosec B603 - absolute tool, literal argv; validated Windows shim
                            cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=120, stdin=subprocess.DEVNULL)
    except OSError as e:
        raise ProfileError(f"could not start claude: {e}") from e
    except subprocess.TimeoutExpired as e:
        raise ProfileError("the audit request timed out after 120s") from e
    if p.returncode:
        raise ProfileError(f"claude audit failed (exit {p.returncode}): {p.stderr.strip()}")
    return p.stdout


def run_audit(project: Path, deny: list[str] | None = None,
              runner: Callable[[list[str], Path], str] = _default_runner) -> Audit:
    """One cheap request on the user's own setup. deny, when given, is passed on the command line only."""
    cmd = ["claude", "-p", AUDIT_PROMPT, "--output-format", "stream-json", "--verbose", "--model", "haiku",
           "--max-budget-usd", AUDIT_BUDGET_USD, "--no-session-persistence"]
    if deny:
        cmd += ["--settings", json.dumps({"permissions": {"deny": deny}})]
    return parse_audit(runner(cmd, Path(project)))
