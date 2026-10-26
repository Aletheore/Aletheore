from __future__ import annotations

import difflib
import hashlib
import json
import os
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


import click
import tomli_w
import typer
import typer.rich_utils as _typer_rich_utils
import uvicorn
from rich.console import Console, Group
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from aletheore._lazy import lazy_module
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
    format_citation,
    load_verifiable_evidence,
    local_line_count_fetcher,
    verify_citations,
)
from aletheore.credentials import HOSTED_EMBEDDINGS_DISCLOSURE, get_api_key
from aletheore.device_auth import infer_repo_full_name_from_cwd_git_remote
from aletheore.preferences import (
    has_shown_crash_reporting_notice,
    is_crash_reporting_enabled,
    crash_reporting_env_override,
    mark_crash_reporting_notice_shown,
    set_crash_reporting_enabled,
    stored_crash_reporting_enabled,
)
from aletheore import skill_hub
from rich.markup import escape as escape_markup
from aletheore.sentry_reporting import init_cli_sentry
from aletheore.evidence import (
    EvidenceWriteError,
    IncompatibleEvidenceVersionError,
    MalformedEvidenceError,
    check_evidence_writable,
    load_evidence,
    load_evidence_file,
    scan_repository,
    write_evidence,
)
from aletheore.git_intel.analyzer import GIT_ANALYSIS_RESOURCE_EXIT_CODE, GitAnalysisError
from aletheore.healthcheck import is_healthy_status, run_healthcheck, save_healthcheck
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
from aletheore.toon_encoding import ToonEncodingError, to_toon
from aletheore.watch import DEBOUNCE_SECONDS as WATCH_DEBOUNCE_SECONDS
from aletheore.watch import WATCH_ENV_VAR, watching_disabled_by_env
from aletheore.user_paths import user_home

httpx = lazy_module("httpx")
sentry_sdk = lazy_module("sentry_sdk")

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

_typer_rich_utils.COLOR_SYSTEM = None  # monochrome --help as well
for _name in ("STYLE_OPTION", "STYLE_SWITCH", "STYLE_NEGATIVE_OPTION", "STYLE_NEGATIVE_SWITCH", "STYLE_METAVAR", "STYLE_USAGE", "STYLE_COMMANDS_TABLE_FIRST_COLUMN"):
    if hasattr(_typer_rich_utils, _name):
        setattr(_typer_rich_utils, _name, "bold")
console = Console(color_system=None)  # black and white: bold, dim and reverse only, on any terminal theme
# Crash-reporting notices only (main()'s first-run notice and "this was
# reported" line) - never for command output. Final-review finding:
# `aletheore mcp` uses stdout as a JSON-RPC protocol channel (see its own
# comment below), and `diff`/`--format sarif` output is routinely
# redirected or piped - an unrelated line on stdout would corrupt either.
_stderr_console = Console(stderr=True, color_system=None)

# Grouped rather than flat because this list is the CLI's only map of what
# `query` can actually do - every kind printed as one comma-separated run is
# unreadable, and a bare `aletheore query` previously got Typer's "Missing
# argument 'KIND'" with no kinds named at all. The grouping is also the
# answer to "why so few commands": every kind below reads the same air.json,
# which is the point, so they belong under one verb rather than each being
# promoted to its own top-level command.
QUERY_KIND_GROUPS: dict[str, list[str]] = {
    "Structure": [
        "imports",
        "imported-by",
        "symbols",
        "cluster",
        "layer-violations",
        "dead-code",
        "schema",
        "ast-pattern",
    ],
    "Security": ["secrets", "vulnerabilities", "licenses", "static-analysis"],
    "Runtime": ["endpoints", "database", "infrastructure", "environment-variables", "error-handling"],
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
    if option is None:
        return positional
    if positional != "." and Path(positional).resolve() != Path(option).resolve():
        _stderr_console.print(
            f"[bold red]error:[/bold red] the PATH argument ({positional!r}) and --path ({option!r}) "
            "name different folders; give only one of them"
        )
        raise typer.Exit(code=2)
    return option


def _load_evidence_or_exit(repo: Path) -> dict:
    """The repo's evidence, or a one-line `error:` on stderr and exit 1 (never a traceback, and the
    'run aletheore scan' hint exactly once)."""
    if not repo.exists():
        _stderr_console.print(f"[bold red]error:[/bold red] path does not exist: {escape(str(repo))}")
        raise typer.Exit(code=1)
    if not repo.is_dir():
        _stderr_console.print(f"[bold red]error:[/bold red] path is not a directory: {escape(str(repo))}")
        raise typer.Exit(code=1)
    try:
        return load_evidence(repo)
    except (FileNotFoundError, IncompatibleEvidenceVersionError, MalformedEvidenceError) as exc:
        hint = "" if "aletheore scan" in str(exc) else " (run 'aletheore scan' first)"
        print(f"error: {exc}{hint}", file=sys.stderr)
        raise typer.Exit(code=1)


def _checked_dir(path: str) -> str:
    """Exit with a one-line error, not a traceback, when PATH isn't a directory."""
    p = Path(path)
    if not p.exists():
        _stderr_console.print(f"[bold red]error:[/bold red] path does not exist: {escape(str(p))}")
        raise typer.Exit(code=1)
    if not p.is_dir():
        _stderr_console.print(f"[bold red]error:[/bold red] path is not a directory: {escape(str(p))}")
        raise typer.Exit(code=1)
    return path


_YES_OPTION = typer.Option(
    False,
    "--yes",
    "-y",
    help="don't ask before using a folder that doesn't look like a project (your home folder, Desktop, ...)",
)


def _workspace_guard(path: str, yes: bool) -> None:
    """Stop and ask before a scan (or a config write) lands in a folder that is clearly not a project.

    Running `aletheore` from your Desktop or home folder would read everything in it. Flagged only for the file
    system root, your home folder and its standard personal folders, and only when no project files are present, so
    real repositories (even ones under Documents) are never questioned. In a terminal it asks; without one it
    refuses with a message, unless --yes or ALETHEORE_ASSUME_YES=1 is given.
    """
    from aletheore import ui

    reason = ui.broad_directory_reason(path)
    if reason is None or yes or os.environ.get("ALETHEORE_ASSUME_YES"):
        return
    resolved = Path(path).expanduser().resolve()
    _stderr_console.print(
        f"[bold]{resolved}[/bold] is {reason}, not a project: no .git, package.json, pyproject.toml or other "
        "project files were found there. Using it would read everything inside."
    )
    hint = "Pass the project's path instead, for example `aletheore scan ~/code/my-project`."
    if ui.is_interactive():
        try:
            answer = input("Use this folder anyway? [y/N] ").strip().lower()
        except EOFError:
            answer = ""
        if answer in ("y", "yes"):
            return
        _stderr_console.print(f"Cancelled. {hint}")
        raise typer.Exit(code=1)
    _stderr_console.print(f"{hint} Or add --yes to use this folder anyway.")
    raise typer.Exit(code=2)



def _confirm(prompt: str) -> bool:
    """y/N prompt that treats a closed stdin (CI, pipes) as "no" instead of crashing."""
    try:
        return input(prompt).strip().lower() == "y"
    except EOFError:
        console.print("\n[yellow]no interactive input available - treating as 'no'[/yellow]")
        return False


def _installed_version() -> str:
    import importlib.metadata

    try:
        return importlib.metadata.version("aletheore")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _version_tuple(version: str) -> tuple[int, ...] | None:
    try:
        return tuple(int(part) for part in version.split("."))
    except ValueError:
        return None


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
    footer.append("Run 'aletheore query --help' for options.\n", style="dim")
    footer.append(
        "Also top-level commands: locate, usages, dependents, tests-for, cycles (see 'aletheore --help').",
        style="dim",
    )

    return Panel(
        Group(header, "", kinds_table, "", footer),
        title="query kinds",
        border_style="cyan",
        width=78,
    )


def _sponsor_panel() -> Panel:
    # Shown only after 'audit' (see the sole call site below), which always
    # hands evidence to some external agent - an API Aletheore calls
    # directly, or a CLI tool the user already authenticated. A "nothing
    # leaves this machine" claim printed right after that would contradict
    # the consent prompt (or the CLI-adapter invocation) from the same run.
    body = Text()
    body.append("Aletheore is 100% open-source and free.\n\n", style="bold")
    body.append("If it saved you time, consider supporting development:\n")
    body.append("https://github.com/sponsors/ArihantK15", style="cyan underline")
    return Panel(body, border_style="magenta", width=78)


def _free_pr_review_nudge() -> str:
    # Lives in the top-level 'scan'/'audit' command bodies, not inside the
    # shared _scan()/_audit() helpers those commands call - a helper's
    # call count isn't this function's to assume, today or later, and a
    # promotional nudge printed from inside a reusable helper repeats
    # anywhere that helper gets reused. ('watch' was considered as an
    # example of exactly that risk, then checked and ruled out - it drives
    # re-indexing through its own aletheore.watch module, not _scan()/
    # _audit(), so it was never actually a live case here. Still the right
    # place for this to live, on the general principle, not that specific
    # example.)
    return (
        "\n[bold]Want this on every pull request automatically?[/bold] Aletheore's "
        "free tier reviews PRs with the same evidence-grounded findings this run just "
        "used. Install the GitHub App: "
        "[bold cyan]https://github.com/apps/aletheore/installations/new[/bold cyan]\n"
        "[dim](free tier is globally rate-limited)[/dim]"
    )


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


def _print_query_result(result: object) -> None:
    # Falls back to plain JSON on a TOON encoding failure rather than
    # crashing `query` outright - a less compact result beats no result.
    try:
        print(to_toon({"result": result}))
    except ToonEncodingError as exc:
        console.print(f"[yellow]warning: TOON encoding failed ({exc}); falling back to JSON[/yellow]")
        print(json.dumps({"result": result}, indent=2, default=str))


_COMMAND_SUMMARIES = [
    ("scan", "run the scanner, write evidence, no LLM call"),
    ("audit", "scan, then have a coding agent write a grounded report"),
    ("index", "build the semantic search index used by 'query search-codebase' and 'query answer'"),
    ("query", "answer a targeted question from existing evidence"),
    ("locate", "find where names are defined and print their source"),
    ("usages", "every call site and reference to a function or class"),
    ("dependents", "every file that imports a file, directly or transitively"),
    ("tests-for", "every test file that exercises a file"),
    ("cycles", "every circular import dependency, with an example chain"),
    ("diff", "compare two evidence snapshots"),
    ("verify", "check a report's file:line citations against a repo's evidence"),
    ("mcp", "run an MCP server so an agent can query a repo directly"),
    ("mcp-install", "write MCP client config for Claude Code, Cursor, VS Code, Kiro, Opencode, Antigravity, Codex CLI, or Claude Desktop"),
    ("dashboard", "a live local web UI over the same evidence"),
    ("healthcheck", "GET-only live check of mapped API endpoints"),
    ("watch", "re-scan and re-index automatically as files change"),
    ("init", "scaffold a repository-local .aletheore.json config file"),
    ("hook", "commands for coding-agent hooks (read guard)"),
    ("tokens", "measure and reduce the tokens a coding agent re-reads every turn"),
    ("login", "authenticate and save a managed-audit API token"),
    ("logout", "clear the locally saved managed-audit and Hallbook tokens"),
    ("status", "installed version, update availability, and login state"),
]


# Two commands the banner does not list but the "All commands" picker does. The banner (and so the plain `aletheore`
# output) keeps its own list exactly as it was.
_PICKER_ONLY_SUMMARIES = [
    ("hallbook", "browse, search and read the licences of the library of vetted agent skills"),
    ("config", "manage local CLI preferences, such as crash reporting"),
]
_COMMAND_DESCRIPTIONS = dict(_COMMAND_SUMMARIES + _PICKER_ONLY_SUMMARIES)
# The picker groups every command above by task, each exactly once.
_COMMAND_GROUPS = [
    (
        "Scan and explore",
        ["scan", "index", "query", "locate", "usages", "dependents", "tests-for", "cycles", "diff", "verify", "healthcheck"],
    ),
    ("Agents and skills", ["mcp", "mcp-install", "audit", "hallbook", "hook", "tokens"]),
    ("Dashboard and watch", ["dashboard", "watch", "init"]),
    ("Account", ["login", "logout", "status", "config"]),
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
    commands.add_column(style="bold", no_wrap=True)
    commands.add_column()
    for name, desc in _COMMAND_SUMMARIES:
        commands.add_row(f"  {name}", desc)

    footer = Text()
    footer.append("Run ")
    footer.append("aletheore <command> --help", style="bold")
    footer.append(" for details on any command.\n")
    footer.append("https://github.com/Aletheore/Aletheore", style="dim underline")
    footer.append("\nIssues or suggestions? ")
    footer.append("support@aletheore.com", style="dim underline")

    return Panel(
        Group(intro, commands, Text(""), footer),
        title="[bold]ALETHEORE[/bold]",
        title_align="left",
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
    # Everything here goes to stderr: progress is not the command's result, and
    # a piped or CI run needs stdout to stay machine-usable.
    is_tty = sys.stderr.isatty() if is_tty is None else is_tty
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
            print(f"\r  {spinner} {message}" + " " * 15, end="", flush=True, file=sys.stderr)
            state["in_place"] = True
        else:
            if state["in_place"]:
                print(file=sys.stderr)
                state["in_place"] = False
            _stderr_console.print(f"  [green]→[/green] {message}")

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
            print(file=sys.stderr)
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
    map_schema: bool | None,
    check_static_analysis: bool | None,
) -> tuple[bool, bool, bool, bool, bool, bool]:
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
        map_schema if map_schema is not None else "schema" not in disabled,
        check_static_analysis if check_static_analysis is not None else "static_analysis" not in disabled,
    )


def _resolve_bearer_toggle(check_bearer: bool | None) -> bool:
    """Bearer is opt-in, unlike the other static-analysis scanners - real
    timing gap found live: its full-repo runtime doesn't scale cleanly
    with repo size (18.7s on a 241-file subtree, still running past 300s
    on this repo's own real ~3,331-file tree). An explicit
    --check-bearer/--no-check-bearer flag always wins. With neither
    passed: on an interactive terminal, ask - a human is right there to
    decide, and the warning below is real, not boilerplate. Non-
    interactive (CI, a script, the hosted worker's own subprocess
    invocation - none of which have a real stdin to answer a prompt)
    defaults to skipping it rather than hanging forever on an
    unanswerable question.
    """
    if check_bearer is not None:
        return check_bearer
    if not sys.stdin.isatty():
        return False
    console.print(
        "[bold yellow]Bearer[/bold yellow] (sensitive-data/PII flow scanner) finds real "
        "issues nothing else here does, but it's opt-in: its runtime doesn't scale cleanly "
        "with repo size and it can take significantly longer than Aletheore's other checks "
        "on a large repo."
    )
    return _confirm("Include Bearer in this scan? [y/N]: ")


def _os_error_text(exc: Exception) -> str:
    """A short lowercase reason for an OSError (or a decode error), without the file name
    the caller already prints."""
    if isinstance(exc, OSError):
        return (exc.strerror or exc.__class__.__name__).lower()
    return str(exc)


def _scan(
    repo_path: str,
    check_vulnerabilities: bool | None,
    scan_git_history: bool | None,
    check_licenses: bool | None = None,
    map_endpoints: bool | None = None,
    map_schema: bool | None = None,
    check_static_analysis: bool | None = None,
    check_bearer: bool | None = None,
    check_joern: bool | None = None,
) -> tuple[int, dict, Path]:
    repo = Path(repo_path).resolve()
    (
        resolved_vulnerabilities, resolved_git_history, resolved_licenses,
        resolved_endpoints, resolved_schema, resolved_static_analysis,
    ) = _resolve_check_toggles(
        repo, check_vulnerabilities, scan_git_history, check_licenses, map_endpoints, map_schema,
        check_static_analysis,
    )
    # Only worth asking about Bearer if static analysis is running at all -
    # --no-check-static-analysis already answers the question.
    resolved_bearer = resolved_static_analysis and _resolve_bearer_toggle(check_bearer)
    # Joern gets no interactive prompt, unlike Bearer - it requires a
    # whole separate toolchain (Joern + a JVM) almost no install will have
    # by default, so check_joern's own shutil.which self-skip already
    # covers the common case gracefully; nagging every scan with a prompt
    # for a capability that's rarely even installed would be more
    # annoying than useful. Plain opt-in flag only.
    resolved_joern = resolved_static_analysis and bool(check_joern)

    if repo.is_dir():
        # A read-only folder fails here, in a second, instead of after the scan.
        try:
            check_evidence_writable(repo)
        except EvidenceWriteError as exc:
            _stderr_console.print(f"[bold red]error:[/bold red] {escape_markup(str(exc))}")
            return 1, {}, repo

    _stderr_console.print(f"Scanning {repo}...")
    try:
        evidence = scan_repository(
            repo,
            check_vulnerabilities=resolved_vulnerabilities,
            scan_git_history=resolved_git_history,
            check_licenses=resolved_licenses,
            map_endpoints=resolved_endpoints,
            map_schema=resolved_schema,
            check_static_analysis=resolved_static_analysis,
            run_bearer=resolved_bearer,
            run_joern=resolved_joern,
            progress=_make_progress_printer(),
        )
    except GitAnalysisError as exc:
        console.print(f"[bold red]error:[/bold red] {exc}")
        return GIT_ANALYSIS_RESOURCE_EXIT_CODE, {}, repo
    try:
        evidence_path = write_evidence(evidence, repo)
    except EvidenceWriteError as exc:
        _stderr_console.print(f"[bold red]error:[/bold red] {escape_markup(str(exc))}")
        return 1, {}, repo
    try:
        snapshot_path = save_snapshot(evidence, repo)
    except OSError as exc:
        _stderr_console.print(
            f"[bold red]error:[/bold red] evidence was written to {escape_markup(str(evidence_path))} but the "
            f"snapshot could not be saved to {escape_markup(str(repo / '.aletheore' / 'history'))}: "
            f"{escape_markup((exc.strerror or exc.__class__.__name__).lower())}"
        )
        return 1, {}, repo
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
    check_static_analysis: bool | None = None,
    check_bearer: bool | None = None,
    check_joern: bool | None = None,
) -> int:
    if forced_agent is not None:
        # Fail before the (slow) scan, not after it: a mistyped or missing agent
        # cannot succeed whatever the scan finds.
        known_names = [a.name for a in KNOWN_ADAPTERS]
        if forced_agent not in known_names:
            _stderr_console.print(
                f"[bold red]error:[/bold red] unknown agent {forced_agent!r}; choose one of: {', '.join(known_names)}"
            )
            return 2
        try:
            select_adapter(KNOWN_ADAPTERS, forced_name=forced_agent, interactive=False)
        except (NoAdapterAvailableError, AmbiguousAdapterError) as exc:
            _stderr_console.print(f"[bold red]error:[/bold red] {exc}")
            return 1
    scan_exit_code, _evidence, evidence_path = _scan(
        repo_path, check_vulnerabilities, scan_git_history, check_licenses, map_endpoints, map_schema,
        check_static_analysis, check_bearer, check_joern,
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
        if not _confirm("Continue? [y/N]: "):
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
    except OSError as exc:
        _stderr_console.print(f"[bold red]error:[/bold red] could not write the audit report: {escape_markup(_os_error_text(exc))}")
        console.print(f"Evidence is still available at {evidence_path} for manual use.")
        return 1

    report_file = Path(report_path)
    try:
        report_text = report_file.read_text(encoding="utf-8")
        verification_section = citation_verification_section(report_text, repo)
        report_file.write_text(report_text + verification_section, encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        _stderr_console.print(
            f"[bold red]error:[/bold red] could not update {escape_markup(str(report_file))}: "
            f"{escape_markup(_os_error_text(exc))}"
        )
        return 1

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
    check_static_analysis: bool | None = None,
    check_bearer: bool | None = None,
    check_joern: bool | None = None,
) -> int:
    resolved_token = token or get_api_key(
        "ALETHEORE_API_TOKEN", "aletheore-managed-audit", disclosure=HOSTED_EMBEDDINGS_DISCLOSURE
    )
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
        check_static_analysis,
        check_bearer,
        check_joern,
    )
    if scan_exit_code != 0:
        return scan_exit_code
    repo = Path(repo_path).resolve()
    repo_full_name = infer_repo_full_name_from_cwd_git_remote(cwd=str(repo))

    console.print("Running managed audit (using Aletheore's shared key)...")
    try:
        with _ElapsedTicker("Waiting on the managed audit service"):
            report_text = run_managed_audit_request(evidence, resolved_token, repo_full_name=repo_full_name)
    except (ManagedAuditError, httpx.HTTPError, KeyError, ValueError) as exc:
        console.print(f"[bold red]error:[/bold red] managed audit failed: {exc}")
        console.print(f"Evidence is still available at {evidence_path} for manual use.")
        return 1

    report_path = repo / ".aletheore" / "audit-report.md"
    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report_text, encoding="utf-8")
    except OSError as exc:
        _stderr_console.print(
            f"[bold red]error:[/bold red] could not write {escape_markup(str(report_path))}: "
            f"{escape_markup(_os_error_text(exc))}"
        )
        return 1
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
    latest, installed = _version_tuple(latest_version), _version_tuple(installed_version)
    if latest is not None and installed is not None and installed > latest:
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
    # CI and offline machines can opt out: the check blocks up to 5s with no network.
    installed_version = _installed_version()
    if os.environ.get("ALETHEORE_NO_UPDATE_CHECK") or installed_version == "unknown":
        return
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
    api_base_url: str | None = None,
    http_client: httpx.Client | None = None,
) -> dict | None:
    from aletheore.device_auth import _resolved_api_base_url

    client = http_client or httpx.Client(base_url=api_base_url or _resolved_api_base_url())
    try:
        response = client.get(
            "/v1/whoami", headers={"Authorization": f"Bearer {token}"}, timeout=5.0
        )
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError):
        return None


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
    except (FileNotFoundError, IncompatibleEvidenceVersionError, MalformedEvidenceError) as exc:
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
        print(f"error: previous snapshot is unreadable ({snapshots[-2]})")
        return 1
    except (IncompatibleEvidenceVersionError, MalformedEvidenceError) as exc:
        print(f"error: {exc}")
        return 1

    try:
        new = load_evidence_file(snapshots[-1])
    except json.JSONDecodeError:
        print(f"error: most recent snapshot is unreadable ({snapshots[-1]})")
        return 1
    except (IncompatibleEvidenceVersionError, MalformedEvidenceError) as exc:
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
    except (IncompatibleEvidenceVersionError, MalformedEvidenceError) as exc:
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


