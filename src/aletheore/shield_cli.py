"""`aletheore shield ...`: set up, inspect, undo, check and remove the local safety net for AI-written changes.

This is only the user-facing layer over aletheore.shield.*; every decision about files lives there. All text that
came from a repository (paths, descriptions, refs, notes) is cleaned before it is printed, and nothing is printed
through rich markup, so a hostile file name cannot inject escape sequences or styling. Errors and refusals go to
stderr as `error: ...`; plans, status and reports go to stdout.
"""

from __future__ import annotations

import contextlib
import io
import sys
from datetime import timezone
from pathlib import Path
from typing import TYPE_CHECKING

import typer

if TYPE_CHECKING:
    from aletheore.shield.checkpoint import Checkpoint
    from aletheore.shield.setup import SetupChange

# Every aletheore.shield module is imported inside the command that needs it: `aletheore.cli` registers this app at
# import time, so a top-level import here would make `aletheore --help`, `aletheore scan` and every other command pay
# for Shield's setup, checkpoint, guard and registry code (tests/test_shield_cli.py pins this). Modules are reached
# as `module.name` at call time, so patching the name on its own module works.

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
    from aletheore.shield.guard import clean_text

    return clean_text(str(text))


def _safe_block(text: str, indent: str = "    ") -> None:
    """Print a multi-line block with indentation kept but control and bidi characters neutralised."""
    from aletheore.shield.guard import _UNSAFE

    for line in str(text).splitlines():
        _out(indent + _UNSAFE.sub("?", line.expandtabs(4)).rstrip())


def _interactive() -> bool:
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def _confirm(question: str, yes: bool) -> bool:
    """Only the word "yes" (any case, trimmed) confirms. tokens_cli._confirm compares exactly; Shield is
    forgiving about case but never about "y"."""
    if yes:
        return True
    answer = typer.prompt(f"{question} Type 'yes' to continue", default="no").strip().lower()
    if answer == "yes":
        return True
    if answer.startswith("y"):
        _out("Only 'yes' sets it up.")
    return False


def _opts(ctx: typer.Context, path: Path | None, yes: bool) -> tuple[Path | None, bool]:
    """A subcommand's own --path/--yes win; otherwise the ones given to `aletheore shield` itself apply."""
    inherited = ctx.obj if isinstance(ctx.obj, dict) else {}
    return (path if path is not None else inherited.get("path")), bool(yes or inherited.get("yes"))


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


_PRIVATE = "aletheore-shield"


def _is_private(path: Path) -> bool:
    return _PRIVATE in Path(path).parts


def _rel(repo: Path, path: Path) -> str:
    try:
        return Path(path).relative_to(repo).as_posix()
    except ValueError:
        return str(path)


def _error(message: str) -> None:
    """An error or refusal: stderr, with the `error:` prefix the rest of the CLI uses."""
    typer.echo(f"error: {message}", err=True)


def _fail(message: str, code: int = 1) -> "typer.Exit":
    _error(message)
    return typer.Exit(code)


def _resolve_repo(path: Path | None) -> Path:
    start = Path(path) if path is not None else Path(".")
    if not start.is_dir():
        raise _fail(f"Shield needs a git repository, and {_safe(start)} is not a folder.")
    from aletheore.shield.gitutil import run_git

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


def _show_change(repo: Path, change: SetupChange) -> None:
    _out(f"  {_safe(change.kind)}: {_safe(_rel(repo, change.path))}")
    _out(f"      {_safe(change.description)}")
    if change.kind == "snippet":
        if change.detail:
            _safe_block(change.detail, "        ")
        _out("      Shield will not write this file; add it yourself.")
    elif change.diff:
        _safe_block(change.diff, "        ")


def _show_network(repo: Path) -> None:
    from aletheore.network_plan import shield_network_plan
    from aletheore.shield import state

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
    elif state.load_config(repo).registry_lookups:
        # Only this shell's environment turns them off; hooks Claude Code starts do not inherit it.
        _out(
            "  Registry lookups are turned off in this shell (ALETHEORE_SHIELD_OFFLINE is set); hooks started "
            "elsewhere will still look up unless `registry_lookups` is false in .aletheore/shield/config.json."
        )
    else:
        _out(
            "  Registry lookups are turned off (`registry_lookups` is false in .aletheore/shield/config.json), so "
            "the guard makes no network requests."
        )
    for entry in shield_network_plan(registry_lookups=lookups):
        marker = "on" if entry["enabled"] else "off"
        _out(f"  - {_safe(entry['service'])} ({marker})")


