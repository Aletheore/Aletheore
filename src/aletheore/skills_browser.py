"""The interactive Hallbook skills browser behind the home menu's "Find a Hallbook skill".

A small submenu (search by task, or browse by shelf), a picker of skills at every level, and a detail view with the
licence, the upstream source and the files a skill ships. Black and white only, like the rest of the menu; every list
is an arrow-key picker whose "Back" returns to the same row.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import click
from rich.console import Console
from rich.text import Text

from aletheore import skill_hub, ui

SEARCH_LIMIT = 30  # the picker scrolls, so a longer list than the plain command's 10 is fine
_BACK = ""  # a menu key that no skill id or category can have
_DETAIL_WIDTH = 100  # a description is easier to read in a column than across a 200-column window
_FILES_SHOWN = 8
_LIST_FOOTER = "up/down to move, enter to open, q to go back"
_SUBMENU_FOOTER = "up/down to move, enter to choose, q to go back"


def _count(number: int, noun: str) -> str:
    plural = {"category": "categories", "match": "matches"}.get(noun, noun + "s")
    return f"{number} {noun if number == 1 else plural}"


def shelf_title(shelf: str) -> str:
    if shelf == skill_hub._CUSTOM_CATEGORY:
        return "Your skills"
    return skill_hub.SHELF_TITLES.get(shelf, shelf.replace("-", " ").title()) if shelf else ""


def skill_items(metas: Sequence[skill_hub.SkillMeta]) -> list[ui.MenuItem]:
    """One picker row per skill: the id and title as the label, the description as a one-line hint."""
    return [ui.MenuItem(meta.skill_id, f"{meta.skill_id}  {meta.title}", " ".join(meta.description.split())) for meta in metas]


def detail_lines(skill: skill_hub.Skill) -> list[tuple[str, str]]:
    """The labelled facts of a skill, in display order; a fact the skill does not have is left out."""
    category = skill.skill_id.split("/", 1)[0]
    facts = [("Shelf", shelf_title(skill.shelf)), ("Category", category), ("License", skill.license or ""),
             ("Copyright", skill.copyright or ""), ("Source", skill.source_url or ""), ("Needs", ", ".join(skill.requires)),
             ("Also", ", ".join(skill_hub.skill_alternates(skill.skill_id)))]
    return [(label, value) for label, value in facts if value]


def print_detail(console: Console, skill: skill_hub.Skill) -> None:
    """Print the detail view of a skill. Text objects throughout, so a title or description is never read as markup."""
    width = min(console.width, _DETAIL_WIDTH)
    console.print(Text(skill.title, style="bold"), width=width)
    console.print(Text(skill.skill_id, style="dim"), width=width)
    console.print()
    for label, value in detail_lines(skill):
        line = Text()
        line.append(f"{label:<10}", style="dim")
        line.append(value)
        # The source URL is printed unwrapped, so the terminal soft-wraps it and it can still be copied whole.
        console.print(line, soft_wrap=label == "Source")
    description = " ".join(skill.description.split())
    if description:
        console.print()
        console.print(Text(description), width=width)
    console.print()
    if skill.files:
        console.print(Text(f"Files ({skill.files_total})", style="bold"))
        for rel in skill.files[:_FILES_SHOWN]:
            console.print(Text("  " + ui.truncate(rel, console.width - 2, unicode_ok=ui.supports_unicode(console.file))))
        hidden = skill.files_total - min(len(skill.files), _FILES_SHOWN)
        if hidden > 0:
            console.print(Text(f"  and {hidden} more", style="dim"))
    else:
        console.print(Text("Files: SKILL.md only", style="dim"))
    console.print()


class SkillsBrowser:
    """The submenu and its pickers. Everything that touches the terminal is injectable, so tests drive it with keys."""

    def __init__(
        self,
        console: Console,
        *,
        read_key: Callable[[], str] | None = None,
        ask: Callable[[str], str] | None = None,
        pager: Callable[[str], None] | None = None,
        clear: Callable[[], None] | None = None,
    ) -> None:
        self.console = console
        self._read_key = read_key
        self._ask = ask or (lambda question: click.prompt(question, default="", show_default=False))
        self._pager = pager or click.echo_via_pager
        self._clear = clear or (lambda: None)

    def choose(self, items: Sequence[ui.MenuItem], **kwargs) -> ui.MenuItem | None:
        if self._read_key is not None:
            kwargs["read_key"] = self._read_key
        header = kwargs.get("header")
        if isinstance(header, Text):
            title, _, subtitle = header.plain.partition("\n")
            kwargs["header"] = ui.command_header(title, width=self.console.width,
                                                  height=self.console.height, subtitle=subtitle)
        return ui.choose(items, self.console, **kwargs)

    # --- the submenu --------------------------------------------------------------------------------------------

    def run(self) -> None:
        total = sum(shelf["skills"] for shelf in skill_hub.list_overview())
        header = Text()
        header.append("Find a Hallbook skill\n", style="bold")
        header.append(f"{total} vetted skills, bundled with Aletheore and usable offline", style="dim")
        items = [
            ui.MenuItem("search", "Search by what you are doing", "describe the task, get the best matches"),
            ui.MenuItem("browse", "Browse by shelf", "shelves, then categories, then skills"),
            ui.MenuItem(_BACK, "Back"),
        ]
        start = 0
        while True:
            self._clear()
            picked = self.choose(items, header=header, footer=_SUBMENU_FOOTER, start=start)
            if picked is None or picked.key == _BACK:
                return
            start = [item.key for item in items].index(picked.key)
            if picked.key == "search":
                self.search()
            else:
                self.browse()

    # --- search -------------------------------------------------------------------------------------------------

    def search(self) -> None:
        while True:
            self._clear()
            try:
                wanted = self._ask("What are you about to do? (for example: flaky test)").strip()
            except (click.Abort, EOFError):
                return
            if not wanted:
                return
            results = skill_hub.search_skills(wanted, limit=SEARCH_LIMIT)
            if results:
                title = f'{_count(len(results), "match")} for "{ui.truncate(wanted, 50)}"'
                self.pick_skill(results, title, "best match first")
                return
            header = Text()
            header.append(f'No skills matched "{ui.truncate(wanted, 50)}".\n', style="bold")
            header.append("Try fewer or different words, or browse by shelf.", style="dim")
            items = [ui.MenuItem("again", "Search again"), ui.MenuItem(_BACK, "Back")]
            picked = self.choose(items, header=header, footer=_SUBMENU_FOOTER)
            if picked is None or picked.key == _BACK:
                return

    # --- browse -------------------------------------------------------------------------------------------------

    def browse(self) -> None:
        overview = skill_hub.list_overview()
        items = [
            ui.MenuItem(shelf["shelf"], shelf["title"],
                        f"{_count(shelf['skills'], 'skill')} in {_count(len(shelf['categories']), 'category')}")
            for shelf in overview
        ]
        items.append(ui.MenuItem(_BACK, "Back"))
        start = 0
        while True:
            self._clear()
            header = Text()
            header.append("Browse by shelf\n", style="bold")
            header.append("pick a shelf", style="dim")
            picked = self.choose(items, header=header, footer=_LIST_FOOTER, start=start, numbers=False)
            if picked is None or picked.key == _BACK:
                return
            start = [item.key for item in items].index(picked.key)
            self._browse_shelf(next(shelf for shelf in overview if shelf["shelf"] == picked.key))

    def _browse_shelf(self, shelf: dict) -> None:
        items = [ui.MenuItem(entry["category"], entry["category"], _count(entry["skills"], "skill"))
                 for entry in shelf["categories"]]
        items.append(ui.MenuItem(_BACK, "Back"))
        start = 0
        while True:
            self._clear()
            header = Text()
            header.append(f"Browse by shelf / {shelf['title']}\n", style="bold")
            header.append("pick a category", style="dim")
            picked = self.choose(items, header=header, footer=_LIST_FOOTER, start=start, numbers=False)
            if picked is None or picked.key == _BACK:
                return
            start = [item.key for item in items].index(picked.key)
            metas = skill_hub.list_skills(picked.key)
            self.pick_skill(metas, f"{shelf['title']} / {picked.key}", _count(len(metas), "skill"))

    # --- skills -------------------------------------------------------------------------------------------------

    def pick_skill(self, metas: Sequence[skill_hub.SkillMeta], title: str, subtitle: str) -> None:
        """A picker over skills. Enter opens the detail view; coming back lands on the same row."""
        items = [*skill_items(metas), ui.MenuItem(_BACK, "Back")]
        start = 0
        while True:
            self._clear()
            header = Text()
            header.append(f"{title}\n", style="bold")
            header.append(subtitle, style="dim")
            picked = self.choose(items, header=header, footer=_LIST_FOOTER, start=start, numbers=False)
            if picked is None or picked.key == _BACK:
                return
            start = [item.key for item in items].index(picked.key)
            self.show_skill(picked.key)

    def show_skill(self, skill_id: str) -> None:
        try:
            skill = skill_hub.get_skill(skill_id)
        except skill_hub.SkillNotFoundError:
            self.console.print(Text(f"Skill not found: {skill_id}", style="bold"))
            return
        self._clear()
        self.console.print(ui.command_header("Hallbook / Skill details", width=self.console.width,
                                             height=self.console.height))
        self.console.print()
        print_detail(self.console, skill)
        items = [ui.MenuItem("read", "Read SKILL.md", "opens in your pager, q to close it"), ui.MenuItem(_BACK, "Back")]
        while True:
            picked = self.choose(items, footer=_SUBMENU_FOOTER)
            if picked is None or picked.key == _BACK:
                return
            self._pager(skill.content)
