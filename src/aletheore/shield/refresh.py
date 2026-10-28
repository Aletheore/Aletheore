"""Entry point of the detached evidence refresh Shield's guard starts: `python -m aletheore.shield.refresh <repo>`.

It runs the same re-scan the watcher runs (`aletheore.watch.rebuild`: slow checks stay off) and exits. Output is
discarded and every failure is swallowed into an exit code: nobody is waiting on it.
"""

from __future__ import annotations

import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        return 2
    from aletheore import watch

    try:
        # allow_hosted=False: a background process the user never asked for must not send chunks to a hosted
        # embedding provider; an existing local index is still refreshed.
        watch.rebuild(Path(args[0]), lambda _message: None, allow_hosted=False)
    except Exception:  # noqa: BLE001  # nosec B110 - detached, nothing to report to
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
