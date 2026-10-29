"""`aletheore shield ...`: set up, inspect, undo, check and remove the local safety net for AI-written changes.

This is only the user-facing layer over aletheore.shield.*; every decision about files lives there. All text that
came from a repository (paths, descriptions, refs, notes) is cleaned before it is printed, and nothing is printed
through rich markup, so a hostile file name cannot inject escape sequences or styling.
"""

from __future__ import annotations

import sys
from datetime import timezone
from pathlib import Path

import typer

from aletheore.shield import state
from aletheore.shield.checkpoint import Checkpoint, CheckpointError, list_checkpoints, plan_restore, restore_checkpoint
from aletheore.shield.gitutil import run_git
from aletheore.shield.guard import _UNSAFE, clean_text, render_report, run_guard
from aletheore.shield.receipt import latest_receipt
from aletheore.shield.setup import (
    SetupBlocked,
    SetupChange,
    apply_setup,
    plan_setup,
    remove_setup,
    shared_blocks_kept,
    shield_status,
)

shield_app = typer.Typer(
    help="a local safety net for AI-written changes: guard, undo, receipts",
    invoke_without_command=True,
    no_args_is_help=False,
)

_CHECKPOINT_PREFIX = "refs/aletheore/checkpoints/"
_UNDO_LIST = 10


def _out(text: str = "") -> None:
    typer.echo(text)


def _safe(text: object) -> str:
    return clean_text(str(text))


def _safe_block(text: str, indent: str = "    ") -> None:
    """Print a multi-line block with indentation kept but control and bidi characters neutralised."""
    for line in str(text).splitlines():
        _out(indent + _UNSAFE.sub("?", line.expandtabs(4)).rstrip())


def _interactive() -> bool:
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def _confirm(question: str, yes: bool) -> bool:
    if yes:
        return True
    return typer.prompt(f"{question} Type 'yes' to continue", default="no").strip().lower() == "yes"


def _fail(message: str, code: int = 1) -> "typer.Exit":
    typer.echo(message)
    return typer.Exit(code)


def _resolve_repo(path: Path | None) -> Path:
    start = Path(path) if path is not None else Path(".")
    if not start.is_dir():
        raise _fail(f"Shield needs a git repository, and {_safe(start)} is not a folder.")
    probe = run_git(start, "rev-parse", "--show-toplevel")
    top = probe.stdout.decode("utf-8", "replace").strip()
    if probe.returncode != 0 or not top:
        raise _fail("Shield needs a git repository. Run `git init` here first, or pass --path to a repository.")
    return Path(top)


def _short_ref(ref: str) -> str:
    return ref[len(_CHECKPOINT_PREFIX):] if ref.startswith(_CHECKPOINT_PREFIX) else ref


def _when(checkpoint: Checkpoint) -> str:
    moment = checkpoint.created_at
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------- setup / status


def _show_change(change: SetupChange) -> None:
    _out(f"  {_safe(change.kind)}: {_safe(change.path)}")
    _out(f"      {_safe(change.description)}")
    if change.kind == "snippet":
        if change.detail:
            _safe_block(change.detail, "        ")
        _out("      Shield will not write this file; add it yourself.")
    elif change.diff:
        _safe_block(change.diff, "        ")


def _show_network(repo: Path) -> None:
    from aletheore.network_plan import shield_network_plan

    lookups = state.registry_lookups_enabled(repo)
    _out("")
    _out("Network:")
    if lookups:
        _out(
            "  When the guard checks a new dependency it sends only the package NAME to pypi.org / "
            "registry.npmjs.org (never versions, file paths or source code)."
        )
        _out(
            "  Turn this off with ALETHEORE_SHIELD_OFFLINE=1 or `registry_lookups: false` in "
            ".aletheore/shield/config.json."
        )
    else:
        _out("  Registry lookups are turned off here, so the guard makes no network requests.")
    for entry in shield_network_plan(registry_lookups=lookups):
        marker = "on" if entry["enabled"] else "off"
        _out(f"  - {_safe(entry['service'])} ({marker})")


def _first_scan(repo: Path) -> None:
    from aletheore import cli

    _out("")
    _out("No evidence yet; running a quick local scan (no network, no history, no external scanners)...")
    code, _evidence, _path = cli._scan(str(repo), False, False, False, None, None, False, False, False)
    if code != 0:
        _out("The first scan did not finish. Shield works without it; run `aletheore scan` later.")


