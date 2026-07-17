import json
import os
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
    credentials_path.write_text(json.dumps(data, indent=2))
    credentials_path.chmod(0o600)