def _first_scan(repo: Path) -> None:
    """The quick local scan Shield's evidence checks read. Never raises: setup already succeeded."""
    from aletheore import cli

    _out("Scanning (local only)...")
    captured = io.StringIO()
    try:
        with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
            code, _evidence, _path = cli._scan(
                str(repo), False, False, False, None, None, False, False, False,
                edit_gitignore=False, progress=lambda _message: None,
            )
    except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001
        code, captured = 1, io.StringIO(type(exc).__name__)
    if code == 0:
        _out("Scanning done.")
        return
    lines = [ln for ln in captured.getvalue().splitlines() if ln.strip()]
    reason = f" ({_safe(lines[-1])})" if lines else ""
    _out(f"The first scan did not finish{reason}. Shield works without it; run `aletheore scan` later.")


_NOTE_HINTS = {
    "agent hooks not registered": "run `aletheore shield setup` to add them, or add the hooks snippet it shows",
    "agent hooks partly registered": "run `aletheore shield setup` to add the missing ones",
    "git hook not installed": "run `aletheore shield setup` to add it, or add the block it shows to the pre-commit hook",
}


def _status(repo: Path) -> None:
    from aletheore.shield import setup

    status = setup.shield_status(repo)
    if not status.enabled:
        _out(f"Shield is off in {_safe(repo)}.")
        _out("  aletheore shield setup   set it up (shows every change first)")
        return
    _out(f"Shield is on in {_safe(repo)}.")
    _out(f"  checkpoints: {status.checkpoints}")
    if status.latest_receipt is not None:
        _out(f"  latest receipt: {_safe(_rel(repo, status.latest_receipt))}")
        _out("  open it to see what the last session changed.")
    else:
        _out("  latest receipt: none yet")
    for note in status.notes:
        hint = next((h for key, h in _NOTE_HINTS.items() if note.startswith(key)), "")
        _out(f"  note: {_safe(note)}" + (f" ({hint})" if hint else ""))
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
    """A local safety net for AI-written changes: guard, undo, receipts. On its own, `aletheore shield` is
    shorthand for `shield setup` until Shield is on here, and for `shield status` after that."""
    ctx.obj = {"path": path, "yes": yes}
    if ctx.invoked_subcommand is not None:
        return
    repo = _resolve_repo(path)
    from aletheore.shield import setup

    if setup.shield_status(repo).enabled:
        _status(repo)
        return
    _setup(repo, yes)


@shield_app.command("setup", help="set Shield up here, or update an existing setup (shows every change first)")
def setup_command(
    ctx: typer.Context,
    path: Path = typer.Option(None, "--path", help="repository (default: current directory)"),
    yes: bool = typer.Option(False, "--yes", help="set up without asking"),
) -> None:
    path, yes = _opts(ctx, path, yes)
    repo = _resolve_repo(path)
    from aletheore.shield import setup

    _setup(repo, yes, already_on=setup.shield_status(repo).enabled)


@shield_app.command("status", help="show whether Shield is on, its checkpoints and latest receipt (changes nothing)")
def status_command(
    ctx: typer.Context,
    path: Path = typer.Option(None, "--path", help="repository (default: current directory)"),
) -> None:
    path, _ = _opts(ctx, path, False)
    _status(_resolve_repo(path))


