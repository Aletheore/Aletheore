import difflib
import json
import shutil
import socket
import sys
import threading
import time
import tomllib
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import Optional

import httpx
import tomli_w
import typer
import uvicorn
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from aletheore.adapters.anthropic_native import AnthropicAdapter
from aletheore.adapters.claude_code import AdapterInvocationError, ClaudeCodeAdapter
from aletheore.adapters.codex_cli import CodexCliAdapter
from aletheore.adapters.gemini_cli import GeminiCliAdapter
from aletheore.adapters.grok_build import GrokBuildAdapter
from aletheore.adapters.mistral_vibe import MistralVibeAdapter
from aletheore.adapters.openai_compatible import OpenAICompatibleAdapter
from aletheore.adapters.opencode import OpenCodeAdapter
from aletheore.citation_verifier import (
    citation_verification_section,
    load_verifiable_evidence,
    local_line_count_fetcher,
    verify_citations,
)
from aletheore.credentials import get_api_key
from aletheore.device_auth import infer_repo_full_name_from_cwd_git_remote
from aletheore.evidence import (
    IncompatibleEvidenceVersionError,
    load_evidence,
    load_evidence_file,
    scan_repository,
    write_evidence,
)
from aletheore.git_intel.analyzer import GIT_ANALYSIS_RESOURCE_EXIT_CODE, GitAnalysisError
from aletheore.healthcheck import run_healthcheck, save_healthcheck
from aletheore.history import compute_diff, list_snapshots, save_snapshot, to_sarif
from aletheore.managed_audit_client import ManagedAuditError, run_managed_audit_request
from aletheore.query import (
    BranchNotFoundInEvidenceError,
    ModuleNotFoundInEvidenceError,
    QUERY_FUNCTIONS,
    SymbolNotFoundInEvidenceError,
    find_symbol_source,
)
from aletheore.repo_config import DISABLEABLE_CHECKS, load_repo_config
from aletheore.report import (
    AmbiguousAdapterError,
    NoAdapterAvailableError,
    run_reasoning_phase,
    select_adapter,
)
from aletheore.toon_encoding import to_toon
from aletheore.watch import DEBOUNCE_SECONDS as WATCH_DEBOUNCE_SECONDS

KNOWN_ADAPTERS = [
    ClaudeCodeAdapter(),
    AnthropicAdapter(),
    OpenCodeAdapter(),
    CodexCliAdapter(),
    OpenAICompatibleAdapter(
        name="openai",
        base_url="https://api.openai.com/v1",
        api_key_env_var="OPENAI_API_KEY",
        model="gpt-5.2",
    ),
    OpenAICompatibleAdapter(
        name="mistral",
        base_url="https://api.mistral.ai/v1",
        api_key_env_var="MISTRAL_API_KEY",
        model="mistral-large-latest",
    ),
    MistralVibeAdapter(),
    OpenAICompatibleAdapter(
        name="grok",
        base_url="https://api.x.ai/v1",
        api_key_env_var="XAI_API_KEY",
        model="grok-4-latest",
    ),
    GrokBuildAdapter(),
    OpenAICompatibleAdapter(
        name="ollama",
        base_url="http://localhost:11434/v1",
        api_key_env_var="",
        model="llama3.1:8b",
        needs_key=False,
        requires_consent=False,
        supports_tool_choice=False,
    ),
    GeminiCliAdapter(),
    OpenAICompatibleAdapter(
        name="gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        api_key_env_var="GEMINI_API_KEY",
        model="gemini-3.5-flash",
    ),
    OpenAICompatibleAdapter(
        name="deepseek",
        base_url="https://api.deepseek.com",
        api_key_env_var="DEEPSEEK_API_KEY",
        model="deepseek-v4-pro",
        # deepseek-v4-pro runs in "thinking mode" by default, which rejects
        # tool_choice="required" (BadRequestError: "Thinking mode does not
        # support this tool_choice"), confirmed against the real API during
        # the 2026-07-26 PR-review-benchmark dry run. Falling back to the
        # default tool_choice ("auto") works, so disable forced tool_choice
        # for this adapter, mirroring the ollama entry above.
        supports_tool_choice=False,
    ),
]

MANUAL_DIR = str(Path(__file__).resolve().parent / "manual")

console = Console()

# Grouped rather than flat because this list is the CLI's only map of what
# `query` can actually do - 23 kinds printed as one comma-separated run is
# unreadable, and a bare `aletheore query` previously got Typer's "Missing
# argument 'KIND'" with no kinds named at all. The grouping is also the
# answer to "why so few commands": every kind below reads the same air.json,
# which is the point, so they belong under one verb rather than promoted to
# 23 top-level commands.
QUERY_KIND_GROUPS: dict[str, list[str]] = {
    "Structure": [
        "imports",
        "imported-by",
        "symbols",
        "cluster",
        "layer-violations",
        "dead-code",
        "schema",
    ],
    "Security": ["secrets", "vulnerabilities", "licenses"],
    "Runtime": ["endpoints", "database", "infrastructure", "environment-variables"],
    "History": ["branch", "ownership", "hotspots", "changes"],
    "Evidence": [
        "evidence-for-symbol",
        "evidence-for-endpoint",
        "evidence-for-dependency",
    ],
    "AI-assisted": ["answer", "search-codebase", "symbol-source"],
}

# Derived from the groups rather than maintained beside them - a kind added
# to QUERY_FUNCTIONS but forgotten in a group would otherwise silently vanish
# from the listing while still being dispatchable. The four kinds not in
# QUERY_FUNCTIONS ("changes", "search-codebase", "answer", "symbol-source")
# are dispatched by hand inside _query, so the check runs one way only.
QUERY_KIND_CHOICES = [kind for kinds in QUERY_KIND_GROUPS.values() for kind in kinds]

_MISSING_FROM_GROUPS = set(QUERY_FUNCTIONS) - set(QUERY_KIND_CHOICES)
if _MISSING_FROM_GROUPS:  # pragma: no cover - import-time invariant
    raise RuntimeError(
        "QUERY_KIND_GROUPS is missing query kinds that QUERY_FUNCTIONS defines: "
        f"{', '.join(sorted(_MISSING_FROM_GROUPS))} - add them to a group so "
        "`aletheore query` can list them"
    )


# Every command that takes a repository takes it positionally (`aletheore
# scan .`) except `query`, whose first positional is the kind and whose third
# is already a symbol name - PATH cannot be added there without breaking
# `query symbol-source <module> <symbol>`. So rather than leave `--path`
# working on exactly one command out of nine, it is accepted as an alias on
# all of them. `aletheore dashboard --path .` previously failed with "No such
# option '--path'. Did you mean '--port'?" purely because the user had just
# learned `--path` from `query`.
_PATH_OPTION = typer.Option(None, "--path", help="repository path (alias for the PATH argument)")


def _resolve_path(positional: str, option: Optional[str]) -> str:
    """The positional PATH unless --path was given, which wins.

    Not `option or positional` - an explicit `--path ""` is still a choice
    and should not silently fall back to the positional default.
    """
    return positional if option is None else option


def _query_kinds_panel() -> Panel:
    # A Table rather than manual padding in a Text: several groups are wider
    # than the panel, and hand-padded rows wrap back to column 0, leaving
    # "dead-code" and "evidence-for-dependency" hanging under the group
    # labels as if they were groups themselves. A table wraps the kind column
    # within its own bounds, so continuation lines stay aligned.
    header = Text()
    header.append("Usage: ", style="bold")
    header.append("aletheore query KIND [TARGET] [SYMBOL] [--path PATH]")

    kinds_table = Table(box=None, show_header=False, pad_edge=False, padding=(0, 2, 0, 0))
    kinds_table.add_column(style="bold cyan", no_wrap=True, vertical="top")
    kinds_table.add_column(overflow="fold")
    for group, kinds in QUERY_KIND_GROUPS.items():
        kinds_table.add_row(group, "  ".join(kinds))

    footer = Text()
    footer.append(
        f"All {len(QUERY_KIND_CHOICES)} read the same .aletheore/air.json - "
        "run 'aletheore scan' first.\n",
        style="dim",
    )
    footer.append("Run 'aletheore query --help' for options.", style="dim")

    return Panel(
        Group(header, "", kinds_table, "", footer),
        title="query kinds",
        border_style="cyan",
        width=78,
    )


