"""Turn Shield on and off for one repository: plan, apply, remove, status. No prompts and no console output here;
the CLI shows the plan and asks.

What setup touches, and nothing else:

- `<top>/.claude/settings.local.json` (personal, not committed, because the commands hold an absolute interpreter
  path): four Claude Code hook entries, PreToolUse and PostToolUse (matcher `Edit|MultiEdit|Write`), Stop and
  SessionEnd, each running `<python> -m aletheore.shield.hooks <event>`. The module name in the command is how
  removal recognises Shield's entries. The file is merged, never overwritten: other keys, entries, key order,
  indentation and line endings are kept. A file that is not valid JSON, has an unexpected shape, is a symlink or
  is tracked by git is never written; the plan returns a "snippet" change with the text to add by hand.
- The git `pre-commit` hook in `git rev-parse --git-path hooks` (the common hooks folder, so linked worktrees
  share it): a block between `# >>> aletheore shield >>>` and `# <<< aletheore shield <<<`, inserted right after
  the shebang line so an `exit` in the user's script cannot skip it. It never fails the commit. A hook with a
  non-sh shebang, no shebang, a symlink, a non-executable hook or undecodable bytes gets a snippet instead.
  `core.hooksPath`: when it points inside the git dir the hook there is edited; anywhere else (husky's `.husky/`
  inside the working tree, or a shared folder used by many repositories) it gets a snippet, because those hooks
  are tracked or shared and not Shield's to edit.
- `<top>/.aletheore/shield/config.json`: Shield's config, plus a record of which files, folders and settings
  keys setup created, so removal deletes exactly those (and only when they are empty of anything else).

Removal undoes exactly that: Shield's hook commands, its marked block, config.json, and whatever setup created
that is now empty. `purge=True` also deletes every checkpoint ref (never running gc or prune), the whole
`.aletheore/shield/` folder (receipts and session state) and the hook state in `<git dir>/aletheore-shield/`.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath

from aletheore.hook_command import build_hook_command
from aletheore.shield import checkpoint, receipt, state
from aletheore.shield.gitutil import repo_state, run_git

MODULE = "aletheore.shield.hooks"
EDIT_MATCHER = "Edit|MultiEdit|Write"
# (Claude Code event, matcher or None, Shield event)
AGENT_EVENTS: tuple[tuple[str, str | None, str], ...] = (
    ("PreToolUse", EDIT_MATCHER, "pre-edit"),
    ("PostToolUse", EDIT_MATCHER, "post-edit"),
    ("Stop", None, "turn-end"),
    ("SessionEnd", None, "session-end"),
)
MARK_START = "# >>> aletheore shield >>>"
MARK_END = "# <<< aletheore shield <<<"
SH_SHELLS = frozenset({"sh", "bash", "dash", "zsh", "ksh", "ash", "mksh"})
SETTINGS_RELATIVE = PurePosixPath(".claude/settings.local.json")
_UTF8_BOM = b"\xef\xbb\xbf"
_HOOK_STATE_DIRNAME = "aletheore-shield"  # hooks.STATE_DIRNAME; not imported so setup stays light


@dataclass(frozen=True)
class SetupChange:
    path: Path
    kind: str  # "create" | "edit" | "snippet" (snippet: nothing is written; `detail` holds the text to add)
    description: str
    detail: str = ""


@dataclass(frozen=True)
class SetupBlocked:
    reason: str


@dataclass(frozen=True)
class ShieldStatus:
    enabled: bool
    checkpoints: int
    latest_receipt: Path | None
    notes: tuple[str, ...]


def _interpreter() -> str:
    return sys.executable


# --------------------------------------------------------------------------------------------- where things live


@dataclass(frozen=True)
class _Where:
    top: Path
    git_dir: Path
    common_dir: Path
    hooks_dir: Path
    hooks_path: str | None  # core.hooksPath when set


def _absolute(base: Path, value: str) -> Path:
    path = Path(os.path.expanduser(value))
    return path if path.is_absolute() else base / path


def _locate(repo: Path) -> _Where | None:
    probe = run_git(repo, "rev-parse", "--show-toplevel")
    lines = probe.stdout.decode("utf-8", "surrogateescape").splitlines()
    if probe.returncode != 0 or len(lines) != 1 or not lines[0]:
        return None
    top = Path(lines[0])
    paths = run_git(top, "rev-parse", "--absolute-git-dir", "--git-common-dir", "--git-path", "hooks")
    lines = paths.stdout.decode("utf-8", "surrogateescape").splitlines()
    if paths.returncode != 0 or len(lines) != 3:
        return None
    configured = run_git(top, "config", "--get", "core.hooksPath")
    hooks_path = configured.stdout.decode("utf-8", "surrogateescape").strip() if configured.returncode == 0 else ""
    return _Where(
        top=top,
        git_dir=Path(lines[0]),
        common_dir=_absolute(top, lines[1]),
        hooks_dir=_absolute(top, lines[2]),
        hooks_path=hooks_path or None,
    )


def _inside(path: Path, folder: Path) -> bool:
    try:
        path.resolve().relative_to(folder.resolve())
    except (ValueError, OSError):
        return False
    return True


def _record_name(where: _Where, path: Path) -> str:
    try:
        return path.relative_to(where.top).as_posix()
    except ValueError:
        return str(path)


def _from_record(where: _Where, name: str) -> Path:
    path = Path(name)
    return path if path.is_absolute() else where.top / path


def _missing_dirs(folder: Path) -> list[Path]:
    """The folders that would have to be made for `folder` to exist, outermost first."""
    missing: list[Path] = []
    while not folder.exists():
        missing.append(folder)
        if folder.parent == folder:
            break
        folder = folder.parent
    return list(reversed(missing))


# ------------------------------------------------------------------------------------------------ agent hooks


def _command(event: str) -> str:
    return build_hook_command([_interpreter(), "-m", MODULE, event])


def _entry(matcher: str | None, event: str) -> dict:
    entry: dict = {} if matcher is None else {"matcher": matcher}
    entry["hooks"] = [{"type": "command", "command": _command(event)}]
    return entry


def _settings_snippet() -> str:
    return json.dumps(
        {"hooks": {name: [_entry(matcher, event)] for name, matcher, event in AGENT_EVENTS}}, indent=2
    )


def _is_ours(hook: object) -> bool:
    return isinstance(hook, dict) and isinstance(hook.get("command"), str) and MODULE in hook["command"]


def _commands(entries: list) -> list[str]:
    return [
        hook["command"]
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("hooks"), list)
        for hook in entry["hooks"]
        if _is_ours(hook)
    ]


class _Shape(ValueError):
    """The settings file is JSON, but not shaped the way Claude Code settings are."""


def _strip_ours(entries: list, only_event: str | None = None) -> bool:
    """Remove Shield's commands from a list of hook entries in place (only those for one Shield event when
    `only_event` is set); drop entries left with no hooks. Returns whether anything was removed."""
    removed = False
    for entry in list(entries):
        if not isinstance(entry, dict) or not isinstance(entry.get("hooks"), list):
            continue
        kept = [
            hook
            for hook in entry["hooks"]
            if not (_is_ours(hook) and (only_event is None or hook["command"].split()[-1:] == [only_event]))
        ]
        if len(kept) == len(entry["hooks"]):
            continue
        removed = True
        if kept:
            entry["hooks"] = kept
        else:
            entries.remove(entry)
    return removed


def _merge(data: object) -> tuple[dict, list[str], bool]:
    """Add Shield's entries to parsed settings. Returns (data, keys created, changed). Raises _Shape."""
    if not isinstance(data, dict):
        raise _Shape("the top level is not a JSON object")
    created: list[str] = []
    changed = False
    if "hooks" not in data:
        data["hooks"] = {}
        created.append("hooks")
    hooks = data["hooks"]
    if not isinstance(hooks, dict):
        raise _Shape('"hooks" is not a JSON object')
    for name, matcher, event in AGENT_EVENTS:
        if name in hooks and not isinstance(hooks[name], list):
            raise _Shape(f'"hooks.{name}" is not a list')
    for name, matcher, event in AGENT_EVENTS:
        if name not in hooks:
            hooks[name] = []
            created.append(f"hooks.{name}")
        entries = hooks[name]
        if _command(event) in _commands(entries):
            continue
        # A Shield entry for this event with another interpreter (an older venv) is replaced, not duplicated.
        _strip_ours(entries, event)
        entries.append(_entry(matcher, event))
        changed = True
    return data, created, changed


