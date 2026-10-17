"""Claude Code PreToolUse/PostToolUse hooks for `Read`: skip re-reading a file whose content on
disk hasn't changed since the agent already read it in full earlier in the same session.

Motivation (measured, not assumed): a waste audit of 59 real Claude Code sessions found 34% of
all Read calls were re-reads of a file unchanged since the agent's own last read of it - the
single largest concrete waste pattern found that wasn't already explained by something else.

Opt-in (ALETHEORE_READ_GUARD=1) until benchmarked, same convention as task_locator's prompt-context
hook. Only ever acts on a *full* read (no offset/limit in tool_input) - a partial read is a
different request and is never blocked or recorded, so it can't collide with full-read tracking.

Deliberately conservative about what the deny reason claims: there is no reliable signal in the
PreToolUse payload for whether the agent's context has since been compacted, so the message never
asserts the file's contents are still in the agent's context - only that the file is unchanged on
disk since a recorded earlier read this session, leaving the decision (reuse, re-check with a
scoped read, or re-read anyway) to the agent. This is a soft deny with an escape valve, not a hard
block: the agent can always issue a scoped read (with offset/limit) to see the file again, since
scoped reads are never denied.

State lives in the scanned repo's own `.aletheore/` directory (same precedent as file_cache.py:
never create that directory standalone, only use it if a scan already created one), one JSON file
per session so concurrent sessions never contend on the same file.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

READ_GUARD_ENV_VAR = "ALETHEORE_READ_GUARD"
_STATE_DIRNAME = "read_guard"


def _state_path(repo_path: Path, session_id: str) -> Path | None:
    """None when there's nowhere safe to keep state (no `.aletheore/` dir, or a bad session id)."""
    if not session_id or "/" in session_id or "\\" in session_id or session_id in (".", ".."):
        return None
    if not (repo_path / ".aletheore").is_dir():
        return None
    return repo_path / ".aletheore" / _STATE_DIRNAME / f"{session_id}.json"


def _load_state(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(path: Path, state: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state))
    except OSError:
        pass


def _file_digest(path: Path) -> tuple[str, int] | None:
    """(sha256 hex, size) of the file's current bytes on disk, or None if it can't be read."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return hashlib.sha256(data).hexdigest(), len(data)


def _is_full_read(tool_input: dict[str, Any]) -> bool:
    """Only a plain, whole-file Read is tracked; a ranged one (offset/limit) is a different
    request and must never be matched against - or allowed to overwrite - full-read state."""
    return "offset" not in tool_input and "limit" not in tool_input


def pre_read_decision(payload: dict[str, Any]) -> dict[str, Any] | None:
    """The JSON a PreToolUse hook on Read should print, or None to allow silently with no output."""
    tool_input = payload.get("tool_input")
    session_id = payload.get("session_id")
    cwd = payload.get("cwd")
    if not isinstance(tool_input, dict) or not isinstance(session_id, str) or not isinstance(cwd, str):
        return None
    file_path = tool_input.get("file_path")
    if not isinstance(file_path, str) or not file_path or not _is_full_read(tool_input):
        return None

    state_path = _state_path(Path(cwd), session_id)
    if state_path is None:
        return None
    state = _load_state(state_path)
    record = state.get(file_path)
    if not isinstance(record, dict):
        return None

    digest = _file_digest(Path(file_path))
    if digest is None:
        return None
    current_hash, current_size = digest
    if record.get("sha256") != current_hash or record.get("size") != current_size:
        return None

    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"{file_path} was already read in full earlier this session and is byte-for-byte "
                "unchanged on disk since. If you still have its contents from that earlier read, "
                "use that instead of re-reading. If you only need to confirm or re-check a specific "
                "part, a targeted grep or a Read with offset/limit will cost far less than a full "
                "re-read and is not blocked by this guard. Re-read in full only if you have reason "
                "to believe your earlier view of it is no longer reliable."
            ),
        }
    }


def record_read(payload: dict[str, Any]) -> None:
    """What a PostToolUse hook on Read should do: record the file's current on-disk hash so a
    later identical full read can be recognized as redundant. Deliberately ignores the tool's own
    response payload (its exact shape is a moving target) and re-stats the file itself instead -
    the fact worth recording is "unchanged since the agent last saw it", which only the file's own
    bytes can answer accurately."""
    tool_input = payload.get("tool_input")
    session_id = payload.get("session_id")
    cwd = payload.get("cwd")
    if not isinstance(tool_input, dict) or not isinstance(session_id, str) or not isinstance(cwd, str):
        return
    file_path = tool_input.get("file_path")
    if not isinstance(file_path, str) or not file_path or not _is_full_read(tool_input):
        return

    state_path = _state_path(Path(cwd), session_id)
    if state_path is None:
        return
    digest = _file_digest(Path(file_path))
    if digest is None:
        return
    current_hash, current_size = digest

    state = _load_state(state_path)
    state[file_path] = {"sha256": current_hash, "size": current_size}
    _save_state(state_path, state)


def main_pre(stdin=None, stdout=None, environ=None) -> int:
    """Entry point for `aletheore hook pre-read`. Always exits 0; prints nothing unless denying."""
    import sys

    env = os.environ if environ is None else environ
    try:
        if not env.get(READ_GUARD_ENV_VAR):
            return 0
        payload = json.loads((stdin or sys.stdin).read() or "{}")
        if not isinstance(payload, dict):
            return 0
        out = pre_read_decision(payload)
        if out:
            (stdout or sys.stdout).write(json.dumps(out))
    except Exception:
        pass
    return 0


def main_post(stdin=None, stdout=None, environ=None) -> int:
    """Entry point for `aletheore hook post-read`. Always exits 0 and prints nothing - it only
    records state for the next PreToolUse call to use."""
    import sys

    env = os.environ if environ is None else environ
    try:
        if not env.get(READ_GUARD_ENV_VAR):
            return 0
        payload = json.loads((stdin or sys.stdin).read() or "{}")
        if isinstance(payload, dict):
            record_read(payload)
    except Exception:
        pass
    return 0
