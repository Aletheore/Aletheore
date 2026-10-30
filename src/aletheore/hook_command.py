"""The shell command line Shield's Claude Code agent hooks run, quoted for the shell that will run it.

Other Aletheore hooks (the evidence snippet, the Hallbook hook) use the exec form in `aletheore.hook_config`
(`command` plus separate `args`, no shell). Shield's four agent hooks stay a single command string because that is
the form its setup, status and removal recognise in existing `.claude/settings.local.json` files (`setup._our_event`);
moving them to the exec form would need a migration of entries already written.
"""

import shlex
import sys
from pathlib import PureWindowsPath

# cmd.exe metacharacters (plus space, tab and quote) that make a plain argument need quoting.
_WINDOWS_SPECIAL = ' \t"&^|<>()'


def _windows_arg(arg: str, first: bool) -> str:
    if first or "\\" in arg or " " in arg:
        # Forward slashes and double quotes: Git Bash would strip the backslashes of an unquoted C:\...\python.exe,
        # and both cmd.exe and bash accept "C:/.../python.exe".
        return '"' + PureWindowsPath(arg).as_posix().replace('"', '\\"') + '"'
    if not arg or any(c in arg for c in _WINDOWS_SPECIAL):
        return '"' + arg.replace('"', '\\"') + '"'
    return arg


def build_shield_command(argv: list[str], *, platform: str | None = None) -> str:
    """Join argv into the command Shield's agent hooks run.

    POSIX sh quoting (`shlex.join`) everywhere but Windows. On Windows, where Claude Code may run the command
    through Git Bash, the interpreter path and any argument holding a backslash or a space are written with forward
    slashes and always double-quoted; other arguments are double-quoted only when empty or holding a cmd.exe
    metacharacter. `platform` defaults to `sys.platform`.
    """
    if (sys.platform if platform is None else platform) != "win32":
        return shlex.join(argv)
    return " ".join(_windows_arg(arg, position == 0) for position, arg in enumerate(argv))
