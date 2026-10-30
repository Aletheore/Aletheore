"""The end-of-session receipt: what an agent session changed, what the guard found, and how to undo it."""

import os
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aletheore.shield import state
from aletheore.shield.checkpoint import Checkpoint, changed_since, list_checkpoints
from aletheore.shield.guard import GuardReport, clean_text, render_report, run_guard
from aletheore.shield.guard_evidence import load_base_evidence

MAX_FILES_LISTED = 50
GUARD_MAX_LINES = 40
KEEP_RECEIPTS = 50
KEEP_DAYS = 7
_CHECKPOINT_SCAN = 1000
_STAMP = "%Y%m%dT%H%M%SZ"
_RECEIPT_NAME = re.compile(r"(\d{8}T\d{6}Z)-(.+)\.md")
_NAME_CAP = 300
# A receipt stamped later than "now" by more than this is forged or from a skewed clock: it is ignored when
# finding the previous receipt and is pruned on the next write.
_FUTURE_TOLERANCE = timedelta(minutes=5)


@dataclass(frozen=True)
class Receipt:
    session: str
    created_at: datetime
    files: tuple[str, ...]
    report: GuardReport
    first_checkpoint: str | None
    session_known: bool = True
    previous_receipt: bool = True  # only meaningful when session_known is False


def _utc(moment: datetime | None) -> datetime:
    if moment is None:
        return datetime.now(timezone.utc)
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _receipts_dir(repo: Path) -> Path:
    return state.shield_dir(repo) / "receipts"


def _stamp_of(name: str) -> datetime | None:
    match = _RECEIPT_NAME.fullmatch(name)
    if match is None:
        return None
    try:
        return datetime.strptime(match.group(1), _STAMP).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _receipt_files(repo: Path, not_after: datetime | None = None) -> list[tuple[datetime, str, Path]]:
    """Receipt files, newest first by the timestamp in the file name (file mtime breaks ties within a second).
    With not_after, receipts stamped later than that are left out."""
    try:
        entries = list(_receipts_dir(repo).iterdir())
    except OSError:
        return []
    found = []
    for path in entries:
        stamp = _stamp_of(path.name)
        if stamp is None or (not_after is not None and stamp > not_after) or not path.is_file():
            continue
        try:
            mtime = path.stat().st_mtime_ns
        except OSError:
            mtime = 0
        found.append((stamp, mtime, path.name, path))
    found.sort(key=lambda item: item[:3], reverse=True)
    return [(stamp, name, path) for stamp, _, name, path in found]


def latest_receipt(repo: Path, *, now: datetime | None = None) -> Path | None:
    """The newest receipt by file-name timestamp. Receipts stamped in the future (beyond a small tolerance) are
    ignored, so a forged or clock-skewed name cannot shadow real ones."""
    found = _receipt_files(Path(repo), _utc(now) + _FUTURE_TOLERANCE)
    return found[0][2] if found else None


def _session_checkpoints(
    repo: Path, session_id: str, now: datetime
) -> tuple[list[Checkpoint], bool]:
    """The checkpoints that belong to the session, oldest first."""
    everything = list_checkpoints(repo, limit=_CHECKPOINT_SCAN)
    previous = True
    if state.session_state_path(repo, session_id) is not None:
        mine = [c for c in everything if c.session == session_id]
    else:
        newest_checkpoint = max((c.created_at for c in everything), default=now)
        newest = _receipt_files(repo, max(now, newest_checkpoint) + _FUTURE_TOLERANCE)
        since = newest[0][0] if newest else None
        previous = since is not None
        # Receipt names carry whole seconds, so a checkpoint in the same second counts as before the receipt.
        mine = [c for c in everything if since is None or c.created_at.replace(microsecond=0) > since]
    return sorted(mine, key=lambda c: c.created_at), previous


def build_receipt(
    repo: Path,
    session_id: str,
    *,
    now: datetime | None = None,
    lookups: bool | None = None,
    budget_s: float = 6.0,
    opener: Callable | None = None,
) -> Receipt | None:
    """The session's receipt, or None when the session did nothing worth a receipt. Never raises."""
    repo = Path(repo)
    try:
        known = state.session_state_path(repo, session_id) is not None
        created = _utc(now)
        checkpoints, previous = _session_checkpoints(repo, session_id, created)
        if not checkpoints:
            return None
        first = checkpoints[0]
        extra: list[str] = []
        try:
            files = tuple(sorted(dict.fromkeys(changed_since(repo, first))))
        except Exception as exc:  # noqa: BLE001 - a hook must not fail because git did
            files = ()
            extra.append(f"could not compute the files touched ({type(exc).__name__})")
        try:
            report = run_guard(
                repo,
                base="HEAD",
                lookups=lookups,
                budget_s=budget_s,
                base_evidence=load_base_evidence(repo, session_id),
                opener=opener,
            )
        except Exception as exc:  # noqa: BLE001
            report = GuardReport((), (f"could not compute the guard report ({type(exc).__name__})",))
        if extra:
            report = GuardReport(report.findings, tuple(extra) + tuple(report.not_checked))
        session = session_id if known else "unknown"
        return Receipt(session, created, files, report, first.ref, known, previous)
    except Exception:  # noqa: BLE001
        return None