@dataclass(frozen=True)
class _JsonFormat:
    indent: int | str | None = 2
    newline_at_end: bool = True
    crlf: bool = False
    bom: bool = False


def _parse_settings(raw: bytes) -> tuple[object, _JsonFormat]:
    """Parse settings bytes and note their layout. Raises ValueError (UnicodeDecodeError, JSONDecodeError)."""
    bom = raw.startswith(_UTF8_BOM)
    text = raw[len(_UTF8_BOM) :].decode("utf-8") if bom else raw.decode("utf-8")
    data = json.loads(text)
    indent: int | str | None = None
    if "\n" in text.strip():
        indent = 2
        for line in text.splitlines()[1:]:
            stripped = line.lstrip(" \t")
            if stripped and len(stripped) < len(line):
                lead = line[: len(line) - len(stripped)]
                indent = len(lead) if set(lead) == {" "} else lead
                break
    return data, _JsonFormat(
        indent=indent, newline_at_end=text.endswith("\n"), crlf="\r\n" in text, bom=bom
    )


def _dump_settings(data: object, layout: _JsonFormat) -> bytes:
    text = json.dumps(data, indent=layout.indent, ensure_ascii=False)
    if layout.newline_at_end:
        text += "\n"
    if layout.crlf:
        text = text.replace("\n", "\r\n")
    return (_UTF8_BOM if layout.bom else b"") + text.encode("utf-8")


