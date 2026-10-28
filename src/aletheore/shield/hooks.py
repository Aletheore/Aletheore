"""Shield hook entry points: `python -m aletheore.shield.hooks <event>`.

Events, and what each one does:

- `pre-edit` (Claude Code PreToolUse, Edit|MultiEdit|Write): checkpoint the working tree, save the session's
  base evidence (once), prune old checkpoints at most once an hour per session. Prints nothing.
- `post-edit` (PostToolUse, Edit|MultiEdit|Write): run the guard without registry lookups and, when its text
  is non-empty and differs from the last text this session produced, hand it to the agent as
  `hookSpecificOutput.additionalContext` (the shape the hook-contract spike verified).
- `turn-end` (Stop): run the guard with registry lookups and show new text to the user as `systemMessage`.
  Never `decision: block`; silent when `stop_hook_active` is true.
- `session-end` (SessionEnd): write the session receipt, then prune old checkpoints. Prints nothing.
- `pre-commit` (git pre-commit hook; no stdin; repo is the cwd or `--repo <path>`): print the guard text to
  stderr, one `aletheore shield: ` prefixed line each. Never blocks the commit.

Hook rules, as in `evidence_hook.py`: every event always returns 0, swallows every exception (a traceback goes
to stderr only when ALETHEORE_HOOK_DEBUG is set) and runs on a daemon worker thread joined with a hard stop.
The worker only computes; the caller's thread writes the output, so a worker that overruns its stop never
prints. Heavy modules are imported inside the handlers so the process starts fast.

Session ids: an id `state.session_state_path` rejects (missing, empty, or not a plain file-name token) is
treated as "". Checkpoints are still taken with session "" (the receipt's "since the previous receipt"
fallback groups them), the guard and receipt run with "", and nothing per-session is written: no state file,
no base evidence, and no dedupe (every non-empty post-edit text is emitted).

Per-session state is a small JSON dict in `.aletheore/shield/sessions/<id>.json`. It is only written when
`.aletheore/` already exists in the repository (this module never creates it); a corrupt file reads as empty
and is overwritten atomically.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

PRE_EDIT_STOP_S = 2.0
POST_EDIT_STOP_S = 2.0
TURN_END_STOP_S = 8.0
SESSION_END_STOP_S = 8.0
PRE_COMMIT_STOP_S = 8.0

POST_EDIT_BUDGET_S = 1.5
TURN_END_BUDGET_S = 6.0
SESSION_END_BUDGET_S = 6.0
PRE_COMMIT_BUDGET_S = 6.0

PRUNE_INTERVAL_S = 3600.0
GUARD_MAX_LINES = 12
MAX_PAYLOAD_CHARS = 64 * 1024 * 1024
HEADER = "Aletheore Shield guard (working tree vs HEAD):"
PRE_COMMIT_PREFIX = "aletheore shield: "
_GIT_PROBE_TIMEOUT_S = 2.0
_EDIT_TOOLS = frozenset({"Edit", "MultiEdit", "Write"})

_now = time.time


def agent_output(event: str, text: str) -> dict | None:
    """The JSON a Claude Code hook prints for `text`: agent context after an edit, a user-only message at turn
    end, None for events whose output does not go through stdout JSON (or when there is nothing to say)."""
    if not text:
        return None
    body = f"{HEADER}\n{text}"
    if event == "post-edit":
        return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": body}}
    if event == "turn-end":
        return {"systemMessage": body}
    return None


# --------------------------------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------------------------------


def _debug_traceback(stderr=None) -> None:
    """Stderr is not part of the hook protocol, so a traceback there is safe when someone asks why it was silent."""
    if os.environ.get("ALETHEORE_HOOK_DEBUG"):
        import traceback

        traceback.print_exc(file=stderr or sys.stderr)


def _read_payload(stdin) -> dict | None:
    stream = stdin if stdin is not None else sys.stdin
    if stream is None:
        return None
    try:
        if stream.isatty():
            return None
    except (AttributeError, OSError, ValueError):
        pass
    if stdin is None and getattr(stream, "buffer", None) is not None:
        raw = stream.buffer.read(MAX_PAYLOAD_CHARS + 1)
    else:
        raw = stream.read(MAX_PAYLOAD_CHARS + 1)
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    if not raw or len(raw) > MAX_PAYLOAD_CHARS:
        return None
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _session_id(repo: Path, payload: dict) -> str:
    from aletheore.shield import state

    value = payload.get("session_id")
    if isinstance(value, str) and state.session_state_path(repo, value) is not None:
        return value
    return ""


def _toplevel(start: Path) -> Path | None:
    from aletheore.shield.gitutil import run_git

    if not start.is_dir():
        return None
    result = run_git(start, "rev-parse", "--show-toplevel", timeout=_GIT_PROBE_TIMEOUT_S)
    if result.returncode != 0:
        return None
    text = result.stdout.decode("utf-8", "replace").strip()
    return Path(text) if text else None


def _payload_repo(payload: dict) -> Path | None:
    cwd = payload.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        return None
    return _toplevel(Path(cwd))


def _edit_target_ok(repo: Path, payload: dict) -> bool:
    """True when the payload is a file edit inside `repo` (tool_name, when present, must be an edit tool)."""
    tool = payload.get("tool_name")
    if tool is not None and tool not in _EDIT_TOOLS:
        return False
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return False
    file_path = tool_input.get("file_path")
    if not isinstance(file_path, str) or not file_path:
        return False
    path = Path(file_path)
    if not path.is_absolute():
        path = Path(str(payload.get("cwd"))) / path
    try:
        path.resolve().relative_to(repo.resolve())
    except (ValueError, OSError):
        return False
    return True


# --------------------------------------------------------------------------------------------------
# Per-session state
# --------------------------------------------------------------------------------------------------


def _state_path(repo: Path, session_id: str) -> Path | None:
    """The session's state file, or None when it may not be written (no session id, or no `.aletheore/`)."""
    from aletheore.shield import state

    if not session_id or not (repo / ".aletheore").is_dir():
        return None
    return state.session_state_path(repo, session_id)