def _sponsor_panel() -> Panel:
    body = Text()
    body.append("Aletheore is 100% open-source, local, and free.\n", style="bold")
    body.append("No accounts, no tracking — nothing leaves this machine.\n\n")
    body.append("If it saved you time, consider supporting development:\n")
    body.append("https://github.com/sponsors/ArihantK15", style="cyan underline")
    return Panel(body, border_style="magenta", width=78)


def _print_result(title: str, lines: list[str], color: str = "green") -> None:
    """No box: these lines are almost always absolute file paths of
    unpredictable length, and a boxed panel's normal wrapping breaks a
    too-long line by inserting a real newline wherever it runs out of box
    width, including mid-filename - corrupting the path if the line is
    ever copied. soft_wrap leaves wrapping to the terminal instead, which
    never inserts a literal character into the copyable text."""
    console.print(f"[bold {color}]✓ {title}[/bold {color}]")
    for line in lines:
        console.print(f"  {line}", soft_wrap=True)


_COMMAND_SUMMARIES = [
    ("scan", "run the scanner, write evidence, no LLM call"),
    ("audit", "scan, then have a coding agent write a grounded report"),
    ("query", "answer a targeted question from existing evidence"),
    ("diff", "compare two evidence snapshots"),
    ("verify", "check a report's file:line citations against a repo's evidence"),
    ("mcp", "run an MCP server so an agent can query a repo directly"),
    ("mcp-install", "write MCP client config for Claude Code, Cursor, VS Code, Kiro, Opencode, or Codex CLI"),
    ("dashboard", "a live local web UI over the same evidence"),
    ("healthcheck", "GET-only live check of mapped API endpoints"),
    ("watch", "re-scan and re-index automatically as files change"),
    ("init", "scaffold a repository-local .aletheore.json config file"),
    ("login", "authenticate and save a managed-audit API token"),
    ("logout", "clear the locally saved managed-audit API token"),
    ("status", "installed version, update availability, and login state"),
]


def _banner_panel() -> Panel:
    intro = Text(
        "Evidence-grounded repository audit — a deterministic scanner (tree-sitter + "
        "git log, no LLM) reads a repo and writes .aletheore/air.json. Every "
        "other command below reads from that same evidence, never re-scans blind.\n"
    )

    # A Table (not hand-joined Text) so a description that wraps to a second
    # line lands in its own column instead of falling back to the panel's
    # left edge - Rich only hanging-indents wrapped text inside a cell, not
    # inside a flat Text blob built from literal "\n"s.
    commands = Table.grid(padding=(0, 2, 0, 0))
    commands.add_column(style="bold green", no_wrap=True)
    commands.add_column()
    for name, desc in _COMMAND_SUMMARIES:
        commands.add_row(f"  {name}", desc)

    footer = Text()
    footer.append("Run ")
    footer.append("aletheore <command> --help", style="bold cyan")
    footer.append(" for details on any command.\n")
    footer.append("https://github.com/Aletheore/Aletheore", style="dim underline")

    return Panel(
        Group(intro, commands, Text(""), footer),
        title="[bold cyan]ALETHEORE[/bold cyan]",
        title_align="left",
        border_style="cyan",
        width=78,
    )


_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


_OVERWRITABLE_PREFIXES = ("Checking dependency licenses:", "Embedding chunks:")


def _make_progress_printer(is_tty: bool | None = None) -> Callable[[str], None]:
    # License checks and hosted-embedding batches each report one message
    # per unit of repeating work (dozens of dependencies; up to hundreds of
    # embedding batches on a large repo - thrift alone needs 553 sequential
    # ones at the old char cap). In a real terminal those overwrite in place
    # via \r instead of scrolling, since they're the same phase repeating,
    # not a new step. \r only means "return to start of line" on an actual
    # TTY though - piped to a CI log or a file, it prints as a literal
    # character with no effect, so non-TTY output instead prints every
    # message on its own line: more lines, but a real, readable history in
    # a log rather than concatenated garbage.
    is_tty = sys.stdout.isatty() if is_tty is None else is_tty
    state = {"in_place": False, "frame": 0}

    def report(message: str) -> None:
        overwritable = is_tty and message.startswith(_OVERWRITABLE_PREFIXES)
        if overwritable:
            # A single phase repeating many times in place is the one spot
            # where a rotating glyph is actually visible (many updates over
            # time) rather than flashing past on a single-shot phase
            # announcement, so only this line gets a real spinner frame.
            spinner = _SPINNER_FRAMES[state["frame"] % len(_SPINNER_FRAMES)]
            state["frame"] += 1
            print(f"\r  {spinner} {message}" + " " * 15, end="", flush=True)
            state["in_place"] = True
        else:
            if state["in_place"]:
                print()
                state["in_place"] = False
            console.print(f"  [green]→[/green] {message}")

    def finish() -> None:
        # scan's own report() calls always end on a non-overwritable message
        # ("Mapping API endpoints", a final "Done"), which closes any
        # pending in-place line as a side effect - callers whose last update
        # can genuinely be the overwritable kind (index: the last batch
        # finishing is the last thing that happens) need an explicit way to
        # close it, or the next line printed via console.print directly
        # concatenates onto the same terminal line instead of starting a
        # new one.
        if state["in_place"]:
            print()
            state["in_place"] = False

    report.finish = finish  # type: ignore[attr-defined]
    return report


class _ElapsedTicker:
    """Prints an elapsed-time indicator while a blocking call (e.g. an external
    coding-agent subprocess) runs, so a multi-minute wait doesn't look
    identical to a hang. On a real terminal this updates in place every few
    seconds; piped to a log/file (no TTY), it prints once at the start and
    once at the end instead of spamming a new line every interval."""

    def __init__(self, label: str, interval: float = 3.0, is_tty: bool | None = None) -> None:
        self._label = label
        self._interval = interval
        self._is_tty = sys.stdout.isatty() if is_tty is None else is_tty
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        start = time.monotonic()
        frame = 0
        while not self._stop.wait(self._interval):
            elapsed = int(time.monotonic() - start)
            spinner = _SPINNER_FRAMES[frame % len(_SPINNER_FRAMES)]
            frame += 1
            print(f"\r  {spinner} {self._label}... ({elapsed}s elapsed)" + " " * 10, end="", flush=True)

    def __enter__(self) -> "_ElapsedTicker":
        self._start = time.monotonic()
        if self._is_tty:
            self._thread.start()
        else:
            console.print(f"  [green]→[/green] {self._label}...")
        return self

    def __exit__(self, *exc_info) -> None:
        if self._is_tty:
            self._stop.set()
            self._thread.join()
            print()
        else:
            elapsed = int(time.monotonic() - self._start)
            console.print(f"  [green]→[/green] {self._label}: done ({elapsed}s elapsed)")


def _resolve_check_toggles(
    repo: Path,
    check_vulnerabilities: bool | None,
    scan_git_history: bool | None,
    check_licenses: bool | None,
    map_endpoints: bool | None,
) -> tuple[bool, bool, bool, bool]:
    """Each toggle is bool|None from the CLI: None means "no explicit flag
    passed", so the repo's .aletheore.json disabled_checks decides. An
    explicit --check-x/--no-check-x flag always overrides the config,
    regardless of which way it points."""
    disabled = set(load_repo_config(repo)["disabled_checks"])
    return (
        check_vulnerabilities if check_vulnerabilities is not None else "vulnerabilities" not in disabled,
        scan_git_history if scan_git_history is not None else "secrets_history" not in disabled,
        check_licenses if check_licenses is not None else "licenses" not in disabled,
        map_endpoints if map_endpoints is not None else "endpoints" not in disabled,
    )


