"""Shell-free Claude Code command hooks (documented exec-form `command`/`args`)."""

import sys


def python_hook(module: str, *args: str) -> dict:
    # https://code.claude.com/docs/en/hooks#command-hook-fields
    # Separate arguments avoid Bash/cmd/PowerShell quoting and PATH ambiguity.
    return {"type": "command", "command": sys.executable, "args": ["-X", "utf8", "-m", module, *args]}
