import json
import socket
import sys
import threading
import time
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import Optional

import httpx
import typer
import uvicorn
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from aletheore.answer import answer_question
from aletheore.adapters.anthropic_native import AnthropicAdapter
from aletheore.adapters.claude_code import AdapterInvocationError, ClaudeCodeAdapter
from aletheore.adapters.codex_cli import CodexCliAdapter
from aletheore.adapters.gemini_cli import GeminiCliAdapter
from aletheore.adapters.grok_build import GrokBuildAdapter
from aletheore.adapters.mistral_vibe import MistralVibeAdapter
from aletheore.adapters.openai_compatible import OpenAICompatibleAdapter
from aletheore.adapters.opencode import OpenCodeAdapter
from aletheore.credentials import get_api_key
from aletheore.dashboard import build_app
from aletheore.device_auth import infer_repo_full_name_from_cwd_git_remote
from aletheore.evidence import scan_repository, write_evidence
from aletheore.healthcheck import run_healthcheck, save_healthcheck
from aletheore.history import compute_diff, list_snapshots, save_snapshot
from aletheore.managed_audit_client import ManagedAuditError, run_managed_audit_request
from aletheore.mcp_server import build_server
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
from aletheore.search_index import IndexNotFoundError, build_index, search_index
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


_COMMAND_SUMMARIES = [
    ("scan", "run the scanner, write evidence, no LLM call"),
    ("audit", "scan, then have a coding agent write a grounded report"),
    ("query", "answer a targeted question from existing evidence"),
    ("diff", "compare two evidence snapshots"),
    ("mcp", "run an MCP server so an agent can query a repo directly"),
    ("dashboard", "a live local web UI over the same evidence"),
    ("healthcheck", "GET-only live check of mapped API endpoints"),
    ("login", "authenticate and save a managed-audit API token"),
    ("status", "installed version, update availability, and login state"),
]


def _banner_panel() -> Panel:
    body = Text()
    body.append(
        "Evidence-grounded repository audit — a deterministic scanner (tree-sitter + "
        "git log, no LLM) reads a repo and writes .aletheore/air.json. Every "
        "other command below reads from that same evidence, never re-scans blind.\n\n"
    )
    for name, desc in _COMMAND_SUMMARIES:
        body.append(f"  {name:<12} ", style="bold green")
        body.append(f"{desc}\n")
    body.append("\nRun ")
    body.append("aletheore <command> --help", style="bold cyan")
    body.append(" for details on any command.\n")
    body.append("https://github.com/Aletheore/Aletheore", style="dim underline")
    return Panel(
        body,
        title="[bold cyan]ALETHEORE[/bold cyan]",
        title_align="left",
        border_style="cyan",
        width=78,
    )


def _make_progress_printer(is_tty: bool | None = None) -> Callable[[str], None]:
    # License checks report one message per pinned dependency (can be dozens).
    # In a real terminal those overwrite in place via \r instead of scrolling,
    # since they're the same phase repeating, not a new step. \r only means
    # "return to start of line" on an actual TTY though - piped to a CI log or
    # a file, it prints as a literal character with no effect, so non-TTY
    # output instead prints every message on its own line: more lines, but a
    # real, readable history in a log rather than concatenated garbage.
    is_tty = sys.stdout.isatty() if is_tty is None else is_tty
    state = {"in_place": False}

    def report(message: str) -> None:
        overwritable = is_tty and message.startswith("Checking dependency licenses:")
        if overwritable:
            print(f"\r  → {message}" + " " * 15, end="", flush=True)
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
        while not self._stop.wait(self._interval):
            elapsed = int(time.monotonic() - start)
            print(f"\r  → {self._label}... ({elapsed}s elapsed)" + " " * 10, end="", flush=True)

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
    evidence = scan_repository(
        repo,
        check_vulnerabilities=check_vulnerabilities,
        scan_git_history=scan_git_history,
        check_licenses=check_licenses,
        map_endpoints=map_endpoints,
        progress=_make_progress_printer(),
    )
    evidence_path = write_evidence(evidence, repo)
    console.print(f"[green]Evidence written to[/green] {evidence_path}")
    snapshot_path = save_snapshot(evidence, repo)
    console.print(f"Snapshot saved to {snapshot_path}")
    return 0, evidence, evidence_path