def _is_tracked(where: _Where, relative: PurePosixPath) -> bool:
    listed = run_git(where.top, "ls-files", "-z", "--", relative.as_posix())
    return listed.returncode == 0 and bool(listed.stdout.strip(b"\0"))


# ---------------------------------------------------------------------------------------------------- git hook


def _sh_quote(text: str) -> str:
    return "'" + text.replace("'", "'\\''") + "'"


def git_hook_block(executable: str, *, platform: str | None = None) -> str:
    """The marked block for a sh pre-commit hook. Git for Windows runs hooks with its own sh, which takes
    forward-slash paths, so a Windows interpreter path is converted. The block runs from any folder (it names
    the repository explicitly) and can never fail the commit."""
    if (sys.platform if platform is None else platform) == "win32":
        executable = PureWindowsPath(executable).as_posix()
    python = _sh_quote(executable)
    return (
        f"{MARK_START}\n"
        "# Added by Aletheore Shield: reports what changed before you commit; it never blocks a commit.\n"
        f'if [ -x {python} ]; then {python} -m {MODULE} pre-commit --repo "$(git rev-parse --show-toplevel)"'
        " || true; fi\n"
        f"{MARK_END}\n"
    )


def _block_span(text: str) -> tuple[int, int] | None:
    """Start and end of the marked block, including the newline that joined it to the text around it."""
    start = text.find(MARK_START)
    if start < 0 or (start > 0 and text[start - 1] != "\n"):
        return None
    end = text.find(MARK_END, start)
    if end < 0:
        return None
    end += len(MARK_END)
    if text.startswith("\r\n", end):
        end += 2
    elif text.startswith("\n", end):
        end += 1
    elif start > 0:
        start -= 1  # block at the very end, added after a shebang that had no newline: take that newline back
    return start, end


def _sh_shebang(first_line: str) -> bool:
    if not first_line.startswith("#!"):
        return False
    words = first_line[2:].split()
    if not words:
        return False
    program = PurePosixPath(words[0]).name
    if program == "env":
        rest = [word for word in words[1:] if not word.startswith("-") and "=" not in word]
        program = PurePosixPath(rest[0]).name if rest else ""
    return program in SH_SHELLS


def _with_block(text: str, block: str) -> str:
    newline = text.find("\n")
    if newline < 0:
        return text + "\n" + block[:-1]
    return text[: newline + 1] + block + text[newline + 1 :]


def _only_shebang(text: str) -> bool:
    lines = [line for line in text.splitlines() if line.strip()]
    return len(lines) == 1 and lines[0].startswith("#!")


