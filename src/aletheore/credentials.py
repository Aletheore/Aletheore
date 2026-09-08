import contextlib
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path

DEFAULT_CREDENTIALS_PATH = Path.home() / ".config" / "aletheore" / "credentials.json"


def has_api_key(
    env_var: str,
    provider_name: str,
    credentials_path: Path = DEFAULT_CREDENTIALS_PATH,
) -> bool:
    if env_var and os.environ.get(env_var):
        return True
    return _load_saved_key(provider_name, credentials_path) is not None


def get_api_key(
    env_var: str,
    provider_name: str,
    credentials_path: Path = DEFAULT_CREDENTIALS_PATH,
    prompt_fn: Callable[[str], str] = input,
) -> str | None:
    if env_var:
        env_value = os.environ.get(env_var)
        if env_value:
            return env_value

    saved = _load_saved_key(provider_name, credentials_path)
    if saved:
        return saved

    # A worker process, cron job, or any other non-interactive caller has no
    # one to answer this prompt - input() would block forever (or raise
    # EOFError once stdin closes) rather than fail cleanly. Only guards the
    # real interactive prompt (prompt_fn left at its default); a caller that
    # supplies its own prompt_fn (e.g. a test double) has already opted out
    # of this check.
    if prompt_fn is input and not sys.stdin.isatty():
        return None

    entered = prompt_fn(
        f"No {env_var} found. Enter your {provider_name} API key "
        f"(or press Enter to cancel): "
    ).strip()
    if not entered:
        return None

    choice = (
        prompt_fn(
            f"Save this key locally for future {provider_name} runs, or use it once? "
            f"[save/once]: "
        )
        .strip()
        .lower()
    )
    if choice == "save":
        _save_key(provider_name, entered, credentials_path)

    return entered


def _load_saved_key(provider_name: str, credentials_path: Path) -> str | None:
    if not credentials_path.exists():
        return None
    try:
        data = json.loads(credentials_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    value = data.get(provider_name)
    return value if isinstance(value, str) and value else None


@contextlib.contextmanager
def _locked_rw_credentials_file(credentials_path: Path):
    """Opens credentials_path for read+write under an exclusive advisory
    lock held for the whole read-modify-write, yielding the parsed dict -
    whatever the caller mutates it to is what gets written back on exit.

    Real bug this closes: _save_key/clear_api_key used to read the whole
    file, modify a plain in-memory dict, then write the whole file back
    as three independent, UNLOCKED steps. Two CLI invocations started
    close together (a real, plausible scenario - a user with two
    terminal tabs, or an interactive run overlapping a CI job) could both
    read the file's original state before either wrote, so whichever
    wrote last silently discarded the other's saved key - with no error
    surfaced to the process whose own save call returned normally.
    Confirmed directly: process A saves "anthropic", process B (reading
    that same pre-A state) saves "gemini" - the file ends up holding only
    "gemini"; "anthropic" is gone with no indication it never landed.

    Platform-conditional locking (fcntl on POSIX, msvcrt on Windows)
    matches this module's own established pattern for exactly this kind
    of platform difference - see cli.py's O_NOFOLLOW handling, this CLI
    ships for Windows too (claude-desktop mcp-install is a documented
    Windows target).
    """
    credentials_path.parent.mkdir(parents=True, exist_ok=True)
    # O_CREAT + fchmod before any read locks permissions down for the
    # whole locked section, whether the file is new or pre-existing -
    # same reasoning _write_credentials previously used to avoid ever
    # leaving the file briefly world/group-readable.
    fd = os.open(str(credentials_path), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        os.fchmod(fd, 0o600)
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            raw = os.read(fd, os.fstat(fd).st_size)
            try:
                loaded = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                loaded = {}
            data = loaded if isinstance(loaded, dict) else {}
            yield data
            os.lseek(fd, 0, os.SEEK_SET)
            os.ftruncate(fd, 0)
            os.write(fd, json.dumps(data, indent=2).encode())
        finally:
            if sys.platform == "win32":
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _save_key(provider_name: str, key: str, credentials_path: Path) -> None:
    with _locked_rw_credentials_file(credentials_path) as data:
        data[provider_name] = key


def clear_api_key(provider_name: str, credentials_path: Path = DEFAULT_CREDENTIALS_PATH) -> bool:
    if not credentials_path.exists():
        return False
    removed = False
    with _locked_rw_credentials_file(credentials_path) as data:
        if provider_name in data:
            del data[provider_name]
            removed = True
    return removed


def save_api_token(
    provider_name: str,
    token: str,
    credentials_path: Path | None = None,
) -> None:
    _save_key(provider_name, token, credentials_path or DEFAULT_CREDENTIALS_PATH)
