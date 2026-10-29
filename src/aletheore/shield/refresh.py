"""Entry point of the detached evidence refresh Shield's guard starts: `python -m aletheore.shield.refresh <repo>`.

It runs the same re-scan the watcher runs (`aletheore.watch.rebuild`: slow checks stay off, .gitignore untouched) and exits. Output is
discarded and every failure is swallowed into an exit code: nobody is waiting on it.
"""

from __future__ import annotations

import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    """`<repo> [<start time>]`. With the start time the guard recorded in its marker, a successful rebuild is
    followed by a completion record naming that start and the evidence file this rebuild wrote."""
    args = sys.argv[1:] if argv is None else argv
    if len(args) not in (1, 2):
        return 2
    started = None
    if len(args) == 2:
        try:
            started = float(args[1])
        except ValueError:
            started = None
    from aletheore import watch

    try:
        # allow_hosted=False: a background process the user never asked for must not send chunks to a hosted
        # embedding provider; an existing local index is still refreshed. edit_gitignore=False: Shield never
        # touches .gitignore (setup hides .aletheore/ through .git/info/exclude).
        watch.rebuild(Path(args[0]), lambda _message: None, allow_hosted=False, edit_gitignore=False)
    except Exception:  # noqa: BLE001  # nosec B110 - detached, nothing to report to
        return 1
    if started is not None:
        from aletheore.shield import guard_evidence

        try:
            guard_evidence.record_refresh_finished(Path(args[0]), started)
        except Exception:  # noqa: BLE001  # nosec B110 - without the record the guard falls back to its margin
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