def _audit(
    repo_path: str,
    forced_agent: str | None,
    check_vulnerabilities: bool,
    scan_git_history: bool,
    check_licenses: bool = True,
    map_endpoints: bool = True,
) -> int:
    _exit_code, _evidence, evidence_path = _scan(
        repo_path, check_vulnerabilities, scan_git_history, check_licenses, map_endpoints
    )
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

    console.print(f"[green]Audit report written to[/green] {report_path}")
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

    _exit_code, evidence, evidence_path = _scan(
        repo_path,
        check_vulnerabilities,
        scan_git_history,
        check_licenses,
        map_endpoints,
    )
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
    console.print(f"[green]Managed audit report written to[/green] {report_path}")
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
        old = json.loads(snapshots[-2].read_text())
    except json.JSONDecodeError:
        print(f"error: most recent snapshot is unreadable ({snapshots[-2]})")
        return 1

    new = json.loads(snapshots[-1].read_text())
    diff = compute_diff(old, new, full=full)
    print(json.dumps(diff, indent=2))
    return 0


def _index(repo_path: str) -> int:
    repo = Path(repo_path).resolve()
    evidence_path = repo / ".aletheore" / "air.json"
    if not evidence_path.exists():
        console.print(f"[bold red]error:[/bold red] no evidence found at {evidence_path}")
        console.print(f"Run 'aletheore scan {repo}' first.")
        return 1
    evidence = json.loads(evidence_path.read_text())
    console.print(
        "Building semantic search index (embedding via local Ollama, "
        "falling back to OpenAI if unavailable)..."
    )
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
        try:
            result = answer_question(Path(repo_path).resolve(), target, adapter, k=k)
        except IndexNotFoundError as exc:
            console.print(f"[bold red]error:[/bold red] {exc}")
            return 1
        print(to_toon({"result": result}))
        return 0

    repo = Path(repo_path).resolve()
    evidence_path = repo / ".aletheore" / "air.json"
    if not evidence_path.exists():
        print(f"error: no evidence found at {evidence_path}")
        print(f"Run 'aletheore scan {repo}' first.")
        return 1

    if kind == "symbol-source":
        if target is None or symbol is None:
            print("error: query type 'symbol-source' requires module and symbol arguments")
            return 1
        evidence = json.loads(evidence_path.read_text())
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

    evidence = json.loads(evidence_path.read_text())
    try:
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
) -> int:
    old_file = Path(old_path)
    new_file = Path(new_path)

    if not old_file.exists():
        print(f"error: evidence file not found: {old_file}")
        return 1
    if not new_file.exists():
        print(f"error: evidence file not found: {new_file}")
        return 1

    try:
        old = json.loads(old_file.read_text())
    except json.JSONDecodeError:
        print(f"error: {old_file} is not valid JSON")
        return 1
    try:
        new = json.loads(new_file.read_text())
    except json.JSONDecodeError:
        print(f"error: {new_file} is not valid JSON")
        return 1

    diff = compute_diff(old, new, full=full)
    print(json.dumps(diff, indent=2))

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


def _healthcheck(repo_path: str, base_url: str) -> int:
    repo = Path(repo_path).resolve()
    evidence_path = repo / ".aletheore" / "air.json"
    if not evidence_path.exists():
        print(f"error: no evidence found at {evidence_path}")
        print(f"Run 'aletheore scan {repo}' first.")
        return 1

    evidence = json.loads(evidence_path.read_text())
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
    server.run(transport="stdio")
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
    add_completion=False,
    no_args_is_help=False,
)


@app.callback(invoke_without_command=True)
def _main_callback(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        console.print(_banner_panel())
        raise typer.Exit(code=0)


@app.command(help="audit a repository")
def audit(
    path: str = typer.Argument(".", help="repository path"),
    agent: Optional[str] = typer.Option(None, "--agent", help="force a specific agent adapter by name"),
    managed: bool = typer.Option(
        False,
        "--managed",
        help="run the audit using Aletheore's shared managed key instead of BYOK",
    ),
    token: Optional[str] = typer.Option(
        None,
        "--token",
        help="managed-audit API token (or set ALETHEORE_API_TOKEN)",
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


@app.command(help="build a local semantic search index over the repository's code")
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
            old, new, full, fail_on_new_secrets, fail_on_new_vulnerabilities, fail_on_new_layer_violations
        )
    )


@app.command(help="run an MCP server scoped to a repository")
def mcp(
    path: str = typer.Argument(".", help="repository path"),
    agent: Optional[str] = typer.Option(None, "--agent", help="provider for the aletheore_answer tool"),
) -> None:
    raise typer.Exit(code=_mcp(path, agent))


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