_SCHEMA_UNENTITLED_REASON = (
    "requires a paid plan - run 'aletheore login' to connect an entitled installation"
)


def _resolve_schema_entitlement() -> tuple[bool, str]:
    """Whether this machine may run schema mapping, and why not if not.

    Resolved from the saved managed-audit token via the existing /v1/whoami,
    not from a new licensing mechanism - `status` already does exactly this.
    Any failure (no token, offline, API down, free plan) is treated as
    unentitled rather than raised: a scan must still produce evidence when
    the entitlement service is unreachable, it just produces it with the
    schema section unchecked and a reason saying so.
    """
    token = get_api_key("ALETHEORE_API_TOKEN", "aletheore-managed-audit", prompt_fn=lambda _: "")
    if not token:
        return False, _SCHEMA_UNENTITLED_REASON
    who = _fetch_whoami(token)
    if who is None:
        return False, "could not reach the entitlement service - schema mapping skipped"
    if who.get("plan", "free") == "free":
        return False, _SCHEMA_UNENTITLED_REASON
    return True, ""


def _scan(
    repo_path: str,
    check_vulnerabilities: bool | None,
    scan_git_history: bool | None,
    check_licenses: bool | None = None,
    map_endpoints: bool | None = None,
    map_schema: bool | None = None,
) -> tuple[int, dict, Path]:
    repo = Path(repo_path).resolve()
    resolved_vulnerabilities, resolved_git_history, resolved_licenses, resolved_endpoints = (
        _resolve_check_toggles(repo, check_vulnerabilities, scan_git_history, check_licenses, map_endpoints)
    )
    if map_schema is False:
        resolved_schema, schema_reason = False, "skipped (--no-map-schema)"
    else:
        resolved_schema, schema_reason = _resolve_schema_entitlement()

    console.print(f"Scanning {repo}...")
    try:
        evidence = scan_repository(
            repo,
            check_vulnerabilities=resolved_vulnerabilities,
            scan_git_history=resolved_git_history,
            check_licenses=resolved_licenses,
            map_endpoints=resolved_endpoints,
            map_schema=resolved_schema,
            map_schema_skip_reason=schema_reason,
            progress=_make_progress_printer(),
        )
    except GitAnalysisError as exc:
        console.print(f"[bold red]error:[/bold red] {exc}")
        return GIT_ANALYSIS_RESOURCE_EXIT_CODE, {}, repo
    evidence_path = write_evidence(evidence, repo)
    snapshot_path = save_snapshot(evidence, repo)
    result_lines = [f"Evidence written to {evidence_path}", f"Snapshot saved to {snapshot_path}"]
    if evidence.get("security", {}).get("secrets", {}).get("history_scan_timed_out"):
        result_lines.append(
            "[yellow]Warning: secrets history scan timed out - findings reflect a partial scan[/yellow]"
        )
    _print_result("Scan complete", result_lines)
    return 0, evidence, evidence_path


def _audit(
    repo_path: str,
    forced_agent: str | None,
    check_vulnerabilities: bool | None,
    scan_git_history: bool | None,
    check_licenses: bool | None = None,
    map_endpoints: bool | None = None,
    map_schema: bool | None = None,
) -> int:
    scan_exit_code, _evidence, evidence_path = _scan(
        repo_path, check_vulnerabilities, scan_git_history, check_licenses, map_endpoints, map_schema
    )
    if scan_exit_code != 0:
        return scan_exit_code
    repo = Path(repo_path).resolve()

    try:
        adapter = select_adapter(
            KNOWN_ADAPTERS, forced_name=forced_agent, interactive=sys.stdin.isatty()
        )
    except (NoAdapterAvailableError, AmbiguousAdapterError) as exc:
        console.print(f"[bold red]error:[/bold red] {exc}")
        console.print(f"Evidence is still available at {evidence_path} for manual use.")
        return 1

    if adapter.requires_consent:
        console.print(
            f"[bold yellow]This will send this repository's evidence "
            f"(not source code) to {adapter.name}'s API.[/bold yellow]"
        )
        confirmed = input("Continue? [y/N]: ").strip().lower() == "y"
        if not confirmed:
            console.print("Cancelled - no data was sent.")
            return 0

    console.print(f"Running audit with [bold]{adapter.name}[/bold]...")
    try:
        with _ElapsedTicker(f"Waiting on {adapter.name}"):
            report_path = run_reasoning_phase(adapter, repo_path=str(repo), manual_dir=MANUAL_DIR)
    except AdapterInvocationError as exc:
        console.print(f"[bold red]error:[/bold red] {exc}")
        console.print(f"Evidence is still available at {evidence_path} for manual use.")
        return 1

    report_file = Path(report_path)
    report_text = report_file.read_text()
    verification_section = citation_verification_section(report_text, repo)
    report_file.write_text(report_text + verification_section)

    result_lines = [f"Report written to {report_path}"]
    if "could not be verified" in verification_section:
        result_lines.append(
            "[yellow]Some citations in this report could not be verified - see the "
            "Citation Verification section[/yellow]"
        )
    _print_result("Audit complete", result_lines)
    console.print()
    console.print(_sponsor_panel())
    return 0


def _managed_audit(
    repo_path: str,
    token: str | None,
    check_vulnerabilities: bool | None,
    scan_git_history: bool | None,
    check_licenses: bool | None = None,
    map_endpoints: bool | None = None,
    map_schema: bool | None = None,
) -> int:
    resolved_token = token or get_api_key("ALETHEORE_API_TOKEN", "aletheore-managed-audit")
    if not resolved_token:
        console.print("[bold red]error:[/bold red] no managed-audit token available")
        return 1

    scan_exit_code, evidence, evidence_path = _scan(
        repo_path,
        check_vulnerabilities,
        scan_git_history,
        check_licenses,
        map_endpoints,
        map_schema,
    )
    if scan_exit_code != 0:
        return scan_exit_code
    repo = Path(repo_path).resolve()
    repo_full_name = infer_repo_full_name_from_cwd_git_remote(cwd=str(repo))

    console.print("Running managed audit (using Aletheore's shared key)...")
    try:
        with _ElapsedTicker("Waiting on the managed audit service"):
            report_text = run_managed_audit_request(evidence, resolved_token, repo_full_name=repo_full_name)
    except ManagedAuditError as exc:
        console.print(f"[bold red]error:[/bold red] {exc}")
        console.print(f"Evidence is still available at {evidence_path} for manual use.")
        return 1

    report_path = repo / ".aletheore" / "audit-report.md"
    report_path.write_text(report_text)
    _print_result("Managed audit complete", [f"Report written to {report_path}"])
    return 0


def _check_for_update(installed_version: str, http_client: httpx.Client | None = None) -> str:
    client = http_client or httpx.Client(base_url="https://pypi.org")
    try:
        response = client.get("/pypi/aletheore/json", timeout=5.0)
        response.raise_for_status()
        latest_version = response.json()["info"]["version"]
    except (httpx.HTTPError, KeyError, ValueError):
        return "couldn't check for updates"
    if latest_version == installed_version:
        return "up to date"
    return f"update available: {latest_version}"


def _print_update_notice_if_available() -> None:
    # Only on the bare invocation - previously the only way to learn a
    # newer version existed was to already know to run 'aletheore status'.
    # Deliberately not added to scan/audit/query/diff/verify/mcp/etc.: the
    # privacy policy promises 'aletheore scan' makes no network calls of
    # its own, and the same reasoning extends to every other command whose
    # job is local evidence work, not talking to Aletheore's own infra.
    # Silent on "up to date" or a failed check - this is a friendly nudge,
    # not something worth cluttering the banner over when there's nothing
    # to report.
    import importlib.metadata

    installed_version = importlib.metadata.version("aletheore")
    version_note = _check_for_update(installed_version)
    if not version_note.startswith("update available: "):
        return
    latest_version = version_note.removeprefix("update available: ")
    console.print(
        f"\n[bold yellow]Update available:[/bold yellow] {installed_version} → {latest_version}. "
        "Run [cyan]pipx upgrade aletheore[/cyan] (or [cyan]pip install --upgrade aletheore[/cyan])."
    )


