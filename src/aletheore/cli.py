import json
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
from aletheore.telemetry import report_scan_event
from aletheore.managed_audit_client import ManagedAuditError, run_managed_audit_request
from aletheore.query import (
    BranchNotFoundInEvidenceError,
    ModuleNotFoundInEvidenceError,
    QUERY_FUNCTIONS,
    SymbolNotFoundInEvidenceError,
    find_symbol_source,
)
from aletheore.report import (
    AmbiguousAdapterError,
    NoAdapterAvailableError,
    run_reasoning_phase,
    select_adapter,
)
from aletheore.toon_encoding import to_toon

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

QUERY_KIND_CHOICES = list(QUERY_FUNCTIONS.keys()) + [
    "changes",
    "search-codebase",
    "answer",
    "symbol-source",
]


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


def _make_progress_printer(is_tty: bool | None = None) -> Callable[[str], None]:
    # License checks report one message per pinned dependency (can be dozens).
    # In a real terminal those overwrite in place via \r instead of scrolling,
    # since they're the same phase repeating, not a new step. \r only means
    # "return to start of line" on an actual TTY though - piped to a CI log or
    # a file, it prints as a literal character with no effect, so non-TTY
    # output instead prints every message on its own line: more lines, but a
    # real, readable history in a log rather than concatenated garbage.
    is_tty = sys.stdout.isatty() if is_tty is None else is_tty
    state = {"in_place": False, "frame": 0}

    def report(message: str) -> None:
        overwritable = is_tty and message.startswith("Checking dependency licenses:")
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


def _scan(
    repo_path: str,
    check_vulnerabilities: bool,
    scan_git_history: bool,
    check_licenses: bool = True,
    map_endpoints: bool = True,
) -> tuple[int, dict, Path]:
    repo = Path(repo_path).resolve()
    console.print(f"Scanning {repo}...")
    try:
        evidence = scan_repository(
            repo,
            check_vulnerabilities=check_vulnerabilities,
            scan_git_history=scan_git_history,
            check_licenses=check_licenses,
            map_endpoints=map_endpoints,
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
    # Fire-and-forget, off the main thread: report_scan_event already has
    # its own short timeout and swallows every exception, but a background
    # thread means even a slow/hanging network path can never add latency
    # to a real scan.
    threading.Thread(target=report_scan_event, daemon=True).start()
    return 0, evidence, evidence_path


def _audit(
    repo_path: str,
    forced_agent: str | None,
    check_vulnerabilities: bool,
    scan_git_history: bool,
    check_licenses: bool = True,
    map_endpoints: bool = True,
) -> int:
    scan_exit_code, _evidence, evidence_path = _scan(
        repo_path, check_vulnerabilities, scan_git_history, check_licenses, map_endpoints
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
    check_vulnerabilities: bool,
    scan_git_history: bool,
    check_licenses: bool = True,
    map_endpoints: bool = True,
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
    from aletheore.search_index import build_index

    try:
        count = build_index(repo, evidence)
    except Exception as exc:
        console.print(f"[bold red]error:[/bold red] {exc}")
        return 1
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
) -> int:
    if kind not in QUERY_KIND_CHOICES:
        console.print(
            f"[bold red]error:[/bold red] '{kind}' is not a valid query kind. "
            f"Choose from: {', '.join(QUERY_KIND_CHOICES)}"
        )
        return 1

    if kind == "changes":
        return _query_changes(repo_path, full)

    if kind == "search-codebase":
        if target is None:
            print("error: query type 'search-codebase' requires a natural-language query")
            return 1
        from aletheore.search_index import IndexNotFoundError, search_index

        try:
            result = search_index(Path(repo_path).resolve(), target, k=k)
        except IndexNotFoundError as exc:
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
        from aletheore.search_index import IndexNotFoundError

        try:
            result = answer_question(Path(repo_path).resolve(), target, adapter, k=k)
        except IndexNotFoundError as exc:
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
    result = run_healthcheck(endpoints, base_url)
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


def _stdio_entry(repo_path: Path, include_type: bool) -> dict:
    entry: dict = {"command": "aletheore", "args": ["mcp", str(repo_path)]}
    if include_type:
        entry = {"type": "stdio", **entry}
    return entry


def _opencode_entry(repo_path: Path) -> dict:
    return {"type": "local", "command": ["aletheore", "mcp", str(repo_path)], "enabled": True}


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
            entry = {"command": "aletheore", "args": ["mcp", str(repo_path)]}
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
    uvicorn.run(app, host=host, port=port)
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
        raise typer.Exit(code=0)


@app.command(help="audit a repository")
def audit(
    path: str = typer.Argument(".", help="repository path"),
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
    check_vulnerabilities: bool = typer.Option(
        True,
        "--check-vulnerabilities/--no-check-vulnerabilities",
        help="OSV.dev dependency-vulnerability check (on by default)",
    ),
    scan_git_history: bool = typer.Option(
        True,
        "--scan-git-history/--no-scan-git-history",
        help="walk git history for secrets (on by default)",
    ),
    check_licenses: bool = typer.Option(
        True,
        "--check-licenses/--no-check-licenses",
        help="dependency-license check (on by default)",
    ),
    map_endpoints: bool = typer.Option(
        True,
        "--map-endpoints/--no-map-endpoints",
        help="static API endpoint mapping (on by default)",
    ),
) -> None:
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
    check_vulnerabilities: bool = typer.Option(
        True,
        "--check-vulnerabilities/--no-check-vulnerabilities",
        help="OSV.dev dependency-vulnerability check (on by default)",
    ),
    scan_git_history: bool = typer.Option(
        True,
        "--scan-git-history/--no-scan-git-history",
        help="walk git history for secrets (on by default)",
    ),
    check_licenses: bool = typer.Option(
        True,
        "--check-licenses/--no-check-licenses",
        help="dependency-license check (on by default)",
    ),
    map_endpoints: bool = typer.Option(
        True,
        "--map-endpoints/--no-map-endpoints",
        help="static API endpoint mapping (on by default)",
    ),
) -> None:
    exit_code, _evidence, _evidence_path = _scan(
        path, check_vulnerabilities, scan_git_history, check_licenses, map_endpoints
    )
    raise typer.Exit(code=exit_code)


