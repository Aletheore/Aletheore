"""Resolve Windows npm launchers without passing prompts through cmd.exe."""

import os
from pathlib import Path
import re
import shutil
import sys


def command_argv(executable: str, *args: str) -> list[str]:
    if sys.platform != "win32" or Path(executable).suffix.lower() not in (".cmd", ".bat"):
        return [executable, *args]
    shim = Path(executable)
    try:
        text = shim.read_text(encoding="utf-8-sig")
    except UnicodeError as exc:
        raise OSError(f"unsupported encoding in batch launcher {shim}; reinstall the agent with npm") from exc
    # npm's cmd-shim uses %dp0% or %~dp0 to locate its JS entry point. Run
    # Node directly: cmd.exe otherwise expands metacharacters and truncates
    # multiline prompts, even when subprocess was called with shell=False.
    if re.search(r"\bnode(?:\.exe)?\b", text, re.I):
        for relative in re.findall(r'"(?:%~dp0|%dp0%)[\\/]([^"\r\n]+)"', text, re.I):
            script = shim.parent.joinpath(*relative.replace("\\", "/").split("/"))
            if not script.is_file() or script.suffix.lower() == ".exe":
                continue
            if script.suffix.lower() not in (".js", ".cjs", ".mjs"):
                with script.open(encoding="utf-8", errors="replace") as stream:
                    first_line = stream.readline(4096)
                if not (first_line.startswith("#!") and "node" in first_line):
                    continue
            sibling = shim.parent / "node.exe"
            node = str(sibling) if sibling.is_file() else shutil.which("node")
            if not node:
                raise FileNotFoundError("Node.js is required to run this npm-installed coding agent")
            return [os.path.abspath(node), str(script.resolve()), *args]
    raise OSError(f"unsupported batch launcher {shim}; use the agent's native executable or standard npm installation")
