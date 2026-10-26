"""A paced terminal tour, using the same wordmark and picker as the home screen."""

from __future__ import annotations

import asyncio
import json
import subprocess  # nosec B404 - exception types only
import textwrap
from pathlib import Path

import click
import toon
from mcp.server.mcpserver.exceptions import ToolError
from rich.console import Console
from rich.text import Text

from aletheore import ui
from aletheore.demo import create_demo
from aletheore.git_intel.analyzer import GitAnalysisError
from aletheore.query import (
    ModuleNotFoundInEvidenceError,
    SymbolNotFoundInEvidenceError,
    find_blast_radius,
    find_symbol_source,
)

STEPS = ("Scan", "Query", "Map", "Agent", "Explore")


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
        self.agent_result = {}

    def _line(self, text: Text, value: str = "", style: str = "") -> None:
        if not value and self.console.height < 24:
            return
        width = max(20, min(self.console.width - 2, 88))
        for line in value.split("\n"):
            text.append(textwrap.fill(line, width=width, replace_whitespace=False) + "\n", style=style)

    def screen(self, step: int) -> Text:
        """One bounded page, with real scan results rather than a transcript of commands."""
        page = Text()
        if step == 0:
            art = (ui.wordmark_lines(self.console.width)
                   if self.console.height >= 28 and ui.supports_unicode(self.console.file)
                   else ["ALETHEORE"])
            for line in art:
                self._line(page, line, "bold")
            self._line(page)
            self._line(page, "Scan once. Explore it. Give your agent evidence.", "bold")
            self._line(page)
            self._line(page, "See how Aletheore maps a repository, answers a query, and exposes that evidence through MCP.")
            self._line(page)
            self._line(page, "A five-step tour of a real scan on a small synthetic repository.", "dim")
            self._line(page, "No account, model, clone or external checks. Git required.", "dim")
            return page

        titles = ("BUILD REUSABLE EVIDENCE", "QUERY THE SOURCE", "FOLLOW THE IMPORT MAP", "GIVE AN AGENT EVIDENCE", "ACCESS THE SAME EVIDENCE")
        self._line(page, "ALETHEORE / GUIDED DEMO", "dim")
        self._line(page, f"STEP {step} OF 5  |  {titles[step - 1]}", "bold")
        self._line(page, "=" * min(self.console.width - 2, 88), "dim")
        if step == 1:
            self._line(page, "Aletheore parses source and reads local Git history.")
            modules = len(self.evidence["repository"]["modules"])
            edges = self.evidence["repository"]["dependency_graph"]["edges"]
            commits = self.evidence["git"]["total_commits"]
            self._box(page, "SCAN OUTPUT", [
                f"{modules} files / {len(edges)} import links / {commits} sample commits",
                ".aletheore/air.json   structured evidence",
                ".aletheore/air.toon   compact evidence",
            ])
            meaning = "Scan once. CLI, dashboard and MCP reuse this evidence. No LLM is needed to build it."
        elif step == 2:
            self._line(page, "CLI > aletheore query symbol-source pricing.py total")
            location = f"{self.source['module']}:{self.source['start_line']}-{self.source['end_line']}"
            code = [f"{number:>2}  {line}" for number, line in
                    enumerate(self.source["source"].splitlines(), self.source["start_line"])]
            self._box(page, "SOURCE  " + location, code)
            meaning = "Aletheore resolves the symbol to its file, lines and source. You retrieve a precise result, not the whole repository."
        elif step == 3:
            self._line(page, "CLI > aletheore dependents pricing.py")
            rows = ["pricing.py  <-- the file you are changing"]
            rows += [f"  {name}  (direct importer)" for name in self.impact["direct_dependents"]]
            rows += [f"    {name}  (transitive importer)" for name in self.impact["transitive_dependents"]]
            self._box(page, f"{self.impact['affected_total']} DEPENDENT FILES", rows)
            meaning = "Aletheore resolves imports to files, then walks them backwards. This shows static dependents."
        elif step == 4:
            self._line(page, "MCP > aletheore mcp <repo> --profile lean")
            self._box(page, "ACTUAL LOCAL TOOL CALL / RESPONSE EXCERPT", [
                "aletheore_get_blast_radius",
                '  target="pricing.py", symbol="total"',
                f"affected_total: {self.agent_result['affected_total']}",
                f"affected_test_files: {self.agent_result['affected_test_files']} (candidates)",
            ])
            meaning = "The agent gets scoped impact evidence to plan its next reads. This demo calls the tool without a model."
        else:
            self._line(page, "Choose how you want to use the evidence next.")
            self._box(page, "ONE SCAN / THREE WAYS TO ACCESS IT", [
                "CLI        targeted source and dependency queries",
                "Dashboard  browse the repository graph",
                "MCP        agent connects to aletheore mcp",
            ])
            meaning = "Open the dashboard now, or scan your own repository. MCP access needs no model inside Aletheore."
        if self.console.height >= 28:
            self._line(page)
        self._line(page, "WHAT THIS MEANS", "bold")
        self._line(page, meaning)
        if self.console.height >= 28:
            self._line(page)
        self._line(page, "ACTIONS", "bold")
        self._line(page, "-" * min(self.console.width - 2, 88), "dim")
        return page

    def _box(self, page: Text, title: str, rows: list[str]) -> None:
        width = max(24, min(self.console.width - 2, 88))
        inner = width - 4
        self._line(page)
        label = ui.truncate(title, width - 6, unicode_ok=False)
        page.append("+- " + label + " " + "-" * (width - len(label) - 5) + "+\n", style="bold")
        for row in rows:
            for line in textwrap.wrap(row, width=inner, replace_whitespace=False) or [""]:
                page.append("| " + line.ljust(inner) + " |\n")
        page.append("+" + "-" * (width - 2) + "+\n", style="dim")

    def _choose(self, step: int, items):
        self.console.clear()
        return ui.choose(items, self.console, header=self.screen(step), read_key=self.read_key,
                         footer="up/down to move, enter to choose, q to exit", numbers=False)

    def _prepare(self) -> None:
        with self.console.status("Building the sample and scanning locally..."):
            self.repo, self.evidence = create_demo()
            self.source = find_symbol_source(self.evidence, self.repo, "pricing.py", "total")
            self.impact = find_blast_radius(self.evidence, self.repo, "pricing.py", "total")
            self.agent_result = asyncio.run(self._agent_call())

    async def _agent_call(self) -> dict:
        from aletheore.mcp_server import build_server

        server = build_server(self.repo, allow=frozenset(), watch=False, profile="lean")
        result = await server.call_tool("aletheore_get_blast_radius", {"target": "pricing.py", "symbol": "total"})
        if result.is_error:
            raise OSError("The demo MCP tool could not retrieve impact evidence.")
        try:
            text = result.content[0].text
            # MCP's serializer can fall back to JSON if TOON encoding fails.
            payload = json.loads(text) if text.lstrip().startswith("{") else toon.decode(text)
            data = payload["result"]
            for key in ("affected_total", "affected_test_files"):
                if type(data[key]) is not int or data[key] < 0:
                    raise ValueError(f"Invalid {key} count")
            return data
        except (IndexError, AttributeError, KeyError, TypeError, ValueError, toon.ToonDecodeError) as exc:
            raise OSError("The demo MCP tool returned an unreadable impact response.") from exc

    def _command(self, args: list[str]) -> int:
        try:
            return self.run_command(args)
        except (OSError, GitAnalysisError, subprocess.SubprocessError) as exc:
            self.console.print(Text(f"Could not complete this action: {exc}"))
            return 1

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
        if self._command(["scan", str(repo), "--explain-network"]) != 0:
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
            return self._command(["scan", str(repo)])
        return None

    def run(self) -> int:
        choice = self._choose(0, [ui.MenuItem("start", "Start the walkthrough", "about two minutes"),
                                  ui.MenuItem("quit", "Exit")])
        if choice is None or choice.key == "quit":
            return 0
        try:
            self._prepare()
        except (OSError, subprocess.SubprocessError, GitAnalysisError, ModuleNotFoundInEvidenceError,
                SymbolNotFoundInEvidenceError, ToolError) as exc:
            self.console.print(Text(f"Demo could not prepare its sample: {exc}"))
            if self.repo:
                self.console.print(Text(f"Sample kept at: {self.repo}"), soft_wrap=True)
            return 1
        step = 1
        while True:
            if step < 5:
                items = [ui.MenuItem("next", "Continue", f"Next: {STEPS[step]}"),
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
                if self._command(["dashboard", "--path", str(self.repo)]) != 0:
                    self.console.print(Text("Press any key to return to the demo."))
                    self.read_key()
            elif choice.key == "own":
                result = self._own_repository()
                if result is not None:
                    return result