@app.command(help="scaffold a .aletheore.json config file in a repository")
def init(path: str = typer.Argument(".", help="repository path")) -> None:
    config_path = Path(path) / ".aletheore.json"
    if config_path.exists():
        console.print(f"[bold red]error:[/bold red] {config_path} already exists - not overwriting it.")
        raise typer.Exit(code=1)

    default_config = {
        "layer_markers": {},
        "cluster_resolution": 1.0,
        "dead_code_entry_points": [],
        "accepted_secrets": [],
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
    console.print(keys)


@app.command(
    help=(
        "build a local semantic search index over the repository's code "
        "(requires a prior 'aletheore scan'; embeds via a local Ollama instance, "
        "falling back to OpenAI if Ollama is unavailable - needed by 'query search-codebase'/"
        "'query answer' and the aletheore_search_codebase/aletheore_answer MCP tools)"
    )
)
def index(path: str = typer.Argument(".", help="repository path")) -> None:
    raise typer.Exit(code=_index(path))


@app.command(help="query an existing air.json")
def query(
    kind: str = typer.Argument(..., help=f"one of: {', '.join(QUERY_KIND_CHOICES)}"),
    target: Optional[str] = typer.Argument(None, help="target for kinds that need one (a file path, branch name, ...)"),
    symbol: Optional[str] = typer.Argument(None, help="symbol name for 'symbol-source'"),
    repo_path: str = typer.Option(".", "--path", help="repository path"),
    full: bool = typer.Option(
        False, "--full", help="show the full raw diff instead of the curated summary (only 'changes')"
    ),
    agent: Optional[str] = typer.Option(None, "--agent", help="provider for 'answer'"),
    k: int = typer.Option(10, "--k", help="number of semantic search results"),
) -> None:
    raise typer.Exit(code=_query(kind, target, repo_path, full, agent, k, symbol))


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
    agent: Optional[str] = typer.Option(None, "--agent", help="provider for the aletheore_answer tool"),
) -> None:
    raise typer.Exit(code=_mcp(path, agent))


@app.command(
    name="mcp-install",
    help="write MCP client config so a coding agent auto-launches this repo's MCP server",
)
def mcp_install(
    path: str = typer.Argument(".", help="repository path"),
    target: list[str] = typer.Option(
        [],
        "--target",
        help=(
            "which client(s) to configure (default: all); one of: "
            f"{', '.join([*_MCP_CLIENT_CONFIGS.keys(), 'codex-cli'])}"
        ),
    ),
) -> None:
    raise typer.Exit(code=_mcp_install(path, target))


@app.command(help="run a live local dashboard scoped to a repository")
def dashboard(
    path: str = typer.Argument(".", help="repository path"),
    port: int = typer.Option(8420, "--port", help="port to serve the dashboard on"),
) -> None:
    raise typer.Exit(code=_dashboard(path, port))


@app.command(help="GET-only live health check of mapped API endpoints")
def healthcheck(
    path: str = typer.Argument(".", help="repository path"),
    base_url: str = typer.Option(..., "--base-url", help="base URL of the running instance to check"),
) -> None:
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
            choice = int(input("Enter a number: "))
            installation = resolved[choice - 1]

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