# -------------------------------------------------------------------------------------------------------- plan


@dataclass(frozen=True)
class _Step:
    change: SetupChange
    data: bytes | None = None  # bytes to write; None for a snippet or for the config step
    mode: int | None = None
    make_dirs: tuple[Path, ...] = ()
    created: tuple[str, ...] = ()  # record names of the files and folders this step creates
    settings_keys: tuple[str, ...] = ()


@dataclass
class _Plan:
    where: _Where
    steps: list[_Step] = field(default_factory=list)


def _settings_step(where: _Where) -> _Step | None:
    path = where.top / Path(SETTINGS_RELATIVE)

    def snippet(why: str) -> _Step:
        return _Step(
            SetupChange(path, "snippet", f"{why}; Shield will not rewrite it. Add these hooks to it yourself.",
                        _settings_snippet())
        )

    if path.is_symlink():
        return snippet("is a symbolic link")
    if _is_tracked(where, SETTINGS_RELATIVE):
        return snippet("is committed to git, and the hooks hold a path that only works on this machine")
    if not path.exists():
        data, keys, _ = _merge({})
        dirs = tuple(_missing_dirs(path.parent))
        return _Step(
            SetupChange(path, "create", "register Shield's four Claude Code hooks (personal settings, not committed)"),
            data=_dump_settings(data, _JsonFormat()),
            mode=0o644,
            make_dirs=dirs,
            created=tuple(_record_name(where, p) for p in (*dirs, path)),
            settings_keys=tuple(keys),
        )
    try:
        raw = path.read_bytes()
        parsed, layout = _parse_settings(raw)
        data, keys, changed = _merge(parsed)
    except OSError:
        return snippet("could not be read")
    except _Shape as problem:
        return snippet(f"is not shaped like Claude Code settings ({problem})")
    except ValueError:
        return snippet("is not valid JSON")
    if not changed:
        return None
    return _Step(
        SetupChange(path, "edit", "add Shield's four Claude Code hooks; other settings and hooks are kept"),
        data=_dump_settings(data, layout),
        mode=stat.S_IMODE(path.stat().st_mode),
        settings_keys=tuple(keys),
    )


def _hook_step(where: _Where, block: str) -> _Step | None:
    path = where.hooks_dir / "pre-commit"

    def snippet(why: str) -> _Step:
        return _Step(
            SetupChange(path, "snippet", f"{why}. Add this block to it yourself, after the first line.", block)
        )

    if where.hooks_path is not None and not _inside(where.hooks_dir, where.common_dir):
        return snippet(f"core.hooksPath is set to {where.hooks_path}, whose hooks are shared or tracked")
    if path.is_symlink():
        return snippet("the pre-commit hook is a symbolic link")
    if not path.exists():
        dirs = tuple(_missing_dirs(path.parent))
        return _Step(
            SetupChange(path, "create", "git pre-commit hook that prints Shield's report; never blocks a commit"),
            data=("#!/bin/sh\n" + block).encode("utf-8"),
            mode=0o755,
            make_dirs=dirs,
            created=tuple(_record_name(where, p) for p in (*dirs, path)),
        )
    try:
        raw = path.read_bytes()
        info = path.stat()
        text = raw.decode("utf-8")
    except OSError:
        return snippet("the pre-commit hook could not be read")
    except UnicodeDecodeError:
        return snippet("the pre-commit hook is not UTF-8 text")
    span = _block_span(text)
    if span is not None:
        current = text[span[0] : span[1]].strip("\r\n")
        if current == block.strip("\n"):
            return None
        updated = text[: span[0]] + text[span[1] :]
    else:
        updated = text
    if not _sh_shebang(text.split("\n", 1)[0].rstrip("\r")):
        return snippet("the pre-commit hook is not a sh script")
    if os.name != "nt" and not info.st_mode & stat.S_IXUSR:
        return snippet("the pre-commit hook is not executable, so git does not run it")
    return _Step(
        SetupChange(path, "edit", "add a marked block to the existing pre-commit hook; the rest is kept"),
        data=_with_block(updated, block).encode("utf-8"),
        mode=stat.S_IMODE(info.st_mode),
    )


