"""The side panel: agents with live progress, members with last seen, and a security summary. Pure text builders."""

from __future__ import annotations

from datetime import datetime

from rich.cells import cell_len
from rich.text import Text

from . import theme
from .tui_cards import fit

_D = theme.DEPTH
FULL, EMPTY = "█", "░"


def _c(token: str) -> str:
    return theme.rich_color(token, _D)


def heading(name: str) -> Text:
    return Text(f"§ {name}\n", style=f"bold {_c('muted')}")


def bar(done: int, total: int, width: int, frame: int | None = None) -> str:
    """A progress bar of `width` cells. With a known total it fills in proportion; with no total (or `frame` given and
    total 0) a short block sweeps along so a busy agent still looks busy."""
    width = max(3, width)
    if total > 0:
        filled = max(0, min(width, round(width * done / total)))
        return FULL * filled + EMPTY * (width - filled)
    if frame is None:
        return EMPTY * width
    span = min(3, width)
    start = frame % (width + span) - span
    return "".join(FULL if start <= i < start + span else EMPTY for i in range(width))


def agents_section(rows, tasks, width: int, frame: int | None, spin: str) -> Text:
    """One block per agent: state mark and name, then owner and what it is doing, then a bar while it works."""
    t = heading("AGENTS")
    if not rows:
        t.append("no areas yet\n", style=_c("faint"))
        return t
    for r in rows:
        mine = [k for k in tasks if k.area == r.area]
        done = sum(1 for k in mine if k.status == "done")
        working = r.status == "working"
        t.append(f"{spin if working else '○'} ", style=_c("ok") if working else _c("faint"))
        t.append(fit(r.agent_id, width - 2), style=f"bold {theme.person_color(r.owner, _D)}")
        if working:
            t.append("\n  " + fit(r.detail, width - 2), style=_c("ok"))
            t.append("\n  ")
            t.append(bar(done, len(mine), min(12, width - 10), frame), style=_c("ok"))
            if mine:
                t.append(f" {done}/{len(mine)}", style=_c("muted"))
        else:
            t.append("\n  " + fit(r.owner, 14), style=_c("muted"))
            t.append(f" · idle, {r.last}" if r.last else " · idle", style=_c("faint"))
        t.append("\n")
    return t


def last_seen_label(iso: str | None, now: datetime) -> str:
    """Council only knows when someone last did something in the room, so it never says 'online'."""
    if not iso:
        return "not seen yet"
    try:
        then = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return "not seen yet"
    secs = int((now - then).total_seconds())
    if secs < 300:
        return "active now"
    if secs < 3600:
        return f"last seen {secs // 60}m ago"
    if secs < 86400:
        return f"last seen {secs // 3600}h ago"
    return f"last seen {secs // 86400}d ago"


def members_section(members: list[str], host: str, me: str, seen: dict[str, str], now: datetime, width: int) -> Text:
    t = heading("MEMBERS")
    for name in members:
        label = last_seen_label(seen.get(name), now)
        t.append("● " if label == "active now" else "○ ", style=_c("ok") if label == "active now" else _c("faint"))
        t.append(fit(name, width - 8), style=f"bold {theme.person_color(name, _D)}")
        if name == host:
            t.append(" host", style=_c("faint"))
        if name == me:
            t.append(" you", style=_c("faint"))
        t.append(f"\n  {label}\n", style=_c("muted") if label == "active now" else _c("faint"))
    return t


def security_facts(store) -> dict:
    """What the sealing store has counted; empty for a room that is not sealed (the store then has no such counters)."""
    if not hasattr(store, "rejected"):
        return {}
    epoch = None
    try:
        latest = store._keyring.latest()  # noqa: SLF001 - read-only peek; there is no public accessor
        epoch = latest[0] if latest else None
    except Exception:  # nosec B110 - best effort: a failure here is expected and must not stop this path
        pass
    return {"epoch": epoch, "rejected": store.rejected, "unreadable": store.unreadable, "gaps": len(store.gaps)}


def security_section(sealed: bool, facts: dict, read_count: int) -> Text:
    t = heading("SECURITY")
    if not sealed:
        t.append("not sealed\n", style=_c("muted"))
        t.append("the server can read\nthis room\n", style=_c("faint"))
        return t
    t.append("▌SEALED▐", style=f"bold {_c('accent')}")
    if facts.get("epoch") is not None:
        t.append(f"  key {facts['epoch']}", style=_c("muted"))
    t.append("\n")
    bad = facts.get("rejected", 0) + facts.get("unreadable", 0)
    t.append(f"✓ {read_count} read and checked\n", style=_c("ok"))
    if bad or facts.get("gaps"):
        t.append(f"! {facts.get('rejected', 0)} ignored (failed checks)\n", style=_c("needs"))
        t.append(f"! {facts.get('unreadable', 0)} unreadable\n", style=_c("needs"))
        t.append(f"! {facts.get('gaps', 0)} gaps in order\n", style=_c("needs"))
    else:
        t.append("✓ nothing ignored\n", style=_c("ok"))
    return t


def badge(count: int) -> Text:
    """A small amber count: `[2]`. Zero shows nothing."""
    return Text(f" {count} ", style=f"bold {_c('paper')} on {_c('needs')}") if count > 0 else Text("")


def budget_section(snap: dict | None, width: int = 24) -> Text:
    """Today's file allowance on this host: a bar, the numbers, and plain words when it is nearly or fully used. Empty with no cap."""
    t = Text()
    if not snap or not snap.get("cap_bytes"):
        return t
    cap, used = int(snap["cap_bytes"]), int(snap.get("used_bytes", 0))
    mb = lambda b: f"{b / 1048576:.0f}"  # noqa: E731
    t.append("\n")
    t.append_text(heading("FILES TODAY"))
    full = used >= cap
    tone = "needs" if used >= 0.8 * cap else "ok"
    t.append(bar(used, cap, min(14, width - 8)), style=_c(tone))
    t.append(f" {mb(used)}/{mb(cap)} MB\n", style=_c("muted"))
    if full:
        hours = max(1, round(int(snap.get("resets_in_s", 0)) / 3600))
        t.append(f"used up, frees in about {hours} h\nmessages still work\n", style=_c("needs"))
    elif used >= 0.8 * cap:
        t.append("nearly used up today\n", style=_c("needs"))
    return t
