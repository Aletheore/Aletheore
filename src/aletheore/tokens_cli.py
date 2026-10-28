"""`aletheore tokens ...`: audit the agent's fixed token cost and apply or remove the lean tool profile."""

from __future__ import annotations

import json
import os
from pathlib import Path

import typer

from aletheore import token_profile as tp

tokens_app = typer.Typer(help="measure coding-agent context cost and local token savings")


def _project_dir(path: Path | None, project: Path | None) -> Path:
    """--path is the documented name (every other command uses it); --project is kept as a hidden alias."""
    if path is not None and project is not None and path.resolve() != project.resolve():
        typer.echo("error: --path and --project name different folders; give only one of them", err=True)
        raise typer.Exit(2)
    chosen = path if path is not None else project if project is not None else Path(".")
    # Exit 1 for both, matching the other commands' path checks (_checked_dir); 2 is for conflicting flags.
    if not chosen.exists():
        typer.echo(f"error: project directory does not exist: {chosen}", err=True)
        raise typer.Exit(1)
    if not chosen.is_dir():
        typer.echo(f"error: project path is not a directory: {chosen}", err=True)
        raise typer.Exit(1)
    return chosen


def _confirm(question: str, yes: bool) -> bool:
    if yes:
        return True
    return typer.prompt(f"{question} Type 'yes' to continue", default="no") == "yes"


@tokens_app.command("audit", help="send one tiny request to see the tools your agent loads and its fixed token cost")
def audit(
    path: Path = typer.Option(None, "--path", help="project directory to measure in (default: current directory)"),
    project: Path = typer.Option(None, "--project", hidden=True, help="alias for --path"),
    compare: str = typer.Option("", "--compare", help="also measure with this profile's deny list (core or core+web)"),
    yes: bool = typer.Option(False, "--yes", help="skip the cost confirmation"),
) -> None:
    project = _project_dir(path, project)
    try:
        if compare and compare not in tp.PROFILES:
            raise tp.ProfileError(f"unknown profile {compare!r}; choose one of {', '.join(tp.PROFILES)}")
        n = 2 if compare else 1
        if not _confirm(f"This sends {n} tiny `claude` request(s), about {n} cent(s) in total.", yes):
            raise typer.Exit(1)
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
    path: Path = typer.Option(None, "--path", help="project directory (default: current directory)"),
    project: Path = typer.Option(None, "--project", hidden=True, help="alias for --path"),
    yes: bool = typer.Option(False, "--yes", help="apply without asking"),
) -> None:
    project = _project_dir(path, project)
    try:
        if name == "off":
            removed = tp.remove_deny(project)
            typer.echo(f"removed {len(removed)} deny entries" if removed else "nothing to remove")
            return
        deny = tp.build_deny_list(None, name)
        typer.echo(f"profile {name} will add these to permissions.deny in {project / '.claude' / 'settings.json'}:")
        typer.echo(json.dumps(deny, indent=2))
        typer.echo("Undo any time with: aletheore tokens profile off")
        if not _confirm(f"Apply profile {name}?", yes):
            typer.echo("cancelled; nothing was changed")
            raise typer.Exit(1)
        added = tp.apply_deny(project, deny)
        typer.echo(f"added {len(added)} deny entries")
    except tp.ProfileError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1)


def _meter_profile_status(project: Path) -> str:
    settings_path, sidecar_path = tp._paths(project)
    try:
        sidecar = tp._load(sidecar_path)
        added = tp._validated_added(sidecar, sidecar_path)
    except Exception:
        return "unknown (the Aletheore profile record is unreadable)"
    return "active" if added else "not active"


def _period_source_text(totals, *, source: str, guard_configured: bool) -> str:
    if source == "read_guard":
        if totals.read_denials:
            return (f"{totals.read_denials} unchanged full reread(s) denied; "
                    f"~{totals.read_tokens_estimated:,} tokens estimated "
                    f"({totals.read_bytes:,} file bytes ÷ 4)")
        if not guard_configured:
            return "unmeasured (no events recorded; opt-in hook not detected here)"
        return "no denial events recorded"
    if totals.mcp_trims:
        return (f"{totals.mcp_omitted_chars_measured:,} serialized characters omitted (measured); "
                f"~{totals.mcp_tokens_estimated:,} tokens estimated "
                f"({totals.mcp_omitted_chars_measured:,} characters ÷ 4)")
    return "no trims recorded"