def _config_step(where: _Where, steps: list[_Step]) -> _Step | None:
    path = state.shield_dir(where.top) / "config.json"
    existing = state.load_config(where.top)
    created = list(existing.created_files)
    keys = list(existing.created_settings_keys)
    dirs = tuple(_missing_dirs(path.parent))
    for name in [*(s for step in steps for s in step.created), *(_record_name(where, d) for d in dirs)]:
        if name not in created:
            created.append(name)
    for key in (k for step in steps for k in step.settings_keys):
        if key not in keys:
            keys.append(key)
    if path.exists() and created == list(existing.created_files) and keys == list(existing.created_settings_keys):
        return None
    kind, what = ("edit", "update") if path.exists() else ("create", "create")
    return _Step(
        SetupChange(path, kind, f"{what} Shield's config, which records what setup created so removal is exact"),
        created=tuple(created),
        settings_keys=tuple(keys),
    )


def _plan(repo: Path) -> _Plan | SetupBlocked:
    checked = repo_state(Path(repo))
    if not checked.ok:
        return SetupBlocked(checked.reason or "this repository is not supported")
    where = _locate(Path(repo))
    if where is None:
        return SetupBlocked("git could not say where this repository's files are")
    plan = _Plan(where)
    for step in (_settings_step(where), _hook_step(where, git_hook_block(_interpreter()))):
        if step is not None:
            plan.steps.append(step)
    config = _config_step(where, [s for s in plan.steps if s.change.kind != "snippet"])
    if config is not None:
        plan.steps.append(config)
    return plan


def plan_setup(repo: Path) -> list[SetupChange] | SetupBlocked:
    """Every file setup would create or edit, and the snippets it would ask the user to add. Writes nothing."""
    plan = _plan(repo)
    if isinstance(plan, SetupBlocked):
        return plan
    return [step.change for step in plan.steps]


# ------------------------------------------------------------------------------------------------------- apply


def _write_atomic(path: Path, data: bytes, mode: int | None) -> None:
    temporary = path.with_name(f".{path.name}.aletheore-{os.getpid()}.tmp")
    try:
        temporary.write_bytes(data)
        if mode is not None:
            os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def apply_setup(repo: Path) -> None:
    """Make the plan's create and edit changes; snippet changes are left to the user. ValueError when blocked."""
    plan = _plan(repo)
    if isinstance(plan, SetupBlocked):
        raise ValueError(plan.reason)
    for step in plan.steps:
        if step.change.kind == "snippet":
            continue
        if step.data is None:  # the config step
            existing = state.load_config(plan.where.top)
            state.save_config(
                plan.where.top,
                state.ShieldConfig(
                    registry_lookups=existing.registry_lookups,
                    created_files=step.created,
                    created_settings_keys=step.settings_keys,
                ),
            )
            continue
        for folder in step.make_dirs:
            folder.mkdir(exist_ok=True)
        _write_atomic(step.change.path, step.data, step.mode)


# ------------------------------------------------------------------------------------------------------ remove


def _remove_settings(where: _Where, created: set[str], keys: set[str]) -> Path | None:
    path = where.top / Path(SETTINGS_RELATIVE)
    if path.is_symlink() or not path.is_file():
        return None
    try:
        data, layout = _parse_settings(path.read_bytes())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("hooks"), dict):
        return None
    hooks = data["hooks"]
    removed = False
    for name in list(hooks):
        entries = hooks[name]
        if isinstance(entries, list) and _strip_ours(entries):
            removed = True
            if not entries and f"hooks.{name}" in keys:
                del hooks[name]
    if not removed:
        return None
    if not hooks and "hooks" in keys:
        del data["hooks"]
    if not data and SETTINGS_RELATIVE.as_posix() in created:
        path.unlink()
    else:
        _write_atomic(path, _dump_settings(data, layout), stat.S_IMODE(path.stat().st_mode))
    return path


def _remove_hook_block(where: _Where, path: Path, created: set[str]) -> Path | None:
    if path.is_symlink() or not path.is_file():
        return None
    try:
        text = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    span = _block_span(text)
    if span is None:
        return None
    left = text[: span[0]] + text[span[1] :]
    if _record_name(where, path) in created and _only_shebang(left):
        path.unlink()
    else:
        _write_atomic(path, left.encode("utf-8"), stat.S_IMODE(path.stat().st_mode))
    return path