def _fetch_whoami(
    token: str,
    api_base_url: str = "https://app.aletheore.com",
    http_client: httpx.Client | None = None,
) -> dict | None:
    client = http_client or httpx.Client(base_url=api_base_url)
    try:
        response = client.get(
            "/v1/whoami", headers={"Authorization": f"Bearer {token}"}, timeout=5.0
        )
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    return response.json()


def _query_schema(repo_path: str) -> int:
    """The database schema as a mermaid erDiagram.

    Printed as mermaid rather than TOON because the whole point of this
    section is the picture - the raw tables/relations arrays are already
    reachable through the evidence file for anything that wants them.
    """
    from aletheore.wiki_diagrams import build_schema_diagram

    repo = Path(repo_path).resolve()
    try:
        evidence = load_evidence(repo)
    except (FileNotFoundError, IncompatibleEvidenceVersionError) as exc:
        console.print(f"[bold red]error:[/bold red] {exc}")
        return 1

    schema = evidence["repository"]["database"]["schema"]
    if not schema["checked"]:
        console.print(f"[yellow]schema mapping not run:[/yellow] {schema['reason']}")
        return 1
    diagram = build_schema_diagram(evidence)
    if diagram is None:
        console.print("no tables found - no migration directories were detected in this repo")
        return 0
    print(diagram)
    return 0


def _query_changes(repo_path: str, full: bool) -> int:
    repo = Path(repo_path).resolve()
    snapshots = list_snapshots(repo)

    if len(snapshots) < 2:
        print("no prior snapshot to compare against - run 'aletheore scan' again later to compare")
        return 0

    try:
        old = load_evidence_file(snapshots[-2])
    except json.JSONDecodeError:
        print(f"error: most recent snapshot is unreadable ({snapshots[-2]})")
        return 1
    except IncompatibleEvidenceVersionError as exc:
        print(f"error: {exc}")
        return 1

    try:
        new = load_evidence_file(snapshots[-1])
    except json.JSONDecodeError:
        print(f"error: most recent snapshot is unreadable ({snapshots[-1]})")
        return 1
    except IncompatibleEvidenceVersionError as exc:
        print(f"error: {exc}")
        return 1

    diff = compute_diff(old, new, full=full)
    print(json.dumps(diff, indent=2))
    return 0


def _index(repo_path: str) -> int:
    repo = Path(repo_path).resolve()
    try:
        evidence = load_evidence(repo)
    except FileNotFoundError as exc:
        console.print(f"[bold red]error:[/bold red] {exc}")
        return 1
    except IncompatibleEvidenceVersionError as exc:
        console.print(f"[bold red]error:[/bold red] {exc}")
        return 1
    console.print(
        "Building semantic search index (embedding via local Ollama, "
        "falling back to OpenAI if unavailable)..."
    )
    # Same reasoning as _scan's cache-hit message: only a chunk whose text
    # actually changed gets re-embedded (see search_index.build_index), so a
    # repeat index of a mostly-unchanged repo is much faster than the first
    # one - said explicitly here so a fast rebuild reads as the cache
    # working, not as something having been skipped.
    console.print(
        "[dim]Only chunks that changed since the last index are re-embedded - "
        "the first index of a repo is the slow one.[/dim]"
    )
    from aletheore.search_index import build_index

    report = _make_progress_printer()

    def on_progress(done: int, total: int) -> None:
        report(f"Embedding chunks: {done}/{total}")

    try:
        count = build_index(repo, evidence, on_progress=on_progress)
    except Exception as exc:
        report.finish()  # type: ignore[attr-defined]
        console.print(f"[bold red]error:[/bold red] {exc}")
        return 1
    report.finish()  # type: ignore[attr-defined]
    console.print(f"[green]Indexed {count} chunks.[/green]")
    return 0


def _query(
    kind: str,
    target: str | None,
    repo_path: str,
    full: bool = False,
    forced_agent: str | None = None,
    k: int = 10,
    symbol: str | None = None,
    language: str | None = None,
) -> int:
    if kind not in QUERY_KIND_CHOICES:
        # Suggestion first, listing after: a typo ("secret", "hotspot") is the
        # common case and one close match answers it without making the user
        # read 23 names. cutoff is difflib's default 0.6, which accepts
        # "secret"->"secrets" while rejecting an unrelated word - a wrong
        # suggestion is worse than none, since it sends the user off to run
        # something they never meant.
        suggestions = difflib.get_close_matches(kind, QUERY_KIND_CHOICES, n=1)
        console.print(f"[bold red]error:[/bold red] '{kind}' is not a valid query kind.")
        if suggestions:
            console.print(f"Did you mean [bold cyan]{suggestions[0]}[/bold cyan]?\n")
        console.print(_query_kinds_panel())
        return 1

    if kind == "schema":
        return _query_schema(repo_path)

    if kind == "changes":
        return _query_changes(repo_path, full)

    if kind == "search-codebase":
        if target is None:
            print("error: query type 'search-codebase' requires a natural-language query")
            return 1
        from aletheore.search_index import (
            IndexDimensionMismatchError,
            IndexNotFoundError,
            search_index,
        )

        try:
            result = search_index(Path(repo_path).resolve(), target, k=k, language=language)
        except IndexNotFoundError as exc:
            console.print(f"[bold red]error:[/bold red] {exc}")
            return 1
        except IndexDimensionMismatchError as exc:
            console.print(f"[bold red]error:[/bold red] {exc}")
            return 1
        print(to_toon({"result": result}))
        return 0

    if kind == "answer":
        if target is None:
            print("error: query type 'answer' requires a natural-language question")
            return 1
        try:
            adapter = select_adapter(
                KNOWN_ADAPTERS, forced_name=forced_agent, interactive=sys.stdin.isatty()
            )
        except (NoAdapterAvailableError, AmbiguousAdapterError) as exc:
            console.print(f"[bold red]error:[/bold red] {exc}")
            return 1
        if adapter.requires_consent:
            console.print(
                f"[bold yellow]This will send retrieved code chunks and your question "
                f"to {adapter.name}'s API.[/bold yellow]"
            )
            if input("Continue? [y/N]: ").strip().lower() != "y":
                console.print("Cancelled - no data was sent.")
                return 0
        from aletheore.answer import answer_question
        from aletheore.search_index import IndexDimensionMismatchError, IndexNotFoundError

        try:
            result = answer_question(Path(repo_path).resolve(), target, adapter, k=k)
        except IndexNotFoundError as exc:
            console.print(f"[bold red]error:[/bold red] {exc}")
            return 1
        except IndexDimensionMismatchError as exc:
            console.print(f"[bold red]error:[/bold red] {exc}")
            return 1
        print(to_toon({"result": result}))
        return 0

    repo = Path(repo_path).resolve()
    try:
        evidence = load_evidence(repo)
    except (FileNotFoundError, IncompatibleEvidenceVersionError) as exc:
        print(f"error: {exc}")
        return 1

    if kind == "symbol-source":
        if target is None or symbol is None:
            print("error: query type 'symbol-source' requires module and symbol arguments")
            return 1
        try:
            result = find_symbol_source(evidence, repo, target, symbol)
        except (ModuleNotFoundInEvidenceError, SymbolNotFoundInEvidenceError) as exc:
            print(f"error: {exc}")
            return 1
        print(to_toon({"result": result}))
        return 0

    func, requires_target = QUERY_FUNCTIONS[kind]
    if requires_target and target is None:
        print(f"error: query type '{kind}' requires a target argument")
        return 1

    try:
        if kind in ("evidence-for-endpoint", "evidence-for-symbol", "evidence-for-dependency"):
            result = func(evidence, target, repo)
        else:
            result = func(evidence, target)
    except (ModuleNotFoundInEvidenceError, BranchNotFoundInEvidenceError) as exc:
        print(f"error: {exc}")
        return 1

    print(json.dumps(result, indent=2))
    return 0


