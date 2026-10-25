"""Console-script entry point.

`aletheore hallbook suggest` runs on every user prompt (it is the Claude Code
prompt hook), so its budget is Python start plus a few milliseconds of search.
Importing the full CLI (uvicorn, evidence, the scanners) alone costs about a
second, so that one command is handled here before aletheore.cli is imported.
Keeping the check in this launcher, not at import time of aletheore.cli, means
importing aletheore.cli never inspects sys.argv or exits the interpreter.
"""

from __future__ import annotations

import sys


def main() -> None:
    argv = sys.argv[1:]
    if len(argv) >= 2 and argv[0] == "hallbook" and argv[1] == "suggest" and "--help" not in argv[2:]:
        from aletheore.hallbook_hook import main as hook_main

        raise SystemExit(hook_main(argv[2:]))
    from aletheore.cli import main as cli_main

    cli_main()
