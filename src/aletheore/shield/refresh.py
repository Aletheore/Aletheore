"""Entry point of the detached evidence refresh Shield's guard starts: `python -m aletheore.shield.refresh <repo>`.

It runs the same re-scan the watcher runs (`aletheore.watch.rebuild`: slow checks stay off, .gitignore untouched) and exits. Output is
discarded and every failure is swallowed into an exit code: nobody is waiting on it.
"""

from __future__ import annotations

import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    """`<repo> [<start time>]`. With the start time the guard recorded in its marker, a successful rebuild is
    followed by a completion record naming that start and the mtime air.json had right after this rebuild wrote it
    (none when the rebuild never reported an evidence write)."""
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
    from aletheore.shield import guard_evidence

    repo = Path(args[0])
    written: list[int | None] = []

    def report(message: str) -> None:
        # rebuild reports "evidence updated" right after its own write of air.json and before the index build, which
        # can take minutes; the mtime is taken here so a file another writer puts there later is never recorded.
        if message == "evidence updated" and not written:
            written.append(guard_evidence.evidence_mtime_ns(repo))

    try:
        # allow_hosted=False: a background process the user never asked for must not send chunks to a hosted
        # embedding provider; an existing local index is still refreshed. edit_gitignore=False: Shield never
        # touches .gitignore (setup hides .aletheore/ through .git/info/exclude).
        watch.rebuild(repo, report, allow_hosted=False, edit_gitignore=False)
    except Exception:  # noqa: BLE001  # nosec B110 - detached, nothing to report to
        return 1
    if started is not None and written:
        try:
            guard_evidence.record_refresh_finished(repo, started, written[0])
        except Exception:  # noqa: BLE001  # nosec B110 - without the record the guard falls back to its margin
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