def _status(repo: Path) -> None:
    status = shield_status(repo)
    _out(f"Shield is on in {_safe(repo)}.")
    _out(f"  checkpoints: {status.checkpoints}")
    if status.latest_receipt is not None:
        _out(f"  latest receipt: {_safe(status.latest_receipt)}")
        _out("  open it to see what the last session changed.")
    else:
        _out("  latest receipt: none yet")
    for note in status.notes:
        _out(f"  note: {_safe(note)}")
    _out("")
    _out("  aletheore shield check   run the guard now")
    _out("  aletheore shield undo    put files back to a checkpoint")
    _out("  aletheore shield off     remove Shield")


@shield_app.callback()
def shield(
    ctx: typer.Context,
    path: Path = typer.Option(None, "--path", help="repository (default: current directory)"),
    yes: bool = typer.Option(False, "--yes", help="set up without asking"),
) -> None:
    if ctx.invoked_subcommand is not None:
        return
    repo = _resolve_repo(path)
    if shield_status(repo).enabled:
        _status(repo)
        return
    plan = plan_setup(repo)
    if isinstance(plan, SetupBlocked):
        raise _fail(f"Shield cannot be set up here: {_safe(plan.reason)}")
    if not plan:
        raise _fail("Shield found nothing to set up here.")
    _out(f"Shield will make these changes in {_safe(repo)}:")
    for change in plan:
        _show_change(change)
    _show_network(repo)
    _out("")
    _out("Checkpoints are kept as git refs under refs/aletheore/checkpoints/ and never touch your branches.")
    if not yes and not _interactive():
        raise _fail("Nothing was changed. Run in a terminal or pass --yes to set up Shield.")
    if not _confirm("Set up Shield with the changes above?", yes):
        _out("Nothing changed.")
        raise typer.Exit(0)
    needs_scan = not (repo / ".aletheore" / "air.json").exists()
    # Setup goes first: the scan creates .aletheore/, which would make the plan the user just approved stale.
    try:
        apply_setup(repo, approved=list(plan))
    except ValueError:
        raise _fail(
            "Files changed while you were reading the plan; nothing was written. "
            "Run `aletheore shield` again."
        )
    except Exception as exc:  # noqa: BLE001 - apply_setup rolls itself back; the user needs a sentence, not a trace
        raise _fail(f"Shield setup failed and was rolled back: {_safe(exc) or type(exc).__name__}")
    if needs_scan:
        _first_scan(repo)
    _out("")
    _out("Shield is on.")
    _out("  checkpoints: git refs under refs/aletheore/checkpoints/")
    _out("  undo:        aletheore shield undo")
    _out("  remove:      aletheore shield off")


# ---------------------------------------------------------------- undo


def _pick(checkpoints: list[Checkpoint], ref: str) -> Checkpoint:
    wanted = ref.strip()
    exact = [c for c in checkpoints if c.ref == wanted or _short_ref(c.ref) == wanted]
    if len(exact) == 1:
        return exact[0]
    suffix = [c for c in checkpoints if c.ref.endswith(wanted)] if wanted else []
    if len(suffix) == 1:
        return suffix[0]
    if not exact and not suffix:
        raise _fail(f"No checkpoint matches {_safe(wanted)}. Run `aletheore shield undo` to see the list.")
    raise _fail(f"{_safe(wanted)} matches more than one checkpoint; give more of the name.")


