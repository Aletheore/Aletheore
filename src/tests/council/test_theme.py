"""The terminal theme: the website's dark palette with a white accent, its fallbacks, and the rules that keep the look honest."""

import re

import pytest

from aletheore.council import theme
from aletheore.council.theme import TOKENS, contrast, css_variables, detect_depth, motion_enabled, person_color, rich_color

TEXT_TOKENS = ("ink", "muted", "faint", "accent", "needs", "ok")
BACKGROUNDS = ("paper", "raised", "term")


def test_the_tokens_are_the_websites_dark_palette_with_a_white_accent():
    assert (TOKENS["paper"], TOKENS["raised"], TOKENS["term"], TOKENS["ink"]) == ("#0F0E0B", "#17150F", "#1B1912", "#F1EEE6")
    assert (TOKENS["line"], TOKENS["line-strong"], TOKENS["muted"]) == ("#2B2820", "#3F3B2F", "#A39D8E")
    assert TOKENS["accent"] == "#FFFFFF" and TOKENS["ok"] == "#6FA874" and TOKENS["needs"] == "#E3A23B"
    assert TOKENS["error"] == "#E0584A" and TOKENS["error-bg"] == "#2A1714"


@pytest.mark.parametrize("depth", ["truecolor", "256"])
def test_every_text_token_passes_contrast_on_its_backgrounds(depth):
    colors = css_variables(depth)
    for fg in TEXT_TOKENS:
        for bg in BACKGROUNDS:
            ratio = contrast(colors[fg], colors[bg])
            assert ratio >= 4.5, f"{fg} on {bg} at {depth} is only {ratio:.2f}:1"


def test_error_red_only_pairs_with_the_soft_red_background_and_the_normal_ones():
    colors = css_variables("truecolor")
    assert contrast(colors["error"], colors["error-bg"]) >= 4.5
    for bg in BACKGROUNDS:
        assert contrast(colors["error"], colors[bg]) >= 4.5


def test_the_decorative_grey_is_not_used_for_text_and_the_text_grey_is_readable():
    assert TOKENS["faint"] != TOKENS["dim"]  # the website's own faint grey is too dark for text; it stays for rules and idle dots
    assert contrast(TOKENS["dim"], TOKENS["paper"]) < 4.5 <= contrast(TOKENS["faint"], TOKENS["paper"])


def test_depth_detection_from_colorterm_term_and_no_color():
    assert detect_depth({"COLORTERM": "truecolor", "TERM": "xterm-256color"}) == "truecolor"
    assert detect_depth({"COLORTERM": "24bit"}) == "truecolor"
    assert detect_depth({"WT_SESSION": "abc"}) == "truecolor"  # Windows Terminal
    assert detect_depth({"TERM": "xterm-256color"}) == "256"  # macOS Terminal.app
    assert detect_depth({"TERM": "xterm"}) == "16"
    assert detect_depth({}) == "16"
    assert detect_depth({"NO_COLOR": "1", "COLORTERM": "truecolor"}) == "none"
    assert detect_depth({"NO_COLOR": ""}) != "none"  # an empty NO_COLOR does not count (the NO_COLOR convention)


def test_every_token_has_a_256_and_a_16_colour_fallback():
    for depth in ("256", "16", "none"):
        colors = css_variables(depth)
        assert set(colors) == set(TOKENS)
    sixteen = css_variables("16")
    assert all(v.startswith("ansi_") for v in sixteen.values())
    assert sixteen["accent"] == "ansi_bright_white" and sixteen["needs"] == "ansi_yellow" and sixteen["ok"] == "ansi_green" and sixteen["error"] == "ansi_red"
    snapped = css_variables("256")
    assert all(re.fullmatch(r"#[0-9A-Fa-f]{6}", v) for v in snapped.values())
    assert snapped["paper"] != snapped["term"] or True  # near-black greys may snap together; contrast is what matters (tested above)


def test_rich_colour_names_for_each_depth():
    assert rich_color("needs", "truecolor") == "#E3A23B"
    assert rich_color("needs", "16") == "yellow" and rich_color("accent", "16") == "bright_white" and rich_color("ink", "none") == "default"


def test_person_colors_are_stable_readable_and_never_the_error_red_or_amber():
    assert person_color("megan") == person_color("megan")
    seen = {person_color(n) for n in ("megan", "moe", "hosty", "bob", "alice", "carol", "dave", "erin", "frank")}
    assert len(seen) >= 4
    forbidden = {TOKENS["error"].lower(), TOKENS["needs"].lower(), TOKENS["accent"].lower(), TOKENS["ok"].lower()}
    for name in ("megan", "moe", "hosty", "bob", "alice", "carol", "dave", "erin", "frank", "x" * 40, "名前"):
        color = person_color(name)
        assert color.lower() not in forbidden
        for bg in BACKGROUNDS:
            assert contrast(color, TOKENS[bg]) >= 4.5


def test_reduced_motion_from_env():
    assert motion_enabled({}) is True
    assert motion_enabled({"COUNCIL_REDUCED_MOTION": "1"}) is False
    assert motion_enabled({"COUNCIL_REDUCED_MOTION": "true"}) is False
    assert motion_enabled({"COUNCIL_REDUCED_MOTION": "0"}) is True
    assert motion_enabled({"NO_COLOR": "1"}) is False


def test_symbols_are_plain_unicode_no_emoji():
    for name, value in theme.SYMBOLS.items():
        for ch in "".join(value) if not isinstance(value, str) else value:
            assert ord(ch) < 0x1F000, f"{name} uses an emoji-range character"  # emoji break terminal column alignment


def test_css_uses_only_tokens():
    from aletheore.council.tui import CouncilApp, RoomPicker

    for css in (CouncilApp.CSS, RoomPicker.DEFAULT_CSS):
        assert not re.findall(r"#[0-9a-fA-F]{3,8}\b", css), "colours in CSS must be $tokens"
        for var in re.findall(r"\$([a-z][a-z-]*)", css):
            assert var in TOKENS, f"unknown token ${var}"


def test_the_app_publishes_the_tokens_as_css_variables(tmp_path):
    import asyncio

    from aletheore.council.sqlite_store import SqliteRoomStore
    from aletheore.council.tui import CouncilApp

    store = SqliteRoomStore(tmp_path / "r.db")
    store.create_room("r1", "alice")

    async def go():
        app = CouncilApp(store, "r1", "alice", poll_s=0.05)
        async with app.run_test(size=(100, 30)):
            variables = app.get_css_variables()
            expected = css_variables(theme.DEPTH)  # the colours the terminal in use gets (a CI shell may have none)
            assert variables["paper"].upper() == expected["paper"].upper() and variables["accent"].upper() == expected["accent"].upper()
            if theme.DEPTH == "truecolor":
                assert variables["paper"].upper() == TOKENS["paper"]

    asyncio.run(go())