def _setup(repo: Path, yes: bool, *, already_on: bool = False) -> None:
    """The first-run flow: show the plan, ask, apply, run the first scan. With `already_on`, an empty plan is not an
    error: Shield is set up and nothing needs updating."""
    from aletheore.shield import setup

    plan = setup.plan_setup(repo)
    if isinstance(plan, setup.SetupBlocked):
        raise _fail(f"Shield cannot be set up here: {_safe(plan.reason)}")
    if not plan and already_on:
        _out(f"Shield is already set up in {_safe(repo)}; nothing to change.")
        return
    if not plan:
        raise _fail("Shield found nothing to set up here.")
    _out(f"Shield will make these changes in {_safe(repo)}:")
    visible = [c for c in plan if not _is_private(c.path)]
    for change in visible:
        _show_change(repo, change)
    if len(visible) < len(plan):
        _out("  Shield's private records in .git/aletheore-shield/ (not shown)")
    needs_scan = not (repo / ".aletheore" / "air.json").exists()
    if needs_scan:
        _out("")
        _out(
            "Then run a first local scan (no network): it writes evidence into .aletheore/ (not tracked by git) "
            "- this stays after `shield off`; delete .aletheore/ yourself to remove it."
        )
    _show_network(repo)
    _out("")
    _out("Checkpoints are kept as git refs under refs/aletheore/checkpoints/ and never touch your branches.")
    if not yes and not _interactive():
        raise _fail("Nothing was changed. Run in a terminal or pass --yes to set up Shield.")
    if not _confirm("Set up Shield with the changes above?", yes):
        _out("Nothing changed.")
        raise typer.Exit(0)
    # Setup goes first: the scan creates .aletheore/, which would make the plan the user just approved stale.
    try:
        setup.apply_setup(repo, approved=list(plan))
    except ValueError as exc:
        if str(exc) == setup.PLAN_CHANGED:
            raise _fail(
                "Files changed while you were reading the plan; nothing was written. "
                "Run `aletheore shield` again."
            )
        raise _fail(f"Shield setup failed and was rolled back: {_safe(exc) or 'ValueError'}")
    except Exception as exc:  # noqa: BLE001 - apply_setup rolls itself back; the user needs a sentence, not a trace
        raise _fail(f"Shield setup failed and was rolled back: {_safe(exc) or type(exc).__name__}")
    if needs_scan:
        _first_scan(repo)
    _out("")
    _out("Shield is on.")
    _out("  checkpoints: git refs under refs/aletheore/checkpoints/")
    _out("  receipts:    .aletheore/shield/receipts/ (hidden from git status; open one to see what a session changed)")
    _out("  config:      .aletheore/shield/config.json")
    _out("  check now:   aletheore shield check")
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
    ctx: typer.Context,
    ref: str = typer.Argument(None, help="checkpoint ref (or its unique ending); default: choose from the list"),
    path: Path = typer.Option(None, "--path", help="repository (default: current directory)"),
    yes: bool = typer.Option(False, "--yes", help="restore without asking"),
) -> None:
    path, yes = _opts(ctx, path, yes)
    repo = _resolve_repo(path)
    from aletheore.shield import checkpoint

    checkpoints = checkpoint.list_checkpoints(repo, 50)
    if not checkpoints:
        _out("No checkpoints yet, so there is nothing to undo.")
        raise typer.Exit(0)
    shown = checkpoints[:_UNDO_LIST]
    if ref:
        chosen = _pick(checkpoints, ref)
    else:
        _out("Checkpoints (newest first):")
        for number, item in enumerate(shown, start=1):
            _out(f"  {number}. {_safe(_short_ref(item.ref))}  {_when(item)}  {_plural(item.files, 'file')}")
        if _interactive() and not yes:
            number = typer.prompt("Which checkpoint?", default=1, type=int)
            if not 1 <= number <= len(shown):
                raise _fail(f"Choose a number from 1 to {len(shown)}.")
            chosen = shown[number - 1]
        elif yes:
            chosen = shown[0]
        else:
            raise _fail("Nothing was changed. Run in a terminal, or pass a checkpoint ref with --yes.")
    plan = checkpoint.plan_restore(repo, chosen)
    _out(f"Checkpoint {_safe(_short_ref(chosen.ref))} from {_when(chosen)}:")
    _out(f"  files that will be written: {len(plan.to_write)}")
    for name in plan.to_write[:20]:
        _out(f"    {_safe(name)}")
    if len(plan.to_write) > 20:
        _out(f"    ... and {len(plan.to_write) - 20} more")
    if plan.left_in_place:
        _out(f"  {_plural(len(plan.left_in_place), 'file')} left in place (not in the checkpoint, never deleted):")
        for name in plan.left_in_place[:10]:
            _out(f"    {_safe(name)}")
        if len(plan.left_in_place) > 10:
            _out(f"    ... and {len(plan.left_in_place) - 10} more")
    if plan.blocked:
        _out(f"  {_plural(len(plan.blocked), 'file')} left untouched because restoring is not safe:")
        for name in plan.blocked[:10]:
            _out(f"    {_safe(name)}")
    not_captured = len(plan.not_captured) + plan.not_captured_more
    if not_captured:
        # Their content was never read (secret-looking names, size caps), so whether they changed is unknown.
        _out(f"  {_plural(not_captured, 'file')} not captured by the checkpoint, cannot be restored:")
        for name in plan.not_captured[:10]:
            _out(f"    {_safe(name)}")
        if not_captured > 10:
            _out(f"    ... and {not_captured - 10} more")
    if not plan.to_write:
        if plan.blocked or not_captured:
            _out("Nothing restorable differs; the files listed above were left as they are.")
        else:
            _out("Nothing to restore; the files already match.")
        raise typer.Exit(0)
    if not yes and not _interactive():
        raise _fail("Nothing was changed. Run in a terminal or pass --yes to restore.")
    if not _confirm("This overwrites the files listed above (a safety checkpoint is taken first).", yes):
        _out("Nothing changed.")
        raise typer.Exit(0)
    try:
        result = checkpoint.restore_checkpoint(repo, chosen, session_id="cli-undo")
    except checkpoint.CheckpointError as exc:
        raise _fail(f"Undo did not complete: {_safe(exc)}")
    _out(f"Restored {_plural(len(result.restored), 'file')}.")
    for name in result.restored[:20]:
        _out(f"  {_safe(name)}")
    if result.safety is not None:
        _out(f"Undo this undo with: aletheore shield undo {_safe(_short_ref(result.safety.ref))}")


