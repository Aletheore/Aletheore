"""Shell-free Claude Code command hooks (documented exec-form `command`/`args`)."""

import sys

# `-P` keeps the current folder off sys.path. Hooks run with the repository as the current folder, and `python -m`
# would otherwise put that folder first, so a repository holding its own `aletheore/` package (or `tree_sitter.py`,
# `json.py`, ...) would run its code inside the hook. Needs Python 3.11+, the oldest aletheore supports.
HOOK_FLAGS = ("-X", "utf8", "-P")


def python_hook(module: str, *args: str) -> dict:
    # https://code.claude.com/docs/en/hooks#command-hook-fields
    # Separate arguments avoid Bash/cmd/PowerShell quoting and PATH ambiguity.
    return {"type": "command", "command": sys.executable, "args": [*HOOK_FLAGS, "-m", module, *args]}


def without_isolation(hook: object) -> object:
    """The same hook in the shape earlier versions wrote (no `-P`); anything else comes back unchanged."""
    return _rewrite(hook, add=False)


def with_isolation(hook: object) -> object:
    """The same hook with `-P` added to the `-X utf8 -m <module>` shape earlier versions wrote."""
    return _rewrite(hook, add=True)


def _rewrite(hook: object, *, add: bool) -> object:
    if not isinstance(hook, dict) or not isinstance(hook.get("args"), list):
        return hook
    args = hook["args"]
    old, new = ["-X", "utf8", "-m"], ["-X", "utf8", "-P", "-m"]
    src, dst = (old, new) if add else (new, old)
    if args[: len(src)] != src:
        return hook
    return {**hook, "args": [*dst, *args[len(src) :]]}