def _diff(
    old_path: str,
    new_path: str,
    full: bool,
    fail_on_new_secrets: bool,
    fail_on_new_vulnerabilities: bool = False,
    fail_on_new_layer_violations: bool = False,
    output_format: str = "json",
) -> int:
    if output_format not in ("json", "sarif"):
        print(f"error: unknown --format {output_format!r} (expected 'json' or 'sarif')")
        return 1
    if output_format == "sarif" and full:
        print("error: --format sarif is incompatible with --full (SARIF needs the curated diff)")
        return 1

    old_file = Path(old_path)
    new_file = Path(new_path)

    if not old_file.exists():
        print(f"error: evidence file not found: {old_file}")
        return 1
    if not new_file.exists():
        print(f"error: evidence file not found: {new_file}")
        return 1

    try:
        old = load_evidence_file(old_file)
    except json.JSONDecodeError:
        print(f"error: {old_file} is not valid JSON")
        return 1
    except IncompatibleEvidenceVersionError as exc:
        print(f"error: {exc}")
        return 1
    try:
        new = load_evidence_file(new_file)
    except json.JSONDecodeError:
        print(f"error: {new_file} is not valid JSON")
        return 1
    except IncompatibleEvidenceVersionError as exc:
        print(f"error: {exc}")
        return 1

    diff = compute_diff(old, new, full=full)
    print(json.dumps(to_sarif(diff) if output_format == "sarif" else diff, indent=2))

    if fail_on_new_secrets or fail_on_new_vulnerabilities or fail_on_new_layer_violations:
        curated = diff if not full else compute_diff(old, new, full=False)
        should_fail = False

        if fail_on_new_secrets:
            new_real_secrets = [
                f
                for f in curated["secrets"]["new"]
                if not f.get("likely_placeholder", False) and not f.get("accepted", False)
            ]
            new_real_history_secrets = [
                f
                for f in curated["history_secrets"]["new"]
                if not f.get("likely_placeholder", False) and not f.get("accepted", False)
            ]
            should_fail = should_fail or bool(new_real_secrets or new_real_history_secrets)

        if fail_on_new_vulnerabilities:
            should_fail = should_fail or bool(curated["vulnerabilities"]["new"])

        if fail_on_new_layer_violations:
            should_fail = should_fail or bool(curated["layer_violations"]["new"])

        if should_fail:
            return 1

    return 0


def _verify(report_path: str, repo_path: str) -> int:
    report_file = Path(report_path)
    if not report_file.exists():
        console.print(f"[bold red]error:[/bold red] report file not found: {report_file}")
        return 1

    repo = Path(repo_path).resolve()
    evidence = load_verifiable_evidence(repo)
    if evidence is None:
        evidence_path = repo / ".aletheore" / "air.json"
        console.print(
            f"[bold red]error:[/bold red] no usable evidence at {evidence_path} - "
            f"run 'aletheore scan {repo}' first"
        )
        return 1

    report_text = report_file.read_text()
    result = verify_citations(report_text, evidence, fetch_line_count=local_line_count_fetcher(repo))
    total = result["total_citations"]
    verified = len(result["verified"])
    unverified = result["unverified"]

    if total == 0:
        console.print(f"No `file:line` citations found in {report_file}.")
        return 0

    console.print(f"{verified} of {total} citations in {report_file} verified against {repo}.")
    if unverified:
        console.print("[bold red]Unverified citations:[/bold red]")
        for citation in unverified:
            console.print(f"  - {citation['file']}:{citation['line']}")
        return 1

    console.print("[bold green]All citations verified.[/bold green]")
    return 0


def _healthcheck(repo_path: str, base_url: str) -> int:
    repo = Path(repo_path).resolve()
    try:
        evidence = load_evidence(repo)
    except (FileNotFoundError, IncompatibleEvidenceVersionError) as exc:
        print(f"error: {exc}")
        return 1

    endpoints = evidence["repository"].get("api_endpoints", {}).get("endpoints", [])
    try:
        result = run_healthcheck(endpoints, base_url)
    except ValueError as exc:
        print(f"error: {exc}")
        return 1
    save_healthcheck(result, repo)

    for entry in result["results"]:
        method = entry.get("method") or "?"
        if entry.get("skipped"):
            print(f"{method:6} {entry['path']:40} SKIPPED ({entry['reason']})")
        else:
            status = entry["status_code"] if entry["reachable"] else "UNREACHABLE"
            note = f" ({entry['note']})" if entry.get("note") else ""
            print(f"{method:6} {entry['path']:40} {status} {entry['latency_ms']}ms{note}")

    return 0


def _mcp(repo_path: str, forced_agent: str | None = None) -> int:
    from aletheore.mcp_server import build_server

    repo = Path(repo_path).resolve()
    answer_adapter = None
    if forced_agent is not None:
        try:
            answer_adapter = select_adapter(
                KNOWN_ADAPTERS, forced_name=forced_agent, interactive=False
            )
        except (NoAdapterAvailableError, AmbiguousAdapterError) as exc:
            console.print(f"[bold red]error:[/bold red] {exc}")
            return 1
    server = build_server(repo, answer_adapter=answer_adapter)
    # stderr, never stdout - an MCP client treats this process's stdout as the
    # JSON-RPC channel from the moment it starts, so anything written there
    # that isn't a protocol message would corrupt the stream.
    print(
        "MCP server ready, waiting for a client on stdio "
        "(this process produces no further output until one connects)",
        file=sys.stderr,
    )
    server.run(transport="stdio")
    return 0


def _aletheore_command() -> str:
    # A bare "aletheore" only resolves if the launching process inherits the
    # same PATH the install happened under - true for a terminal-launched
    # coding tool, false for most GUI-launched ones (they don't source
    # .zshrc/.bashrc, so a pip-installed-in-a-venv or pipx-installed
    # aletheore silently isn't on PATH from the tool's point of view, and
    # the MCP server fails to start with no explanation). Writing the
    # already-resolved absolute path removes that dependency entirely.
    #
    # Prefer the console script sitting next to *this exact* interpreter
    # (sys.executable) over a bare shutil.which() PATH search: which()
    # returns whatever "aletheore" resolves to on the current PATH, which
    # can be a different install than the one actually running this
    # command right now (e.g. this venv's binary invoked by absolute path
    # without activating the venv first, while some other aletheore sits
    # earlier on PATH) - sys.executable is never ambiguous like that.
    # Falls back to a PATH search, then the bare name, only if neither
    # resolves - preserving today's behavior rather than writing something
    # obviously broken.
    sibling = Path(sys.executable).parent / "aletheore"
    if sibling.exists():
        return str(sibling)
    return shutil.which("aletheore") or "aletheore"


def _stdio_entry(repo_path: Path, include_type: bool) -> dict:
    entry: dict = {"command": _aletheore_command(), "args": ["mcp", str(repo_path)]}
    if include_type:
        entry = {"type": "stdio", **entry}
    return entry


def _opencode_entry(repo_path: Path) -> dict:
    return {"type": "local", "command": [_aletheore_command(), "mcp", str(repo_path)], "enabled": True}


_MCP_CLIENT_CONFIGS: dict[str, tuple[str, str, Callable[[Path], dict]]] = {
    "claude-code": (".mcp.json", "mcpServers", lambda p: _stdio_entry(p, include_type=True)),
    "cursor": (".cursor/mcp.json", "mcpServers", lambda p: _stdio_entry(p, include_type=False)),
    "vscode": (".vscode/mcp.json", "servers", lambda p: _stdio_entry(p, include_type=True)),
    "kiro": (".kiro/settings/mcp.json", "mcpServers", lambda p: _stdio_entry(p, include_type=False)),
    "opencode": ("opencode.json", "mcp", _opencode_entry),
}


