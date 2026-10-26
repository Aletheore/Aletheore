"""A paced terminal tour, using the same wordmark and picker as the home screen."""

from __future__ import annotations

import subprocess  # nosec B404 - exception types only
import textwrap
from pathlib import Path

import click
from rich.console import Console
from rich.text import Text

from aletheore import ui
from aletheore.demo import create_demo
from aletheore.git_intel.analyzer import GitAnalysisError
from aletheore.query import (
    ModuleNotFoundInEvidenceError,
    SymbolNotFoundInEvidenceError,
    find_blast_radius,
    find_hotspots,
    find_symbol_source,
)
from aletheore.tests_for import TestsForError, tests_for_report

STEPS = ("Scan", "Source", "Impact", "Tests", "Next")


class DemoWalkthrough:
    def __init__(self, console: Console, *, run_command, ask, read_key=None):
        self.console = console
        self.run_command = run_command
        self.ask = ask
        self.read_key = read_key or click.getchar
        self.repo: Path | None = None
        self.evidence = {}
        self.source = {}
        self.impact = {}
        self.candidates = ""
        self.hotspots = []

    def _line(self, text: Text, value: str = "", style: str = "") -> None:
        if not value and self.console.height < 24:
            return
        width = max(20, min(self.console.width - 2, 94))
        for line in value.split("\n"):
            text.append(textwrap.fill(line, width=width, replace_whitespace=False) + "\n", style=style)

    def screen(self, step: int) -> Text:
        """One bounded page, with real scan results rather than a transcript of commands."""
        page = Text()
        if step == 0:
            art = ui.wordmark_lines(self.console.width) if self.console.height >= 28 else ["ALETHEORE"]
            for line in art:
                self._line(page, line, "bold")
            self._line(page)
            self._line(page, "A change. Its impact. The evidence.", "bold")
            self._line(page)
            self._line(page, "You need to change a pricing function. Which files depend on it? Which test should you inspect?")
            self._line(page)
            self._line(page, "A five-step tour of a real scan on a small synthetic repository.", "dim")
            self._line(page, "No account, model, clone or external checks. Git required.", "dim")
            return page

        self._line(page, "ALETHEORE  /  GUIDED DEMO", "bold")
        if self.console.width < 60 or self.console.height < 24:
            self._line(page, f"{step} / 5   {STEPS[step - 1]}", "bold reverse")
        else:
            for index, label in enumerate(STEPS, 1):
                page.append(f" {index} {label} ", style="bold reverse" if index == step else "dim")
            page.append("\n\n")
        if step == 1:
            self._line(page, "A small repository. A real scan.", "bold")
            self._line(page)
            modules = len(self.evidence["repository"]["modules"])
            edges = self.evidence["repository"]["dependency_graph"]["edges"]
            commits = self.evidence["git"]["total_commits"]
            self._line(page, f"{modules} files   /   {len(edges)} import links   /   {commits} synthetic commits", "bold")
            self._line(page)
            for name, purpose in (("app.py", "entry point"), ("service.py", "checkout flow"),
                                  ("pricing.py", "the function you will change"), ("test_service.py", "a test to investigate")):
                self._line(page, f"  {name:<19} {purpose}", "bold" if name == "pricing.py" else "")
            self._line(page)
            self._line(page, "The scanner built reusable evidence locally. No LLM was called.", "dim")
        elif step == 2:
            self._line(page, "Start at the exact source.", "bold")
            self._line(page)
            self._line(page, f"{self.source['module']}:{self.source['start_line']}-{self.source['end_line']}", "dim")
            for number, line in enumerate(self.source["source"].splitlines(), self.source["start_line"]):
                self._line(page, f"  {number:>2}  {line}", "bold")
            self._line(page)
            hotspot = next(h for h in self.hotspots if h["path"] == "pricing.py")
            self._line(page, f"pricing.py changed in {hotspot['churn_count']} sample commits.")
            self._line(page, "Before editing total(), trace who depends on this file.", "dim")
        elif step == 3:
            self._line(page, "One edit reaches beyond one file.", "bold")
            self._line(page)
            self._line(page, f"{self.impact['affected_total']} dependent files  /  {self.impact['direct_total']} direct  /  {self.impact['transitive_total']} transitive", "bold")
            self._line(page)
            self._line(page, "  pricing.py  <--- change here", "bold reverse")
            for name in self.impact["direct_dependents"]:
                self._line(page, f"    {name:<21} direct importer", "bold")
            for name in self.impact["transitive_dependents"]:
                self._line(page, f"      {name:<19} through another file")
            self._line(page)
            self._line(page, "Caller of total(): " + ", ".join(self.impact["confirmed_callers"]), "bold")
            self._line(page, "Resolved static imports; runtime and dynamic paths may differ.", "dim")
        elif step == 4:
            self._line(page, "A test to investigate before you edit.", "bold")
            self._line(page)
            self._line(page, self.candidates)
            self._line(page)
            self._line(page, "Candidate, not proven coverage. The demo has not run this test.", "bold")
        else:
            self._line(page, "You have a reasoned starting point.", "bold")
            self._line(page)
            self._line(page, "Source located. Callers traced. Candidate test identified.")
            self._line(page, "Open the same evidence as a graph, revisit a step, or try your repository.", "dim")
        return page

    def _choose(self, step: int, items):
        self.console.clear()
        return ui.choose(items, self.console, header=self.screen(step), read_key=self.read_key,
                         footer="up/down to move, enter to choose, q to exit", numbers=False)

    def _prepare(self) -> None:
        with self.console.status("Building the sample and scanning locally..."):
            self.repo, self.evidence = create_demo()
            self.source = find_symbol_source(self.evidence, self.repo, "pricing.py", "total")
            self.impact = find_blast_radius(self.evidence, self.repo, "pricing.py", "total")
            self.candidates = tests_for_report(self.evidence, self.repo, "pricing.py", summary_only=True)
            self.hotspots = find_hotspots(self.evidence, None)

    def _own_repository(self) -> int | None:
        self.console.clear()
        self.console.print(Text("Try your repository", style="bold"))
        value = self.ask("Repository path (empty to go back)")
        if not value:
            return None
        repo = Path(value).expanduser().resolve()
        if not repo.is_dir():
            self.console.print(Text("That folder does not exist. Press any key to go back."))
            self.read_key()
            return None
        # Explain first. Scanning a real repo may enable checks the sample skipped.
        if self.run_command(["scan", str(repo), "--explain-network"]) != 0:
            self.console.print(Text("Could not read the network plan. Press any key to go back."))
            self.read_key()
            return None
        self.console.print(Text("Press any key after reading the network plan.", style="dim"))
        self.read_key()
        header = Text("Scan this repository?\n", style="bold")
        header.append(str(repo) + "\n", style="dim")
        header.append("Uses your normal scan settings, including the network checks just shown.")
        self.console.clear()
        choice = ui.choose([ui.MenuItem("scan", "Scan this repository"), ui.MenuItem("back", "Back to the demo")],
                           self.console, header=header, read_key=self.read_key, numbers=False)
        if choice and choice.key == "scan":
            self.console.clear()
            return self.run_command(["scan", str(repo)])
        return None

    def run(self) -> int:
        choice = self._choose(0, [ui.MenuItem("start", "Start the walkthrough", "about two minutes"),
                                  ui.MenuItem("quit", "Exit")])
        if choice is None or choice.key == "quit":
            return 0
        try:
            self._prepare()
        except (OSError, subprocess.SubprocessError, GitAnalysisError, ModuleNotFoundInEvidenceError,
                SymbolNotFoundInEvidenceError, TestsForError) as exc:
            self.console.print(Text(f"Demo could not prepare its sample: {exc}"))
            if self.repo:
                self.console.print(Text(f"Sample kept at: {self.repo}"), soft_wrap=True)
            return 1
        step = 1
        while True:
            if step < 5:
                items = [ui.MenuItem("next", "Continue", f"{step + 1} / 5  {STEPS[step]}"),
                         ui.MenuItem("back", "Previous step" if step > 1 else "Back to welcome"),
                         ui.MenuItem("quit", "Exit")]
            else:
                items = [ui.MenuItem("dashboard", "Open the sample dashboard", "the same evidence, as a graph"),
                         ui.MenuItem("own", "Try your repository", "inspect the network plan before scanning"),
                         ui.MenuItem("back", "Previous step"), ui.MenuItem("quit", "Finish")]
            choice = self._choose(step, items)
            if choice is None or choice.key == "quit":
                self.console.clear()
                self.console.print(Text("Demo finished. Your sample is kept at:", style="bold"))
                self.console.print(Text(str(self.repo)), soft_wrap=True)
                return 0
            if choice.key == "next":
                step += 1
            elif choice.key == "back":
                if step == 1:
                    choice = self._choose(0, [ui.MenuItem("start", "Resume the walkthrough"), ui.MenuItem("quit", "Exit")])
                    if choice is None or choice.key == "quit":
                        self.console.print(Text(f"Sample kept at: {self.repo}"), soft_wrap=True)
                        return 0
                else:
                    step -= 1
            elif choice.key == "dashboard":
                self.console.clear()
                self.run_command(["dashboard", "--path", str(self.repo)])
            elif choice.key == "own":
                result = self._own_repository()
                if result is not None:
                    return result