def remove_setup(repo: Path, *, purge: bool = False) -> list[Path]:
    """Undo `apply_setup`: Shield's hook entries, its git hook block, config.json, and the files and folders
    setup created that are now empty. `purge` also deletes all checkpoint refs (no gc), `.aletheore/shield/`
    and the hook state in the git dir. Returns the paths changed or removed."""
    where = _locate(Path(repo))
    if where is None:
        return []
    config = state.load_config(where.top)
    created = set(config.created_files)
    keys = set(config.created_settings_keys)
    changed: list[Path] = []

    settings = _remove_settings(where, created, keys)
    if settings is not None:
        changed.append(settings)
    hook_files = {where.hooks_dir / "pre-commit"} if (
        where.hooks_path is None or _inside(where.hooks_dir, where.common_dir)
    ) else set()
    hook_files |= {
        _from_record(where, name) for name in created if PurePosixPath(name.replace("\\", "/")).name == "pre-commit"
    }
    for hook in sorted(hook_files):
        done = _remove_hook_block(where, hook, created)
        if done is not None:
            changed.append(done)

    config_path = state.shield_dir(where.top) / "config.json"
    if config_path.is_file():
        config_path.unlink()
        changed.append(config_path)

    if purge:
        checkpoint.delete_all_checkpoints(where.top)
        for folder in (state.shield_dir(where.top), where.git_dir / _HOOK_STATE_DIRNAME):
            if folder.is_dir() and not folder.is_symlink():
                shutil.rmtree(folder)
                changed.append(folder)

    folders = [_from_record(where, name) for name in created]
    for folder in sorted(folders, key=lambda p: len(p.parts), reverse=True):
        try:
            if folder.is_dir() and not folder.is_symlink() and not any(folder.iterdir()):
                folder.rmdir()
                changed.append(folder)
        except OSError:
            continue
    return changed


# ------------------------------------------------------------------------------------------------------ status


def _agent_events_registered(where: _Where) -> list[str]:
    path = where.top / Path(SETTINGS_RELATIVE)
    try:
        data, _ = _parse_settings(path.read_bytes())
    except (OSError, ValueError):
        return []
    hooks = data.get("hooks") if isinstance(data, dict) else None
    if not isinstance(hooks, dict):
        return []
    found = []
    for name, _matcher, event in AGENT_EVENTS:
        entries = hooks.get(name)
        if isinstance(entries, list) and any(c.split()[-1:] == [event] for c in _commands(entries)):
            found.append(name)
    return found


def _git_hook_installed(where: _Where) -> bool:
    try:
        text = (where.hooks_dir / "pre-commit").read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    return _block_span(text) is not None


def shield_status(repo: Path) -> ShieldStatus:
    """Whether Shield is on here, how many checkpoints this worktree has, the newest receipt, and plain notes on
    anything missing or limiting."""
    checked = repo_state(Path(repo))
    where = _locate(Path(repo))
    if where is None:
        return ShieldStatus(False, 0, None, (checked.reason or "this folder is not a git repository",))
    notes: list[str] = []
    registered = _agent_events_registered(where)
    if not registered:
        notes.append("agent hooks not registered")
    elif len(registered) < len(AGENT_EVENTS):
        missing = ", ".join(name for name, _, _ in AGENT_EVENTS if name not in registered)
        notes.append(f"agent hooks partly registered (missing: {missing})")
    installed = _git_hook_installed(where)
    if not installed:
        notes.append("git hook not installed")
    if not checked.ok and checked.reason:
        notes.append(checked.reason)
    found = checkpoint.list_checkpoints(where.top, limit=1000)
    if found:
        skipped = max(found[0].skipped_count, len(found[0].skipped))
        if skipped:
            noun = "file" if skipped == 1 else "files"
            notes.append(f"{skipped} {noun} skipped in the latest checkpoint (too large or deny-listed)")
    return ShieldStatus(
        enabled=bool(registered) or installed,
        checkpoints=len(found),
        latest_receipt=receipt.latest_receipt(where.top),
        notes=tuple(notes),
    )