def _write_json_mcp_client_config(config_path: Path, top_level_key: str, entry: dict) -> str:
    if config_path.exists():
        try:
            data = json.loads(config_path.read_text())
        except json.JSONDecodeError:
            return f"skipped (existing file is not valid JSON): {config_path}"
        if not isinstance(data, dict):
            return f"skipped (existing file's top level is not a JSON object): {config_path}"
    else:
        data = {}

    servers = data.get(top_level_key, {})
    if not isinstance(servers, dict):
        return f"skipped (existing '{top_level_key}' is not a JSON object): {config_path}"

    already_present = "aletheore" in servers
    servers["aletheore"] = entry
    data[top_level_key] = servers

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(data, indent=2) + "\n")
    return f"{'updated' if already_present else 'wrote'} {config_path}"


def _write_toml_mcp_client_config(config_path: Path, top_level_key: str, entry: dict) -> str:
    if config_path.exists():
        try:
            data = tomllib.loads(config_path.read_text())
        except tomllib.TOMLDecodeError:
            return f"skipped (existing file is not valid TOML): {config_path}"
        if not isinstance(data, dict):
            return f"skipped (existing file's top level is not a TOML table): {config_path}"
    else:
        data = {}

    servers = data.get(top_level_key, {})
    if not isinstance(servers, dict):
        return f"skipped (existing '{top_level_key}' is not a TOML table): {config_path}"

    already_present = "aletheore" in servers
    servers["aletheore"] = entry
    data[top_level_key] = servers

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(tomli_w.dumps(data))
    return f"{'updated' if already_present else 'wrote'} {config_path}"


def _mcp_install(path: str, targets: list[str]) -> int:
    repo_path = Path(path).resolve()
    all_targets = [*_MCP_CLIENT_CONFIGS.keys(), "codex-cli"]
    selected = targets or all_targets
    unknown = [target for target in selected if target not in all_targets]
    if unknown:
        console.print(
            f"[bold red]error:[/bold red] unknown target(s): {', '.join(unknown)}. "
            f"Valid targets: {', '.join(all_targets)}"
        )
        return 1

    for target in selected:
        if target == "codex-cli":
            config_path = repo_path / ".codex" / "config.toml"
            entry = {"command": _aletheore_command(), "args": ["mcp", str(repo_path)]}
            message = _write_toml_mcp_client_config(config_path, "mcp_servers", entry)
        else:
            relative_path, top_level_key, entry_builder = _MCP_CLIENT_CONFIGS[target]
            config_path = repo_path / relative_path
            entry = entry_builder(repo_path)
            message = _write_json_mcp_client_config(config_path, top_level_key, entry)
        console.print(f"[bold green]{target}[/bold green]: {message}")

    console.print(
        "\nRestart or reload your coding tool so it picks up the new MCP server - "
        "Aletheore's tools will then be available without running 'aletheore mcp' yourself."
    )
    if "claude-code" in selected:
        console.print(
            "\n[bold]Claude Code:[/bold] .mcp.json above is picked up automatically on reload - "
            "no extra step needed. Prefer registering it yourself instead (or want a command to "
            "verify the same server), paste this:"
        )
        # soft_wrap: this is a copy-pasteable shell command containing a full
        # repo path - Rich's default wrapping inserts a real newline into
        # long text at the console width, which would corrupt the command if
        # pasted. Same reasoning as _print_result above.
        console.print(f"  claude mcp add aletheore -- {_aletheore_command()} mcp {repo_path}", soft_wrap=True)
    console.print(
        "\n[bold]PyCharm / other JetBrains IDEs:[/bold] not auto-configured - there's no single "
        "stable, documented file format to script against yet. Instead: open Settings | Tools | "
        "AI Assistant | Model Context Protocol, and use \"Import a Claude MCP config\", pointing "
        "at the .mcp.json written above."
    )
    console.print(
        "[bold]vim / Neovim / Emacs / other terminal editors:[/bold] no native MCP client exists "
        "in any of them - support depends entirely on whichever AI plugin you have installed "
        "(e.g. avante.nvim, codecompanion.nvim). Point that plugin's own MCP config at: "
        f"aletheore mcp {repo_path}"
    )
    console.print(
        "[bold]OpenAI Codex CLI:[/bold] wrote .codex/config.toml, but Codex only reads "
        "project-scoped MCP config for projects it already trusts - if the tools don't show up, "
        "check Codex's own trust prompt for this directory. Also note: writing this file "
        "reformats it - any hand-written comments in an existing config.toml are not preserved."
    )
    return 0


def _port_is_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


def _watch(repo_path: str, debounce: float) -> int:
    from aletheore.watch import watch as run_watch

    repo = Path(repo_path).resolve()

    def report(message: str) -> None:
        console.print(f"[dim]{time.strftime('%H:%M:%S')}[/dim] {message}")

    try:
        run_watch(repo, report, debounce_seconds=debounce)
    except KeyboardInterrupt:
        # Ctrl-C is how this command is meant to end, so it exits 0 with a
        # word rather than a traceback - the dashboard's own Ctrl-C handling
        # was a bug worth not repeating.
        console.print("stopped")
    return 0


def _dashboard(repo_path: str, port: int) -> int:
    from aletheore.dashboard import build_app

    repo = Path(repo_path).resolve()
    host = "127.0.0.1"

    # Checked and reported *before* printing success or opening a browser tab -
    # otherwise a stale process already bound to this port (e.g. a dashboard
    # left running for a different repo) silently answers instead, and a
    # browser reload looks like a normal working dashboard while actually
    # showing a completely unrelated repo's data. Confirmed as a real bug,
    # not hypothetical: this exact sequence was hit against a real stale
    # process on the default port.
    if not _port_is_available(host, port):
        console.print(
            f"[bold red]error:[/bold red] port {port} is already in use - probably another "
            f"aletheore dashboard (or something else) is already bound to it.\n"
            f"Pass --port to use a different one, or stop whatever's using {port}."
        )
        return 1

    app = build_app(repo)
    url = f"http://{host}:{port}"
    console.print(f"[green]Dashboard running at[/green] {url}")
    webbrowser.open(url)

    # A plain uvicorn.run() hung on Ctrl-C for as long as a browser tab was
    # open: the dashboard's /events SSE stream never ends on its own, so
    # uvicorn sat at "Waiting for connections to close" forever and the user
    # had to press Ctrl-C a second time - which force-quits mid-shutdown and
    # printed two tracebacks. Reproduced directly: one SIGINT produced zero
    # tracebacks and never exited.
    #
    # sse_starlette is supposed to handle this itself and does not (see
    # dashboard._sleep_unless_shutting_down for the ContextVar bug in 3.0.3),
    # so the signal is ours. Starlette's own on_shutdown hook is too late -
    # uvicorn waits for connections to close *before* running lifespan
    # shutdown, which is the exact wait being blocked. handle_exit fires at
    # signal time, which is early enough for the stream to notice and end.
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port))
    original_handle_exit = server.handle_exit

    def handle_exit(sig, frame):  # noqa: ANN001 - matches uvicorn's signature
        app.state.shutdown_event.set()
        original_handle_exit(sig, frame)

    server.handle_exit = handle_exit
    server.run()
    return 0


app = typer.Typer(
    name="aletheore",
    help="Evidence-grounded repository audit — a deterministic scanner, MCP server, live "
    "dashboard, and a GitHub Action that posts PR diffs.",
    add_completion=True,
    no_args_is_help=False,
)


def _version_callback(value: bool) -> None:
    if value:
        import importlib.metadata

        console.print(f"aletheore {importlib.metadata.version('aletheore')}")
        raise typer.Exit(code=0)


@app.callback(invoke_without_command=True)
def _main_callback(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="show the installed version and exit",
    ),
) -> None:
    if ctx.invoked_subcommand is None:
        console.print(_banner_panel())
        _print_update_notice_if_available()
        raise typer.Exit(code=0)