@shield_app.command("undo", help="put files back to how they were at a checkpoint")
def undo(
    ref: str = typer.Argument(None, help="checkpoint ref (or its unique ending); default: choose from the list"),
    path: Path = typer.Option(None, "--path", help="repository (default: current directory)"),
    yes: bool = typer.Option(False, "--yes", help="restore without asking"),
) -> None:
    repo = _resolve_repo(path)
    checkpoints = list_checkpoints(repo, 50)
    if not checkpoints:
        _out("No checkpoints yet, so there is nothing to undo.")
        raise typer.Exit(0)
    shown = checkpoints[:_UNDO_LIST]
    if ref:
        chosen = _pick(checkpoints, ref)
    else:
        _out("Checkpoints (newest first):")
        for number, item in enumerate(shown, start=1):
            _out(f"  {number}. {_safe(_short_ref(item.ref))}  {_when(item)}  {item.files} files")
        if _interactive() and not yes:
            number = typer.prompt("Which checkpoint?", default=1, type=int)
            if not 1 <= number <= len(shown):
                raise _fail(f"Choose a number from 1 to {len(shown)}.")
            chosen = shown[number - 1]
        elif yes:
            chosen = shown[0]
        else:
            raise _fail("Nothing was changed. Run in a terminal, or pass a checkpoint ref with --yes.")
    plan = plan_restore(repo, chosen)
    _out(f"Restoring {_safe(_short_ref(chosen.ref))} from {_when(chosen)}.")
    _out(f"  files that will be written: {len(plan.to_write)}")
    for name in plan.to_write[:20]:
        _out(f"    {_safe(name)}")
    if len(plan.to_write) > 20:
        _out(f"    ... and {len(plan.to_write) - 20} more")
    if plan.left_in_place:
        _out(f"  files left in place (not in the checkpoint, never deleted): {len(plan.left_in_place)}")
    if plan.blocked:
        _out(f"  files left untouched because restoring them is not safe: {len(plan.blocked)}")
        for name in plan.blocked[:10]:
            _out(f"    {_safe(name)}")
    if not plan.to_write:
        _out("Nothing to restore; the files already match.")
        raise typer.Exit(0)
    if not yes and not _interactive():
        raise _fail("Nothing was changed. Run in a terminal or pass --yes to restore.")
    if not _confirm("This overwrites the files listed above (a safety checkpoint is taken first).", yes):
        _out("Nothing changed.")
        raise typer.Exit(0)
    try:
        result = restore_checkpoint(repo, chosen, session_id="cli-undo")
    except CheckpointError as exc:
        raise _fail(f"Undo did not complete: {_safe(exc)}")
    _out(f"Restored {len(result.restored)} files.")
    for name in result.restored[:20]:
        _out(f"  {_safe(name)}")
    if result.safety is not None:
        _out(f"Undo this undo with: aletheore shield undo {_safe(_short_ref(result.safety.ref))}")


# ---------------------------------------------------------------- off


@shield_app.command("off", help="remove Shield's hooks, config and git hook block")
def off(
    purge: bool = typer.Option(False, "--purge", help="also delete checkpoints, receipts and hook state"),
    yes: bool = typer.Option(False, "--yes", help="remove without asking"),
    path: Path = typer.Option(None, "--path", help="repository (default: current directory)"),
) -> None:
    repo = _resolve_repo(path)
    _out("Shield will remove:")
    _out("  - the hooks Shield registered in .claude/settings.local.json")
    _out("  - Shield's block in the git hook")
    _out("  - Shield's block in .git/info/exclude")
    _out("  - Shield's config")
    if purge:
        _out("  - checkpoints, receipts and hook state (--purge)")
    else:
        _out("Checkpoints and receipts are kept unless --purge.")
    if not yes and not _interactive():
        raise _fail("Nothing was changed. Run in a terminal or pass --yes to remove Shield.")
    if not _confirm("Remove Shield?", yes):
        _out("Nothing changed.")
        raise typer.Exit(0)
    try:
        removed = remove_setup(repo, purge=purge)
    except Exception as exc:  # noqa: BLE001
        raise _fail(f"Removing Shield failed: {_safe(exc) or type(exc).__name__}")
    if removed:
        _out("Removed:")
        for item in removed:
            _out(f"  {_safe(item)}")
    else:
        _out("Nothing to remove.")
    if shared_blocks_kept(repo):
        _out("Another worktree still uses Shield: the shared git hook was kept.")


# ---------------------------------------------------------------- check


@shield_app.command("check", help="run the guard now over the current changes")
def check(
    path: Path = typer.Option(None, "--path", help="repository (default: current directory)"),
    no_lookups: bool = typer.Option(False, "--no-lookups", help="do not ask package registries about new dependencies"),
) -> None:
    repo = _resolve_repo(path)
    try:
        report = run_guard(repo, lookups=False if no_lookups else None, budget_s=6.0)
        text = render_report(report, max_lines=40)
    except Exception as exc:  # noqa: BLE001 - informational command: one line, never a trace
        _out(f"The guard could not run: {_safe(type(exc).__name__)}")
        return
    if not text:
        _out("No findings; every check ran.")
        return
    for line in text.splitlines():
        _out(_safe(line))