def _code(text: str) -> str:
    """A Markdown code span for already-cleaned text, whatever backticks it contains."""
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * (longest + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def _inert(text: str) -> str:
    """Make already-cleaned text unable to form links, images or HTML when rendered as Markdown."""
    return (
        text.replace("[", "\\[").replace("]", "\\]").replace("<", "&lt;").replace(">", "&gt;")
    )


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _verdict(report: GuardReport) -> str:
    findings = report.findings
    if findings:
        warns = sum(1 for f in findings if f.severity == "warn")
        text = _plural(len(findings), "finding", "findings")
        if warns:
            text += f" ({_plural(warns, 'warning', 'warnings')})"
    else:
        text = "no findings"
    if report.not_checked:
        text += "; some checks did not run"
    return text


def render_markdown(receipt: Receipt) -> str:
    created = _utc(receipt.created_at).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = ["# Shield receipt", ""]
    if receipt.session_known:
        lines.append(f"- Session: {_code(clean_text(receipt.session, _NAME_CAP))}")
    else:
        if receipt.previous_receipt:
            lines.append("- Session: session id unavailable: covers changes since the previous receipt")
        else:
            lines.append("- Session: session id unavailable: no previous receipt, covers all recorded checkpoints")
    lines += [
        f"- Created: {created}",
        f"- Guard verdict: {_verdict(receipt.report)}",
        "",
        f"## Files touched ({len(receipt.files)})",
        "",
        "Changes since the first checkpoint of this session; edits by other sessions in that window are included.",
        "",
    ]
    shown = receipt.files[:MAX_FILES_LISTED]
    lines += [f"- {_code(clean_text(name, _NAME_CAP))}" for name in shown] or ["- none"]
    if len(receipt.files) > len(shown):
        lines.append(f"- and {len(receipt.files) - len(shown)} more")
    lines += ["", "## Guard", ""]
    rendered = render_report(receipt.report, max_lines=GUARD_MAX_LINES)
    if rendered:
        lines += [f"- {_inert(clean_text(line))}" for line in rendered.splitlines()]
    else:
        lines.append("No findings.")
    lines += ["", "## Undo", "", "To undo this session: `aletheore shield undo`", ""]
    if receipt.first_checkpoint:
        lines.append(f"Restore checkpoint: {_code(clean_text(receipt.first_checkpoint, _NAME_CAP))}")
    else:
        lines.append("Restore checkpoint: none recorded")
    return "\n".join(lines) + "\n"


def _prune(directory: Path, repo: Path, now: datetime, keep_name: str) -> None:
    cutoff = now - timedelta(days=KEEP_DAYS)
    future = now + _FUTURE_TOLERANCE
    rank = 0
    for stamp, name, path in _receipt_files(repo):
        if name == keep_name:
            rank += 1
            continue
        if stamp > future:  # forged or clock-skewed: never counts toward the kept receipts
            pass
        else:
            rank += 1
            if rank <= KEEP_RECEIPTS or stamp > cutoff:
                continue
        try:
            path.unlink()
        except OSError:
            pass


def write_receipt(repo: Path, receipt: Receipt) -> Path:
    """Write the receipt atomically and prune old ones. Raises OSError only when the write itself fails."""
    repo = Path(repo)
    directory = _receipts_dir(repo)
    directory.mkdir(parents=True, exist_ok=True)
    created = _utc(receipt.created_at)
    token = receipt.session if state.session_state_path(repo, receipt.session) is not None else "unknown"
    stem = f"{created.strftime(_STAMP)}-{token}"
    target = directory / f"{stem}.md"
    suffix = 2
    while target.exists():  # same second, same session: keep both
        target = directory / f"{stem}-{suffix}.md"
        suffix += 1
    handle, temp_name = tempfile.mkstemp(dir=directory, prefix=".receipt-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as out:
            out.write(render_markdown(receipt))
        os.replace(temp_name, target)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
    try:
        _prune(directory, repo, created, target.name)
    except Exception:  # noqa: BLE001 - failing to tidy old receipts must not fail the write
        pass
    return target