@app.command(help="audit a repository")
def audit(
    path: str = typer.Argument(".", help="repository path"),
    path_option: Optional[str] = _PATH_OPTION,
    agent: Optional[str] = typer.Option(
        None, "--agent", help="force a specific agent adapter by name (ignored with --managed)"
    ),
    managed: bool = typer.Option(
        False,
        "--managed",
        help="run the audit using Aletheore's shared managed key instead of BYOK",
    ),
    token: Optional[str] = typer.Option(
        None,
        "--token",
        help="managed-audit API token, or set ALETHEORE_API_TOKEN (only has effect with --managed)",
    ),
    check_vulnerabilities: Optional[bool] = typer.Option(
        None,
        "--check-vulnerabilities/--no-check-vulnerabilities",
        help="OSV.dev dependency-vulnerability check (on by default, or set by "
        ".aletheore.json's disabled_checks)",
    ),
    scan_git_history: Optional[bool] = typer.Option(
        None,
        "--scan-git-history/--no-scan-git-history",
        help="walk git history for secrets (on by default, or set by "
        ".aletheore.json's disabled_checks)",
    ),
    check_licenses: Optional[bool] = typer.Option(
        None,
        "--check-licenses/--no-check-licenses",
        help="dependency-license check (on by default, or set by .aletheore.json's disabled_checks)",
    ),
    map_schema: Optional[bool] = typer.Option(
        None,
        "--map-schema/--no-map-schema",
        help="map database schema from migrations (paid plans; on by default when entitled)",
    ),
    map_endpoints: Optional[bool] = typer.Option(
        None,
        "--map-endpoints/--no-map-endpoints",
        help="static API endpoint mapping (on by default, or set by .aletheore.json's disabled_checks)",
    ),
) -> None:
    path = _resolve_path(path, path_option)
    if managed:
        if agent is not None:
            console.print(
                "[bold yellow]warning:[/bold yellow] --agent has no effect with --managed "
                "(the managed audit always uses Aletheore's own service, not a local agent) - ignored."
            )
        raise typer.Exit(
            code=_managed_audit(
                path,
                token,
                check_vulnerabilities,
                scan_git_history,
                check_licenses,
                map_endpoints,
            )
        )
    if token is not None:
        console.print(
            "[bold yellow]warning:[/bold yellow] --token has no effect without --managed - ignored."
        )
    raise typer.Exit(
        code=_audit(path, agent, check_vulnerabilities, scan_git_history, check_licenses, map_endpoints)
    )


@app.command(help="run only the deterministic scan phase")
def scan(
    path: str = typer.Argument(".", help="repository path"),
    path_option: Optional[str] = _PATH_OPTION,
    check_vulnerabilities: Optional[bool] = typer.Option(
        None,
        "--check-vulnerabilities/--no-check-vulnerabilities",
        help="OSV.dev dependency-vulnerability check (on by default, or set by "
        ".aletheore.json's disabled_checks)",
    ),
    scan_git_history: Optional[bool] = typer.Option(
        None,
        "--scan-git-history/--no-scan-git-history",
        help="walk git history for secrets (on by default, or set by "
        ".aletheore.json's disabled_checks)",
    ),
    check_licenses: Optional[bool] = typer.Option(
        None,
        "--check-licenses/--no-check-licenses",
        help="dependency-license check (on by default, or set by .aletheore.json's disabled_checks)",
    ),
    map_schema: Optional[bool] = typer.Option(
        None,
        "--map-schema/--no-map-schema",
        help="map database schema from migrations (paid plans; on by default when entitled)",
    ),
    map_endpoints: Optional[bool] = typer.Option(
        None,
        "--map-endpoints/--no-map-endpoints",
        help="static API endpoint mapping (on by default, or set by .aletheore.json's disabled_checks)",
    ),
) -> None:
    path = _resolve_path(path, path_option)
    exit_code, _evidence, _evidence_path = _scan(
        path, check_vulnerabilities, scan_git_history, check_licenses, map_endpoints, map_schema
    )
    raise typer.Exit(code=exit_code)


@app.command(
    help="re-scan and re-index automatically whenever source files change"
)
def watch(
    path: str = typer.Argument(".", help="repository path"),
    path_option: Optional[str] = _PATH_OPTION,
    debounce: float = typer.Option(
        WATCH_DEBOUNCE_SECONDS,
        "--debounce",
        help="seconds of quiet before rebuilding, so one burst of saves is one rebuild",
    ),
) -> None:
    path = _resolve_path(path, path_option)
    raise typer.Exit(code=_watch(path, debounce))


@app.command(help="scaffold a .aletheore.json config file in a repository")
def init(
    path: str = typer.Argument(".", help="repository path"),
    path_option: Optional[str] = _PATH_OPTION,
) -> None:
    path = _resolve_path(path, path_option)
    config_path = Path(path) / ".aletheore.json"
    if config_path.exists():
        console.print(f"[bold red]error:[/bold red] {config_path} already exists - not overwriting it.")
        raise typer.Exit(code=1)

    default_config = {
        "layer_markers": {},
        "cluster_resolution": 1.0,
        "dead_code_entry_points": [],
        "accepted_secrets": [],
        "ignored_paths": [],
        "disabled_checks": [],
        "severity_threshold": None,
    }
    config_path.write_text(json.dumps(default_config, indent=2) + "\n")
    console.print(f"[bold green]Wrote {config_path}[/bold green]")
    # A Table (not console.print per key) so a description that wraps to a
    # second line lands under the key column instead of the terminal's left
    # edge - plain console.print has no concept of a hanging indent.
    keys = Table.grid(padding=(0, 2, 0, 0))
    keys.add_column(style="bold", no_wrap=True)
    keys.add_column()
    keys.add_row(
        "  layer_markers",
        "folder-name -> layer-order int, for custom layer-violation conventions "
        '(e.g. {"domain": 0, "infrastructure": 2})',
    )
    keys.add_row("  cluster_resolution", "tunes architecture cluster detection (default 1.0)")
    keys.add_row("  dead_code_entry_points", "extra file paths to treat as entry points")
    keys.add_row(
        "  accepted_secrets", "baseline of reviewed secret findings to suppress (leave empty for now)"
    )
    keys.add_row(
        "  ignored_paths",
        'glob patterns excluded from every check (e.g. ["vendor/**", "*.gen.go"])',
    )
    keys.add_row(
        "  disabled_checks",
        f"checks to skip by default: {', '.join(sorted(DISABLEABLE_CHECKS))}",
    )
    keys.add_row(
        "  severity_threshold",
        "critical/high/medium/low - filters dependency-vulnerability findings in PR "
        "comments only (evidence.json always keeps everything)",
    )
    console.print(keys)


@app.command(
    help=(
        "build a local semantic search index over the repository's code "
        "(requires a prior 'aletheore scan'; embeds via a local Ollama instance, "
        "falling back to OpenAI if Ollama is unavailable - needed by 'query search-codebase'/"
        "'query answer' and the aletheore_search_codebase/aletheore_answer MCP tools)"
    )
)
def index(
    path: str = typer.Argument(".", help="repository path"),
    path_option: Optional[str] = _PATH_OPTION,
) -> None:
    path = _resolve_path(path, path_option)
    raise typer.Exit(code=_index(path))


@app.command(help="query an existing air.json")
def query(
    # Optional rather than required: a bare `aletheore query` previously hit
    # Typer's "Missing argument 'KIND'", which names no kinds at all, leaving
    # 23 capabilities discoverable only via --help. Defaulting to None turns
    # the most likely first invocation into the listing the user was after.
    # Exit code stays 0 - asking what the kinds are is a successful question,
    # not a usage error. An *unknown* kind still exits 1, via _query.
    kind: Optional[str] = typer.Argument(
        None, help="one of the 23 query kinds; omit to list them by category"
    ),
    target: Optional[str] = typer.Argument(None, help="target for kinds that need one (a file path, branch name, ...)"),
    symbol: Optional[str] = typer.Argument(None, help="symbol name for 'symbol-source'"),
    repo_path: str = typer.Option(".", "--path", help="repository path"),
    full: bool = typer.Option(
        False, "--full", help="show the full raw diff instead of the curated summary (only 'changes')"
    ),
    agent: Optional[str] = typer.Option(None, "--agent", help="provider for 'answer'"),
    k: int = typer.Option(10, "--k", help="number of semantic search results"),
    language: Optional[str] = typer.Option(
        None, "--language", help="restrict 'search-codebase' to one language, e.g. python"
    ),
) -> None:
    if kind is None:
        console.print(_query_kinds_panel())
        raise typer.Exit(code=0)
    raise typer.Exit(code=_query(kind, target, repo_path, full, agent, k, symbol, language))


