"""Build the shell command line an agent hook runs, quoted for the shell that will run it."""

import shlex
import sys
from pathlib import PureWindowsPath


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


def _windows_shield_arg(arg: str, first: bool) -> str:
    if first or "\\" in arg or " " in arg:
        return '"' + PureWindowsPath(arg).as_posix().replace('"', '\\"') + '"'
    return build_hook_command([arg], platform="win32")


def build_shield_command(argv: list[str], *, platform: str | None = None) -> str:
    """Join argv into the command Shield's agent hooks run.

    Same as `build_hook_command` except on Windows, where Claude Code may run the command through Git Bash: an
    unquoted `C:\\Python312\\python.exe` would lose its backslashes there. So the interpreter path, and any argument
    holding a backslash or a space, is written with forward slashes and always double-quoted, which cmd.exe and bash
    both accept. `build_hook_command` keeps its output byte for byte (the evidence snippet depends on it).
    """
    if (sys.platform if platform is None else platform) != "win32":
        return build_hook_command(argv, platform=platform)
    return " ".join(_windows_shield_arg(arg, position == 0) for position, arg in enumerate(argv))
