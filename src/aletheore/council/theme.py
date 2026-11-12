"""The look of the Council terminal: the Aletheore website's dark palette, with white as the single accent.

Everything that draws colour takes it from here, as
named tokens, so the look can be checked (contrast, fallbacks, no stray colours) instead of trusted.

* `paper`, `raised`, `term`: backgrounds (stream, panels and bars, cards and inputs).
* `ink`, `muted`, `faint`: text from strongest to quietest. `faint` is the website's grey lightened until it reads at 4.5:1;
  the website's own darker grey is kept as `dim` for things that are not text (idle dots, folded outlines).
* `accent` (white): brand, selection, primary actions, the SEALED stamp. The only accent.
* `needs` (amber): something is waiting for a person. `ok` (green): working, verified, live. `error` (red): only real errors,
  always with a symbol and words, on `error-bg`.

Terminals differ: true colour shows these exactly; 256-colour terminals (macOS Terminal.app) get the nearest colours that still pass
contrast; 16-colour terminals get ANSI names; `NO_COLOR` drops colour entirely and the screens carry meaning in symbols and words.
"""

from __future__ import annotations

import hashlib
import os
import zlib
from typing import Mapping

TOKENS: dict[str, str] = {
    "paper": "#0F0E0B", "raised": "#17150F", "term": "#1B1912",
    "line": "#2B2820", "line-strong": "#3F3B2F",
    "ink": "#F1EEE6", "muted": "#A39D8E", "faint": "#8C8676", "dim": "#756F61",
    "accent": "#FFFFFF", "needs": "#E3A23B", "ok": "#6FA874", "error": "#E0584A", "error-bg": "#2A1714",
}

SYMBOLS = {"brand": "◆", "dot": "●", "busy": "◐◓◑◒", "ok": "✓", "error": "✕", "off": "○", "prompt": "›", "section": "§", "ellipsis": "…"}

_BACKGROUNDS = ("paper", "raised", "term")
_TEXT = ("ink", "muted", "faint", "accent", "needs", "ok", "error")

# 16-colour terminals: ANSI names. Backgrounds stay the terminal's own so nothing is unreadable.
_SIXTEEN = {
    "paper": "ansi_default", "raised": "ansi_default", "term": "ansi_default", "line": "ansi_bright_black", "line-strong": "ansi_bright_black",
    "ink": "ansi_bright_white", "muted": "ansi_white", "faint": "ansi_white", "dim": "ansi_bright_black",
    "accent": "ansi_bright_white", "needs": "ansi_yellow", "ok": "ansi_green", "error": "ansi_red", "error-bg": "ansi_default",
}
_RICH_NAME = {"ansi_default": "default", "ansi_bright_white": "bright_white", "ansi_white": "white", "ansi_bright_black": "bright_black",
              "ansi_yellow": "yellow", "ansi_green": "green", "ansi_red": "red"}


# -- contrast ---------------------------------------------------------------------------------------------------------
def _rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _luminance(hex_color: str) -> float:
    def channel(c: int) -> float:
        v = c / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = _rgb(hex_color)
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(fg: str, bg: str) -> float:
    """WCAG contrast ratio between two #rrggbb colours."""
    a, b = _luminance(fg), _luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


# -- 256 colours ------------------------------------------------------------------------------------------------------
def _xterm_256() -> list[str]:
    steps = (0, 95, 135, 175, 215, 255)
    cube = [f"#{r:02X}{g:02X}{b:02X}" for r in steps for g in steps for b in steps]
    greys = [f"#{v:02X}{v:02X}{v:02X}" for v in range(8, 239, 10)]
    return cube + greys


_PALETTE_256 = _xterm_256()


def _nearest(hex_color: str, candidates: list[str]) -> str:
    r, g, b = _rgb(hex_color)
    return min(candidates, key=lambda c: (_rgb(c)[0] - r) ** 2 + (_rgb(c)[1] - g) ** 2 + (_rgb(c)[2] - b) ** 2)


def _snapped() -> dict[str, str]:
    """The palette on a 256-colour terminal: each background snaps to its nearest colour, and each text colour to the nearest one
    that still reads at 4.5:1 on all three snapped backgrounds."""
    out = {name: _nearest(value, _PALETTE_256) for name, value in TOKENS.items()}
    backgrounds = [out[b] for b in _BACKGROUNDS]
    for name in _TEXT:
        ok = [c for c in _PALETTE_256 if all(contrast(c, bg) >= 4.5 for bg in backgrounds)]
        out[name] = _nearest(TOKENS[name], ok)
    return out


_SNAPPED = _snapped()


# -- environment ------------------------------------------------------------------------------------------------------
def detect_depth(env: Mapping[str, str]) -> str:
    """truecolor, 256, 16 or none, from the environment (https://no-color.org for NO_COLOR)."""
    if env.get("NO_COLOR"):
        return "none"
    if env.get("COLORTERM", "").lower() in ("truecolor", "24bit") or env.get("WT_SESSION") or env.get("TERM_PROGRAM") in ("iTerm.app", "vscode", "WezTerm"):
        return "truecolor"
    term = env.get("TERM", "")
    if "kitty" in term or "direct" in term:
        return "truecolor"
    if "256color" in term:
        return "256"
    return "16"


def motion_enabled(env: Mapping[str, str]) -> bool:
    if env.get("NO_COLOR"):
        return False
    return env.get("COUNCIL_REDUCED_MOTION", "").lower() not in ("1", "true", "yes", "on")


def css_variables(depth: str) -> dict[str, str]:
    """Token name -> colour value for Textual's CSS variables ($paper, $accent, ...) at the given colour depth."""
    if depth == "truecolor":
        return dict(TOKENS)
    if depth == "256":
        return dict(_SNAPPED)
    if depth == "16":
        return dict(_SIXTEEN)
    return {name: "ansi_default" for name in TOKENS}  # none: no colour, structure and symbols carry the meaning


def rich_color(token: str, depth: str) -> str:
    """The same colour as a Rich style string."""
    value = css_variables(depth)[token]
    return _RICH_NAME.get(value, value)


DEPTH = detect_depth(os.environ)  # what this terminal can show, decided once at start


def color(token: str) -> str:
    """A Rich colour string for a token at this terminal's depth."""
    return rich_color(token, DEPTH)


# -- people -----------------------------------------------------------------------------------------------------------
# A muted set for the thin bar beside each person's messages. None of them is the accent, amber, green or red.
_PEOPLE = ("#8FB3C9", "#C8A3C8", "#C9B38A", "#8AC9C2", "#B7A6D9", "#D1A398", "#A3C28A", "#9AA8D9")


def person_color(name: str, depth: str = "truecolor") -> str:
    """A stable colour for a name. At 16 colours or none, everyone shares the quiet text colour (the name itself tells them apart)."""
    if depth in ("16", "none"):
        return rich_color("muted", depth)
    index = zlib.crc32(hashlib.sha256(name.encode("utf-8")).digest()) % len(_PEOPLE)
    color = _PEOPLE[index]
    return _nearest(color, _PALETTE_256) if depth == "256" else color
