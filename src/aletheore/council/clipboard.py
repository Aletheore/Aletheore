"""Read an image from the system clipboard, if there is one.

Terminals paste text, never images, so this asks the operating system directly: AppleScript on macOS, PowerShell on
Windows, wl-paste or xclip on Linux. Whatever comes back is saved to a file, checked by its bytes, and discarded if it
is not a supported image. A missing tool or an empty clipboard simply means "no image".
"""

from __future__ import annotations

import subprocess  # nosec B404 - no shell: argument lists only
import sys
import time
import uuid
from pathlib import Path

from aletheore.council.attachments import MAX_IMAGE_BYTES, sniff_image

_TIMEOUT = 10


# PowerShell ends a '...' string at any of these, not just the ASCII apostrophe (U+2018, U+2019, U+201A, U+201B).
_PS_SINGLE_QUOTES = "'\u2018\u2019\u201a\u201b"


def _ps_single_quoted(text: str) -> str:
    """`text` made safe inside a PowerShell single-quoted string: every quote-like character is doubled."""
    for ch in _PS_SINGLE_QUOTES:
        text = text.replace(ch, ch + ch)
    return text


def _try(run, argv: list[str]) -> subprocess.CompletedProcess | None:
    try:
        return run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None


def read_clipboard_image(dest_dir: Path, run=subprocess.run, platform: str | None = None) -> Path | None:
    platform = platform or sys.platform
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / f"clipboard-{int(time.time())}-{uuid.uuid4().hex[:6]}.png"
    if platform == "darwin":
        info = _try(run, ["osascript", "-e", "clipboard info"])
        if info is None or info.returncode != 0 or "PNGf" not in info.stdout:
            return None
        script = [
            "osascript",
            "-e", f'set f to (open for access POSIX file "{out}" with write permission)',
            "-e", "set eof f to 0",
            "-e", "write (the clipboard as «class PNGf») to f",
            "-e", "close access f",
        ]
        _try(run, script)
    elif platform == "win32":
        ps_out = _ps_single_quoted(str(out))
        ps = ("Add-Type -AssemblyName System.Windows.Forms; Add-Type -AssemblyName System.Drawing; "
              "$i=[System.Windows.Forms.Clipboard]::GetImage(); "
              f"if($i){{$i.Save('{ps_out}',[System.Drawing.Imaging.ImageFormat]::Png)}}")
        _try(run, ["powershell", "-NoProfile", "-STA", "-Command", ps])
    else:
        for argv in (["wl-paste", "--type", "image/png"], ["xclip", "-selection", "clipboard", "-t", "image/png", "-o"]):
            try:
                res = run(argv, capture_output=True, timeout=_TIMEOUT)
            except (OSError, subprocess.SubprocessError):
                continue
            if res.returncode == 0 and isinstance(res.stdout, (bytes, bytearray)) and res.stdout:
                out.write_bytes(bytes(res.stdout))
                break
    if not out.is_file():
        return None
    data = out.read_bytes()
    if len(data) > MAX_IMAGE_BYTES or sniff_image(data) is None:
        out.unlink(missing_ok=True)
        return None
    return out


def open_in_viewer(path: Path) -> None:
    """Open an (already verified) image file in the system's default viewer."""
    import os

    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]  # nosec B606 - opens a file Council created, no shell
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)],  # nosec B603 - argument list, no shell, no user-controlled program
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