def _render_meter_plain(summary, *, guard_configured: bool, profile_status: str) -> str:
    periods = (("Today", summary.today), ("This week", summary.this_week), ("All time", summary.all_time))
    chunks = []
    for label, totals in periods:
        chunks.append(
            f"{label}: Read guard { _period_source_text(totals, source='read_guard', guard_configured=guard_configured) }; "
            f"MCP budget { _period_source_text(totals, source='mcp_budget', guard_configured=guard_configured) }"
        )
    chunks.append(f"Read guard setup: {'detected' if guard_configured else 'not detected here'}")
    if not guard_configured:
        chunks.append("enable with --enable-read-guard (shows changes and asks first)")
    chunks.append(f"Lean profile: {profile_status}; token savings unmeasured")
    if summary.corrupt_events:
        chunks.append(f"warning: {summary.corrupt_events} corrupt meter event file(s) skipped")
    return "Aletheore meter | " + " | ".join(chunks)


@tokens_app.command(
    "meter",
    help="show local context savings by source and period; --enable-read-guard is opt-in and consent-gated",
)
def meter(
    path: Path = typer.Option(None, "--path", help="project directory (default: current directory)"),
    project: Path = typer.Option(None, "--project", hidden=True, help="alias for --path"),
    plain: bool = typer.Option(False, "--plain", help="emit a single line for scripts and status displays"),
    enable_read_guard: bool = typer.Option(False, "--enable-read-guard", help="preview and consent to add local Read hooks"),
    disable_read_guard: bool = typer.Option(False, "--disable-read-guard", help="remove only the local Read hooks Aletheore added"),
    yes: bool = typer.Option(False, "--yes", help="confirm the displayed read-guard setup changes"),
) -> None:
    project = _project_dir(path, project)
    if enable_read_guard and disable_read_guard:
        typer.echo("error: choose only one of --enable-read-guard or --disable-read-guard", err=True)
        raise typer.Exit(2)

    from aletheore import read_guard_setup

    try:
        if enable_read_guard:
            plan = read_guard_setup.plan_install(project)
            if plan.already_installed:
                typer.echo("Aletheore read-guard hooks are already registered in this project.")
                return
            typer.echo(f"This will update {plan.settings_path} with these two project-local hooks:")
            typer.echo(json.dumps(plan.entries, indent=2))
            typer.echo(f"It will also write this ownership record to {plan.sidecar_path}:")
            typer.echo(json.dumps(plan.sidecar_after, indent=2))
            if not (project / ".aletheore").is_dir():
                typer.echo("Note: the guard only records reads after this repo has `.aletheore/` state (for example, after a scan).")
            typer.echo("Other settings and hooks will be preserved. Remove these exact entries with:")
            typer.echo("  aletheore tokens meter --disable-read-guard")
            if not _confirm("Apply the displayed read-guard setup?", yes):
                typer.echo("cancelled; nothing was changed")
                raise typer.Exit(1)
            read_guard_setup.install(project, plan)
            typer.echo("Read guard enabled for this project. Restart Claude Code to load the hooks.")
            return

        if disable_read_guard:
            removed = read_guard_setup.remove(project)
            typer.echo(
                f"removed Aletheore read-guard hooks for {', '.join(removed)}" if removed
                else "no Aletheore read-guard hooks were registered here"
            )
            return

        from aletheore.token_meter import summarize

        summary = summarize(project)
        guard_configured = read_guard_setup.is_installed(project)
        profile_status = _meter_profile_status(project)
        if plain:
            typer.echo(_render_meter_plain(summary, guard_configured=guard_configured,
                                           profile_status=profile_status))
        else:
            from aletheore.meter_ui import print_meter

            print_meter(summary, project, guard_configured=guard_configured, profile_status=profile_status)
    except read_guard_setup.ReadGuardSetupError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1)