@app.command(help="compare two air.json files")
def diff(
    old: str = typer.Argument(..., help="path to the baseline air.json"),
    new: str = typer.Argument(..., help="path to the comparison air.json"),
    full: bool = typer.Option(False, "--full", help="show the full raw diff instead of the curated summary"),
    output_format: str = typer.Option(
        "json", "--format", help="output format: 'json' (default) or 'sarif' for code-scanning tools"
    ),
    fail_on_new_secrets: bool = typer.Option(
        False,
        "--fail-on-new-secrets",
        help="exit 1 if a new real (non-placeholder) secret finding appears",
    ),
    fail_on_new_vulnerabilities: bool = typer.Option(
        False,
        "--fail-on-new-vulnerabilities",
        help="exit 1 if a new dependency vulnerability finding appears",
    ),
    fail_on_new_layer_violations: bool = typer.Option(
        False,
        "--fail-on-new-layer-violations",
        help="exit 1 if a new layer-convention violation appears",
    ),
) -> None:
    raise typer.Exit(
        code=_diff(
            old,
            new,
            full,
            fail_on_new_secrets,
            fail_on_new_vulnerabilities,
            fail_on_new_layer_violations,
            output_format,
        )
    )


@app.command(help="check a report's file:line citations against a repository's evidence")
def verify(
    report: str = typer.Argument(..., help="path to a markdown report to check"),
    repo_path: str = typer.Option(".", "--path", help="repository the report's citations refer to"),
) -> None:
    """Works on a report from any tool, not just aletheore's own audit -
    it only reads the report's text and the repo's own air.json, both of
    which are just files. Exits 1 if any citation can't be verified, for
    use as a CI gate on hand-written or third-party reports too."""
    raise typer.Exit(code=_verify(report, repo_path))


@app.command(help="run an MCP server scoped to a repository")
def mcp(
    path: str = typer.Argument(".", help="repository path"),
    path_option: Optional[str] = _PATH_OPTION,
    agent: Optional[str] = typer.Option(None, "--agent", help="provider for the aletheore_answer tool"),
) -> None:
    path = _resolve_path(path, path_option)
    raise typer.Exit(code=_mcp(path, agent))


@app.command(
    name="mcp-install",
    help="write MCP client config so a coding agent auto-launches this repo's MCP server",
)
def mcp_install(
    path: str = typer.Argument(".", help="repository path"),
    path_option: Optional[str] = _PATH_OPTION,
    target: list[str] = typer.Option(
        [],
        "--target",
        help=(
            "which client(s) to configure (default: all); one of: "
            f"{', '.join([*_MCP_CLIENT_CONFIGS.keys(), 'codex-cli'])}"
        ),
    ),
) -> None:
    path = _resolve_path(path, path_option)
    raise typer.Exit(code=_mcp_install(path, target))


@app.command(help="run a live local dashboard scoped to a repository")
def dashboard(
    path: str = typer.Argument(".", help="repository path"),
    path_option: Optional[str] = _PATH_OPTION,
    port: int = typer.Option(8420, "--port", help="port to serve the dashboard on"),
) -> None:
    path = _resolve_path(path, path_option)
    raise typer.Exit(code=_dashboard(path, port))


@app.command(help="GET-only live health check of mapped API endpoints")
def healthcheck(
    path: str = typer.Argument(".", help="repository path"),
    path_option: Optional[str] = _PATH_OPTION,
    # Optional rather than required so the missing case can explain itself.
    # Typer's own required-option error is a bare "Missing option
    # '--base-url'", which says nothing about what a base URL is for here -
    # this command probes endpoints already mapped in evidence against a
    # *running* instance, so the value is the root of a server the user has
    # started, not the repository and not a URL found in the code.
    base_url: Optional[str] = typer.Option(
        None,
        "--base-url",
        help="base URL of the running instance to check, e.g. http://localhost:8000",
    ),
) -> None:
    path = _resolve_path(path, path_option)
    if base_url is None:
        console.print(
            "[bold red]error:[/bold red] --base-url is required - it is the root URL of a "
            "*running* instance of this repo, which healthcheck probes using the endpoints "
            "already mapped in .aletheore/air.json."
        )
        console.print("\n  aletheore healthcheck . --base-url http://localhost:8000", style="cyan")
        console.print(
            "\nOnly GET endpoints are probed, and no request body is ever sent.", style="dim"
        )
        raise typer.Exit(code=1)
    raise typer.Exit(code=_healthcheck(path, base_url))


@app.command(help="authenticate with GitHub via device flow and save a personal API token")
def login() -> None:
    from aletheore.credentials import save_api_token
    from aletheore.device_auth import (
        DeviceFlowError,
        mint_cli_token,
        poll_for_access_token,
        request_device_code,
        resolve_installation,
    )

    try:
        code = request_device_code()
        console.print("First, authenticate with GitHub:")
        console.print(f"  1. Go to: [bold]{code.verification_uri}[/bold]")
        console.print(f"  2. Enter code: [bold cyan]{code.user_code}[/bold cyan]")
        console.print("Waiting for authorization...")
        github_token = poll_for_access_token(code)

        resolved = resolve_installation(github_token)
        if isinstance(resolved, dict):
            installation = resolved
        else:
            console.print("Multiple paid installations found - pick one:")
            for index, candidate in enumerate(resolved, start=1):
                console.print(f"  {index}. {candidate['account_login']}")
            while True:
                raw = input(f"Enter a number [1-{len(resolved)}]: ").strip()
                if raw.isdigit() and 1 <= int(raw) <= len(resolved):
                    installation = resolved[int(raw) - 1]
                    break
                console.print(
                    f"[bold red]error:[/bold red] enter a number between 1 and {len(resolved)}"
                )

        label = f"{socket.gethostname()} (device flow)"
        token = mint_cli_token(github_token, installation["installation_id"], label)
        save_api_token("aletheore-managed-audit", token)
        console.print(
            f"[bold green]Logged in.[/bold green] Token saved for "
            f"[bold]{installation['account_login']}[/bold]. "
            "This replaces any previously saved token."
        )
    except DeviceFlowError as exc:
        console.print(f"[bold red]error:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc


@app.command(help="clear the locally saved managed-audit API token")
def logout() -> None:
    import aletheore.credentials as credentials

    removed = credentials.clear_api_key(
        "aletheore-managed-audit",
        credentials_path=credentials.DEFAULT_CREDENTIALS_PATH,
    )
    if removed:
        console.print("[bold green]Logged out.[/bold green] Saved token removed.")
    else:
        console.print("Not logged in - nothing to clear.")


@app.command(help="show installed version, update availability, and login state")
def status() -> None:
    import importlib.metadata

    import aletheore.credentials as credentials

    installed_version = importlib.metadata.version("aletheore")
    version_note = _check_for_update(installed_version)
    console.print(f"Aletheore v{installed_version} ({version_note})")

    if not credentials.has_api_key(
        "ALETHEORE_API_TOKEN",
        "aletheore-managed-audit",
        credentials_path=credentials.DEFAULT_CREDENTIALS_PATH,
    ):
        console.print("Not logged in - run [bold]aletheore login[/bold]")
        return

    token = credentials.get_api_key(
        "ALETHEORE_API_TOKEN",
        "aletheore-managed-audit",
        credentials_path=credentials.DEFAULT_CREDENTIALS_PATH,
        prompt_fn=lambda _msg: "",
    )
    who = _fetch_whoami(token)
    if who is None:
        console.print("A token is saved locally, but it couldn't be verified right now.")
    else:
        console.print(f"Logged in as: [bold]{who['account_login']}[/bold] ({who['plan']} plan)")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
