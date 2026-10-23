import io

import pytest
from rich.cells import cell_len
from rich.console import Console

from aletheore import skill_hub, ui
from aletheore.skills_browser import SkillsBrowser, detail_lines, print_detail, skill_items

DOWN, UP, ENTER = "\x1b[B", "\x1b[A", "\r"


def _keys(*keys):
    it = iter(keys)
    return lambda: next(it)


def _console(width=100, height=40):
    return Console(file=io.StringIO(), width=width, height=height, force_terminal=False, color_system=None)


METAS = [
    skill_hub.SkillMeta("testing/alpha", "testing", "Alpha Skill", "Does the alpha thing.\nOn two lines.", shelf="quality"),
    skill_hub.SkillMeta("testing/beta", "testing", "Beta Skill", "Does the beta thing.", shelf="quality"),
    skill_hub.SkillMeta("debugging/gamma", "debugging", "Gamma Skill", "Does the gamma thing.", shelf="quality"),
]
SOURCE = "https://github.com/example/skills/tree/0123456789abcdef0123456789abcdef01234567/skills/a-very-long-path-name"
OVERVIEW = [
    {"shelf": "build", "title": "Build", "skills": 5,
     "categories": [{"category": "backend", "skills": 3}, {"category": "frontend", "skills": 2}]},
    {"shelf": "quality", "title": "Quality", "skills": 3,
     "categories": [{"category": "debugging", "skills": 1}, {"category": "testing", "skills": 2}]},
]


def _skill(meta, files=("references/one.md", "scripts/run.sh"), total=None):
    return skill_hub.Skill(
        skill_id=meta.skill_id, title=meta.title, description=meta.description, content=f"# {meta.title}\nbody text\n",
        source_url=SOURCE, license="MIT", shelf=meta.shelf, requires=["scripts"], files=list(files),
        files_total=len(files) if total is None else total,
    )


@pytest.fixture
def hub(monkeypatch):
    calls = {"opened": [], "listed": [], "searched": []}
    by_id = {m.skill_id: m for m in METAS}

    def search(query, category=None, limit=10):
        calls["searched"].append(query)
        return list(METAS) if "test" in query else []

    def list_skills(category=None):
        calls["listed"].append(category)
        return [m for m in METAS if m.category == category]

    def get_skill(skill_id):
        calls["opened"].append(skill_id)
        if skill_id not in by_id:
            raise skill_hub.SkillNotFoundError(skill_id)
        return _skill(by_id[skill_id])

    monkeypatch.setattr(skill_hub, "search_skills", search)
    monkeypatch.setattr(skill_hub, "list_overview", lambda: OVERVIEW)
    monkeypatch.setattr(skill_hub, "list_skills", list_skills)
    monkeypatch.setattr(skill_hub, "get_skill", get_skill)
    return calls


def _browser(keys, answers=("flaky test",), console=None, pager=None):
    typed = iter(answers)
    return SkillsBrowser(console or _console(), read_key=_keys(*keys), ask=lambda question: next(typed),
                         pager=pager or (lambda text: None), clear=lambda: None)


# --- the rows -------------------------------------------------------------------------------------------------------


def test_skill_rows_carry_id_and_title_as_label_and_a_one_line_description_as_hint():
    rows = skill_items(METAS)
    assert rows[0].key == "testing/alpha" and rows[0].label == "testing/alpha  Alpha Skill"
    assert rows[0].hint == "Does the alpha thing. On two lines."  # newlines collapse: a hint is one line


def test_long_skill_rows_are_trimmed_to_the_terminal_width():
    long = [skill_hub.SkillMeta("testing/" + "x" * 80, "testing", "Title " * 10, "word " * 200)]
    for width in (60, 80, 200):
        out = _console(width)
        out.print(ui.render_menu(skill_items(long), 0, unicode_ok=True, width=width))
        assert all(cell_len(line) <= width for line in out.file.getvalue().splitlines())


# --- the submenu: search --------------------------------------------------------------------------------------------


def test_search_then_detail_then_back_returns_to_the_same_row(hub):
    pages = []
    # submenu: enter on "search"; results: down to the second skill and open it; detail: enter on "Read SKILL.md",
    # then down + enter on Back; the list is back on the second skill, so enter opens it again; back out of the
    # detail, q out of the results and q out of the submenu.
    keys = (ENTER, DOWN, ENTER, ENTER, DOWN, ENTER, ENTER, DOWN, ENTER, "q", "q")
    _browser(keys, pager=pages.append).run()
    assert hub["searched"] == ["flaky test"]
    assert hub["opened"] == ["testing/beta", "testing/beta"]
    assert pages == ["# Beta Skill\nbody text\n"]  # the SKILL.md went to the pager, not to the terminal


def test_a_blank_search_goes_back_to_the_submenu_without_searching(hub):
    _browser((ENTER, "q"), answers=("   ",)).run()
    assert hub["searched"] == []


def test_a_search_with_no_match_says_so_and_offers_to_search_again(hub):
    console = _console()
    # no match, "Search again" (enter), a matching query, q out of the results, q out of the submenu
    _browser((ENTER, ENTER, "q", "q"), answers=("zzzz", "test"), console=console).run()
    assert hub["searched"] == ["zzzz", "test"]


def test_back_row_on_the_results_leaves_the_list(hub):
    # results: three skills, so Back is the fourth row
    _browser((ENTER, DOWN, DOWN, DOWN, ENTER, "q")).run()
    assert hub["opened"] == []


