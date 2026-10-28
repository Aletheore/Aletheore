"""Build the shell command line an agent hook runs, quoted for the shell that will run it."""

import shlex
import sys


def build_hook_command(argv: list[str], *, platform: str | None = None) -> str:
    """Join argv into one command string.

    The interpreter path can hold spaces (a pipx venv lives under "Application Support" on macOS), so each
    argument is quoted for the shell: POSIX sh quoting everywhere but Windows, cmd.exe style there. `platform`
    defaults to `sys.platform`.
    """
    if (sys.platform if platform is None else platform) == "win32":
        # cmd.exe style: quote an argument that is empty or holds a space, tab, quote or cmd metacharacter
        return " ".join(
            '"' + arg.replace('"', '\\"') + '"' if not arg or any(c in arg for c in ' \t"&^|<>()') else arg
            for arg in argv
        )
    return shlex.join(argv)