# ---------------------------------------------------------------- off


@shield_app.command("off", help="remove Shield's hooks, config and git hook block")
def off(
    ctx: typer.Context,
    purge: bool = typer.Option(False, "--purge", help="also delete checkpoints, receipts and hook state"),
    yes: bool = typer.Option(False, "--yes", help="remove without asking"),
    path: Path = typer.Option(None, "--path", help="repository (default: current directory)"),
) -> None:
    path, yes = _opts(ctx, path, yes)
    repo = _resolve_repo(path)
    _out("Shield will remove:")
    _out("  - the hooks Shield registered in .claude/settings.local.json")
    _out("  - Shield's block in the git hook")
    if (repo / ".aletheore").is_dir():
        _out("  - Shield's block in .git/info/exclude, except a one-line exclude for .aletheore/ (scan evidence stays there)")
    else:
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
        from aletheore.shield import setup

        report = setup.remove_setup_report(repo, purge=purge)
    except Exception as exc:  # noqa: BLE001
        raise _fail(f"Removing Shield failed: {_safe(exc) or type(exc).__name__}")
    for heading, paths in (
        ("Restored to how they were:", report.restored),  # the exact bytes from before setup
        ("Removed Shield's entries from:", report.edited),  # only Shield's part taken out; the rest kept
        ("Removed:", report.removed),
    ):
        public = [p for p in paths if not _is_private(p)]
        if public:
            _out(heading)
            for item in public:
                _out(f"  {_safe(_rel(repo, item))}")
    if any(_is_private(p) for p in report.changed):
        _out("Removed Shield's private records in .git/aletheore-shield/.")
    if not report.changed:
        _out("Nothing to remove.")
    if (repo / ".aletheore").exists():
        _out(
            "Kept a git exclude for .aletheore/ because scan evidence remains there (delete .aletheore/ yourself, "
            "then run `aletheore shield off` again to drop it)."
        )
    if setup.shared_blocks_kept(repo):
        _out("Another worktree still uses Shield: the shared git hook was kept.")


# ---------------------------------------------------------------- check


@shield_app.command("check", help="run the guard now over the current changes")
def check(
    ctx: typer.Context,
    path: Path = typer.Option(None, "--path", help="repository (default: current directory)"),
    no_lookups: bool = typer.Option(False, "--no-lookups", help="do not ask package registries about new dependencies"),
) -> None:
    path, _ = _opts(ctx, path, False)
    repo = _resolve_repo(path)
    try:
        from aletheore.shield import guard

        report = guard.run_guard(repo, lookups=False if no_lookups else None, budget_s=6.0)
        text = guard.render_report(report, max_lines=40)
    except Exception as exc:  # noqa: BLE001 - informational command: one line, never a trace
        _error(f"the guard could not run: {_safe(type(exc).__name__)}")
        return
    if not text:
        _out("No findings; every check ran.")
        return
    for line in text.splitlines():
        _out(_safe(line))