def test_q_quits_the_submenu_straight_away(hub):
    _browser(("q",)).run()
    assert hub["searched"] == [] and hub["listed"] == []


def test_the_back_row_of_the_submenu_leaves_it(hub):
    _browser((DOWN, DOWN, ENTER)).run()


# --- the submenu: browse by shelf -----------------------------------------------------------------------------------


def test_browse_by_shelf_goes_three_levels_deep_and_back_out_one_level_at_a_time(hub):
    # submenu: browse; shelves: the second ("Quality"); categories: the second ("testing"); skills: open the first
    # and go back out of its detail; then q at each level (skills, categories, shelves, submenu)
    keys = (DOWN, ENTER, DOWN, ENTER, DOWN, ENTER, ENTER, DOWN, ENTER, "q", "q", "q", "q")
    _browser(keys).run()
    assert hub["listed"] == ["testing"]
    assert hub["opened"] == ["testing/alpha"]


def test_every_level_has_a_back_row_that_goes_up_one_level(hub):
    # shelves: Back is the row after the two shelves; categories: Back after two; skills: Back after the list
    keys = (
        DOWN, ENTER,  # browse
        ENTER,  # first shelf (Build)
        ENTER,  # first category (backend)
        ENTER,  # backend has no skills in the fake data, so Back is the only row: out of the skills
        DOWN, DOWN, ENTER,  # categories: Back
        DOWN, DOWN, ENTER,  # shelves: Back
        DOWN, ENTER,  # submenu (it remembers "Browse by shelf"): Back
    )
    _browser(keys).run()
    assert hub["listed"] == ["backend"] and hub["opened"] == []


def test_shelf_rows_show_counts():
    console = _console(100)
    rows = [ui.MenuItem(s["shelf"], s["title"], f"{s['skills']} skills") for s in OVERVIEW]
    console.print(ui.render_menu(rows, 0, unicode_ok=True, width=100))
    assert "Build" in console.file.getvalue() and "5 skills" in console.file.getvalue()


# --- the detail view ------------------------------------------------------------------------------------------------


def test_detail_view_has_every_fact_and_wraps_the_description_to_the_width():
    description = " ".join(["words"] * 60)
    skill = _skill(skill_hub.SkillMeta("testing/alpha", "testing", "Alpha Skill", description, shelf="quality"),
                   files=tuple(f"references/file-{i}.md" for i in range(12)), total=15)
    console = _console(60)
    print_detail(console, skill)
    out = console.file.getvalue()
    lines = out.splitlines()
    assert lines[0] == "Alpha Skill" and lines[1] == "testing/alpha"
    assert "Quality" in out and "testing" in out and "MIT" in out and "scripts" in out
    assert SOURCE in out.replace("\n", "")  # the URL is printed whole, never cut
    for line in lines:
        if SOURCE[:20] not in line and not line.startswith("  ") and not line.startswith("Source"):
            assert cell_len(line) <= 60
    assert "Files (15)" in out and "references/file-0.md" in out and "references/file-7.md" in out
    assert "file-8.md" not in out and "and 7 more" in out


def test_detail_view_caps_the_description_column_on_a_very_wide_terminal():
    skill = _skill(skill_hub.SkillMeta("a/b", "a", "T", " ".join(["word"] * 100)))
    console = _console(200)
    print_detail(console, skill)
    assert max(cell_len(line) for line in console.file.getvalue().splitlines() if "word" in line) <= 100


def test_detail_view_for_a_custom_skill_leaves_out_what_it_does_not_have():
    custom = skill_hub.Skill(skill_id="custom/mine", title="mine", description="", content="x", source_url=None,
                             license=None)
    assert detail_lines(custom) == [("Category", "custom")]
    console = _console()
    print_detail(console, custom)
    out = console.file.getvalue()
    assert "mine" in out and "Source" not in out and "License" not in out and "SKILL.md only" in out


def test_titles_are_never_read_as_rich_markup():
    skill = _skill(skill_hub.SkillMeta("a/b", "a", "[bold]Raw[/bold] [x]", "see [link] here"))
    skill.title = "[bold]Raw[/bold] [x]"
    console = _console()
    print_detail(console, skill)
    assert "[bold]Raw[/bold] [x]" in console.file.getvalue() and "see [link] here" in console.file.getvalue()


def test_a_skill_that_vanished_is_reported_instead_of_crashing(hub):
    console = _console()
    _browser((), console=console).show_skill("nope/missing")
    assert "Skill not found" in console.file.getvalue()


def test_the_detail_view_works_against_the_real_bundled_skills():
    first = skill_hub.list_skills()[0]
    skill = skill_hub.get_skill(first.skill_id)
    console = _console(80)
    print_detail(console, skill)
    out = console.file.getvalue()
    assert skill.title in out and skill.skill_id in out


def test_detail_view_shows_needs_and_alternates(monkeypatch):
    skill = _skill(skill_hub.SkillMeta("a/b", "a", "T", "desc"))
    skill.requires = ["node", "git"]
    monkeypatch.setattr(skill_hub, "skill_alternates", lambda skill_id: ["a/c", "a/d"])
    assert ("Needs", "node, git") in detail_lines(skill) and ("Also", "a/c, a/d") in detail_lines(skill)
    console = _console(100)
    print_detail(console, skill)
    assert "a/c, a/d" in console.file.getvalue()


def test_detail_view_of_a_real_skill_with_alternates_lists_them():
    skill = skill_hub.get_skill("ai-engineering/prompt-engineering-patterns")
    assert ("Also", "ai-engineering/prompt-engineer") in detail_lines(skill)
