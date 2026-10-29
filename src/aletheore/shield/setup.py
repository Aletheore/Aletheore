"""Turn Shield on and off for one repository: plan, apply, remove, status. No prompts and no console output here;
the CLI shows the plan (with a diff per file) and asks, then passes the approved plan back to `apply_setup`.

What setup touches, and nothing else:

- `<top>/.claude/settings.local.json` (personal, not committed, because the commands hold an absolute interpreter
  path): four Claude Code hook entries, PreToolUse and PostToolUse (matcher `Edit|MultiEdit|Write`), Stop and
  SessionEnd, each running `<python> -m aletheore.shield.hooks <event>`. The file is merged, never overwritten.
  Before an existing file is edited its exact bytes are saved under `.aletheore/shield/`, so removal puts them back
  byte for byte as long as nobody changed the file since; otherwise removal strips only Shield's entries. A file
  that is not valid JSON, has an unexpected shape, is a symlink, is read-only or is tracked by git is never
  written; the plan returns a "snippet" change with the text to add by hand.
- The git `pre-commit` hook in `git rev-parse --git-path hooks` (shared by all linked worktrees): a block between
  `# >>> aletheore shield >>>` and `# <<< aletheore shield <<<`, inserted right after the shebang line so an
  `exit` in the user's script cannot skip it. It never fails the commit. A non-sh, read-only, non-executable,
  symlinked or undecodable hook gets a snippet. `core.hooksPath` inside the git dir is edited; anywhere else
  (husky's `.husky/`, a shared global folder) gets a snippet, because those hooks are tracked or shared.
- `git rev-parse --git-path info/exclude` (also shared by linked worktrees): a marked block that keeps
  `/.aletheore/` (Shield state and scan evidence) and `/.claude/settings.local.json` out of `git status`. `.gitignore` is never touched.
- `<top>/.aletheore/shield/config.json` (Shield's config, created when missing).
- `<git dir>/aletheore-shield/setup/` (outside the working tree, so a cloned repository cannot plant or redirect
  it): `setup-record.json`, which only this module reads and writes (what setup created: files, folders, settings
  keys; the hash of the settings bytes Shield wrote; whether the original bytes were saved), and the private 0600
  copy of the original settings file. The record is written before any other file, and a failure part-way rolls
  back what was done.

Every path setup writes must resolve (symlinks followed, including in parent folders) inside the working tree, or
inside the git dir for the hook, info/exclude and the record. A file behind a folder symlink that leads elsewhere
gets a snippet; when Shield's own folders lead elsewhere, setup is blocked. Plan text (descriptions and diffs) is
sanitised: control, bidi and invisible format characters are shown as escapes or `?`, and only `\n` breaks lines.

Removal undoes exactly that. When another linked worktree still has Shield set up, the shared hook block and
exclude block stay (`shared_blocks_kept`). When one worktree created the shared hook file and another turns Shield
off last, the block goes but a `#!/bin/sh` stub file can remain (only the first worktree's record knew it created
the file). `purge=True` also deletes every checkpoint ref (never gc or prune),
`.aletheore/shield/` and the hook state in `<git dir>/aletheore-shield/`.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import sys
from dataclasses import dataclass, field, replace
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
SHIELD_EVENTS = frozenset({"pre-edit", "post-edit", "turn-end", "session-end", "pre-commit"})
MARK_START = "# >>> aletheore shield >>>"
MARK_END = "# <<< aletheore shield <<<"
SH_SHELLS = frozenset({"sh", "bash", "dash", "zsh", "ksh", "ash", "mksh"})
SETTINGS_RELATIVE = PurePosixPath(".claude/settings.local.json")
RECORD_NAME = "setup-record.json"
BACKUP_NAME = "original-settings.local.json.bak"
EXCLUDE_BLOCK = f"{MARK_START}\n/.aletheore/\n/{SETTINGS_RELATIVE.as_posix()}\n{MARK_END}\n"
DIFF_MAX_LINES = 200
PLAN_CHANGED = "setup plan changed since it was shown"
_SHARED = "shared by all linked worktrees of this repository"
_UTF8_BOM = b"\xef\xbb\xbf"
_HOOK_STATE_DIRNAME = "aletheore-shield"  # hooks.STATE_DIRNAME; not imported so setup stays light
_SETUP_DIRNAME = "setup"
# Characters that would act as line breaks in a terminal or editor although they are not "\n": shown as "?".
_BREAKS = re.compile("[\x0b\x0c\x1c-\x1e\x85\u2028\u2029]")
# Bidi and invisible format characters that can make text read differently from what it is: shown as "?".
_FORMAT = re.compile("[\u00ad\u061c\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff]")
# Remaining C0 controls (tab kept), DEL and C1 controls: shown as \xNN.
_CONTROL = re.compile("[\x00-\x08\x0a-\x1f\x7f-\x9f]")


@dataclass(frozen=True)
class SetupChange:
    path: Path
    kind: str  # "create" | "edit" | "snippet" (snippet: nothing is written; `detail` holds the text to add)
    description: str
    detail: str = ""
    diff: str = ""  # create/edit: unified diff of what will change (sanitised, capped)


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
    exclude: Path
    hooks_path: str | None  # core.hooksPath when set


def _absolute(base: Path, value: str) -> Path:
    path = Path(os.path.expanduser(value))
    return path if path.is_absolute() else base / path


def _locate(repo: Path) -> _Where | None:
    probe = run_git(repo, "rev-parse", "--show-toplevel")
    lines = probe.stdout.decode("utf-8", "surrogateescape").rstrip("\n").split("\n")
    if probe.returncode != 0 or len(lines) != 1 or not lines[0]:
        return None
    top = Path(lines[0])
    paths = run_git(
        top, "rev-parse", "--absolute-git-dir", "--git-common-dir", "--git-path", "hooks", "--git-path", "info/exclude"
    )
    lines = paths.stdout.decode("utf-8", "surrogateescape").rstrip("\n").split("\n")
    if paths.returncode != 0 or len(lines) != 4:
        return None
    configured = run_git(top, "config", "--get", "core.hooksPath")
    hooks_path = configured.stdout.decode("utf-8", "surrogateescape").strip() if configured.returncode == 0 else ""
    return _Where(
        top=top,
        git_dir=Path(lines[0]),
        common_dir=_absolute(top, lines[1]),
        hooks_dir=_absolute(top, lines[2]),
        exclude=_absolute(top, lines[3]),
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


def _owner_can_write(info: os.stat_result) -> bool:
    return bool(info.st_mode & stat.S_IWUSR)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _settings_path(where: _Where) -> Path:
    return where.top / Path(SETTINGS_RELATIVE)


def _shield_dir(where: _Where) -> Path:
    return state.shield_dir(where.top)


def _setup_dir_for(git_dir: Path) -> Path:
    return git_dir / _HOOK_STATE_DIRNAME / _SETUP_DIRNAME


def _setup_dir(where: _Where) -> Path:
    return _setup_dir_for(where.git_dir)


def _contained(path: Path, root: Path) -> bool:
    """Whether path, with every symlink on the way followed (also through folders that do not exist yet or links
    that dangle), stays inside root."""
    real = os.path.normcase(os.path.realpath(path))
    base = os.path.normcase(os.path.realpath(root))
    try:
        return os.path.commonpath([real, base]) == base
    except ValueError:
        return False


def _clean(text: str) -> str:
    """Text safe to show in a terminal: format characters and stray line breaks become "?", other controls become
    \\xNN escapes. "\\n" is kept as the only line break."""
    lines = [_BREAKS.sub("?", _FORMAT.sub("?", line)) for line in text.split("\n")]
    return "\n".join(_CONTROL.sub(lambda m: f"\\x{ord(m.group()):02x}", line) for line in lines)


def _clean_line(text: str) -> str:
    return _clean(text.replace("\n", "?"))


# ------------------------------------------------------------------------------------------------------- record


@dataclass
class _Record:
    created: list[str] = field(default_factory=list)  # files and folders setup created
    settings_keys: list[str] = field(default_factory=list)  # "hooks", "hooks.Stop", ...
    settings_sha256: str = ""  # hash of the settings bytes Shield last wrote
    settings_backup: bool = False  # BACKUP_NAME holds the settings bytes from before Shield edited them
    corrupt: bool = False

    def to_json(self) -> dict:
        return {
            "version": 1,
            "created": self.created,
            "settings_keys": self.settings_keys,
            "settings_sha256": self.settings_sha256,
            "settings_backup": self.settings_backup,
        }


def _strings(value: object) -> list[str]:
    return [item for item in value if isinstance(item, str) and item] if isinstance(value, list) else []


def _load_record(where: _Where) -> _Record:
    path = _setup_dir(where) / RECORD_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _Record()
    except (OSError, ValueError):
        return _Record(corrupt=True)
    if not isinstance(data, dict):
        return _Record(corrupt=True)
    sha = data.get("settings_sha256")
    return _Record(
        created=_strings(data.get("created")),
        settings_keys=_strings(data.get("settings_keys")),
        settings_sha256=sha if isinstance(sha, str) else "",
        settings_backup=data.get("settings_backup") is True,
    )


def _record_bytes(record: _Record) -> bytes:
    return (json.dumps(record.to_json(), indent=2) + "\n").encode("utf-8")


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


def _our_event(command: str) -> str | None:
    """The Shield event a command runs: its words hold `-m aletheore.shield.hooks <event>`. None otherwise, so a
    user's wrapper that merely mentions the module is not mistaken for Shield's own entry."""
    try:
        words = shlex.split(command)
    except ValueError:
        words = command.split()
    for position in range(len(words) - 2):
        if words[position] == "-m" and words[position + 1] == MODULE and words[position + 2] in SHIELD_EVENTS:
            return words[position + 2]
    return None


def _is_ours(hook: object) -> bool:
    return isinstance(hook, dict) and isinstance(hook.get("command"), str) and _our_event(hook["command"]) is not None


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
            if not (_is_ours(hook) and (only_event is None or _our_event(hook["command"]) == only_event))
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
    for name, _matcher, _event in AGENT_EVENTS:
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
    return data, _JsonFormat(indent=indent, newline_at_end=text.endswith("\n"), crlf="\r\n" in text, bom=bom)


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


# ------------------------------------------------------------------------------------------- marked text blocks


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
        start -= 1  # block at the very end, added after text that had no final newline: take that newline back
    return start, end


def _without_block(text: str) -> tuple[str, str | None]:
    """(text with the marked block removed, the block's own text) or (text, None) when there is no block."""
    span = _block_span(text)
    if span is None:
        return text, None
    return text[: span[0]] + text[span[1] :], text[span[0] : span[1]].strip("\r\n")


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


def _after_first_line(text: str, block: str) -> str:
    newline = text.find("\n")
    if newline < 0:
        return text + "\n" + block[:-1]
    return text[: newline + 1] + block + text[newline + 1 :]


def _at_end(text: str, block: str) -> str:
    if not text:
        return block
    if text.endswith("\n"):
        return text + block
    return text + "\n" + block[:-1]


def _only_shebang(text: str) -> bool:
    lines = [line for line in text.splitlines() if line.strip()]
    return len(lines) == 1 and lines[0].startswith("#!")


# -------------------------------------------------------------------------------------------------------- diffs


def _diff_label(where: _Where, path: Path) -> tuple[str, str]:
    name = _record_name(where, path)
    if Path(name).is_absolute():
        return name, name
    return f"a/{name}", f"b/{name}"


def _lines(data: bytes) -> list[str]:
    """Lines split on "\\n" only, so the diff and the sanitiser agree on where lines end."""
    text = data.decode("utf-8", "replace")
    if not text:
        return []
    lines = text.split("\n")
    return lines[:-1] if text.endswith("\n") else lines


def _diff(where: _Where, path: Path, old: bytes, new: bytes) -> str:
    """Unified diff of old -> new bytes, with control characters shown as escapes and at most DIFF_MAX_LINES."""
    before, after = _diff_label(where, path)
    lines = list(
        difflib.unified_diff(
            _lines(old),
            _lines(new),
            fromfile=before,
            tofile=after,
            lineterm="",
        )
    )
    lines = [_clean(line.replace("\n", "?")) for line in lines]
    if len(lines) > DIFF_MAX_LINES:
        lines = lines[:DIFF_MAX_LINES] + [f"... ({len(lines) - DIFF_MAX_LINES} more lines)"]
    return "\n".join(lines) + ("\n" if lines else "")


# -------------------------------------------------------------------------------------------------------- plan


@dataclass(frozen=True)
class _Step:
    change: SetupChange
    data: bytes | None = None  # bytes to write; None for a snippet
    mode: int | None = None
    make_dirs: tuple[Path, ...] = ()
    created: tuple[str, ...] = ()  # record names of the files and folders this step creates
    settings_keys: tuple[str, ...] = ()


@dataclass
class _Plan:
    where: _Where
    steps: list[_Step] = field(default_factory=list)
    record: _Record | None = None  # the record to write first; None when it is unchanged


@dataclass(frozen=True)
class _SettingsPlan:
    step: _Step | None
    written_sha: str = ""  # hash of the bytes the step writes
    backup: bytes | None = None  # original bytes to save before writing
    keep_backup: bool = False  # the record's saved original still applies


def _settings_plan(where: _Where, record: _Record) -> _SettingsPlan:
    path = _settings_path(where)

    def snippet(why: str) -> _SettingsPlan:
        return _SettingsPlan(
            _Step(
                SetupChange(
                    path,
                    "snippet",
                    f"{why}; Shield will not rewrite it. Add these hooks to it yourself.",
                    _settings_snippet(),
                )
            )
        )

    if path.is_symlink():
        return snippet("is a symbolic link")
    if not _contained(path, where.top):
        return snippet("is inside a folder that is a symbolic link leading outside the repository")
    if _is_tracked(where, SETTINGS_RELATIVE):
        return snippet("is committed to git, and the hooks hold a path that only works on this machine")
    if not path.exists():
        data, keys, _ = _merge({})
        dirs = tuple(_missing_dirs(path.parent))
        written = _dump_settings(data, _JsonFormat())
        return _SettingsPlan(
            _Step(
                SetupChange(
                    path,
                    "create",
                    "register Shield's four Claude Code hooks (personal settings, not committed)",
                    diff=_diff(where, path, b"", written),
                ),
                data=written,
                mode=0o644,
                make_dirs=dirs,
                created=tuple(_record_name(where, p) for p in (*dirs, path)),
                settings_keys=tuple(keys),
            ),
            written_sha=_sha(written),
        )
    try:
        raw = path.read_bytes()
        info = path.stat()
        parsed, layout = _parse_settings(raw)
        original, _ = _parse_settings(raw)
        data, keys, changed = _merge(parsed)
    except OSError:
        return snippet("could not be read")
    except _Shape as problem:
        return snippet(f"is not shaped like Claude Code settings ({problem})")
    except ValueError:
        return snippet("is not valid JSON")
    if not changed:
        return _SettingsPlan(None)
    if not _owner_can_write(info):
        return snippet("is read-only")
    written = _dump_settings(data, layout)
    description = "add Shield's four Claude Code hooks; other settings and hooks are kept"
    if _dump_settings(original, layout) != raw:
        description += "; this file will be re-formatted (duplicate keys, if any, are merged; the original bytes are restored on removal)"
    step = _Step(
        SetupChange(path, "edit", description, diff=_diff(where, path, raw, written)),
        data=written,
        mode=stat.S_IMODE(info.st_mode),
        settings_keys=tuple(keys),
    )
    # Save the original bytes only while they hold none of Shield's entries; a file Shield wrote and nobody touched
    # since keeps the original saved the first time.
    if record.settings_backup and record.settings_sha256 == _sha(raw):
        return _SettingsPlan(step, _sha(written), keep_backup=True)
    if _created_settings(record) and record.settings_sha256 == _sha(raw):
        return _SettingsPlan(step, _sha(written))  # a file Shield created: removal deletes it, no backup needed
    if any(_commands(v) for v in original.get("hooks", {}).values() if isinstance(v, list)):
        return _SettingsPlan(step, _sha(written))  # already holds Shield entries: removal strips them
    return _SettingsPlan(step, _sha(written), backup=raw)


def _created_settings(record: _Record) -> bool:
    return SETTINGS_RELATIVE.as_posix() in record.created


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
    if not _contained(path, where.common_dir):
        return snippet("the hooks folder is a symbolic link leading outside the git folder")
    what = f"git pre-commit hook ({_SHARED}) that prints Shield's report; never blocks a commit"
    if not path.exists():
        dirs = tuple(_missing_dirs(path.parent))
        data = ("#!/bin/sh\n" + block).encode("utf-8")
        return _Step(
            SetupChange(path, "create", what, diff=_diff(where, path, b"", data)),
            data=data,
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
    rest, current = _without_block(text)
    if current == block.strip("\n"):
        return None
    if not _sh_shebang(text.split("\n", 1)[0].rstrip("\r")):
        return snippet("the pre-commit hook is not a sh script")
    if os.name != "nt" and not info.st_mode & stat.S_IXUSR:
        return snippet("the pre-commit hook is not executable, so git does not run it")
    if not _owner_can_write(info):
        return snippet("the pre-commit hook is read-only")
    data = _after_first_line(rest, block).encode("utf-8")
    return _Step(
        SetupChange(path, "edit", f"add a marked block to the existing {what}; the rest is kept",
                    diff=_diff(where, path, raw, data)),
        data=data,
        mode=stat.S_IMODE(info.st_mode),
    )


def _exclude_step(where: _Where) -> _Step | None:
    path = where.exclude
    what = f"keep Shield's files out of git status (info/exclude, {_SHARED}; .gitignore is not touched)"

    def snippet(why: str) -> _Step:
        return _Step(SetupChange(path, "snippet", f"{why}. Add this block to it yourself.", EXCLUDE_BLOCK))

    if path.is_symlink():
        return snippet("info/exclude is a symbolic link")
    if not _contained(path, where.common_dir):
        return snippet("info/exclude is behind a symbolic link leading outside the git folder")
    if not path.exists():
        dirs = tuple(_missing_dirs(path.parent))
        data = EXCLUDE_BLOCK.encode("utf-8")
        return _Step(
            SetupChange(path, "create", what, diff=_diff(where, path, b"", data)),
            data=data,
            mode=0o644,
            make_dirs=dirs,
            created=tuple(_record_name(where, p) for p in (*dirs, path)),
        )
    try:
        raw = path.read_bytes()
        info = path.stat()
        text = raw.decode("utf-8")
    except OSError:
        return snippet("info/exclude could not be read")
    except UnicodeDecodeError:
        return snippet("info/exclude is not UTF-8 text")
    rest, current = _without_block(text)
    if current == EXCLUDE_BLOCK.strip("\n"):
        return None
    if not _owner_can_write(info):
        return snippet("info/exclude is read-only")
    data = _at_end(rest, EXCLUDE_BLOCK).encode("utf-8")
    return _Step(
        SetupChange(path, "edit", what, diff=_diff(where, path, raw, data)),
        data=data,
        mode=stat.S_IMODE(info.st_mode),
    )


def _config_step(where: _Where) -> _Step | None:
    path = _shield_dir(where) / "config.json"
    if path.exists():
        return None
    data = (json.dumps({"registry_lookups": True}, indent=2) + "\n").encode("utf-8")
    return _Step(
        SetupChange(path, "create", "create Shield's config", diff=_diff(where, path, b"", data)),
        data=data,
        mode=0o644,
        make_dirs=tuple(_missing_dirs(path.parent)),
        created=(*(_record_name(where, d) for d in _missing_dirs(path.parent)), _record_name(where, path)),
    )


def _plan(repo: Path) -> _Plan | SetupBlocked:
    plan = _raw_plan(repo)
    return plan if isinstance(plan, SetupBlocked) else _sanitised(plan)


def _raw_plan(repo: Path) -> _Plan | SetupBlocked:
    checked = repo_state(Path(repo))
    if not checked.ok:
        return SetupBlocked(checked.reason or "this repository is not supported")
    where = _locate(Path(repo))
    if where is None:
        return SetupBlocked("git could not say where this repository's files are")
    if not _contained(_shield_dir(where) / "config.json", where.top):
        return SetupBlocked(
            ".aletheore/shield leads outside the repository through a symbolic link, so Shield's config cannot be "
            "placed safely; remove that link first"
        )
    if not _contained(_setup_dir(where) / RECORD_NAME, where.git_dir):
        return SetupBlocked("Shield's folder in the git dir is a symbolic link leading elsewhere; remove it first")
    record = _load_record(where)
    plan = _Plan(where)
    settings = _settings_plan(where, record)
    for step in (settings.step, _hook_step(where, git_hook_block(_interpreter())), _exclude_step(where)):
        if step is not None:
            plan.steps.append(step)
    setup_folder = _setup_dir(where)
    setup_dirs = tuple(_missing_dirs(setup_folder))
    config = _config_step(where)

    writes = [s for s in plan.steps if s.change.kind != "snippet"]
    if not writes and config is None:
        return plan
    if config is not None:
        plan.steps.append(config)
        writes.append(config)

    new = _Record(
        created=list(record.created),
        settings_keys=list(record.settings_keys),
        settings_sha256=record.settings_sha256,
        settings_backup=record.settings_backup,
    )
    for name in [*(_record_name(where, d) for d in setup_dirs), *(n for s in writes for n in s.created)]:
        if name not in new.created:
            new.created.append(name)
    for key in (k for s in writes for k in s.settings_keys):
        if key not in new.settings_keys:
            new.settings_keys.append(key)
    if settings.step is not None and settings.step.change.kind != "snippet":
        new.settings_sha256 = settings.written_sha
        new.settings_backup = settings.keep_backup or settings.backup is not None
        if settings.backup is not None:
            backup_path = setup_folder / BACKUP_NAME
            plan.steps.append(
                _Step(
                    SetupChange(
                        backup_path,
                        "create",
                        "copy of settings.local.json as it is now, so removal can put it back byte for byte",
                        diff=f"copy of your current settings kept privately for exact restore "
                        f"({len(settings.backup)} bytes); not displayed\n",
                    ),
                    data=settings.backup,
                    mode=0o600,
                )
            )
    record_path = setup_folder / RECORD_NAME
    old_bytes = record_path.read_bytes() if record_path.is_file() else b""
    new_bytes = _record_bytes(new)
    if new_bytes != old_bytes:
        plan.record = new
        plan.steps.append(
            _Step(
                SetupChange(
                    record_path,
                    "edit" if old_bytes else "create",
                    "Shield's record of what setup changed, so removal undoes exactly that",
                    diff=_diff(where, record_path, old_bytes, new_bytes),
                ),
                data=new_bytes,
                mode=0o600,
                make_dirs=setup_dirs,
            )
        )
    return plan


def plan_setup(repo: Path) -> list[SetupChange] | SetupBlocked:
    """Every file setup would create or edit (with a diff), and the snippets it would ask the user to add.
    Writes nothing."""
    plan = _plan(repo)
    if isinstance(plan, SetupBlocked):
        return plan
    return [step.change for step in plan.steps]


def _sanitised(plan: _Plan) -> _Plan:
    plan.steps = [
        replace(step, change=replace(step.change, description=_clean_line(step.change.description)))
        for step in plan.steps
    ]
    return plan


# ------------------------------------------------------------------------------------------------------- apply


def _write_atomic(path: Path, data: bytes, mode: int | None) -> None:
    """Write through a temporary file that is created private (0600, exclusive, never following a link), then
    given its final mode and moved into place."""
    temporary = path.with_name(f".{path.name}.aletheore-{os.getpid()}.tmp")
    try:
        temporary.unlink()
    except FileNotFoundError:
        pass
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
        if mode is not None:
            os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.lexists(temporary):
            temporary.unlink()


def _comparable(changes: list[SetupChange]) -> list[tuple]:
    return [(c.path, c.kind, c.description, c.diff) for c in changes]


def apply_setup(repo: Path, approved: list[SetupChange] | None = None) -> None:
    """Make the plan's create and edit changes; snippet changes are left to the user.

    With `approved` (the plan the user was shown), the plan is made again and ValueError(PLAN_CHANGED) is raised
    when it differs, so what is written is exactly what was approved. ValueError when the plan is blocked. The
    setup record is written first; a failure part-way undoes what was done and re-raises."""
    plan = _plan(repo)
    if isinstance(plan, SetupBlocked):
        raise ValueError(plan.reason)
    if approved is not None and _comparable([s.change for s in plan.steps]) != _comparable(list(approved)):
        raise ValueError(PLAN_CHANGED)
    steps = [s for s in plan.steps if s.change.kind != "snippet"]
    if not steps:
        return
    record_path = _setup_dir(plan.where) / RECORD_NAME
    backup_path = _setup_dir(plan.where) / BACKUP_NAME
    # The record (everything setup intends to create) goes first and the saved original settings second, so an
    # interrupted apply is still removable byte for byte.
    order = {record_path: 0, backup_path: 1}
    steps.sort(key=lambda s: order.get(s.change.path, 2))
    record_existed = record_path.is_file()
    try:
        for step in steps:
            for folder in step.make_dirs:
                folder.mkdir(exist_ok=True)
            _write_atomic(step.change.path, step.data or b"", step.mode)
    except BaseException:
        if not record_existed:
            try:
                _remove(plan.where, purge=False, rollback=True)
            except Exception:  # noqa: BLE001 - the original failure is the one to report
                pass
        raise


# ------------------------------------------------------------------------------------------------------ remove


def _other_worktrees_set_up(where: _Where) -> bool:
    listed = run_git(where.top, "worktree", "list", "--porcelain")
    if listed.returncode != 0:
        return False
    here = os.path.normcase(os.path.realpath(where.top))
    for line in listed.stdout.decode("utf-8", "surrogateescape").rstrip("\n").split("\n"):
        if not line.startswith("worktree "):
            continue
        other = Path(line[len("worktree ") :])
        if os.path.normcase(os.path.realpath(other)) == here or not other.is_dir():
            continue
        probe = run_git(other, "rev-parse", "--absolute-git-dir")
        lines = probe.stdout.decode("utf-8", "surrogateescape").rstrip("\n").split("\n")
        if probe.returncode == 0 and len(lines) == 1 and (_setup_dir_for(Path(lines[0])) / RECORD_NAME).is_file():
            return True
    return False


def shared_blocks_kept(repo: Path) -> bool:
    """True when removing Shield here would leave the shared git hook block and info/exclude block in place,
    because another linked worktree of this repository still has Shield set up."""
    where = _locate(Path(repo))
    return where is not None and _other_worktrees_set_up(where)


def _remove_settings(where: _Where, record: _Record) -> Path | None:
    path = _settings_path(where)
    if path.is_symlink() or not path.is_file() or not _contained(path, where.top):
        return None
    try:
        raw = path.read_bytes()
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return None
    created = _created_settings(record)
    if record.settings_sha256 and _sha(raw) == record.settings_sha256:
        # Nobody changed the file since Shield wrote it: put back exactly what was there before.
        backup = _setup_dir(where) / BACKUP_NAME
        if record.settings_backup and backup.is_file():
            _write_atomic(path, backup.read_bytes(), mode)
            return path
        if created:
            path.unlink()
            return path
    try:
        data, layout = _parse_settings(raw)
    except ValueError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("hooks"), dict):
        return None
    hooks = data["hooks"]
    keys = set(record.settings_keys)
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
    if not data and created:
        path.unlink()
    else:
        _write_atomic(path, _dump_settings(data, layout), mode)
    return path


def _remove_block(where: _Where, path: Path, record: _Record, *, hook: bool) -> Path | None:
    if path.is_symlink() or not path.is_file() or not _contained(path, where.common_dir):
        return None
    try:
        text = path.read_bytes().decode("utf-8")
        mode = stat.S_IMODE(path.stat().st_mode)
    except (OSError, UnicodeDecodeError):
        return None
    left, block = _without_block(text)
    if block is None:
        return None
    empty = _only_shebang(left) if hook else not left
    if _record_name(where, path) in record.created and empty:
        path.unlink()
    else:
        _write_atomic(path, left.encode("utf-8"), mode)
    return path


def _remove(where: _Where, *, purge: bool, rollback: bool = False) -> list[Path]:
    record = _load_record(where)
    changed: list[Path] = []

    settings = _remove_settings(where, record)
    if settings is not None:
        changed.append(settings)

    if not _other_worktrees_set_up(where):
        hook_files = (
            {where.hooks_dir / "pre-commit"}
            if where.hooks_path is None or _inside(where.hooks_dir, where.common_dir)
            else set()
        )
        hook_files |= {
            _from_record(where, name)
            for name in record.created
            if PurePosixPath(name.replace("\\", "/")).name == "pre-commit"
        }
        for hook in sorted(hook_files):
            done = _remove_block(where, hook, record, hook=True)
            if done is not None:
                changed.append(done)
        done = _remove_block(where, where.exclude, record, hook=False)
        if done is not None:
            changed.append(done)

    shield = _shield_dir(where)
    setup_folder = _setup_dir(where)
    config_path = shield / "config.json"
    removable = [(setup_folder / BACKUP_NAME, where.git_dir), (setup_folder / RECORD_NAME, where.git_dir)]
    if not rollback or _record_name(where, config_path) in record.created:
        removable.insert(0, (config_path, where.top))
    for path, root in removable:
        if path.is_file() and _contained(path, root):
            path.unlink()
            changed.append(path)

    if purge:
        checkpoint.delete_all_checkpoints(where.top)
        for folder, root in ((shield, where.top), (where.git_dir / _HOOK_STATE_DIRNAME, where.git_dir)):
            if folder.is_dir() and not folder.is_symlink() and _contained(folder, root):
                shutil.rmtree(folder)
                changed.append(folder)

    folders = [_from_record(where, name) for name in record.created]
    for folder in sorted(folders, key=lambda p: len(p.parts), reverse=True):
        try:
            inside = any(_contained(folder, root) for root in (where.top, where.common_dir, where.git_dir))
            if inside and folder.is_dir() and not folder.is_symlink() and not any(folder.iterdir()):
                folder.rmdir()
                changed.append(folder)
        except OSError:
            continue
    return changed


def remove_setup(repo: Path, *, purge: bool = False) -> list[Path]:
    """Undo `apply_setup`: Shield's hook entries (or the original settings bytes, when the file is unchanged since
    Shield wrote it), its git hook and info/exclude blocks (kept while another linked worktree still uses Shield;
    see `shared_blocks_kept`), config.json, the setup record, and the files and folders setup created that are now
    empty. `purge` also deletes all checkpoint refs (no gc), `.aletheore/shield/` and the hook state in the git dir.
    Returns the paths changed or removed."""
    where = _locate(Path(repo))
    if where is None:
        return []
    return _remove(where, purge=purge)


# ------------------------------------------------------------------------------------------------------ status


def _agent_events_registered(where: _Where) -> list[str]:
    try:
        data, _ = _parse_settings(_settings_path(where).read_bytes())
    except (OSError, ValueError):
        return []
    hooks = data.get("hooks") if isinstance(data, dict) else None
    if not isinstance(hooks, dict):
        return []
    found = []
    for name, _matcher, event in AGENT_EVENTS:
        entries = hooks.get(name)
        if isinstance(entries, list) and any(_our_event(c) == event for c in _commands(entries)):
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
    if _load_record(where).corrupt:
        notes.append("the setup record is unreadable; removal will only strip Shield's own entries and blocks")
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