_QUERY_TARGET_HINTS = {
    "imports": "a repo-relative file path, e.g. src/app.py",
    "imported-by": "a repo-relative file path, e.g. src/app.py",
    "symbols": "a repo-relative file path, e.g. src/app.py",
    "cluster": "a repo-relative file path, e.g. src/app.py (the cluster that file belongs to)",
    "secrets": "a repo-relative file path to filter by, e.g. src/config.py",
    "branch": "a branch name, e.g. main",
    "evidence-for-symbol": "a function or class name",
    "evidence-for-endpoint": "'METHOD /path' such as 'GET /users', or a bare path such as /users",
    "evidence-for-dependency": "an import or package name, e.g. flask",
    "ast-pattern": "a tree-sitter query",
}


def _warn_unmatched_under(evidence: dict, under: list[str]) -> None:
    """--under that matches no scanned file would otherwise silently produce an empty answer."""
    from aletheore.scope_filter import normalize_under

    paths = [m["path"] for m in evidence.get("repository", {}).get("modules", []) if m.get("path")]
    for prefix in normalize_under(under):
        if not any(p.startswith(prefix) for p in paths):
            print(f"warning: no files under {prefix.rstrip('/')} in this scan", file=sys.stderr)


def _query(
    kind: str,
    target: str | None,
    repo_path: str,
    full: bool = False,
    forced_agent: str | None = None,
    k: int = 10,
    symbol: str | None = None,
    language: str | None = None,
    exclude: list[str] | None = None,
    under: list[str] | None = None,
    no_tests: bool = False,
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

    if not Path(repo_path).exists():
        _stderr_console.print(f"[bold red]error:[/bold red] path does not exist: {escape(str(Path(repo_path).resolve()))}")
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
        if any(item.get("keyword_only") for item in result):
            console.print("[dim]keyword-only: embeddings still building[/dim]")
        _print_query_result(result)
        return 0

    if kind == "ast-pattern":
        if target is None:
            print("error: query type 'ast-pattern' requires a tree-sitter query as TARGET")
            return 1
        if language is None:
            print("error: query type 'ast-pattern' requires --language (which grammar to compile the query against)")
            return 1
        from aletheore.ast_pattern import (
            InvalidPatternError,
            UnknownLanguageError,
            search_ast_pattern,
        )

        ignored_paths = load_repo_config(Path(repo_path).resolve())["ignored_paths"]
        try:
            result = search_ast_pattern(Path(repo_path).resolve(), language, target, ignored_paths)
        except UnknownLanguageError as exc:
            console.print(f"[bold red]error:[/bold red] {exc}")
            return 1
        except InvalidPatternError as exc:
            console.print(f"[bold red]error:[/bold red] invalid tree-sitter query: {exc}")
            return 1
        _print_query_result(result)
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
            if not _confirm("Continue? [y/N]: "):
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
        _print_query_result(result)
        return 0

    repo = Path(repo_path).resolve()
    evidence = _load_evidence_or_exit(repo)

    if kind == "symbol-source":
        if target is None or symbol is None:
            print(
                "error: query type 'symbol-source' requires a repo-relative file path and a function or class name "
                "(aletheore query symbol-source src/app.py my_function)",
                file=sys.stderr,
            )
            return 1
        try:
            result = find_symbol_source(evidence, repo, target, symbol)
        except (ModuleNotFoundInEvidenceError, SymbolNotFoundInEvidenceError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        _print_query_result(result)
        return 0

    func, requires_target = QUERY_FUNCTIONS[kind]
    if requires_target and target is None:
        hint = _QUERY_TARGET_HINTS.get(kind)
        print(
            f"error: query type '{kind}' requires a target argument" + (f": {hint}" if hint else ""),
            file=sys.stderr,
        )
        return 1

    try:
        if kind in ("evidence-for-endpoint", "evidence-for-symbol", "evidence-for-dependency"):
            result = func(evidence, target, repo)
        else:
            result = func(evidence, target)
    except (ModuleNotFoundInEvidenceError, BranchNotFoundInEvidenceError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if kind.startswith("evidence-for-"):
        from aletheore.query import evidence_target_not_found_message

        if isinstance(result, dict) and not result.get("file") and result.get("evidence_status") == "unavailable":
            print(f"error: {evidence_target_not_found_message(evidence, kind, target)}", file=sys.stderr)
            return 1

    if kind == "secrets" and not result:
        total = len(evidence["security"]["secrets"]["findings"])
        print(
            f"note: no secret findings for the path {target!r}. TARGET is a repo-relative file path used as a "
            f"filter, not a keyword (there is no 'all'); this scan recorded {total} secret finding(s) in total "
            "across all files.",
            file=sys.stderr,
        )

    if exclude or under or no_tests:
        from aletheore.scope_filter import SCOPABLE, scope_result

        if kind not in SCOPABLE:
            print(
                f"error: --under/--exclude/--no-tests are supported for: {', '.join(sorted(SCOPABLE))}",
                file=sys.stderr,
            )
            return 1
        _warn_unmatched_under(evidence, under or [])
        result = scope_result(kind, result, exclude or [], under or [], no_tests)
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
        print(f"error: unknown --format {output_format!r} (expected 'json' or 'sarif')", file=sys.stderr)
        return 2
    if output_format == "sarif" and full:
        print("error: --format sarif is incompatible with --full (SARIF needs the curated diff)", file=sys.stderr)
        return 2

    old_file = Path(old_path)
    new_file = Path(new_path)

    for candidate in (old_file, new_file):
        if not candidate.exists():
            print(f"error: evidence file not found: {candidate}", file=sys.stderr)
            return 2
        if not candidate.is_file():
            print(f"error: {candidate} is a directory, expected an air.json file", file=sys.stderr)
            return 2

    try:
        old = load_evidence_file(old_file)
    except (json.JSONDecodeError, UnicodeDecodeError):
        print(f"error: {old_file} is not valid JSON", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"error: could not read {old_file}: {exc}", file=sys.stderr)
        return 2
    except (IncompatibleEvidenceVersionError, MalformedEvidenceError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        new = load_evidence_file(new_file)
    except (json.JSONDecodeError, UnicodeDecodeError):
        print(f"error: {new_file} is not valid JSON", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"error: could not read {new_file}: {exc}", file=sys.stderr)
        return 2
    except (IncompatibleEvidenceVersionError, MalformedEvidenceError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

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
            checked_old = old["security"]["dependency_vulnerabilities"].get("checked", True)
            checked_new = new["security"]["dependency_vulnerabilities"].get("checked", True)
            if not (checked_old and checked_new):
                # The vulnerability check was off in at least one snapshot, so
                # "new" findings only reflect it being toggled, not a real change.
                skipped_in = " and ".join(
                    name for name, checked in (("the old", checked_old), ("the new", checked_new)) if not checked
                )
                print(
                    f"note: --fail-on-new-vulnerabilities not enforced: the vulnerability check was skipped in "
                    f"{skipped_in} snapshot. Diff two scans that both ran it (no --no-check-vulnerabilities).",
                    file=sys.stderr,
                )
            else:
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

    try:
        report_text = report_file.read_text(encoding="utf-8")
    except OSError as exc:
        # The .exists() check above only proves the file was there at that
        # moment - it can vanish, lose permissions, or (on a race with
        # something else writing it) become briefly unreadable before this
        # read runs. Same class of bug as the MCP crashes fixed alongside
        # this: a real, unhandled exception two lines below a check that
        # looks like it already guarded against exactly this.
        console.print(f"[bold red]error:[/bold red] could not read {report_file}: {exc}")
        return 1
    except UnicodeDecodeError as exc:
        console.print(f"[bold red]error:[/bold red] {report_file} is not valid UTF-8: {exc}")
        return 1
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
            console.print(f"  - {format_citation(citation)}", markup=False)
        return 1

    console.print("[bold green]All citations verified.[/bold green]")
    return 0


def _healthcheck(repo_path: str, base_url: str) -> int:
    repo = Path(repo_path).resolve()
    try:
        evidence = load_evidence(repo)
    except (FileNotFoundError, IncompatibleEvidenceVersionError, MalformedEvidenceError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    endpoints = evidence["repository"].get("api_endpoints", {}).get("endpoints", [])
    try:
        result = run_healthcheck(endpoints, base_url)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    save_healthcheck(result, repo)

    if not endpoints:
        print(
            "No API endpoints are mapped in .aletheore/air.json, so there is nothing to check. "
            "Run `aletheore scan` on a repo that defines HTTP routes first."
        )
        return 0

    checked = 0
    healthy = 0
    unhealthy: list[str] = []
    unreachable: list[str] = []
    for entry in result["results"]:
        method = entry.get("method") or "?"
        if entry.get("skipped"):
            print(f"{method:6} {entry['path']:40} SKIPPED ({entry['reason']})")
            continue
        checked += 1
        note = f" ({entry['note']})" if entry.get("note") else ""
        label = f"{method} {entry['path']}"
        if not entry["reachable"]:
            unreachable.append(label)
            status = f"UNREACHABLE: {entry.get('error') or 'no response'}"
        elif entry.get("healthy", is_healthy_status(entry["status_code"])):
            healthy += 1
            status = str(entry["status_code"])
        else:
            unhealthy.append(label)
            status = f"{entry['status_code']} UNHEALTHY: responded {entry['status_code']}"
        print(f"{method:6} {entry['path']:40} {status} {entry['latency_ms']}ms{note}")

    if not checked:
        print("\nNo concrete endpoints to check (every mapped endpoint is an unresolved routing indirection).")
        return 0

    print(f"\n{healthy} of {checked} endpoint(s) healthy (2xx or 3xx).")
    if unhealthy:
        print(f"{len(unhealthy)} responded with an error status (4xx or 5xx).")
    if unreachable:
        print(f"{len(unreachable)} unreachable (no HTTP response).")
    return 0 if healthy == checked else 1


def _mcp(repo_path: str, forced_agent: str | None = None, watch: bool = True, profile: str = "full") -> int:
    from aletheore.mcp_server import build_server

    repo = Path(repo_path).resolve()
    answer_adapter = None
    if forced_agent is not None:
        try:
            answer_adapter = select_adapter(
                KNOWN_ADAPTERS, forced_name=forced_agent, interactive=False
            )
        except (NoAdapterAvailableError, AmbiguousAdapterError) as exc:
            _stderr_console.print(f"[bold red]error:[/bold red] {exc}")
            return 1
    # Watching is on by default: the server is the long-lived process an agent
    # drives, so it is where stale evidence hurts and where the watcher's
    # lifetime is obvious (it ends with the server). Announced on stderr by
    # build_server, and switched off by --no-watch or ALETHEORE_MCP_WATCH=0.
    server = build_server(
        repo,
        answer_adapter=answer_adapter,
        watch=watch and not watching_disabled_by_env(),
        profile=profile,
    )
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
    # Windows console scripts are aletheore.exe (in Scripts/), never a bare "aletheore".
    for name in ("aletheore", "aletheore.exe"):
        sibling = Path(sys.executable).parent / name
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
    # Verified against Google's own docs (antigravity.google/docs/ide/mcp/):
    # workspace-local config lives at .agents/mcp_config.json under
    # "mcpServers", entries shaped {"command", "args"} with no "type" field -
    # identical shape to Cursor's entry, just a different path.
    "antigravity": (".agents/mcp_config.json", "mcpServers", lambda p: _stdio_entry(p, include_type=False)),
}


def _claude_desktop_config_path() -> Path | None:
    """Claude Desktop's MCP config, verified against Anthropic's own
    quickstart docs (modelcontextprotocol.io/quickstart/user) - unlike
    every other target, it's a single global file, not scoped under the
    repo being installed into. Desktop ships for macOS and Windows only
    (no Linux build exists), so this returns None there rather than
    guessing at a path nothing documents - same discipline already
    applied to PyCharm's config below.
    """
    if sys.platform == "darwin":
        return user_home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if sys.platform == "win32":
        # The Microsoft Store (MSIX) build keeps its config in a virtualised
        # AppData under Packages/, not in %APPDATA%.
        local = os.environ.get("LOCALAPPDATA")
        if local:
            for packaged in sorted((Path(local) / "Packages").glob("Claude_*")):
                candidate = packaged / "LocalCache" / "Roaming" / "Claude" / "claude_desktop_config.json"
                if candidate.parent.is_dir():
                    return candidate
        appdata = os.environ.get("APPDATA")
        return Path(appdata) / "Claude" / "claude_desktop_config.json" if appdata else None
    return None


def _claude_desktop_server_name(repo_path: Path) -> str:
    """A key for this repo's entry in Claude Desktop's one global,
    shared-across-every-project config file.

    Real gap found via audit: keying purely by `repo_path.name` (e.g.
    "aletheore-backend") still collides for two different repos that
    happen to share a directory basename - a common real pattern
    (`~/work/client-a/backend` and `~/work/client-b/backend`), which is
    exactly the class of silent-overwrite bug this keying scheme was
    written to prevent, just not fully closed. A short hash of the full
    resolved path guarantees uniqueness regardless of basename, same
    convention search_index.py's own per-repo cache key already uses.
    """
    digest = hashlib.sha256(str(repo_path.resolve()).encode("utf-8")).hexdigest()[:16]
    return f"aletheore-{repo_path.name}-{digest}"


def _config_path_escapes_repo(config_path: Path, repo_path: Path) -> bool:
    """True if config_path - once any symlinked component along it (the
    file itself, or an intermediate directory like .cursor/.vscode/.kiro)
    is resolved - would land outside repo_path.

    Real bug this closes: `mcp-install` is commonly run against a freshly
    cloned or downloaded repository, which is attacker-controlled input
    the same way a scanned repo's source is everywhere else in this
    codebase (see the SSRF/sandboxing hardening history) - but the write
    path here (`config_path.exists()`/`.write_text()`) followed a symlink
    transparently, both for the config file itself and for any parent
    directory in its path. A malicious repo shipping `.mcp.json` (or
    `.cursor`, `.vscode`, `.kiro/settings`) as a symlink to an arbitrary
    path outside the repo had that target silently overwritten - not
    merely a file inside the scanned repo, any file the OS user running
    this command can write to (a shell rc file, another project's real
    config, anything). Path.resolve() with a nonexistent final segment
    still resolves every existing parent component, which is exactly the
    case that matters: the config file usually doesn't exist yet, but a
    symlinked *directory* component already does.
    """
    try:
        resolved = config_path.resolve()
    except OSError:
        return True
    return not resolved.is_relative_to(repo_path)


def _write_config_file_no_symlink_follow(config_path: Path, content: str) -> None:
    _write_bytes_no_symlink_follow(config_path, content.encode("utf-8"))


def _write_bytes_no_symlink_follow(config_path: Path, data: bytes) -> None:
    """Writes content to config_path's leaf file without ever following a
    symlink there, closing the TOCTOU gap _config_path_escapes_repo's
    separate resolve()-then-check-then-write shape leaves open: a plain
    `write_text()` call, made afterwards, still follows a symlink
    transparently if one was put in place (or swapped in) between the
    check and this call. O_NOFOLLOW makes the open() syscall itself fail
    atomically when the leaf is a symlink, rather than resolving and
    checking as two separate steps with a real (if narrow, for a local,
    single-user CLI run) window between them.

    Deliberately narrower than a fully race-proof write: an intermediate
    *directory* component (.cursor/.vscode/.kiro) swapped for a symlink
    in that same window is still followed by the mkdir(parents=True)/open
    calls below - closing that too needs walking and O_NOFOLLOW-checking
    every parent component individually, which _config_path_escapes_repo
    already does for the common case (a symlinked directory that exists
    at scan time, the actual malicious-repo shape found and fixed here).

    O_NOFOLLOW is POSIX-only - os doesn't define it on Windows, where this
    CLI also runs (claude-desktop is a supported target there). `getattr`
    with a 0 fallback means the flag simply has no effect on a platform
    that lacks it, falling back to the pre-existing follow-symlink
    behavior rather than crashing with AttributeError on every write.
    """
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    if not no_follow and config_path.is_symlink():
        # Windows has no O_NOFOLLOW; refuse a symlinked leaf explicitly instead.
        raise OSError("refusing to write through a symlink")
    fd = os.open(str(config_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | no_follow, 0o644)
    # Bytes, so the line endings and BOM the caller chose are written exactly (no per-platform translation).
    with os.fdopen(fd, "wb") as f:
        f.write(data)


def _read_config_text(config_path: Path) -> tuple[str, str, str]:
    """(text, newline, bom) of an existing config file. The text is decoded without its BOM and normalised to
    "\n"; newline is "\r\n" when the file used CRLF and bom is "\ufeff" when it started with one, so a rewrite
    keeps both."""
    raw = config_path.read_bytes()
    text = raw.decode("utf-8-sig")
    bom = "\ufeff" if raw.startswith(b"\xef\xbb\xbf") else ""
    return text.replace("\r\n", "\n"), ("\r\n" if b"\r\n" in raw else "\n"), bom


def _loads_jsonc(text: str) -> object:
    """json.loads that also accepts // and /* */ comments and trailing commas,
    which VS Code and opencode configs routinely contain. Comments are not
    preserved when the file is written back."""
    out: list[str] = []
    i, n, in_str = 0, len(text), False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
            out.append(c)
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        else:
            out.append(c)
        i += 1
    return json.loads(_strip_trailing_commas("".join(out)))


def _strip_trailing_commas(text: str) -> str:
    """Remove a trailing comma before `}` or `]`, but only outside string
    literals - a blind regex over the whole text would also strip a comma
    that happens to appear inside a string value immediately before one of
    those characters (e.g. a glob pattern like "*.{js,}"), corrupting it.
    `text` has already had comments stripped, so no comment-skipping is
    needed here, just the same in_str tracking."""
    out: list[str] = []
    i, n, in_str = 0, len(text), False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 1
            elif c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        if c == ",":
            j = i + 1
            while j < n and text[j].isspace():
                j += 1
            if j < n and text[j] in "}]":
                i += 1
                continue
        out.append(c)
        i += 1
    return "".join(out)


def _write_json_mcp_client_config(
    config_path: Path,
    top_level_key: str,
    entry: dict,
    *,
    server_name: str = "aletheore",
    repo_path: Path | None = None,
) -> tuple[str, bool]:
    """Returns (message, security_skip) - security_skip is True only for the
    symlink-escapes-the-repo case, never for an ordinary skip (bad existing
    JSON, wrong shape, ...), so a caller can tell "this needs attention
    because something tried to write outside the repo" apart from "this
    file just wasn't in a shape we could merge into"."""
    # repo_path is None for claude-desktop's target: that config file is
    # deliberately global (shared across every project on this machine,
    # not under any one repo), so there's no repo boundary to check -
    # every other target writes a per-repo path and must pass repo_path.
    if repo_path is not None and _config_path_escapes_repo(config_path, repo_path):
        return f"skipped (path escapes the repo via a symlink): {config_path}", True
    newline = "\n"
    bom = ""
    if config_path.exists():
        try:
            existing_text, newline, bom = _read_config_text(config_path)
            data = _loads_jsonc(existing_text)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return f"skipped (existing file is not valid JSON): {config_path}", False
        if not isinstance(data, dict):
            return f"skipped (existing file's top level is not a JSON object): {config_path}", False
        try:
            json.loads(existing_text)
        except json.JSONDecodeError:
            # Parses only as JSONC: rewriting it as strict JSON would silently
            # delete the user's comments, so leave the file alone and show the
            # exact entry to paste instead.
            snippet = json.dumps({top_level_key: {server_name: entry}}, indent=2)
            return (
                f"skipped (existing file has comments or trailing commas that rewriting would destroy): "
                f"{config_path}\n  Add this entry yourself, merging into the existing \"{top_level_key}\" object:\n"
                + "\n".join(f"    {line}" for line in snippet.splitlines())
            ), False
    else:
        data = {}

    servers = data.get(top_level_key, {})
    if not isinstance(servers, dict):
        return f"skipped (existing '{top_level_key}' is not a JSON object): {config_path}", False

    already_present = server_name in servers
    servers[server_name] = entry
    data[top_level_key] = servers

    config_path.parent.mkdir(parents=True, exist_ok=True)
    content = bom + (json.dumps(data, indent=2) + "\n").replace("\n", newline)
    if repo_path is not None:
        try:
            _write_config_file_no_symlink_follow(config_path, content)
        except OSError:
            return f"skipped (path escapes the repo via a symlink): {config_path}", True
    else:
        # claude-desktop's global config has no repo boundary to enforce -
        # a symlink here is the user's own, legitimate choice (e.g.
        # dotfiles synced through a symlinked config directory), not
        # attacker-controlled repo content, so it's followed as before.
        with open(config_path, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
    return f"{'updated' if already_present else 'wrote'} {config_path}", False


def _toml_has_comment(text: str) -> bool:
    """True when the TOML text has a real comment: a `#` outside any string (so "#fff" or a URL fragment in a
    value does not count). Follows TOML's string forms: basic "..." and multi-line \"\"\"...\"\"\" (both honour
    backslash escapes, so an escaped quote does not close them) and literal '...' / '''...''' (no escapes)."""
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "#":
            return True
        if c in "\"'":
            multi = text.startswith(c * 3, i)
            delimiter = c * 3 if multi else c
            i += len(delimiter)
            while i < n:
                if c == '"' and text[i] == "\\":
                    i += 2
                    continue
                if text.startswith(delimiter, i):
                    i += len(delimiter)
                    break
                if not multi and text[i] == "\n":
                    break  # an unterminated single-line string ends at the line
                i += 1
        else:
            i += 1
    return False


def _write_toml_mcp_client_config(
    config_path: Path, top_level_key: str, entry: dict, *, repo_path: Path | None = None
) -> tuple[str, bool]:
    """Returns (message, security_skip) - see _write_json_mcp_client_config's
    docstring."""
    if repo_path is not None and _config_path_escapes_repo(config_path, repo_path):
        return f"skipped (path escapes the repo via a symlink): {config_path}", True
    newline = "\n"
    bom = ""
    if config_path.exists():
        try:
            toml_text, newline, bom = _read_config_text(config_path)
            data = tomllib.loads(toml_text)
        except (UnicodeDecodeError, tomllib.TOMLDecodeError):
            return f"skipped (existing file is not valid TOML): {config_path}", False
        if not isinstance(data, dict):
            return f"skipped (existing file's top level is not a TOML table): {config_path}", False
    else:
        data = {}

    servers = data.get(top_level_key, {})
    if not isinstance(servers, dict):
        return f"skipped (existing '{top_level_key}' is not a TOML table): {config_path}", False

    already_present = "aletheore" in servers
    servers["aletheore"] = entry
    data[top_level_key] = servers

    config_path.parent.mkdir(parents=True, exist_ok=True)
    content = bom + tomli_w.dumps(data).replace("\n", newline)
    if config_path.exists():
        original = config_path.read_bytes()  # bytes: the backup must be exact, CRLF and BOM included
        if _toml_has_comment(original.decode("utf-8", errors="replace")):
            console.print(
                f"[yellow]Warning:[/yellow] {escape(str(config_path))} has comments; rewriting it as TOML "
                "does not preserve them. The original is kept next to it as "
                f"{escape(config_path.name)}.bak."
            )
        backup_path = config_path.with_name(config_path.name + ".bak")
        try:
            if repo_path is not None:
                # One atomic O_NOFOLLOW open, same as the config itself: a check followed by a write would
                # leave a window for a symlink to be swapped in.
                _write_bytes_no_symlink_follow(backup_path, original)
            else:
                backup_path.write_bytes(original)
        except OSError:
            return f"skipped (could not write a backup, so the file was left untouched): {config_path}", False
    if repo_path is not None:
        try:
            _write_config_file_no_symlink_follow(config_path, content)
        except OSError:
            return f"skipped (path escapes the repo via a symlink): {config_path}", True
    else:
        with open(config_path, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
    return f"{'updated' if already_present else 'wrote'} {config_path}", False


def _mcp_target_path(target: str, repo_path: Path) -> Path | None:
    if target == "codex-cli":
        return repo_path / ".codex" / "config.toml"
    if target == "claude-desktop":
        return _claude_desktop_config_path()
    return repo_path / _MCP_CLIENT_CONFIGS[target][0]


def _mcp_install(path: str, targets: list[str], yes: bool = False) -> int:
    from aletheore import ui

    repo_path = Path(path).resolve()
    all_targets = [*_MCP_CLIENT_CONFIGS.keys(), "codex-cli", "claude-desktop"]
    selected = targets or all_targets
    unknown = [target for target in selected if target not in all_targets]
    if unknown:
        _stderr_console.print(
            f"[bold red]error:[/bold red] unknown target(s): {', '.join(unknown)}. "
            f"Valid targets: {', '.join(all_targets)}"
        )
        return 1

    if not targets and not yes and ui.is_interactive():
        console.print("This will write MCP client config to:", markup=False)
        for target in selected:
            target_path = _mcp_target_path(target, repo_path)
            if target_path is None:
                continue
            suffix = "   (machine-global: shared by every project)" if target == "claude-desktop" else ""
            console.print(f"  {target_path}{suffix}", markup=False, soft_wrap=True)
        try:
            answer = input("Write these? [y/N] ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            console.print("Cancelled. Pass --target to choose clients, or --yes to skip this question.")
            return 1

    # Real audit finding: a target skipped because its config path escapes
    # the repo via a symlink (the exact attacker-controlled-repo case
    # _config_path_escapes_repo exists for) printed a warning but still
    # exited 0. Any target that was requested but not written now fails the
    # run; the one exception is Claude Desktop being unavailable on this OS
    # when nobody asked for it by name (the default all-targets run).
    any_security_skip = False
    written: set[str] = set()
    failed: list[str] = []
    for target in selected:
        config_path = None
        try:
            if target == "codex-cli":
                config_path = repo_path / ".codex" / "config.toml"
                entry = {"command": _aletheore_command(), "args": ["mcp", str(repo_path)]}
                message, security_skip = _write_toml_mcp_client_config(
                    config_path, "mcp_servers", entry, repo_path=repo_path
                )
            elif target == "claude-desktop":
                config_path = _claude_desktop_config_path()
                if config_path is None:
                    message, security_skip = "skipped (Claude Desktop is only available on macOS and Windows)", False
                else:
                    entry = _stdio_entry(repo_path, include_type=False)
                    # Global file shared across every project on this machine -
                    # unlike every other target's per-repo file, keying this
                    # entry as plain "aletheore" would mean installing for a
                    # second repo silently overwrites the first repo's entry
                    # under the same key. See _claude_desktop_server_name.
                    message, security_skip = _write_json_mcp_client_config(
                        config_path, "mcpServers", entry,
                        server_name=_claude_desktop_server_name(repo_path),
                    )
            else:
                relative_path, top_level_key, entry_builder = _MCP_CLIENT_CONFIGS[target]
                config_path = repo_path / relative_path
                entry = entry_builder(repo_path)
                message, security_skip = _write_json_mcp_client_config(
                    config_path, top_level_key, entry, repo_path=repo_path
                )
        except OSError as exc:
            where = f" {config_path}" if config_path is not None else ""
            message, security_skip = f"skipped (could not write{where}: {_os_error_text(exc)})", False
        any_security_skip = any_security_skip or security_skip
        console.print(f"[bold green]{target}[/bold green]: {escape(message)}")
        if message.startswith(("wrote ", "updated ")):
            written.add(target)
        elif not (target == "claude-desktop" and not targets and _claude_desktop_config_path() is None):
            failed.append(target)

    if not written:
        _stderr_console.print(
            "[bold red]error:[/bold red] nothing was written; "
            f"skipped or failed: {', '.join(failed) or 'none'}"
        )
        return 1

    console.print(
        "\n[yellow]Note:[/yellow] these files contain absolute paths to this machine's aletheore "
        "and repo. They are not portable - add them to .gitignore rather than committing them "
        "for teammates or other operating systems."
    )
    console.print(
        "\nRestart or reload your coding tool so it picks up the new MCP server - "
        "Aletheore's tools will then be available without running 'aletheore mcp' yourself."
    )
    if "claude-code" in written:
        console.print(
            "\n[bold]Claude Code:[/bold] .mcp.json above is picked up automatically on reload - "
            "no extra step needed. Prefer registering it yourself instead (or want a command to "
            "verify the same server), paste this:"
        )
        # soft_wrap: a copy-pasteable shell command containing a full repo
        # path must not get a real newline inserted at the console width.
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
    if "codex-cli" in written:
        console.print(
            "[bold]OpenAI Codex CLI:[/bold] wrote .codex/config.toml, but Codex only reads "
            "project-scoped MCP config for projects it already trusts (in an untrusted project the "
            "server is silently not loaded) - accept Codex's trust prompt for this directory, or "
            "register it for every project instead:"
        )
        console.print(f"  codex mcp add aletheore -- {_aletheore_command()} mcp {repo_path}", soft_wrap=True)
        console.print(
            "Also note: writing .codex/config.toml reformats it - any hand-written comments are not "
            "preserved; the original is kept as config.toml.bak."
        )
    if "claude-desktop" in written:
        console.print(
            f"[bold]Claude Desktop:[/bold] wrote a config shared across every project on this "
            f"machine, keyed as \"{_claude_desktop_server_name(repo_path)}\" so installing for a "
            "different repo later won't overwrite this one. Fully quit and reopen Claude Desktop "
            "to pick it up - MCP servers only load at startup."
        )
    if any_security_skip:
        _stderr_console.print(
            "\n[bold red]error:[/bold red] at least one target was skipped because its config path "
            "escapes the repo via a symlink - see the per-target lines above for which one."
        )
        return 1
    if failed:
        _stderr_console.print(
            f"\n[bold red]error:[/bold red] not configured: {', '.join(failed)} - see the lines above."
        )
        return 1
    return 0


def _port_is_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        if sys.platform == "win32":
            # On Windows SO_REUSEADDR lets a second socket bind a port that is
            # already in use, so the check would always pass; claim it exclusively.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
            return True
        except (OSError, OverflowError):
            return False


def _watch(repo_path: str, debounce: float) -> int:
    from aletheore.watch import watch as run_watch

    repo = Path(repo_path).resolve()

    def report(message: str) -> None:
        console.print(f"[dim]{time.strftime('%H:%M:%S')}[/dim] {message}")

    if not (repo / ".aletheore" / "air.json").exists():
        # Nothing to keep current yet: scan once now, otherwise the watcher would sit idle
        # (and the dashboard/MCP/query commands empty) until the first file edit.
        report("no scan found yet; running an initial scan")
        scan_exit_code, _evidence, _path = _scan(str(repo), None, None)
        if scan_exit_code != 0:
            return scan_exit_code

    try:
        run_watch(repo, report, debounce_seconds=debounce)
    except OSError as exc:
        hint = ""
        if getattr(exc, "errno", None) == 28 or "inotify" in str(exc).lower():
            hint = (
                " The OS file-watch limit was reached; raise fs.inotify.max_user_watches "
                "(Linux) or point 'aletheore watch' at a smaller directory."
            )
        console.print(f"[bold red]error:[/bold red] could not watch {repo}: {exc}.{hint}")
        return 1
    except KeyboardInterrupt:
        # Ctrl-C is how this command is meant to end, so it exits 0 with a
        # word rather than a traceback - the dashboard's own Ctrl-C handling
        # was a bug worth not repeating.
        console.print("stopped")
    return 0


def _offer_scan_before_dashboard(repo: Path) -> bool:
    """The dashboard only shows what a scan wrote, so with no scan it would open empty and say nothing. In a terminal
    offer to scan first; anywhere else say what to run. True when there is now evidence to show."""
    from aletheore import ui

    hint = f"aletheore scan {repo}"
    console.print(f"[bold]No scan found for {repo}.[/bold] The dashboard shows what a scan finds, so it would open empty.")
    if not ui.is_interactive():
        console.print(f"Run this first, then open the dashboard again:\n\n  {hint}\n")
        return False
    try:
        scan_now = typer.confirm("Scan it now?", default=True)
    except EOFError:  # input closed: treated as "no". Ctrl-C (click.Abort) is left to abort as it does at any prompt.
        scan_now = False
    if not scan_now:
        console.print(f"Run this when you are ready, then open the dashboard again:\n\n  {hint}\n")
        return False
    exit_code, _evidence, _evidence_path = _scan(str(repo), None, None)
    return exit_code == 0


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
    if not 1 <= port <= 65535:
        console.print(f"[bold red]error:[/bold red] --port must be between 1 and 65535 (got {port}).")
        return 1
    if not _port_is_available(host, port):
        console.print(
            f"[bold red]error:[/bold red] port {port} is already in use - probably another "
            f"aletheore dashboard (or something else) is already bound to it.\n"
            f"Pass --port to use a different one, or stop whatever's using {port}."
        )
        return 1

    if not (repo / ".aletheore" / "air.json").exists():
        if not _offer_scan_before_dashboard(repo):
            return 1

    app = build_app(repo)
    url = f"http://{host}:{port}"
    console.print(f"[green]Dashboard running at[/green] {url}")
    # Opened shortly after the server starts listening, not before - the tab
    # would otherwise race uvicorn's bind and hit connection-refused.
    if os.environ.get("ALETHEORE_NO_BROWSER") != "1":
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

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
        console.print(f"aletheore {_installed_version()}")
        raise typer.Exit(code=0)


def _run_cli(args: list[str]) -> int:
    """Run one of this CLI's own commands from the menu, exactly as if it had been typed."""
    from typer.main import get_command

    try:
        get_command(app).main(args=args, prog_name="aletheore", standalone_mode=False)
        return 0
    except click.exceptions.Exit as exc:
        return exc.exit_code
    except click.ClickException as exc:
        exc.show()
        return exc.exit_code
    except click.Abort:
        console.print("Cancelled.")
        return 1
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1


def _menu_header() -> Text:
    from aletheore import ui

    header = Text()
    # The block-letter wordmark needs about 28 rows with the menu; on a short window (or a narrow one) show the
    # plain name instead, so the menu is never cropped by the terminal.
    art = ui.wordmark_lines(console.width) if console.height >= 28 else ["ALETHEORE"]
    for line in art:
        header.append(line + "\n", style="bold")
    header.append("\n" + ui.TAGLINE + "\n", style="dim")
    version = _installed_version()
    folder = Path.cwd()
    shown = str(folder)
    home = str(Path.home())
    if shown == home:
        shown = "~"
    elif shown.startswith(home + os.sep):
        shown = "~" + shown[len(home):]
    reason = ui.broad_directory_reason(folder)
    note = f"   ({reason}, not a project)" if reason else ""
    lead = f"version {version}   folder "
    # A deep path would wrap and push the menu down: keep its tail, which is the part that names the project.
    room = console.width - len(lead) - len(note)
    mark = "…" if ui.supports_unicode(console.file) else "..."
    if len(shown) > room:
        # Keep at least a few characters of the tail; on a terminal too narrow even for that, show only the mark.
        shown = mark + shown[-(room - len(mark)):] if room > len(mark) + 2 else mark
    header.append("\n" + lead + shown, style="dim")
    if note:
        header.append(note, style="bold")
    return header


def _pause() -> None:
    console.print("\n[dim]Press any key to return to the menu[/dim]")
    try:
        click.getchar()
    except (KeyboardInterrupt, EOFError):
        pass


def _clear_screen() -> None:
    """Wipe the visible screen before the menu is drawn again, so earlier output never piles up above a new logo.
    Only in a real interactive terminal: a pipe, a log or a test never gets control codes."""
    from aletheore import ui

    if console.is_terminal and ui.is_interactive():
        console.clear()


def _ask(question: str) -> str:
    """Ask one question on the terminal; an empty answer (or Ctrl-C) means "never mind"."""
    try:
        return typer.prompt(question, default="", show_default=False).strip()
    except (click.Abort, EOFError):
        return ""


_MAIN_MENU = [
    ("scan", "Scan this folder", "build the evidence, no LLM call"),
    ("query", "Explore the evidence", "see what you can ask about the code"),
    ("audit", "Audit with an agent", "scan, then a grounded written report"),
    ("skills", "Find a Hallbook skill", "search or browse the skills your agent can use"),
    ("connect", "Connect your coding agent", "Claude Code, Codex, Cursor, Antigravity, ..."),
    ("dashboard", "Open the dashboard", "a live local view"),
    ("account", "Account", "sign in, sign out, status"),
    ("commands", "All commands", "every command, grouped by task"),
    ("quit", "Quit", ""),
]

# What Enter does on a command in the "All commands" picker. Commands that need nothing run as they are; the three
# in _COMMAND_PROMPTS ask for their one required argument first; the rest print their own --help.
_COMMAND_RUNS = {
    "scan": ["scan", "."], "audit": ["audit", "."], "query": ["query"], "dashboard": ["dashboard", "."],
    "init": ["init", "."], "cycles": ["cycles"], "login": ["login"], "logout": ["logout"], "status": ["status"],
}
_COMMAND_PROMPTS = {
    "diff": ["Baseline evidence file (old air.json)", "Evidence file to compare (new air.json)"],
    "verify": ["Report to check (path to a markdown file)"],
    "healthcheck": ["Base URL of the running server (for example http://localhost:8000)"],
}
_COMMAND_HELP = ("mcp", "watch", "config", "index", "locate", "usages", "dependents", "tests-for", "hook", "tokens")


def _command_args(name: str, answers: list[str]) -> list[str]:
    if name == "healthcheck":
        return ["healthcheck", ".", "--base-url", answers[0]]
    return [name, *answers]


def _connect_flow(kwargs: dict) -> bool:
    """Pick one coding agent to connect to this folder. True when something ran."""
    from aletheore import ui

    targets = [*_MCP_CLIENT_CONFIGS.keys(), "codex-cli", "claude-desktop"]
    # Single agents first: Enter on the first row must not fan out to every agent. Claude Desktop keeps one
    # global config outside the folder, so say so.
    sub = [ui.MenuItem(name, name, "global config, outside this folder" if name == "claude-desktop" else "")
           for name in targets]
    sub.append(ui.MenuItem("all", "All of them", f"writes config for all {len(targets)} above, including Claude Desktop's global one"))
    sub.append(ui.MenuItem("back", "Back"))
    choice = ui.choose(sub, console, header=Text("Connect which coding agent to this folder?", style="bold"),
                       footer="up/down to move, enter to choose, q to go back", **kwargs)
    if choice is None or choice.key == "back":
        return False
    _run_cli(["mcp-install", "."] if choice.key == "all" else ["mcp-install", ".", "--target", choice.key])
    return True


def _account_flow(kwargs: dict) -> bool:
    from aletheore import ui

    sub = [ui.MenuItem("status", "Status", "version and sign-in state"),
           ui.MenuItem("login", "Sign in", "with your GitHub account"),
           ui.MenuItem("logout", "Sign out", ""),
           ui.MenuItem("back", "Back")]
    choice = ui.choose(sub, console, header=Text("Account", style="bold"),
                       footer="up/down to move, enter to choose, q to go back", **kwargs)
    if choice is None or choice.key == "back":
        return False
    _run_cli([choice.key])
    return True


def _skills_flow(kwargs: dict) -> None:
    from aletheore.skills_browser import SkillsBrowser

    SkillsBrowser(console, read_key=kwargs.get("read_key"), ask=_ask, clear=_clear_screen).run()


def _run_command(name: str, kwargs: dict) -> bool:
    """Run one command picked from the "All commands" list. True when it printed something worth reading."""
    if name == "mcp-install":
        return _connect_flow(kwargs)
    if name == "hallbook":
        _skills_flow(kwargs)
        return False
    if name in _COMMAND_RUNS:
        _run_cli(_COMMAND_RUNS[name])
        return True
    if name in _COMMAND_PROMPTS:
        answers = []
        for question in _COMMAND_PROMPTS[name]:
            answer = _ask(question)
            if not answer:
                return False
            answers.append(answer)
        _run_cli(_command_args(name, answers))
        return True
    _run_cli([name, "--help"])
    return True


def _commands_flow(kwargs: dict, pause: Callable[[], None]) -> None:
    """The "All commands" picker: the task groups first, then the commands in the chosen group."""
    from aletheore import ui

    groups = [ui.MenuItem(title, title, ", ".join(names)) for title, names in _COMMAND_GROUPS]
    groups.append(ui.MenuItem("", "Back"))
    group_start = 0
    while True:
        _clear_screen()
        header = Text("All commands\n", style="bold")
        header.append("grouped by what you are doing", style="dim")
        picked = ui.choose(groups, console, header=header, start=group_start,
                           footer="up/down to move, enter to open, q to go back", numbers=False, **kwargs)
        if picked is None or not picked.key:
            return
        group_start = [g.key for g in groups].index(picked.key)
        names = dict(_COMMAND_GROUPS)[picked.key]
        rows = [ui.MenuItem(name, name, _COMMAND_DESCRIPTIONS[name]) for name in names]
        rows.append(ui.MenuItem("", "Back"))
        start = 0
        while True:
            _clear_screen()
            header = Text(f"{picked.label}\n", style="bold")
            header.append("enter runs a command or opens it", style="dim")
            command = ui.choose(rows, console, header=header, start=start, numbers=False,
                                footer="up/down to move, enter to run, q to go back", **kwargs)
            if command is None or not command.key:
                break
            start = [r.key for r in rows].index(command.key)
            if _run_command(command.key, kwargs):
                pause()
                return


def _run_menu(read_key=None, pause=None) -> None:
    """The arrow-key home screen shown by a bare `aletheore` in a terminal."""
    from aletheore import ui

    kwargs = {"read_key": read_key} if read_key is not None else {}
    pause = _pause if pause is None else pause
    items = [ui.MenuItem(*row) for row in _MAIN_MENU]
    start = 0
    first = True
    while True:
        if not first:
            _clear_screen()
        first = False
        picked = ui.choose(items, console, header=_menu_header(), start=start, **kwargs)
        if picked is None or picked.key == "quit":
            return
        start = [i.key for i in items].index(picked.key)
        if picked.key == "scan":
            _run_cli(["scan", "."])
        elif picked.key == "query":
            _run_cli(["query"])
        elif picked.key == "audit":
            _run_cli(["audit", "."])
        elif picked.key == "skills":
            _skills_flow(kwargs)
            continue
        elif picked.key == "connect":
            if not _connect_flow(kwargs):
                continue
        elif picked.key == "dashboard":
            _run_cli(["dashboard", "."])
        elif picked.key == "account":
            if not _account_flow(kwargs):
                continue
        elif picked.key == "commands":
            _commands_flow(kwargs, pause)
            continue
        pause()


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
        from aletheore import ui

        if ui.is_interactive():
            _print_update_notice_if_available()
            _run_menu()
            raise typer.Exit(code=0)
        console.print(_banner_panel())
        _print_update_notice_if_available()
        raise typer.Exit(code=0)


@app.command(help="audit a repository")
def audit(
    path: str = typer.Argument(".", help="repository path"),
    path_option: Optional[str] = _PATH_OPTION,
    yes: bool = _YES_OPTION,
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
        help="map database schema from migrations (on by default, or set by .aletheore.json's disabled_checks)",
    ),
    map_endpoints: Optional[bool] = typer.Option(
        None,
        "--map-endpoints/--no-map-endpoints",
        help="static API endpoint mapping (on by default, or set by .aletheore.json's disabled_checks)",
    ),
    check_static_analysis: Optional[bool] = typer.Option(
        None,
        "--check-static-analysis/--no-check-static-analysis",
        help="run static analysis scanners - Semgrep, gosec, Bandit, opt-in Bearer/Joern/SonarQube "
        "(on by default, or set by .aletheore.json's disabled_checks; each scanner self-skips "
        "if its binary isn't installed, and SonarQube self-skips unless SONARQUBE_HOST_URL is set)",
    ),
    check_bearer: Optional[bool] = typer.Option(
        None,
        "--check-bearer/--no-check-bearer",
        help="include Bearer (sensitive-data/PII flow scanner) in static analysis - opt-in, "
        "since its full-repo runtime doesn't scale cleanly with repo size. With neither flag "
        "passed: asks interactively on a real terminal, defaults to skipped otherwise (CI, "
        "scripts, the hosted worker)",
    ),
    check_joern: Optional[bool] = typer.Option(
        None,
        "--check-joern/--no-check-joern",
        help="include Joern's CFG-based asymmetric-cache-trust query (Go only) in static "
        "analysis - opt-in, off by default with no prompt: requires Joern installed "
        "separately, and a CPG build is real per-scan JVM/parsing cost",
    ),
) -> None:
    path = _resolve_path(path, path_option)
    _checked_dir(path)
    _workspace_guard(path, yes)
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
                map_schema,
                check_static_analysis,
                check_bearer,
                check_joern,
            )
        )
    if token is not None:
        console.print(
            "[bold yellow]warning:[/bold yellow] --token has no effect without --managed - ignored."
        )
    exit_code = _audit(
        path, agent, check_vulnerabilities, scan_git_history, check_licenses, map_endpoints, map_schema,
        check_static_analysis, check_bearer, check_joern,
    )
    if exit_code == 0:
        _stderr_console.print(_free_pr_review_nudge())
    raise typer.Exit(code=exit_code)


@app.command(help="run only the deterministic scan phase")
def scan(
    path: str = typer.Argument(".", help="repository path"),
    path_option: Optional[str] = _PATH_OPTION,
    yes: bool = _YES_OPTION,
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
        help="map database schema from migrations (on by default, or set by .aletheore.json's disabled_checks)",
    ),
    map_endpoints: Optional[bool] = typer.Option(
        None,
        "--map-endpoints/--no-map-endpoints",
        help="static API endpoint mapping (on by default, or set by .aletheore.json's disabled_checks)",
    ),
    check_static_analysis: Optional[bool] = typer.Option(
        None,
        "--check-static-analysis/--no-check-static-analysis",
        help="run static analysis scanners - Semgrep, gosec, Bandit, opt-in Bearer/Joern/SonarQube "
        "(on by default, or set by .aletheore.json's disabled_checks; each scanner self-skips "
        "if its binary isn't installed, and SonarQube self-skips unless SONARQUBE_HOST_URL is set)",
    ),
    check_bearer: Optional[bool] = typer.Option(
        None,
        "--check-bearer/--no-check-bearer",
        help="include Bearer (sensitive-data/PII flow scanner) in static analysis - opt-in, "
        "since its full-repo runtime doesn't scale cleanly with repo size. With neither flag "
        "passed: asks interactively on a real terminal, defaults to skipped otherwise (CI, "
        "scripts, the hosted worker)",
    ),
    check_joern: Optional[bool] = typer.Option(
        None,
        "--check-joern/--no-check-joern",
        help="include Joern's CFG-based asymmetric-cache-trust query (Go only) in static "
        "analysis - opt-in, off by default with no prompt: requires Joern installed "
        "separately, and a CPG build is real per-scan JVM/parsing cost",
    ),
    explain_network: bool = typer.Option(
        False, "--explain-network",
        help="show potential network traffic for the resolved scan settings, then exit without scanning",
    ),
) -> None:
    path = _resolve_path(path, path_option)
    _checked_dir(path)
    if explain_network:
        from aletheore.network_plan import scan_network_plan

        toggles = _resolve_check_toggles(
            Path(path).resolve(), check_vulnerabilities, scan_git_history,
            check_licenses, map_endpoints, map_schema, check_static_analysis,
        )
        plan = scan_network_plan(
            vulnerabilities=toggles[0], licenses=toggles[2], static_analysis=toggles[5],
        )
        console.print("[bold]Potential scan network traffic[/bold] (explanation only; no scan runs)")
        for entry in plan:
            state = "CONDITIONAL" if entry["enabled"] else "DISABLED / UNAVAILABLE"
            console.print(f"\n{state}: {entry['service']}", markup=False)
            console.print(entry["sends"], markup=False)
            console.print(f"Disable: {entry['disable']}", markup=False)
        console.print(
            "\nEnabled paths may make requests; exact counts depend on inputs and caches. "
            "Aletheore's deterministic core does not upload source or air.json. "
            "External scanner behavior depends on its installation and configuration; "
            "this is not a firewall or a packet trace. Audit, index, and healthcheck "
            "have separate network behavior.", markup=False,
        )
        return
    _workspace_guard(path, yes)
    exit_code, _evidence, _evidence_path = _scan(
        path, check_vulnerabilities, scan_git_history, check_licenses, map_endpoints, map_schema,
        check_static_analysis, check_bearer, check_joern,
    )
    if exit_code == 0:
        _stderr_console.print(_free_pr_review_nudge())
    raise typer.Exit(code=exit_code)


@app.command(help="explore a small synthetic repository with the real local scanner")
def demo(
    plain: bool = typer.Option(False, "--plain", help="print the full demo report instead of the interactive tour"),
) -> None:
    from aletheore import ui

    if not plain and ui.is_interactive():
        from aletheore.demo_walkthrough import DemoWalkthrough

        # Keep monochrome styling (bold/reverse) in the tour. The plain command
        # console disables ANSI styling entirely, which flattens the hierarchy.
        tour_console = Console(file=console.file, color_system="standard", no_color=True, highlight=False)
        raise typer.Exit(code=DemoWalkthrough(tour_console, run_command=_run_cli, ask=_ask).run())
    import shlex
    import subprocess  # nosec B404 - exception types only; demo owns fixed Git calls

    from aletheore.demo import create_demo
    from aletheore.query import (
        ModuleNotFoundInEvidenceError, SymbolNotFoundInEvidenceError,
        find_blast_radius, find_hotspots, find_symbol_source,
    )
    from aletheore.tests_for import TestsForError, tests_for_report

    console.print(
        "[bold]Aletheore demo[/bold] — synthetic Python checkout example. "
        "Creating local sample history and scanning it; no clone, hosted model, "
        "registry checks, or external scanners."
    )
    try:
        repo, evidence = create_demo()
    except (OSError, subprocess.SubprocessError, GitAnalysisError) as exc:
        detail = (exc.stderr or exc.stdout) if isinstance(exc, subprocess.CalledProcessError) else None
        console.print(f"Demo failed: {exc}", markup=False)
        if detail:
            console.print(detail.strip(), markup=False)
        raise typer.Exit(code=1)
    try:
        source = find_symbol_source(evidence, repo, "pricing.py", "total")
        impact = find_blast_radius(evidence, repo, "pricing.py", "total")
        candidates = tests_for_report(evidence, repo, "pricing.py", summary_only=True)
    except (OSError, ModuleNotFoundInEvidenceError, SymbolNotFoundInEvidenceError, TestsForError) as exc:
        console.print(f"Demo could not read its sample evidence: {exc}", markup=False)
        console.print(f"Sample and evidence kept at: {repo}", markup=False, soft_wrap=True)
        console.print("Run aletheore demo again to create a fresh sample.")
        raise typer.Exit(code=1)
    console.print("\n[bold]1. Your task: change pricing.py::total[/bold]")
    console.print(f"pricing.py:{source['start_line']}-{source['end_line']}", markup=False)
    console.print(source["source"], markup=False, highlight=False)
    console.print("Before changing this function, trace its callers and find tests to investigate.")
    console.print("\n[bold]2. Follow the dependency graph[/bold] (importer → dependency)")
    for source, target in evidence["repository"]["dependency_graph"]["edges"]:
        console.print(f"  {source} → {target}", markup=False)
    console.print(
        f"\n{impact['affected_total']} files depend on pricing.py: "
        f"{impact['direct_total']} directly, {impact['transitive_total']} transitively.",
        markup=False,
    )
    console.print("Direct: " + ", ".join(impact["direct_dependents"]), markup=False)
    console.print("Transitive: " + ", ".join(impact["transitive_dependents"]), markup=False)
    console.print("Confirmed caller of total: " + ", ".join(impact["confirmed_callers"]), markup=False)
    console.print("These are resolved static imports, not a guarantee of every runtime impact.")
    console.print("\n[bold]3. Find candidate tests[/bold]")
    console.print(candidates, markup=False, highlight=False)
    console.print("Imports and filenames suggest candidates; they do not prove runtime coverage.")
    console.print("The tests-for command below includes full coverage and freshness details.")
    console.print("The demo did not run these tests. Inspect and run them before relying on a change.")
    console.print("\n[bold]4. See why this file deserves attention[/bold]")
    table = Table(title="Hotspots — computed from the sample's synthetic commit history")
    table.add_column("File")
    table.add_column("Commits", justify="right")
    table.add_column("Direct dependents", justify="right")
    for hotspot in find_hotspots(evidence, None):
        table.add_row(hotspot["path"], str(hotspot["churn_count"]), str(hotspot["dependents_count"]))
    console.print(table)
    console.print(f"\nSample and evidence kept at: {repo}", markup=False, soft_wrap=True)
    quote_command = subprocess.list2cmdline if os.name == "nt" else shlex.join
    console.print("\n[bold]Keep exploring the sample[/bold] — run these commands:")
    # Soft wrapping leaves each shell command intact when copied or redirected,
    # including quoted temporary paths with spaces. Short relative commands then
    # avoid repeating an often very long macOS temporary-directory prefix.
    commands = [
        ["cd", str(repo)],
        ["aletheore", "locate", "total"],
        ["aletheore", "dependents", "pricing.py"],
        ["aletheore", "tests-for", "pricing.py"],
        ["aletheore", "query", "hotspots"],
        ["aletheore", "dashboard"],
    ]
    for command in commands:
        console.print(quote_command(command), markup=False, highlight=False, soft_wrap=True)
    console.print("\n[bold]Try your own repository next[/bold]")
    console.print("Normal scans can enable network checks that this sample skipped. Inspect the plan first.")
    own_repo = r"C:\path\to\your-repo" if os.name == "nt" else "/path/to/your-repo"
    for command in (["cd", own_repo], ["aletheore", "scan", "--explain-network"], ["aletheore", "scan"]):
        console.print(quote_command(command), markup=False, highlight=False, soft_wrap=True)
    console.print("Delete this temporary directory when finished. Crash-reporting preferences still apply.")


@app.command(
    help="re-scan and re-index automatically whenever source files change"
)
def watch(
    path: str = typer.Argument(".", help="repository path"),
    path_option: Optional[str] = _PATH_OPTION,
    yes: bool = _YES_OPTION,
    debounce: float = typer.Option(
        WATCH_DEBOUNCE_SECONDS,
        "--debounce",
        min=0.0,
        help="seconds of quiet before rebuilding, so one burst of saves is one rebuild",
    ),
) -> None:
    path = _resolve_path(path, path_option)
    _checked_dir(path)
    _workspace_guard(path, yes)
    raise typer.Exit(code=_watch(path, debounce))


@app.command(help="scaffold a .aletheore.json config file in a repository")
def init(
    path: str = typer.Argument(".", help="repository path"),
    path_option: Optional[str] = _PATH_OPTION,
    yes: bool = _YES_OPTION,
) -> None:
    path = _resolve_path(path, path_option)
    _checked_dir(path)
    _workspace_guard(path, yes)
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
    try:
        config_path.write_text(json.dumps(default_config, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        _stderr_console.print(
            f"[bold red]error:[/bold red] could not write {escape_markup(str(config_path))}: "
            f"{escape_markup(_os_error_text(exc))}"
        )
        raise typer.Exit(code=1)
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
        "comments only (air.json always keeps everything)",
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
    _checked_dir(path)
    raise typer.Exit(code=_index(path))


def _warn_ignored_query_flags(
    kind: str,
    full: bool,
    agent: Optional[str],
    k_given: Optional[int],
    language: Optional[str],
    exclude: list[str],
    under: list[str],
    no_tests: bool,
) -> None:
    from aletheore.scope_filter import SCOPABLE

    relevant = {
        "--full": {"changes"},
        "--agent": {"answer"},
        "--k": {"search-codebase", "answer"},
        "--language": {"search-codebase", "ast-pattern"},
        "--exclude": set(SCOPABLE),
        "--under": set(SCOPABLE),
        "--no-tests": set(SCOPABLE),
    }
    given = {
        "--full": full,
        "--agent": agent is not None,
        "--k": k_given is not None,
        "--language": language is not None,
        "--exclude": bool(exclude),
        "--under": bool(under),
        "--no-tests": no_tests,
    }
    for flag, used in given.items():
        if used and kind not in relevant[flag]:
            if flag in ("--exclude", "--under", "--no-tests"):
                if kind in QUERY_FUNCTIONS:
                    continue  # _query rejects these for the kinds it dispatches itself
                print(
                    f"warning: {flag} has no effect for 'query {kind}' "
                    f"(it applies to: {', '.join(sorted(SCOPABLE))})",
                    file=sys.stderr,
                )
            else:
                print(
                    f"warning: {flag} has no effect for 'query {kind}' (it applies to: "
                    f"{', '.join(sorted(relevant[flag]))})",
                    file=sys.stderr,
                )


@app.command(
    help=(
        "query an existing air.json. Related top-level commands answer common questions directly: "
        "locate, usages, dependents, tests-for and cycles"
    )
)
def query(
    # Optional rather than required: a bare `aletheore query` previously hit
    # Typer's "Missing argument 'KIND'", which names no kinds at all, leaving
    # every capability discoverable only via --help. Defaulting to None turns
    # the most likely first invocation into the listing the user was after.
    # Exit code stays 0 - asking what the kinds are is a successful question,
    # not a usage error. An *unknown* kind still exits 1, via _query.
    kind: Optional[str] = typer.Argument(
        # Computed, not a hardcoded count in the help text itself - a
        # hardcoded "23" here previously drifted to 24 real kinds the
        # moment one more was added and nobody thought to grep for it.
        None, help=f"one of the {len(QUERY_KIND_CHOICES)} query kinds; omit to list them by category"
    ),
    target: Optional[str] = typer.Argument(
        None,
        help=(
            "target for kinds that need one: a repo-relative file path (imports, imported-by, symbols, cluster), "
            "a branch name, a symbol or dependency name, or 'METHOD /path' or a bare /path for evidence-for-endpoint. "
            "For 'secrets' it is a file-path filter, not a keyword: there is no 'all'"
        ),
    ),
    symbol: Optional[str] = typer.Argument(None, help="symbol name for 'symbol-source'"),
    repo_path: str = typer.Option(".", "--path", help="repository path"),
    full: bool = typer.Option(
        False, "--full", help="show the full raw diff instead of the curated summary (only 'changes')"
    ),
    agent: Optional[str] = typer.Option(None, "--agent", help="provider for 'answer'"),
    k_given: Optional[int] = typer.Option(None, "--k", min=1, help="number of semantic search results (default 10)"),
    language: Optional[str] = typer.Option(
        None,
        "--language",
        help="restrict 'search-codebase' to one language, e.g. python; required for 'ast-pattern'",
    ),
    exclude: list[str] = typer.Option([], "--exclude", help="'dead-code'/'error-handling': leave out paths with this exact directory or file name; repeatable"),
    under: list[str] = typer.Option([], "--under", help="'dead-code'/'error-handling': only paths below this directory; repeatable"),
    no_tests: bool = typer.Option(False, "--no-tests", help="'dead-code'/'error-handling': leave out test files"),
) -> None:
    if kind is None:
        console.print(_query_kinds_panel())
        raise typer.Exit(code=0)
    _warn_ignored_query_flags(kind, full, agent, k_given, language, exclude, under, no_tests)
    raise typer.Exit(
        code=_query(kind, target, repo_path, full, agent, 10 if k_given is None else k_given, symbol, language, exclude, under, no_tests)
    )


@app.command(
    help=(
        "list every file that imports a file, directly or through other imports, as one complete deduplicated list, "
        "e.g. aletheore dependents src/click/_compat.py --under src --no-tests, or aletheore dependents internal/js --packages"
    )
)
def dependents(
    file: str = typer.Argument(..., help="file path exactly as it appears in the scan, or a directory to treat all its files as one package"),
    path: str = typer.Argument(".", help="repository path"),
    exclude: list[str] = typer.Option(
        [], "--exclude", help="leave out files under a directory with this exact name, or with this exact file name; repeatable"
    ),
    under: list[str] = typer.Option([], "--under", help="only list files below this directory; repeatable"),
    no_tests: bool = typer.Option(False, "--no-tests", help="leave out test files (tests/ directories and test-shaped file names)"),
    packages: bool = typer.Option(False, "--packages", help="list one line per directory (package) instead of one per file"),
    path_option: Optional[str] = _PATH_OPTION,
) -> None:
    from aletheore.dependents import DependentsError, dependents_report

    repo = Path(_resolve_path(path, path_option)).resolve()
    evidence = _load_evidence_or_exit(repo)
    _warn_unmatched_under(evidence, under)
    try:
        print(dependents_report(evidence, repo, file, exclude, under, no_tests, packages))
    except DependentsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise typer.Exit(code=1)


@app.command(
    help=(
        "every circular import dependency in the repo, with a concrete example chain for each one, "
        "as one complete answer - e.g. aletheore cycles"
    )
)
def cycles(
    path: str = typer.Argument(".", help="repository path"),
    exclude: list[str] = typer.Option(
        [], "--exclude", help="leave out files under a directory with this exact name, or with this exact file name; repeatable"
    ),
    under: list[str] = typer.Option([], "--under", help="only list files below this directory; repeatable"),
    path_option: Optional[str] = _PATH_OPTION,
) -> None:
    from aletheore.cycles import cycles_report

    repo = Path(_resolve_path(path, path_option)).resolve()
    evidence = _load_evidence_or_exit(repo)
    _warn_unmatched_under(evidence, under)
    print(cycles_report(evidence, repo, exclude, under))


@app.command(
    name="tests-for",
    help=(
        "every test file that exercises a file - via the import graph or its naming convention - as one "
        "complete answer, e.g. aletheore tests-for src/click/_compat.py"
    ),
)
def tests_for(
    file: str = typer.Argument(..., help="file path exactly as it appears in the scan"),
    path: str = typer.Argument(".", help="repository path"),
    path_option: Optional[str] = _PATH_OPTION,
) -> None:
    from aletheore.tests_for import TestsForError, tests_for_report

    repo = Path(_resolve_path(path, path_option)).resolve()
    evidence = _load_evidence_or_exit(repo)
    try:
        print(tests_for_report(evidence, repo, file))
    except TestsForError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise typer.Exit(code=1)


@app.command(
    help=(
        "every call site and reference to a function or class, across the whole repo, as one complete answer "
        "with the matching source line - e.g. aletheore usages parse_config --under src --exclude tests"
    )
)
def usages(
    symbol: str = typer.Argument(..., help="function or class name exactly as it appears in the scan"),
    path: str = typer.Argument(".", help="repository path"),
    exclude: list[str] = typer.Option(
        [], "--exclude", help="leave out hits under a directory with this exact name, or in this exact file name; repeatable"
    ),
    under: list[str] = typer.Option([], "--under", help="only show hits below this directory; repeatable"),
    path_option: Optional[str] = _PATH_OPTION,
) -> None:
    from aletheore.usages import UsagesError, usages_report

    repo = Path(_resolve_path(path, path_option)).resolve()
    evidence = _load_evidence_or_exit(repo)
    try:
        _warn_unmatched_under(evidence, under)
        print(usages_report(evidence, repo, symbol, exclude, under))
    except UsagesError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise typer.Exit(code=1)


@app.command(
    help=(
        "find where names are defined and print the matching functions with their source, "
        "e.g. aletheore locate 'write_usage HelpFormatter'"
    )
)
def locate(
    query: str = typer.Argument(..., help="names to look for, as plain words"),
    path: str = typer.Argument(".", help="repository path"),
    path_option: Optional[str] = _PATH_OPTION,
) -> None:
    from aletheore.code_locator import locate_result

    repo = Path(_resolve_path(path, path_option)).resolve()
    evidence = _load_evidence_or_exit(repo)
    report, matched = locate_result(evidence, repo, query)
    print(report)
    if not matched:
        # Same convention as `usages`: nothing found is a non-zero exit.
        raise typer.Exit(code=1)


@app.command(
    help=(
        "compare two air.json files. Exit codes: 0 = no gate failed, 1 = a --fail-on-new-* gate failed, "
        "2 = usage or input error (missing file, invalid JSON, wrong schema)"
    )
)
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
        help=(
            "exit 1 if a new dependency vulnerability finding appears (not enforced, with a note, "
            "when the vulnerability check was skipped in either snapshot)"
        ),
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
    _checked_dir(repo_path)
    raise typer.Exit(code=_verify(report, repo_path))


@app.command(
    help=(
        "run an MCP server scoped to a repository; it re-scans in the background "
        "when source files change unless --no-watch is given"
    )
)
def mcp(
    path: str = typer.Argument(".", help="repository path"),
    path_option: Optional[str] = _PATH_OPTION,
    yes: bool = _YES_OPTION,
    agent: Optional[str] = typer.Option(
        None,
        "--agent",
        help="provider for the aletheore_answer tool; that tool is registered only when --agent is given",
    ),
    no_watch: bool = typer.Option(
        False,
        "--no-watch",
        help=(
            "do not re-scan in the background when source files change "
            f"(same as {WATCH_ENV_VAR}=0); always off under --profile nav regardless of this flag"
        ),
    ),
    profile: str = typer.Option(
        "full",
        "--profile",
        help=(
            "tool set to expose: full (every tool, with background watching), nav (three "
            "code-navigation tools, small enough to load up front, with background watching off) or "
            "router (one tool, aletheore_ask, that picks the right query; watching off) or "
            "lean (the router plus nine direct code tools, about a fifth of full's definitions; watching off)"
        ),
    ),
) -> None:
    if profile not in ("full", "nav", "router", "lean"):
        _stderr_console.print(
            f"[bold red]error:[/bold red] unknown profile {profile!r}; choose full, nav, router or lean"
        )
        raise typer.Exit(code=2)
    path = _resolve_path(path, path_option)
    _checked_dir(path)
    _workspace_guard(path, yes)
    # Real audit finding (Flash Review): `--profile nav` silently disabled
    # watching with no indication - --no-watch's own help text describes
    # watching as the default, and nothing said choosing nav also turns it
    # off. A one-line note here, only when the user didn't already say
    # --no-watch (so it's genuinely a consequence of the profile, not
    # something they already asked for), makes it visible instead of silent.
    if profile == "nav" and not no_watch:
        # stderr: stdout is the JSON-RPC channel from here on.
        _stderr_console.print(
            "[dim]--profile nav does not watch for file changes; re-run the scan yourself after edits, "
            "or use --profile full for background watching.[/dim]"
        )
    raise typer.Exit(code=_mcp(path, agent, watch=not no_watch and profile == "full", profile=profile))


@app.command(
    name="mcp-install",
    help="write MCP client config so a coding agent auto-launches this repo's MCP server",
)
def mcp_install(
    path: str = typer.Argument(".", help="repository path"),
    path_option: Optional[str] = _PATH_OPTION,
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help=(
            "skip questions: the 'Write these? [y/N]' list shown in a terminal when no --target is given, "
            "and the check for folders that don't look like a project"
        ),
    ),
    target: list[str] = typer.Option(
        [],
        "--target",
        help=(
            "which client(s) to configure (default: all); one of: "
            f"{', '.join([*_MCP_CLIENT_CONFIGS.keys(), 'codex-cli', 'claude-desktop'])}"
        ),
    ),
) -> None:
    path = _resolve_path(path, path_option)
    _checked_dir(path)
    _workspace_guard(path, yes)
    raise typer.Exit(code=_mcp_install(path, target, yes))


@app.command(help="run a live local dashboard scoped to a repository")
def dashboard(
    path: str = typer.Argument(".", help="repository path"),
    path_option: Optional[str] = _PATH_OPTION,
    yes: bool = _YES_OPTION,
    port: int = typer.Option(8420, "--port", help="port to serve the dashboard on"),
) -> None:
    path = _resolve_path(path, path_option)
    _checked_dir(path)
    _workspace_guard(path, yes)
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
    _checked_dir(path)
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


config_app = typer.Typer(help="manage local CLI preferences")
app.add_typer(config_app, name="config")

hook_app = typer.Typer(help="commands for coding-agent hooks")
app.add_typer(hook_app, name="hook")


@hook_app.command(
    "pre-read",
    help="PreToolUse hook for Read: denies a full re-read of a file that is unchanged on disk "
    "since the agent already read it in full this session. Opt-in via ALETHEORE_READ_GUARD=1; "
    "silent and a no-op otherwise, and never fails.",
)
def hook_pre_read() -> None:
    from aletheore.read_guard import main_pre as hook_main

    hook_main()


@hook_app.command(
    "post-read",
    help="PostToolUse hook for Read: records a file's current on-disk hash after a full read so "
    "a later identical read can be recognized as redundant. Opt-in via ALETHEORE_READ_GUARD=1; "
    "silent and a no-op otherwise, and never fails.",
)
def hook_post_read() -> None:
    from aletheore.read_guard import main_post as hook_main

    hook_main()


@hook_app.command(
    "evidence",
    help="PreToolUse/PostToolUse hook: tells the agent what the scan knows about a file it is about to edit "
    "or has just read (importers, reach, tests, owner, churn). Once per file per session; silent when the "
    "scan has nothing to say, and never fails. Prefer the settings from `evidence-snippet`, which call the "
    "fast module entry point instead of this command.",
)
def hook_evidence(
    mode: str = typer.Option("inject", "--mode", help="inject (short block) or pointer (one line)"),
) -> None:
    from aletheore.evidence_hook import main as hook_main

    hook_main(["--mode", mode])


@hook_app.command(
    "evidence-snippet",
    help="print the Claude Code settings.json hooks block that turns the evidence hook on",
)
def hook_evidence_snippet(
    mode: str = typer.Option("inject", "--mode", help="inject (short block) or pointer (one line)"),
) -> None:
    import json as _json
    import shlex as _shlex
    import sys as _sys

    if mode not in ("inject", "pointer"):
        console.print(f"[bold red]error:[/bold red] unknown mode {mode!r}; choose inject or pointer")
        raise typer.Exit(code=2)
    # The interpreter path can hold spaces (a pipx venv lives under "Application Support" on macOS), so the
    # command is built from an argv list and quoted for the shell that will run it.
    argv = [_sys.executable, "-m", "aletheore.evidence_hook", "--mode", mode]
    if _sys.platform == "win32":
        # cmd.exe style: quote an argument that is empty or holds a space, tab, quote or cmd metacharacter
        command = " ".join(
            '"' + arg.replace('"', '\\"') + '"' if not arg or any(c in arg for c in ' \t"&^|<>()') else arg
            for arg in argv
        )
    else:
        command = _shlex.join(argv)
    entry = lambda matcher: [{"matcher": matcher, "hooks": [{"type": "command", "command": command}]}]  # noqa: E731
    typer.echo(
        _json.dumps(
            {"hooks": {"PreToolUse": entry("Edit|MultiEdit|Write"), "PostToolUse": entry("Read")}}, indent=2
        )
    )

from aletheore.tokens_cli import tokens_app  # noqa: E402

app.add_typer(tokens_app, name="tokens")


@config_app.command(
    "crash-reporting",
    help="show or change whether unhandled CLI errors are reported to Aletheore",
)
def config_crash_reporting(
    state: Optional[str] = typer.Argument(
        None, help="'on' or 'off' - omit to show the current state"
    ),
) -> None:
    env_override = crash_reporting_env_override()
    if state is None:
        current = "on" if is_crash_reporting_enabled() else "off"
        console.print(f"Crash reporting: {current}")
        if env_override is not None:
            stored = "on" if stored_crash_reporting_enabled() else "off"
            console.print(
                f"(set by the ALETHEORE_CRASH_REPORTING={env_override} environment variable, "
                f"which overrides the saved preference: {stored})"
            )
        raise typer.Exit(code=0)

    normalized = state.strip().lower()
    if normalized not in ("on", "off"):
        console.print(f"[bold red]error:[/bold red] expected 'on' or 'off', got '{state}'")
        raise typer.Exit(code=1)

    if not set_crash_reporting_enabled(normalized == "on"):
        _stderr_console.print(
            "[bold red]error:[/bold red] could not save the preference "
            "(is the config directory writable?); crash reporting was not changed"
        )
        raise typer.Exit(code=1)
    console.print(f"[bold green]Crash reporting turned {normalized}.[/bold green]")
    if env_override is not None:
        effective = "on" if is_crash_reporting_enabled() else "off"
        console.print(
            f"Note: the ALETHEORE_CRASH_REPORTING={env_override} environment variable "
            f"overrides this setting, so crash reporting stays {effective} while it is set."
        )


def _plain_width() -> int:
    """Columns available to plain command output: the terminal's width, or 100 when there is no terminal."""
    return max(shutil.get_terminal_size((100, 24)).columns, 40)


def _skill_line(meta: skill_hub.SkillMeta, width: int) -> str:
    """`id  Title` on one line: the id is never cut unless it alone is wider than the line, the title gives way."""
    from aletheore import ui

    head = f"{meta.skill_id}  "
    if len(head) >= width - 1:
        return ui.truncate(meta.skill_id, width)
    return head + ui.truncate(meta.title, width - len(head))


_EXTRAS_MIN_WIDTH = 70  # below this a third line per result would wrap or crowd the list, so it is left out


def _skill_extras_line(meta: skill_hub.SkillMeta, width: int) -> str | None:
    """`    needs: a, b; also: x, y` for a skill that requires something or has alternates; None when it has
    neither or the terminal is narrow. The full lists are always in the MCP search result."""
    from aletheore import ui

    if width < _EXTRAS_MIN_WIDTH:
        return None
    parts = []
    if meta.requires:
        parts.append("needs: " + ", ".join(meta.requires))
    alternates = getattr(meta, "alternates", None)
    if alternates:
        parts.append("also: " + ", ".join(alternates))
    if not parts:
        return None
    return "    " + ui.truncate("; ".join(parts), width - 4)


skill_hub_app = typer.Typer(help="browse and manage Aletheore Hallbook, the skill hub")
app.add_typer(skill_hub_app, name="hallbook")
# The feature was called "skill-hub" before the rename to Hallbook. Kept as a
# hidden alias so existing scripts keep working without a second name in --help.
app.add_typer(skill_hub_app, name="skill-hub", hidden=True)


def _hallbook_error(message: str) -> None:
    """Print `error: ...` the way every other command does (text escaped, never wrapped) and exit 1."""
    from rich.markup import escape

    console.print(f"[bold red]error:[/bold red] {escape(message)}", soft_wrap=True)
    raise typer.Exit(code=1)


def _check_category_or_exit(category: Optional[str]) -> None:
    try:
        skill_hub.check_category(category)
    except ValueError as exc:
        _hallbook_error(str(exc))


def _skill_not_found(skill_id: str) -> None:
    """`error: skill not found: x` with up to five ids it may have meant, then exit 1."""
    message = f"skill not found: {skill_id}"
    suggestions = skill_hub.suggest_skill_ids(skill_id)
    if suggestions:
        message += ". Did you mean: " + ", ".join(suggestions) + "?"
    _hallbook_error(message)


@skill_hub_app.command("list", help="list curated and custom skills")
def skill_hub_list(
    category: Optional[str] = typer.Option(None, "--category", help="filter to one category"),
) -> None:
    # typer.echo, not console.print: titles come from skill frontmatter (or a
    # custom skill's file name) and must not be parsed as Rich markup.
    from aletheore import ui

    _check_category_or_exit(category)
    # A pipe gets every line whole; a terminal gets lines cut to its width so none wraps.
    width = _plain_width() if sys.stdout.isatty() else None
    for meta in skill_hub.list_skills(category):
        line = f"{meta.skill_id}  {meta.title}"
        typer.echo(line if width is None else _skill_line(meta, width))


@skill_hub_app.command("get", help="print a skill's content")
def skill_hub_get(skill_id: str) -> None:
    try:
        skill = skill_hub.get_skill(skill_id)
    except skill_hub.SkillNotFoundError as exc:
        _skill_not_found(str(exc))
    # Raw write, not console.print: Rich would parse the body as markup
    # (deleting bracketed text like "[borkweb/skills]", or raising
    # MarkupError on a stray "[/x]") and hard-wrap long lines. The output
    # must be the skill body byte for byte, so no extra newline either when
    # the body already ends with one.
    typer.echo(skill.content, nl=not skill.content.endswith("\n"))


@skill_hub_app.command("categories", help="show the shelves and categories, with skill counts")
def skill_hub_categories() -> None:
    for shelf in skill_hub.list_overview():
        typer.echo(f"{shelf['title']} ({shelf['skills']})")
        for entry in shelf["categories"]:
            typer.echo(f"  {entry['category']}  {entry['skills']}")


@skill_hub_app.command("search", help="find skills by what you are about to do, e.g. 'flaky test'")
def skill_hub_search(
    query: list[str] = typer.Argument(..., help="words describing the task"),
    category: Optional[str] = typer.Option(None, "--category", help="only search this category"),
    limit: int = typer.Option(10, "--limit", min=1, max=50, help="maximum results"),
) -> None:
    _check_category_or_exit(category)
    text = " ".join(query).strip()
    if len(text) < 2 or not skill_hub._tokens(text):
        _hallbook_error("the search needs at least 2 characters, or a few words describing the task (for example: flaky test)")
    capped = skill_hub.cap_query(text)
    if capped != text:
        typer.echo(f"Note: the query was cut to its first {len(capped)} characters.", err=True)
    results = skill_hub.search_skills(capped, category=category, limit=limit)
    if not results:
        typer.echo("No skills matched. Try fewer or different words, or run 'aletheore hallbook categories'.")
        return
    from aletheore import ui

    width = _plain_width()
    for meta in results:
        typer.echo(_skill_line(meta, width))
        if " ".join(meta.description.split()):
            typer.echo("    " + ui.truncate(meta.description, width - 4))
        extras = _skill_extras_line(meta, width)
        if extras:
            typer.echo(typer.style(extras, dim=True))


@skill_hub_app.command(
    "snippet", help="print the short instruction to paste into AGENTS.md / CLAUDE.md so your agent uses Hallbook"
)
def skill_hub_snippet() -> None:
    typer.echo(skill_hub.AGENT_INSTRUCTION_SNIPPET, nl=False)


@skill_hub_app.command(
    "suggest", help="suggest a Hallbook skill for a UserPromptSubmit hook (reads JSON on stdin)"
)
def skill_hub_suggest(
    mode: str = typer.Option("hint", "--mode", help="hint or inject"),
) -> None:
    # One implementation for the Typer command and the fast pre-import path.
    from aletheore.hallbook_hook import main

    main(["--mode", mode])


@skill_hub_app.command(
    "hook", help="print the Claude Code UserPromptSubmit settings snippet to paste"
)
def skill_hub_hook(
    mode: str = typer.Option("hint", "--mode", help="hint or inject"),
) -> None:
    if mode not in skill_hub.HOOK_MODES:
        console.print(
            f"[bold red]error:[/bold red] unknown mode '{escape_markup(mode)}'; use one of: {', '.join(skill_hub.HOOK_MODES)}",
            soft_wrap=True,
        )
        raise typer.Exit(code=2)
    executable = sys.executable
    if " " in executable:
        executable = f'"{executable}"'
    command = f"{executable} -m aletheore.hallbook_hook --mode {mode}"
    settings = {
        "hooks": {
            "UserPromptSubmit": [{"hooks": [{"type": "command", "command": command}]}]
        }
    }
    typer.echo(json.dumps(settings, ensure_ascii=False))
    typer.echo(
        "Paste this into .claude/settings.json (project) or ~/.claude/settings.json (user).",
        err=True,
    )


@skill_hub_app.command("files", help="list a skill's companion files (references, scripts, templates)")
def skill_hub_files(skill_id: str) -> None:
    try:
        skill_hub.get_skill(skill_id)
    except skill_hub.SkillNotFoundError as exc:
        _skill_not_found(str(exc))
    files, total = skill_hub.list_skill_files(skill_id)
    if not files:
        typer.echo("No companion files.")
        return
    for rel in files:
        typer.echo(rel)
    if total > len(files):
        typer.echo(f"... and {total - len(files)} more (showing the first {len(files)}).")


@skill_hub_app.command("get-file", help="print one companion file of a skill")
def skill_hub_get_file(skill_id: str, path: str) -> None:
    try:
        text = skill_hub.read_skill_file(skill_id, path)
    except skill_hub.SkillNotFoundError as exc:
        _skill_not_found(str(exc))
    except skill_hub.SkillFileError as exc:
        _hallbook_error(str(exc))
    typer.echo(text, nl=not text.endswith("\n"))


def _info_field(label: str, value: str, width: int) -> list[str]:
    """`Label    value`, wrapped under the value; a long unbroken value (a URL) stays whole so it can be copied."""
    import textwrap

    head = f"{label:<11}"
    return textwrap.wrap(
        value, width=max(width, len(head) + 20), initial_indent=head, subsequent_indent=" " * len(head),
        break_long_words=False, break_on_hyphens=False,
    ) or [head.rstrip()]


def _print_skill_info(info: dict, width: int) -> None:
    from aletheore import ui

    typer.echo(ui.truncate(info["title"], width))
    typer.echo(info["skill_id"])
    typer.echo("")
    if info["custom"]:
        typer.echo("This is a custom skill you added: its text is yours, it is not vetted by Aletheore,")
        typer.echo("and there is no licence information for it.")
        return
    rows = [
        ("Shelf", info["shelf_title"] or "none"),
        ("Category", info["category"]),
        ("License", info["license"] or "not recorded"),
        ("Copyright", info["copyright"] or skill_hub.NOT_RECORDED),
        ("Source", info["source_url"] or "not recorded"),
        ("Needs", ", ".join(info["requires"]) or "nothing"),
        ("Files", f"{info['files_total']} ({info['scripts']} {'script' if info['scripts'] == 1 else 'scripts'})"),
    ]
    for label, value in rows:
        for line in _info_field(label, value, width):
            typer.echo(line)
    typer.echo("")
    import textwrap

    typer.echo(textwrap.fill(
        f"Full licence text: aletheore hallbook info {info['skill_id']} --licence", width=max(width, 40),
        break_long_words=False, break_on_hyphens=False,
    ))


def _print_catalogue_summary(width: int) -> None:
    summary = skill_hub.catalogue_summary()
    typer.echo(f"Hallbook catalogue: {summary['total']} skills")
    typer.echo("")
    typer.echo("Licences")
    for licence, count in summary["licenses"]:
        typer.echo(f"  {licence:<14}{count}")
    typer.echo("")
    typer.echo("Shelves")
    for shelf in summary["shelves"]:
        typer.echo(f"  {shelf['title']:<18}{shelf['skills']}")
    typer.echo("")
    typer.echo("Copyright holders and sources for every skill are in:")
    typer.echo(summary["notice_path"] if summary["notice_present"] else f"{summary['notice_path']} (not found)")
    typer.echo("")
    typer.echo("Details of one skill: aletheore hallbook info SKILL")


@skill_hub_app.command("info", help="show a skill's licence, copyright holder and source (no argument: the catalogue in numbers)")
def skill_hub_info(
    skill: Optional[str] = typer.Argument(
        None, help="a skill id such as debugging/systematic-debugging, or its bare name if that is unambiguous"
    ),
    licence: bool = typer.Option(False, "--licence", "--license", help="print the skill's full licence text"),
) -> None:
    width = _plain_width()
    if skill is not None:
        skill = skill.strip()
        if not skill:
            _hallbook_error("a skill name is required, for example: aletheore hallbook info debugging/systematic-debugging")
    if skill is None:
        if licence:
            _hallbook_error("--licence needs a skill: aletheore hallbook info SKILL --licence")
        _print_catalogue_summary(width)
        return
    skill_id, suggestions = skill_hub.resolve_skill_id(skill)
    if skill_id is None:
        if suggestions and "/" not in skill and all(s.split("/", 1)[-1] == skill for s in suggestions):
            _hallbook_error(f"'{skill}' is ambiguous. Use the full id: " + ", ".join(suggestions))
        _skill_not_found(skill)
    if licence:
        if skill_id.startswith(skill_hub._CUSTOM_PREFIX):
            typer.echo(f"{skill_id} is a custom skill you added: there is no licence information for it.")
            return
        try:
            text = skill_hub.skill_license_text(skill_id)
        except skill_hub.SkillNotFoundError:
            _skill_not_found(skill_id)
        if text is None:
            try:
                spdx = skill_hub.skill_info(skill_id)["license"] or "not recorded"
            except skill_hub.SkillNotFoundError:
                _skill_not_found(skill_id)
            typer.echo(f"{skill_id} ships no licence file. Its SPDX licence id is {spdx}.")
            return
        typer.echo(text, nl=not text.endswith("\n"))
        return
    try:
        info = skill_hub.skill_info(skill_id)
    except skill_hub.SkillNotFoundError:
        _skill_not_found(skill_id)
    _print_skill_info(info, width)


_ADD_NAME_HELP = (
    "name for the skill: letters, digits, - and _, up to 64 characters. It is stored as custom/NAME"
)
_ADD_FILE_HELP = (
    "a markdown file with the skill's instructions (UTF-8 text, up to 1 MB; frontmatter is optional)"
)


@skill_hub_app.command("add", help="add a custom skill from a local markdown file")
def skill_hub_add(
    name: str = typer.Argument(..., help=_ADD_NAME_HELP),
    file: Path = typer.Argument(..., help=_ADD_FILE_HELP),
) -> None:
    try:
        content = skill_hub.read_custom_skill_source(file)
        skill_hub.sanitize_skill_name(name)
        warnings = skill_hub.custom_skill_warnings(content)
        skill_id, note = skill_hub.add_custom_skill_with_sync(name, content)
    except ValueError as exc:
        _hallbook_error(str(exc))
    for warning in warnings:
        _stderr_console.print(f"[bold]WARNING:[/bold] {escape_markup(warning)}.", soft_wrap=True)
    console.print(f"[bold green]Added custom skill[/bold green] {skill_id}")
    if note:
        typer.echo(note)


@skill_hub_app.command("remove", help="remove a custom skill")
def skill_hub_remove(name: str) -> None:
    try:
        existed, note = skill_hub.remove_custom_skill_with_sync(name)
    except ValueError as exc:
        _hallbook_error(str(exc))
    if existed:
        console.print(f"[bold green]Removed custom skill[/bold green] custom/{escape_markup(name)}")
    else:
        console.print(f"custom/{escape_markup(name)} was not present - nothing to remove.")
    if note:
        typer.echo(note)


@app.command(help="authenticate with GitHub via device flow and save a personal API token")
def login() -> None:
    from aletheore.credentials import DEFAULT_CREDENTIALS_PATH, has_api_key, save_api_token
    from aletheore.device_auth import (
        DeviceFlowError,
        mint_cli_token,
        mint_personal_token,
        poll_for_access_token,
        request_device_code,
        resolve_installation,
    )

    # A purely local check (no network round-trip, unlike status()'s whoami
    # call) - just enough to tell the user up front that a token is already
    # saved, before running the whole device flow. Previously this command
    # gave no sign it knew a token already existed until after a brand new
    # one had already been minted and saved.
    if has_api_key(
        "ALETHEORE_API_TOKEN", "aletheore-managed-audit", credentials_path=DEFAULT_CREDENTIALS_PATH
    ):
        console.print("A token is already saved locally - continuing will replace it.\n")

    import os as _os

    if _os.environ.get("ALETHEORE_API_URL"):
        console.print(
            f"[yellow]Using ALETHEORE_API_URL={_os.environ['ALETHEORE_API_URL']} instead of app.aletheore.com "
            "(for testing against your own server).[/yellow]\n"
        )

    try:
        code = request_device_code()
        console.print("First, authenticate with GitHub:")
        console.print(f"  1. Go to: [bold]{code.verification_uri}[/bold]")
        console.print(f"  2. Enter code: [bold cyan]{code.user_code}[/bold cyan]")
        console.print("Waiting for authorization...")
        github_token = poll_for_access_token(code)

        # Hostname plus a short machine id: two machines that share a hostname must not replace each other's token
        # (the server treats an identical label as "the same machine logging in again").
        import uuid

        # ALETHEORE_LOGIN_LABEL lets two test "machines" on one computer log in without replacing each other.
        label = _os.environ.get("ALETHEORE_LOGIN_LABEL") or f"{socket.gethostname()}-{uuid.getnode() & 0xFFFF:04x} (device flow)"

        # Mint and save a personal token before any installation lookup.
        # This has no dependency on an Aletheore GitHub App installation, so
        # it is what lets an account with zero installations still log in
        # for Hallbook purposes. It is best-effort, though: a Hallbook
        # failure (network error, the endpoint 404ing before it is deployed,
        # a timeout, the per-account token cap or rate limit) must never
        # block a paid user's managed-audit login below, so any failure here
        # is a warning and the installation block still runs.
        personal_token_saved = False
        try:
            personal_token = mint_personal_token(github_token, label)
            save_api_token("aletheore-personal", personal_token)
            personal_token_saved = True
        except Exception as exc:  # noqa: BLE001 - best-effort by design
            console.print(
                "[bold yellow]warning:[/bold yellow] Hallbook personal login unavailable "
                f"right now ({type(exc).__name__}) - continuing without it."
            )

        if personal_token_saved:
            console.print(
                "[dim]Custom skills you add sync to your Aletheore account and are available on your other machines.[/dim]"
            )
            # Pull-merge custom skills from the server now that a personal
            # token exists. Never raises (see skill_hub_sync), so it cannot
            # affect the login outcome. Skipped without a personal token:
            # there is nothing to pull or push with.
            from aletheore import skill_hub_sync

            skill_hub_sync.pull_merge_custom_skills()

        # Installation resolution (for the managed-audit token) is
        # best-effort from here on: a GitHub account with no Aletheore
        # installation must not make the whole command exit non-zero
        # when the personal token above already satisfies login. Only
        # the specific "no installations found" DeviceFlowError is
        # downgraded to a warning here - any other failure in this block
        # (a genuine network error, a bad response, etc.) still propagates
        # to the outer handlers below exactly as before.
        installation_account_login: str | None = None
        try:
            resolved = resolve_installation(github_token)
            if isinstance(resolved, dict):
                installation = resolved
            else:
                console.print("Multiple paid installations found - pick one:")
                for index, candidate in enumerate(resolved, start=1):
                    console.print(f"  {index}. {candidate['account_login']}")
                while True:
                    try:
                        raw = input(f"Enter a number [1-{len(resolved)}]: ").strip()
                    except EOFError:
                        console.print("[bold red]error:[/bold red] no interactive input to choose an installation")
                        raise typer.Exit(code=1)
                    if raw.isdigit() and 1 <= int(raw) <= len(resolved):
                        installation = resolved[int(raw) - 1]
                        break
                    console.print(
                        f"[bold red]error:[/bold red] enter a number between 1 and {len(resolved)}"
                    )

            token = mint_cli_token(github_token, installation["installation_id"], label)
            save_api_token("aletheore-managed-audit", token)
            installation_account_login = installation["account_login"]
        except DeviceFlowError:
            if not personal_token_saved:
                # Neither token was saved, so nothing about this login
                # succeeded - say so instead of claiming a Hallbook login.
                console.print(
                    "[bold red]error:[/bold red] No Aletheore GitHub App installation "
                    "found, and Hallbook login is unavailable right now - nothing "
                    "was saved. Retry in a moment."
                )
                raise typer.Exit(code=1)
            console.print(
                "[bold yellow]warning:[/bold yellow] No Aletheore GitHub App "
                "installation found - install it for the full feature set; "
                "you're logged in for Hallbook."
            )

        if installation_account_login is not None:
            console.print(
                f"[bold green]Logged in.[/bold green] Token saved for "
                f"[bold]{installation_account_login}[/bold]. "
                "This replaces any previously saved token."
            )
            console.print(HOSTED_EMBEDDINGS_DISCLOSURE)
        else:
            console.print(
                "[bold green]Logged in.[/bold green] Personal token saved for Hallbook."
            )
    except DeviceFlowError as exc:
        console.print(f"[bold red]error:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc
    except httpx.HTTPStatusError as exc:
        # The server answered with an error status, so the connection is fine: show its reason instead of a
        # misleading "check your connection" hint.
        from aletheore.device_auth import describe_login_http_error

        console.print(f"[bold red]error:[/bold red] {describe_login_http_error(exc)}", soft_wrap=True)
        raise typer.Exit(code=1) from exc
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        console.print(f"[bold red]error:[/bold red] login failed ({type(exc).__name__}: {exc}) - check your connection and retry")
        raise typer.Exit(code=1) from exc


def _logout_revoke_failed(exc: Exception, all_devices: bool) -> None:
    """The server-side revoke did not go through. For --all-devices that is an error (keep the token so the user can
    retry); for a plain logout it is a warning and the local token is still removed."""
    if all_devices:
        # The user asked to revoke every token. Removing the only local token now would leave nothing to
        # authenticate a retry with, and the other tokens would stay valid. Keep it.
        console.print(
            "[bold red]error:[/bold red] couldn't revoke your personal tokens on the "
            f"server ({type(exc).__name__}). You are still logged in on this machine; "
            "retry `aletheore logout --all-devices` when you are online."
        )
        raise typer.Exit(code=1) from exc
    # A plain offline logout must still log the user out locally;
    # the server-side revoke failing is a warning, not a blocker.
    console.print(
        "[bold yellow]warning:[/bold yellow] couldn't revoke the personal "
        f"token on the server ({type(exc).__name__}) - it was removed from "
        "this machine, but it has no expiry and stays valid on the server "
        "until it is revoked. To revoke it, log in again and run "
        "`aletheore logout --all-devices`."
    )


def _revoke_managed_audit_token_best_effort() -> None:
    """Revokes the locally saved managed-audit token on the server so a login/logout cycle does not leak an active
    token toward the installation's cap. Never blocks logout: any failure prints one line and the caller still clears
    the local token. Only the token saved by `aletheore login` is revoked, never one supplied via ALETHEORE_API_TOKEN."""
    import aletheore.credentials as credentials
    from aletheore.device_auth import revoke_managed_audit_token

    managed_token = credentials._load_saved_key("aletheore-managed-audit", credentials.DEFAULT_CREDENTIALS_PATH)
    if not managed_token:
        return
    try:
        revoke_managed_audit_token(managed_token)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 401:
            return  # already revoked on the server; nothing left to do
        reason = f"HTTP {exc.response.status_code}"
    except Exception as exc:  # noqa: BLE001 - best-effort by design
        reason = type(exc).__name__
    else:
        return
    console.print(
        f"[bold yellow]warning:[/bold yellow] couldn't revoke the CLI token on the server ({reason}) - it was "
        "removed from this machine but may still count toward your token limit. You can revoke it in the "
        "dashboard at https://aletheore.com."
    )


@app.command(help="clear the locally saved managed-audit and Hallbook tokens")
def logout(
    all_devices: bool = typer.Option(
        False, "--all-devices", help="also revoke every personal token on the server, not just this one"
    ),
) -> None:
    import aletheore.credentials as credentials

    # Both slots: the managed-audit token and the Hallbook personal token.
    # status() reports them independently, and a user running logout
    # expects to be logged out of both.
    personal_slot = "aletheore-personal"
    # Revoke server-side first, while the token still exists locally. Same
    # guarded read status() uses: never prompts, since logout is not an
    # interactive ask-for-a-token moment.
    personal_token: str | None = None
    if credentials.has_api_key(
        "ALETHEORE_PERSONAL_TOKEN",
        personal_slot,
        credentials_path=credentials.DEFAULT_CREDENTIALS_PATH,
    ):
        personal_token = credentials.get_api_key(
            "ALETHEORE_PERSONAL_TOKEN",
            personal_slot,
            credentials_path=credentials.DEFAULT_CREDENTIALS_PATH,
            prompt_fn=lambda _msg: "",
        )
    if personal_token:
        from aletheore.device_auth import revoke_personal_token

        try:
            revoke_personal_token(personal_token, all_devices=all_devices)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 401:
                _logout_revoke_failed(exc, all_devices)
            # 401: the server already revoked this token (for example by an earlier logout elsewhere), so there is
            # nothing left to revoke; fall through and clear the dead token from this machine.
        except Exception as exc:  # noqa: BLE001 - best-effort by design
            _logout_revoke_failed(exc, all_devices)
    elif all_devices:
        # --all-devices needs a personal token to authenticate the revoke-all
        # call. With none saved (even if a managed-audit token is), there is
        # nothing to authenticate with, so say so instead of silently logging
        # out locally and skipping the revoke the user explicitly asked for.
        console.print(
            "Not logged in to Hallbook - nothing to revoke. Any managed-audit login on this machine was left as it is; "
            "run `aletheore logout` (without --all-devices) to remove that too."
        )
        return

    _revoke_managed_audit_token_best_effort()

    removed_any = False
    for slot in ("aletheore-managed-audit", personal_slot):
        if credentials.clear_api_key(slot, credentials_path=credentials.DEFAULT_CREDENTIALS_PATH):
            removed_any = True
    if removed_any:
        console.print("[bold green]Logged out.[/bold green] Saved tokens removed.")
    else:
        console.print("Not logged in - nothing to clear.")


@app.command(help="show installed version, update availability, and login state")
def status() -> None:
    import aletheore.credentials as credentials

    installed_version = _installed_version()
    version_note = (
        "couldn't check for updates"
        if installed_version == "unknown"
        else _check_for_update(installed_version)
    )
    console.print(f"Aletheore v{installed_version} ({version_note})")

    env_override = crash_reporting_env_override()
    crash_reporting_state = "on" if is_crash_reporting_enabled() else "off"
    if env_override is not None:
        crash_reporting_state += (
            f" (set by ALETHEORE_CRASH_REPORTING={env_override}, which overrides the saved preference)"
        )
    elif crash_reporting_state == "off":
        crash_reporting_state += " (run 'aletheore config crash-reporting on' to enable)"
    console.print(f"Crash reporting: {crash_reporting_state}")

    if not credentials.has_api_key(
        "ALETHEORE_API_TOKEN",
        "aletheore-managed-audit",
        credentials_path=credentials.DEFAULT_CREDENTIALS_PATH,
    ):
        console.print("Not logged in - run [bold]aletheore login[/bold]")
    else:
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
            console.print(
                f"Logged in as: [bold]{who.get('account_login', '?')}[/bold] ({who.get('plan', '?')} plan)"
            )

    # Independent of the managed-audit/installation check above: Hallbook
    # login only needs the personal token slot, which login() now always
    # saves regardless of whether any Aletheore GitHub App installation
    # was found. No network round-trip here (unlike the whoami call
    # above) - there is no installation-scoped identity endpoint for a
    # bare personal token, so this is a purely local presence check.
    if credentials.has_api_key(
        "ALETHEORE_PERSONAL_TOKEN",
        "aletheore-personal",
        credentials_path=credentials.DEFAULT_CREDENTIALS_PATH,
    ):
        console.print("Hallbook: logged in")
    else:
        console.print("Hallbook: not logged in - run [bold]aletheore login[/bold]")


def main() -> None:
    # Windows: PEP 528 already forces UTF-8 for the literal interactive
    # console since Python 3.6, but that guarantee doesn't extend to
    # redirected/piped output (`aletheore scan > out.txt`, or any non-tty
    # stdout) - those fall back to the OS's legacy locale-default codepage,
    # the exact same gap already fixed for file I/O throughout this
    # codebase (see write_evidence). Every print()/console.print() call
    # here can carry non-ASCII, repo-derived content (file paths, endpoint
    # routes, exception messages, commit metadata), so stdout/stderr are
    # pinned to UTF-8 once, here, rather than passing encoding at each of
    # this CLI's dozens of individual print() call sites. errors=
    # "backslashreplace" (not the default "strict") means a
    # still-unencodable byte degrades to a visible escape in the rare
    # non-UTF-8-terminal case, matching this file's off-crash-path
    # printed-error philosophy, rather than crashing the CLI while it's
    # trying to report an unrelated error.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="backslashreplace")

    # A deleted working directory (a removed git worktree, a cleaned-up
    # scratch dir) makes os.getcwd() raise FileNotFoundError, and every
    # command resolves a relative path (the default "." included) through
    # it - so each one would otherwise die in Path.resolve() with a raw
    # traceback and a crash report for what is a user environment problem,
    # not an Aletheore bug (Sentry ALETHEORE-CLI-2). Checked once here,
    # before Sentry initialises, so nothing is reported.
    try:
        os.getcwd()
    except OSError:  # FileNotFoundError on POSIX; a plain OSError (WinError 3) on Windows
        _stderr_console.print(
            "[bold red]error:[/bold red] the current working directory no "
            "longer exists. cd into an existing directory and run the "
            "command again."
        )
        raise SystemExit(1)

    init_cli_sentry()
    # With crash reporting off (the env var or the saved preference) there is
    # nothing to disclose, and a read-only command must not write
    # preferences.json just to remember that.
    if is_crash_reporting_enabled() and not has_shown_crash_reporting_notice():
        # Product decision (2026-10-08, closing the privacy-stance tension
        # the backward PR audit of #915-977 flagged on PR #961): stays
        # opt-out (on by default), but both disclosure points - this one
        # and the per-crash one below - must say explicitly that ONLY
        # crash data is ever reported, not general usage/telemetry, so
        # "on by default" is never mistaken for broader tracking.
        _stderr_console.print(
            "[dim]Aletheore monitors for crashes only - never general "
            "usage - to help fix bugs across environments we can't all "
            "test. Disable with `aletheore config crash-reporting off`.[/dim]"
        )
        mark_crash_reporting_notice_shown()

    try:
        app()
    except (typer.Exit, click.exceptions.Exit, SystemExit, KeyboardInterrupt):
        raise
    except Exception as exc:
        if is_crash_reporting_enabled():
            # A broken Sentry SDK environment (no network, a bad DSN after
            # a future rotation, etc.) must never replace or mask the
            # user's real crash with a second, unrelated one - reporting
            # is a side effect, not a precondition for the exception
            # continuing to propagate normally.
            try:
                sentry_sdk.capture_exception(exc)
            except Exception:
                # A broken Sentry SDK environment (no network, a bad DSN
                # after a future rotation, etc.) must never replace or
                # mask the user's real crash - but silently swallowing it
                # entirely gave no way to notice the capture path itself
                # is broken (ast_pattern.py already fixed this exact
                # "except: pass" pattern once, for the same reason - see
                # its own comment on the convention). Diagnostic only, to
                # stderr; the user's real exception still propagates
                # unchanged below.
                _stderr_console.print(
                    "[dim]Note: could not report this crash to Aletheore.[/dim]"
                )
            else:
                _stderr_console.print(
                    "\n[dim]This crash report (error details, stack trace, "
                    "OS and Python version) was sent to help fix it. No "
                    "usage data is collected. Disable with "
                    "`aletheore config crash-reporting off`.[/dim]"
                )
        raise


if __name__ == "__main__":
    main()
