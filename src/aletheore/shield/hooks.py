"""Shield hook entry points: `python -m aletheore.shield.hooks <event>`.

Events, and what each one does:

- `pre-edit` (Claude Code PreToolUse, Edit|MultiEdit|Write): mark the session as one that edits, save the
  session's base evidence (once), checkpoint the working tree, prune old checkpoints at most once an hour per
  session. Prints nothing.
- `post-edit` (PostToolUse, Edit|MultiEdit|Write): run the guard without registry lookups and, only when it has
  findings whose text differs from the last findings this session was given after an edit, hand them to the
  agent as `hookSpecificOutput.additionalContext` (the shape the hook-contract spike verified).
- `turn-end` (Stop): in a session that edited, run the guard with registry lookups and show text that differs
  from the last turn-end text to the user as `systemMessage`. Never `decision: block`; silent when
  `stop_hook_active` is true.
- `session-end` (SessionEnd): write the session receipt, prune old checkpoints, drop the hook state. Prints
  nothing.
- `pre-commit` (git pre-commit hook; no stdin; repo is the cwd or `--repo <path>`): print the guard text to
  stderr, one `aletheore shield: ` prefixed line each. Never blocks the commit.

Which repository: an edit belongs to the repository that contains the edited file (resolved from the nearest
existing folder of `tool_input.file_path`, so a nested repository, submodule or worktree under the session's
folder is checkpointed and guarded on its own); the payload `cwd` is the fallback, and the only source for
turn-end and session-end.

Hook rules, as in `evidence_hook.py`: every event always returns 0, swallows every exception (a traceback goes
to stderr only when ALETHEORE_HOOK_DEBUG is set) and runs on a daemon worker thread joined with a hard stop.
The worker only computes; the caller's thread records the state tied to the output (the dedupe hashes) and
writes the output, so a worker that overruns its stop never prints and never marks text as said. Heavy modules
are imported inside the handlers so the process starts fast.

Session ids: an id `state.session_state_path` rejects (missing, empty, or not a plain file-name token) is
treated as "" for checkpoints, the guard and the receipt (the receipt's "since the previous receipt" fallback
groups those checkpoints); its hook state is kept under the token `nosession`.

Hook state is a small JSON dict per session in `<git dir>/aletheore-shield/hook-<token>.json`, outside the
working tree, so it works in repositories that were never scanned and never creates `.aletheore/`. A corrupt
file reads as empty; writes re-read the file and merge only the keys the writer owns, then replace it atomically.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple, NoReturn

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
STATE_DIRNAME = "aletheore-shield"
NO_SESSION_TOKEN = "nosession"
# A post-edit guard runs without registry lookups by design, so saying so to the agent on every edit is noise.
_POST_EDIT_DROPPED_REASON = "registry lookups are off"
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


def _debug(stderr, message: str | None = None) -> None:
    """Stderr is not part of the hook protocol, so it is safe to explain a silent hook there when asked to."""
    if not os.environ.get("ALETHEORE_HOOK_DEBUG"):
        return
    try:
        target = stderr or sys.stderr
        if message is None:
            import traceback

            traceback.print_exc(file=target)
        else:
            target.write(message + "\n")
    except Exception:  # noqa: BLE001 - a broken stderr must not break the hook either
        pass


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


def _session_id(payload: dict) -> str:
    """The payload's session id when it is a plain file-name token (the rule state.session_state_path applies)."""
    from aletheore.shield import state

    value = payload.get("session_id")
    if isinstance(value, str) and state.session_state_path(Path("."), value) is not None:
        return value
    return ""


class _Repo(NamedTuple):
    top: Path
    git_dir: Path


def _resolve(start: Path) -> _Repo | None:
    """The working tree containing `start` (an existing folder) and its git dir, in one git call."""
    from aletheore.shield.gitutil import run_git

    try:
        if not start.is_dir():
            return None
    except OSError:
        return None
    result = run_git(start, "rev-parse", "--show-toplevel", "--absolute-git-dir", timeout=_GIT_PROBE_TIMEOUT_S)
    lines = result.stdout.decode("utf-8", "surrogateescape").splitlines()
    if result.returncode != 0 or len(lines) != 2 or not lines[0] or not lines[1]:
        return None
    return _Repo(Path(lines[0]), Path(lines[1]))


def _cwd_repo(payload: dict) -> _Repo | None:
    cwd = payload.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        return None
    return _resolve(Path(cwd))


def _edit_path(payload: dict) -> Path | None:
    """The absolute path the edit tool targets, or None when the payload is not a file edit."""
    tool = payload.get("tool_name")
    if tool is not None and tool not in _EDIT_TOOLS:
        return None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    file_path = tool_input.get("file_path")
    if not isinstance(file_path, str) or not file_path or "\0" in file_path:
        return None
    path = Path(file_path)
    if not path.is_absolute():
        cwd = payload.get("cwd")
        if not isinstance(cwd, str) or not cwd:
            return None
        path = Path(cwd) / path
    return Path(os.path.abspath(path))


