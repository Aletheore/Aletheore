"""Terminal look and feel: the wordmark, the black-and-white theme, the arrow-key menu and the "is this a project?" guard.

Everything here uses only the terminal's own foreground and background (bold, dim and reverse video), so it reads the
same on a dark or a light theme, and it degrades to plain text when there is no real terminal, colour is disabled
(NO_COLOR), or the terminal cannot show box-drawing characters.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import click
from rich.cells import cell_len
from rich.console import Console, Group
from rich.live import Live
from rich.text import Text

# --- the wordmark ------------------------------------------------------------------------------------------------

_GLYPHS = {
    "A": [" █████╗ ", "██╔══██╗", "███████║", "██╔══██║", "██║  ██║", "╚═╝  ╚═╝"],
    "L": ["██╗     ", "██║     ", "██║     ", "██║     ", "███████╗", "╚══════╝"],
    "E": ["███████╗", "██╔════╝", "█████╗  ", "██╔══╝  ", "███████╗", "╚══════╝"],
    "T": ["████████╗", "╚══██╔══╝", "   ██║   ", "   ██║   ", "   ██║   ", "   ╚═╝   "],
    "H": ["██╗  ██╗", "██║  ██║", "███████║", "██╔══██║", "██║  ██║", "╚═╝  ╚═╝"],
    "O": [" ██████╗ ", "██╔═══██╗", "██║   ██║", "██║   ██║", "╚██████╔╝", " ╚═════╝ "],
    "R": ["██████╗ ", "██╔══██╗", "██████╔╝", "██╔══██╗", "██║  ██║", "╚═╝  ╚═╝"],
}


def wordmark_lines(width: int = 80) -> list[str]:
    """The ALETHEORE block-letter wordmark, or a plain two-line fallback when the terminal is too narrow."""
    rows = ["".join(_GLYPHS[c][i] for c in "ALETHEORE").rstrip() for i in range(6)]
    if width < max(len(r) for r in rows) + 2:
        return ["ALETHEORE", "=" * len("ALETHEORE")]
    return rows


TAGLINE = "Evidence-grounded code intelligence. Every answer cites a real file and line."


# --- environment checks ------------------------------------------------------------------------------------------


def is_interactive() -> bool:
    """True only when a person is at a real terminal: a TTY on stdin and stdout, not a dumb terminal, not CI."""
    if os.environ.get("ALETHEORE_NO_MENU"):
        return False
    try:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            return False
    except (AttributeError, ValueError):
        return False
    if os.environ.get("TERM", "").lower() == "dumb":
        return False
    return not os.environ.get("CI")


def supports_unicode(stream=None) -> bool:
    encoding = (getattr(stream or sys.stdout, "encoding", None) or "").lower().replace("-", "")
    return "utf" in encoding


# --- the menu ----------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class MenuItem:
    key: str
    label: str
    hint: str = ""


_UP = {"\x1b[A", "\x1bOA", "\xe0H", "\x00H", "k", "K"}
_DOWN = {"\x1b[B", "\x1bOB", "\xe0P", "\x00P", "j", "J"}
_ENTER = {"\r", "\n", "\r\n"}
_QUIT = {"q", "Q", "\x1b", "\x03", "\x04"}


def _read_key() -> str:
    return click.getchar()


def truncate(text: str, width: int, *, unicode_ok: bool | None = None) -> str:
    """One line of `text` that fits in `width` terminal cells: whitespace collapsed, never wrapped.

    A line that is too long ends in an ellipsis character when the stream can show one, otherwise in three dots. A cut
    never leaves a dangling space or full stop in front of the ellipsis, so the result never contains "..".
    """
    text = " ".join(str(text).split())
    if width <= 0:
        return ""
    if cell_len(text) <= width:
        return text
    if unicode_ok is None:
        unicode_ok = supports_unicode()
    mark = "…" if unicode_ok else "..."
    room = width - len(mark)
    if room <= 0:
        return mark if width >= len(mark) else ""
    kept = ""
    used = 0
    for char in text:
        step = cell_len(char)
        if used + step > room:
            break
        kept += char
        used += step
    return kept.rstrip(" .") + mark


def _row(label: str, hint: str, *, width: int | None, label_w: int, unicode_ok: bool) -> tuple[str, str]:
    """The label cell (padded to `label_w`) and the hint for one menu row, both already fitted to the width."""
    shown = truncate(label, label_w - 2, unicode_ok=unicode_ok) if cell_len(label) > label_w - 2 else label
    padded = shown + " " * (label_w - cell_len(shown))
    if not hint:
        return padded, ""
    if width is None:
        return padded, hint
    room = width - 2 - 2 - label_w - 1
    return padded, (truncate(hint, room, unicode_ok=unicode_ok) if room >= 4 else "")


def render_menu(items: Sequence[MenuItem], selected: int, *, unicode_ok: bool, header: Text | None = None,
                footer: str = "up/down to move, enter to choose, q to quit", width: int | None = None,
                height: int | None = None) -> Group:
    """The menu as renderable lines. With a `width`, every row is fitted to it (labels and hints are cut with an
    ellipsis, nothing wraps); with a `height`, a list taller than the screen shows a window around the selection."""
    pointer = "❯" if unicode_ok else ">"
    if width is not None:
        width -= 1  # never write into the last column: some terminals wrap there and the redraw drifts
    label_w = max(cell_len(i.label) for i in items) + 2
    if width is not None:
        # Long labels (skill ids) may take at most about 60% of the row, so a hint always keeps some room.
        cap = max(14, int((width - 4) * 0.6)) if any(i.hint for i in items) else max(14, width - 4)
        label_w = min(label_w, cap)
    lines: list[Text] = []
    used = 0
    if header is not None:
        lines.append(header)
        lines.append(Text(""))
        columns = max(width or 80, 1)
        used += sum(max(1, -(-cell_len(part) // columns)) for part in header.plain.split("\n")) + 1
    first, last = 0, len(items)
    windowed = False
    if height is not None:
        rows = height - used - 3  # the blank line and footer below, and a spare line for the cursor
        if len(items) > rows:
            windowed = True
            rows = max(rows - 2, 3)  # two more lines for the "more above / below" markers
            first = min(max(selected - rows // 2, 0), len(items) - rows)
            last = first + rows
    arrow_up, arrow_down = ("↑", "↓") if unicode_ok else ("^", "v")
    if windowed:
        lines.append(Text(f"    {arrow_up} {first} more" if first else "", style="dim"))
    for index in range(first, last):
        item = items[index]
        label, hint = _row(item.label, item.hint, width=width, label_w=label_w, unicode_ok=unicode_ok)
        line = Text("  ")
        if index == selected:
            line.append(f"{pointer} ", style="bold")
            line.append(label, style="bold reverse")
        else:
            line.append("  ")
            line.append(label)
        if hint:
            line.append(f" {hint}", style="dim")
        lines.append(line)
    if windowed:
        lines.append(Text(f"    {arrow_down} {len(items) - last} more" if last < len(items) else "", style="dim"))
    lines.append(Text(""))
    text = footer.replace("up/down", "↑ ↓" if unicode_ok else "up/down")
    lines.append(Text("  " + (truncate(text, width - 2, unicode_ok=unicode_ok) if width is not None else text), style="dim"))
    return Group(*lines)


def choose(
    items: Sequence[MenuItem],
    console: Console,
    *,
    header: Text | None = None,
    footer: str = "up/down to move, enter to choose, q to quit",
    read_key: Callable[[], str] = _read_key,
    start: int = 0,
    numbers: bool = True,
) -> MenuItem | None:
    """Show an arrow-key menu and return the chosen item, or None if the person quit.

    Rows are fitted to the console width and a long list scrolls inside the console height. Pass numbers=False for
    a long list, where a stray digit must not pick a row.
    """
    unicode_ok = supports_unicode(console.file)

    def draw(position: int) -> Group:
        return render_menu(items, position, unicode_ok=unicode_ok, header=header, footer=footer,
                           width=console.width, height=console.height)

    selected = start
    with Live(draw(selected), console=console, auto_refresh=False, transient=True) as live:
        while True:
            try:
                key = read_key()
            except (KeyboardInterrupt, EOFError):
                return None
            if key in _UP:
                selected = (selected - 1) % len(items)
            elif key in _DOWN:
                selected = (selected + 1) % len(items)
            elif key in _ENTER:
                return items[selected]
            elif key in _QUIT:
                return None
            elif numbers and key.isdigit() and 1 <= int(key) <= len(items):
                return items[int(key) - 1]
            live.update(draw(selected), refresh=True)


# --- the "is this really a project?" guard -----------------------------------------------------------------------

_PROJECT_MARKERS = (
    ".git", ".hg", ".aletheore.json", "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "package.json",
    "go.mod", "Cargo.toml", "pom.xml", "build.gradle", "build.gradle.kts", "Gemfile", "composer.json", "mix.exs",
    "pubspec.yaml", "Package.swift", "CMakeLists.txt", "Makefile", "deno.json",
)
_PERSONAL_FOLDERS = {
    "desktop": "Desktop", "documents": "Documents", "downloads": "Downloads", "pictures": "Pictures",
    "music": "Music", "movies": "Movies", "videos": "Videos", "public": "Public", "library": "Library",
    "applications": "Applications",
}


def has_project_files(path: Path) -> bool:
    try:
        names = {entry.name for entry in path.iterdir()}
    except OSError:
        return False
    if any(marker in names for marker in _PROJECT_MARKERS):
        return True
    return any(name.endswith((".sln", ".csproj", ".fsproj", ".xcodeproj")) for name in names)


def broad_directory_reason(path: str | Path, home: Path | None = None) -> str | None:
    """Why this folder is probably not a project (the file system root, your home folder, Desktop, Documents,
    Downloads, ...), or None when it looks like somewhere you would sensibly scan. A folder that does contain
    project files (a .git directory, pyproject.toml, package.json, ...) is never flagged, so a dotfiles repo in your
    home folder, or a monorepo checked out under Documents, passes."""
    try:
        resolved = Path(path).expanduser().resolve()
    except OSError:
        return None
    if home is None:
        from aletheore.user_paths import user_home

        home = user_home()
    try:
        home = home.resolve()
    except OSError:
        pass
    reason: str | None = None
    if resolved == Path(resolved.anchor):
        reason = "the top of your file system"
    elif resolved == home:
        reason = "your home folder"
    elif resolved.parent == home and resolved.name.lower() in _PERSONAL_FOLDERS:
        reason = f"your {_PERSONAL_FOLDERS[resolved.name.lower()]} folder"
    if reason is None or has_project_files(resolved):
        return None
    return reason
