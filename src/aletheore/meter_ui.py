"""The savings meter in Aletheore's monochrome terminal style."""

from pathlib import Path

from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from aletheore import ui


def render_meter(summary, project: Path, *, guard_configured: bool, profile_status: str, width: int, height: int,
                 include_heading: bool = True):
    """Keep observed facts and estimated tokens distinct, including an honest empty state."""
    heading = ui.command_header("Token savings meter", width=width, height=height, subtitle=str(project.resolve()))

    periods = (("Today", summary.today), ("This week", summary.this_week), ("All time", summary.all_time))
    totals = summary.all_time
    empty = not (totals.read_denials or totals.mcp_trims)
    content = []
    if empty:
        content.append(Text("No savings recorded yet", style="bold"))
        content.append(Text("Savings appear as Aletheore observes denied rereads or shortened MCP responses."))
        content.append(Text(""))

    table = Table(box=None, padding=(0, 1), expand=True, show_edge=False)
    table.add_column("Estimated tokens", style="bold")
    table.add_column("Read guard", justify="right")
    table.add_column("MCP budget", justify="right")
    for label, period in periods:
        read = f"~{period.read_tokens_estimated:,}" if period.read_denials else "—"
        mcp = f"~{period.mcp_tokens_estimated:,}" if period.mcp_trims else "—"
        table.add_row(label, read, mcp)
    content.append(table)

    if not empty:
        content.append(Text("\nObserved · all time", style="bold"))
        content.append(Text(f"{totals.read_denials:,} rereads denied · {totals.read_bytes:,} file bytes"))
        content.append(Text(f"{totals.mcp_trims:,} MCP trims · {totals.mcp_omitted_chars_measured:,} characters omitted"))

    content.append(Text("\nRead guard  " + ("On · registered for Claude Code" if guard_configured else "Off · read savings unmeasured")))
    content.append(Text(f"Lean profile  {profile_status} · savings unmeasured"))
    if not (project / ".aletheore").is_dir():
        content.append(Text("\nScan this project first to create the local evidence used by the meter."))
    if summary.corrupt_events:
        content.append(Text(f"\nSkipped {summary.corrupt_events} corrupt event file(s); totals may be incomplete.", style="bold"))

    notes = Text("— no observations · ~ estimates: file bytes ÷ 4 or omitted characters ÷ 4.\n", style="dim")
    notes.append("Local observations are measured; tokens are not model-reported usage.", style="dim")
    settings = Text("\nRead-guard settings\n", style="bold")
    settings.append(str(project.resolve() / ".claude" / "settings.local.json"), style="dim")
    if not guard_configured:
        settings.append("\nEnable from the meter menu, or run the meter with --enable-read-guard.")
    lead = [heading, Text("")] if include_heading else [Text(str(project.resolve()), style="dim"), Text("")]
    return Group(*lead, Panel(Group(*content), title="Local savings", title_align="left"), notes, settings)


def print_meter(summary, project: Path, *, guard_configured: bool, profile_status: str,
                header_shown: bool = False) -> None:
    console = Console(color_system=None, highlight=False)
    console.print(render_meter(summary, project, guard_configured=guard_configured,
                               profile_status=profile_status, width=console.width, height=console.height,
                               include_heading=not header_shown))