def _nearest_dir(path: Path) -> Path | None:
    """The closest existing folder holding `path` (the file itself may not exist yet)."""
    for candidate in path.parents:
        try:
            if candidate.is_dir():
                return candidate
        except OSError:
            continue
    return None


def _inside(path: Path, top: Path) -> bool:
    try:
        path.resolve().relative_to(top.resolve())
    except (ValueError, OSError):
        return False
    return True


def _edit_repo(payload: dict) -> _Repo | None:
    """The repository the edited file belongs to; None when the payload is no edit or the file is in none."""
    path = _edit_path(payload)
    if path is None:
        return None
    folder = _nearest_dir(path)
    found = _resolve(folder) if folder is not None else None
    if found is None or not _inside(path, found.top):
        found = _cwd_repo(payload)
    if found is None or not _inside(path, found.top):
        return None
    return found


# --------------------------------------------------------------------------------------------------
# Hook state (in the git dir)
# --------------------------------------------------------------------------------------------------


def _state_path(repo: _Repo, session_id: str) -> Path:
    return repo.git_dir / STATE_DIRNAME / f"hook-{session_id or NO_SESSION_TOKEN}.json"


def _load_state(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _update_state(path: Path, updates: dict) -> None:
    """Merge `updates` into the newest copy of the file and replace it atomically. Never raises OSError."""
    temporary = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        data = _load_state(path)
        data.update(updates)
        path.parent.mkdir(exist_ok=True)  # inside the git dir, which exists; never creates anything else
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
# Events. Each returns an Output for the caller's thread to record and write, or None for silence.
# --------------------------------------------------------------------------------------------------


class Output(NamedTuple):
    stdout: str = ""
    stderr: str = ""
    commit: Callable[[], None] | None = None  # state to record once the output is known to be emitted


def _pre_edit(payload: dict | None) -> Output | None:
    if payload is None:
        return None
    repo = _edit_repo(payload)
    if repo is None:
        return None
    session_id = _session_id(payload)
    path = _state_path(repo, session_id)
    # Written first, before any git work and before the repo-state gate: the session did edit this repository,
    # so turn-end (the only in-session guard with registry lookups) must run even in a detached-HEAD, merge or
    # rebase state where checkpoints are not taken, or when the checkpoint is cut off by the hard stop.
    _update_state(path, {"edited": True})
    from aletheore.shield import checkpoint, guard_evidence
    from aletheore.shield.gitutil import repo_state

    checked = repo_state(repo.top)
    if not checked.ok:
        return None  # only the checkpoint, prune and base-evidence work needs a supported repository state
    now = _now()
    last = _load_state(path).get("last_prune")
    due = not isinstance(last, (int, float)) or not 0 <= now - last < PRUNE_INTERVAL_S
    if due:
        _update_state(path, {"last_prune": now})  # claimed before pruning, so a parallel call skips it
    if session_id:
        guard_evidence.save_base_evidence(repo.top, session_id)  # idempotent: keeps the first copy
    checkpoint.take_checkpoint(repo.top, session_id, checked=checked)
    if due:
        checkpoint.prune_checkpoints(repo.top)
    return None


def _guard_report(repo: _Repo, session_id: str, *, budget_s: float, lookups: bool | None):
    from aletheore.shield import guard, guard_evidence

    base = guard_evidence.load_base_evidence(repo.top, session_id) if session_id else None
    return guard.run_guard(repo.top, budget_s=budget_s, base_evidence=base, lookups=lookups)


def _dedupe(path: Path, key: str, digest: str) -> Callable[[], None] | None:
    """The commit that records `digest` under `key`, or None when it is already recorded."""
    if _load_state(path).get(key) == digest:
        return None
    return lambda: _update_state(path, {key: digest})


def _post_edit(payload: dict | None) -> Output | None:
    if payload is None:
        return None
    repo = _edit_repo(payload)
    if repo is None:
        return None
    from aletheore.shield.guard import GuardReport, render_report

    session_id = _session_id(payload)
    # Registry lookups never run per edit (spec budgets): they belong to turn-end and pre-commit.
    report = _guard_report(repo, session_id, budget_s=POST_EDIT_BUDGET_S, lookups=False)
    findings = render_report(GuardReport(report.findings, ()), max_lines=GUARD_MAX_LINES)
    # The hash covers the findings only: "not checked" reasons vary with timing and evidence age, and a change
    # in them alone is not worth interrupting the agent for.
    commit = _dedupe(_state_path(repo, session_id), "last_edit_sha", _sha(findings) if findings else "")
    if not findings or commit is None:
        return Output(commit=commit)
    reasons = tuple(r for r in report.not_checked if not r.startswith(_POST_EDIT_DROPPED_REASON))
    text = render_report(GuardReport(report.findings, reasons), max_lines=GUARD_MAX_LINES)
    return Output(json.dumps(agent_output("post-edit", text)), "", commit)


def _turn_end(payload: dict | None) -> Output | None:
    if payload is None or payload.get("stop_hook_active") is True:
        return None
    repo = _cwd_repo(payload)
    if repo is None:
        return None
    session_id = _session_id(payload)
    path = _state_path(repo, session_id)
    if not _load_state(path).get("edited"):
        return None  # a session that only read files gets no guard report
    from aletheore.shield.guard import render_report

    report = _guard_report(repo, session_id, budget_s=TURN_END_BUDGET_S, lookups=None)
    text = render_report(report, max_lines=GUARD_MAX_LINES)
    commit = _dedupe(path, "last_turn_sha", _sha(text) if text else "")
    if not text or commit is None:
        return Output(commit=commit)
    return Output(json.dumps(agent_output("turn-end", text)), "", commit)


def _session_end(payload: dict | None) -> Output | None:
    if payload is None:
        return None
    repo = _cwd_repo(payload)
    if repo is None:
        return None
    from aletheore.shield import checkpoint, receipt

    session_id = _session_id(payload)
    built = receipt.build_receipt(repo.top, session_id, budget_s=SESSION_END_BUDGET_S)
    if built is not None:
        receipt.write_receipt(repo.top, built)
    # Bounded refs even where no editing session ever reached its hourly prune.
    checkpoint.prune_checkpoints(repo.top)
    if session_id:
        try:
            _state_path(repo, session_id).unlink()
        except OSError:
            pass
    return None


def _pre_commit(args: list[str]) -> Output | None:
    start = Path.cwd()
    for position, arg in enumerate(args):
        if arg == "--repo" and position + 1 < len(args):
            start = Path(args[position + 1])
        elif arg.startswith("--repo="):
            start = Path(arg.split("=", 1)[1])
    repo = _resolve(start)
    if repo is None:
        return None
    from aletheore.shield import guard

    report = guard.run_guard(repo.top, budget_s=PRE_COMMIT_BUDGET_S)
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

# Set when a worker was abandoned at its hard stop (run_and_exit then exits without interpreter shutdown, which
# could wait on a thread holding the import lock).
abandoned = False


def _work(event: str, args: list[str], stdin) -> Output | None:
    if event == "pre-commit":
        return _pre_commit(args)  # git gives the hook no payload; stdin is never read
    return _HANDLERS[event](_read_payload(stdin))


def _write(stream, text: str) -> None:
    try:
        stream.write(text)
        stream.flush()
    except Exception:  # noqa: BLE001 - a closed or broken pipe must not change the outcome
        pass


def main(argv: list[str] | None = None, stdin=None, stdout=None, stderr=None) -> int:
    """Run one hook event. Always returns 0; prints only the event's own output, and nothing on any error."""
    global abandoned
    try:
        args = list(sys.argv[1:] if argv is None else argv)
        event = args[0] if args else ""
        if event not in _STOPS:
            return 0
        result: list = []

        def worker() -> None:
            try:
                result.append(_work(event, args[1:], stdin))
            except BaseException:  # a hook must never break the agent; keep the reason for debugging
                _debug(stderr)

        stop = globals()[_STOPS[event]]
        thread = threading.Thread(target=worker, name=f"aletheore-shield-{event}", daemon=True)
        thread.start()
        thread.join(stop)
        # The worker never records output state itself, so taking `result` here (or not) is the only decision;
        # an answer that lands after this check is dropped whole.
        if not result:
            abandoned = thread.is_alive()
            if abandoned:
                _debug(stderr, f"aletheore shield: {event} stopped at its {stop:g} s limit")
            return 0
        output = result[0]
        if output is None:
            return 0
        if output.commit is not None:
            output.commit()
        if output.stdout:
            _write(stdout or sys.stdout, output.stdout)
        if output.stderr:
            _write(stderr or sys.stderr, output.stderr)
    except BaseException:
        _debug(stderr)
    return 0


def run_and_exit(argv: list[str] | None = None) -> NoReturn:
    """Process entry point: run `main`, flush, and leave with exit code 0 straight away.

    `os._exit` skips interpreter shutdown, which could otherwise wait on a worker abandoned at its hard stop or
    fail flushing a closed stdout (turning exit 0 into exit 120)."""
    code = 0
    try:
        code = main(argv)
    finally:
        for stream in (sys.stdout, sys.stderr):
            try:
                if stream is not None:
                    stream.flush()
            except Exception:  # noqa: BLE001
                pass
        os._exit(code)


if __name__ == "__main__":
    run_and_exit()