def _load_state(path: Path | None) -> dict:
    if path is None:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_state(path: Path | None, data: dict) -> None:
    if path is None:
        return
    temporary = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        try:
            temporary.unlink()
        except OSError:
            pass


def _sha(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------------------------------
# Events. Each returns an Output for the caller's thread to write, or None for silence.
# --------------------------------------------------------------------------------------------------


class Output(NamedTuple):
    stdout: str = ""
    stderr: str = ""
    commit: Callable[[], None] | None = None  # state to record once the output is known to be emitted


def _pre_edit(payload: dict | None) -> Output | None:
    if payload is None:
        return None
    repo = _payload_repo(payload)
    if repo is None or not _edit_target_ok(repo, payload):
        return None
    from aletheore.shield import checkpoint, guard_evidence
    from aletheore.shield.gitutil import repo_state

    if not repo_state(repo).ok:
        return None
    session_id = _session_id(repo, payload)
    path = _state_path(repo, session_id)
    data = _load_state(path)
    if session_id:
        guard_evidence.save_base_evidence(repo, session_id)  # idempotent: keeps the first copy
    # The session's first checkpoint skips the rate limit, so a session that starts within seconds of
    # another one still gets its own starting point (an unchanged tree is still skipped by take_checkpoint).
    made = checkpoint.take_checkpoint(repo, session_id, force=path is not None and not data.get("baseline"))
    if path is None:
        return None
    changed = False
    if made is not None and not data.get("baseline"):
        data["baseline"] = True
        changed = True
    now = _now()
    last = data.get("last_prune")
    due = not isinstance(last, (int, float)) or not 0 <= now - last < PRUNE_INTERVAL_S
    if due:
        data["last_prune"] = now
        changed = True
    if changed:
        _save_state(path, data)  # claim the prune before running it, so a parallel call skips it
    if due:
        checkpoint.prune_checkpoints(repo)
    return None


def _guard_text(repo: Path, session_id: str, *, budget_s: float, lookups: bool | None) -> str:
    from aletheore.shield import guard, guard_evidence

    base = guard_evidence.load_base_evidence(repo, session_id) if session_id else None
    report = guard.run_guard(repo, budget_s=budget_s, base_evidence=base, lookups=lookups)
    return guard.render_report(report, max_lines=GUARD_MAX_LINES)


def _new_text(repo: Path, session_id: str, text: str):
    """(show, commit): whether `text` should be shown (non-empty and not the session's latest guard text), and
    a callable that records it as the latest. The caller runs `commit` only if the output is really emitted
    (an event abandoned at its hard stop must not mark text as said that nobody saw)."""
    path = _state_path(repo, session_id)
    if path is None:
        return bool(text), None
    data = _load_state(path)
    digest = _sha(text) if text else ""
    if data.get("last_guard_sha") == digest:
        return False, None

    def commit() -> None:
        latest = _load_state(path)
        latest["last_guard_sha"] = digest
        _save_state(path, latest)

    return bool(text), commit


def _post_edit(payload: dict | None) -> Output | None:
    if payload is None:
        return None
    repo = _payload_repo(payload)
    if repo is None or not _edit_target_ok(repo, payload):
        return None
    session_id = _session_id(repo, payload)
    # Registry lookups never run per edit (spec budgets): they belong to turn-end and pre-commit.
    text = _guard_text(repo, session_id, budget_s=POST_EDIT_BUDGET_S, lookups=False)
    show, commit = _new_text(repo, session_id, text)
    return Output(json.dumps(agent_output("post-edit", text)) if show else "", "", commit)


def _turn_end(payload: dict | None) -> Output | None:
    if payload is None or payload.get("stop_hook_active") is True:
        return None
    repo = _payload_repo(payload)
    if repo is None:
        return None
    session_id = _session_id(repo, payload)
    text = _guard_text(repo, session_id, budget_s=TURN_END_BUDGET_S, lookups=None)
    show, commit = _new_text(repo, session_id, text)
    return Output(json.dumps(agent_output("turn-end", text)) if show else "", "", commit)


def _session_end(payload: dict | None) -> Output | None:
    if payload is None:
        return None
    repo = _payload_repo(payload)
    if repo is None:
        return None
    from aletheore.shield import checkpoint, receipt

    session_id = _session_id(repo, payload)
    built = receipt.build_receipt(repo, session_id, budget_s=SESSION_END_BUDGET_S)
    if built is not None:
        receipt.write_receipt(repo, built)
    # Pre-edit prunes only where it can keep state; this keeps checkpoint refs bounded everywhere else.
    checkpoint.prune_checkpoints(repo)
    return None


def _pre_commit(args: list[str]) -> Output | None:
    start = Path.cwd()
    for position, arg in enumerate(args):
        if arg == "--repo" and position + 1 < len(args):
            start = Path(args[position + 1])
        elif arg.startswith("--repo="):
            start = Path(arg.split("=", 1)[1])
    repo = _toplevel(start)
    if repo is None:
        return None
    from aletheore.shield import guard

    report = guard.run_guard(repo, budget_s=PRE_COMMIT_BUDGET_S)
    text = guard.render_report(report, max_lines=GUARD_MAX_LINES)
    if not text:
        return None
    return Output("", "".join(f"{PRE_COMMIT_PREFIX}{line}\n" for line in text.splitlines()))


_STOPS = {
    "pre-edit": "PRE_EDIT_STOP_S",
    "post-edit": "POST_EDIT_STOP_S",
    "turn-end": "TURN_END_STOP_S",
    "session-end": "SESSION_END_STOP_S",
    "pre-commit": "PRE_COMMIT_STOP_S",
}
_HANDLERS = {"pre-edit": _pre_edit, "post-edit": _post_edit, "turn-end": _turn_end, "session-end": _session_end}

# Set when a worker was abandoned at its hard stop; `__main__` then exits without waiting for interpreter
# shutdown (which can block on a thread holding the import lock or a git child).
abandoned = False


def _work(event: str, args: list[str], stdin) -> Output | None:
    if event == "pre-commit":
        return _pre_commit(args)  # git gives the hook no payload; stdin is never read
    return _HANDLERS[event](_read_payload(stdin))


def main(argv: list[str] | None = None, stdin=None, stdout=None, stderr=None) -> int:
    """Run one hook event. Always returns 0; prints only the event's own output, and nothing on any error."""
    global abandoned
    try:
        args = list(sys.argv[1:] if argv is None else argv)
        event = args[0] if args else ""
        if event not in _STOPS:
            return 0
        lock = threading.Lock()
        outcome: dict = {}

        def worker() -> None:
            try:
                output = _work(event, args[1:], stdin)
                with lock:  # the caller either takes this output (and its state) or has given up: never half
                    if outcome.get("cancelled"):
                        return
                    if output is not None and output.commit is not None:
                        output.commit()
                    outcome["output"] = output
            except BaseException:  # a hook must never break the agent; keep the reason for debugging
                _debug_traceback(stderr)

        thread = threading.Thread(target=worker, name=f"aletheore-shield-{event}", daemon=True)
        thread.start()
        thread.join(globals()[_STOPS[event]])
        with lock:
            if "output" not in outcome:
                outcome["cancelled"] = True
                abandoned = thread.is_alive()
                return 0
        output = outcome["output"]
        if output is not None:
            if output.stdout:
                target = stdout or sys.stdout
                target.write(output.stdout)
                target.flush()
            if output.stderr:
                target = stderr or sys.stderr
                target.write(output.stderr)
                target.flush()
    except BaseException:
        _debug_traceback(stderr)
    return 0


if __name__ == "__main__":
    code = main()
    if abandoned:
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        finally:
            os._exit(code)
    raise SystemExit(code)
