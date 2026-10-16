"""`aletheore tokens ...`: audit the agent's fixed token cost and apply or remove the lean tool profile."""

from __future__ import annotations

import json
import os
from pathlib import Path

import typer

from aletheore import token_profile as tp

tokens_app = typer.Typer(help="measure and reduce the tokens a coding agent re-reads every turn")


def _confirm(question: str, yes: bool) -> bool:
    if yes:
        return True
    return typer.prompt(f"{question} Type 'yes' to continue", default="no") == "yes"


@tokens_app.command("audit", help="send one tiny request to see the tools your agent loads and its fixed token cost")
def audit(
    project: Path = typer.Option(Path("."), "--project", help="project directory to measure in"),
    compare: str = typer.Option("", "--compare", help="also measure with this profile's deny list (core or core+web)"),
    yes: bool = typer.Option(False, "--yes", help="skip the cost confirmation"),
) -> None:
    n = 2 if compare else 1
    if not _confirm(f"This sends {n} tiny `claude` request(s), about {n} cent(s) in total.", yes):
        raise typer.Exit(1)
    try:
        before = tp.run_audit(project)
        typer.echo(f"tools loaded: {len(before.tools)}")
        typer.echo(f"ENABLE_TOOL_SEARCH: {os.environ.get('ENABLE_TOOL_SEARCH', 'unset')}")
        typer.echo(f"fixed floor: {before.input_side_tokens:,} input-side tokens per turn")
        if compare:
            deny = tp.build_deny_list(before.tools, compare)
            after = tp.run_audit(project, deny=deny)
            saved = 1 - after.input_side_tokens / before.input_side_tokens
            typer.echo(f"with profile {compare} (denying {len(deny)} tools): {after.input_side_tokens:,} "
                       f"({saved:.0%} lower)")
    except tp.ProfileError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1)


@tokens_app.command("profile", help="apply or remove a lean tool profile in the project's .claude/settings.json")
def profile(
    name: str = typer.Argument(..., help="core, core+web, or off"),
    project: Path = typer.Option(Path("."), "--project", help="project directory"),
    yes: bool = typer.Option(False, "--yes", help="apply without asking"),
) -> None:
    try:
        if name == "off":
            removed = tp.remove_deny(project)
            typer.echo(f"removed {len(removed)} deny entries" if removed else "nothing to remove")
            return
        deny = tp.build_deny_list(None, name)
        typer.echo(f"profile {name} will add these to permissions.deny in {project / '.claude' / 'settings.json'}:")
        typer.echo(json.dumps(deny, indent=2))
        typer.echo("Undo any time with: aletheore tokens profile off")
        if not _confirm("", yes):
            typer.echo("cancelled; nothing was changed")
            raise typer.Exit(1)
        added = tp.apply_deny(project, deny)
        typer.echo(f"added {len(added)} deny entries")
    except tp.ProfileError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1)
