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


def _save_key(provider_name: str, key: str, credentials_path: Path) -> None:
    credentials_path.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if credentials_path.exists():
        try:
            loaded = json.loads(credentials_path.read_text())
            if isinstance(loaded, dict):
                data = loaded
        except json.JSONDecodeError:
            data = {}
    data[provider_name] = key
    _write_credentials(data, credentials_path)


def _write_credentials(data: dict, credentials_path: Path) -> None:
    content = json.dumps(data, indent=2)
    # os.open's mode only applies when it creates the file - an existing
    # file (e.g. one that pre-dates this restrictive-permissions fix, or
    # was seeded some other way) keeps whatever permissions it already had.
    # fchmod before writing closes that gap too: permissions are locked
    # down before any new key content is written, whether the file is new
    # or pre-existing, instead of write_text() then chmod() after, which
    # left the file (and the key) briefly world/group-readable in between.
    fd = os.open(credentials_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
    except BaseException:
        os.close(fd)
        raise
    with os.fdopen(fd, "w") as f:
        f.write(content)


def clear_api_key(provider_name: str, credentials_path: Path = DEFAULT_CREDENTIALS_PATH) -> bool:
    if not credentials_path.exists():
        return False
    try:
        data = json.loads(credentials_path.read_text())
    except json.JSONDecodeError:
        return False
    if not isinstance(data, dict) or provider_name not in data:
        return False
    del data[provider_name]
    _write_credentials(data, credentials_path)
    return True


def save_api_token(
    provider_name: str,
    token: str,
    credentials_path: Path | None = None,
) -> None:
    _save_key(provider_name, token, credentials_path or DEFAULT_CREDENTIALS_PATH)
