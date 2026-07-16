import argparse
import json
import socket
import sys
import threading
import time
import webbrowser
from collections.abc import Callable
from pathlib import Path

import uvicorn

from aletheore.adapters.claude_code import AdapterInvocationError, ClaudeCodeAdapter
from aletheore.dashboard import build_app
from aletheore.evidence import scan_repository, write_evidence
from aletheore.healthcheck import run_healthcheck, save_healthcheck
from aletheore.history import compute_diff, list_snapshots, save_snapshot
from aletheore.mcp_server import build_server
from aletheore.query import (
    BranchNotFoundInEvidenceError,
    ModuleNotFoundInEvidenceError,
    QUERY_FUNCTIONS,
)
from aletheore.report import (
    AmbiguousAdapterError,
    NoAdapterAvailableError,
    run_reasoning_phase,
    select_adapter,
)

KNOWN_ADAPTERS = [ClaudeCodeAdapter()]

MANUAL_DIR = str(Path(__file__).resolve().parent / "manual")


def _box(lines: list[str]) -> str:
    width = max(len(line) for line in lines)
    top = "┌" + "─" * (width + 2) + "┐"
    bottom = "└" + "─" * (width + 2) + "┘"
    body = "\n".join(f"│ {line.ljust(width)} │" for line in lines)
    return f"{top}\n{body}\n{bottom}"


SPONSOR_NOTE = "\n" + _box(
    [
        "Aletheore is 100% open-source, local, and free.",
        "No accounts, no tracking — nothing leaves this machine.",
        "",
        "If it saved you time, consider supporting development:",
        "https://github.com/sponsors/ArihantK15",
    ]
) + "\n"

BANNER_LINES = [
    "ALETHEORE",
    "",
    "Evidence-grounded repository audit — a deterministic scanner (tree-sitter",
    "+ git log, no LLM) reads a repo and writes .aletheore/evidence.json. Every",
    "other command below reads from that same evidence, never re-scans blind.",
    "",
    "  scan         run the scanner, write evidence, no LLM call",
    "  audit        scan, then have a coding agent write a grounded report",
    "  query        answer a targeted question from existing evidence",
    "  diff         compare two evidence snapshots",
    "  mcp          run an MCP server so an agent can query a repo directly",
    "  dashboard    a live local web UI over the same evidence",
    "  healthcheck  GET-only live check of mapped API endpoints",
    "",
    "Run 'aletheore <command> --help' for details on any command.",
    "https://github.com/Aletheore/Aletheore",
]
BANNER = _box(BANNER_LINES)


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
            print(f"  → {message}")

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
            print(f"  → {self._label}...")
        return self

    def __exit__(self, *exc_info) -> None:
        if self._is_tty:
            self._stop.set()
            self._thread.join()
            print()
        else:
            elapsed = int(time.monotonic() - self._start)
            print(f"  → {self._label}: done ({elapsed}s elapsed)")


def _scan(
    repo_path: str,
    check_vulnerabilities: bool,
    scan_git_history: bool,
    check_licenses: bool = True,
    map_endpoints: bool = True,
) -> tuple[int, dict, Path]:
    repo = Path(repo_path).resolve()
    print(f"Scanning {repo}...")
    evidence = scan_repository(
        repo,
        check_vulnerabilities=check_vulnerabilities,
        scan_git_history=scan_git_history,
        check_licenses=check_licenses,
        map_endpoints=map_endpoints,
        progress=_make_progress_printer(),
    )
    evidence_path = write_evidence(evidence, repo)
    print(f"Evidence written to {evidence_path}")
    snapshot_path = save_snapshot(evidence, repo)
    print(f"Snapshot saved to {snapshot_path}")
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
        print(f"error: {exc}")
        print(f"Evidence is still available at {evidence_path} for manual use.")
        return 1

    print(f"Running audit with {adapter.name}...")
    try:
        with _ElapsedTicker(f"Waiting on {adapter.name}"):
            report_path = run_reasoning_phase(adapter, repo_path=str(repo), manual_dir=MANUAL_DIR)
    except AdapterInvocationError as exc:
        print(f"error: {exc}")
        print(f"Evidence is still available at {evidence_path} for manual use.")
        return 1

    print(f"Audit report written to {report_path}")
    print(SPONSOR_NOTE)
    return 0


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


def _query(kind: str, target: str | None, repo_path: str, full: bool = False) -> int:
    if kind == "changes":
        return _query_changes(repo_path, full)

    repo = Path(repo_path).resolve()
    evidence_path = repo / ".aletheore" / "evidence.json"
    if not evidence_path.exists():
        print(f"error: no evidence found at {evidence_path}")
        print(f"Run 'aletheore scan {repo}' first.")
        return 1

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
    evidence_path = repo / ".aletheore" / "evidence.json"
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


def _mcp(repo_path: str) -> int:
    repo = Path(repo_path).resolve()
    server = build_server(repo)
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
        print(
            f"error: port {port} is already in use - probably another aletheore "
            f"dashboard (or something else) is already bound to it.\n"
            f"Pass --port to use a different one, or stop whatever's using {port}."
        )
        return 1

    app = build_app(repo)
    url = f"http://{host}:{port}"
    print(f"Dashboard running at {url}")
    webbrowser.open(url)
    uvicorn.run(app, host=host, port=port)
    return 0


def main() -> int:
    if len(sys.argv) == 1:
        print(BANNER)
        return 0

    parser = argparse.ArgumentParser(
        prog="aletheore",
        description=BANNER,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    audit_parser = subparsers.add_parser("audit", help="audit a repository")
    audit_parser.add_argument("path", nargs="?", default=".")
    audit_parser.add_argument("--agent", default=None, help="force a specific agent adapter by name")
    audit_parser.add_argument(
        "--no-check-vulnerabilities",
        dest="check_vulnerabilities",
        action="store_false",
        default=True,
        help="skip the OSV.dev dependency-vulnerability check (on by default)",
    )
    audit_parser.add_argument(
        "--no-scan-git-history",
        dest="scan_git_history",
        action="store_false",
        default=True,
        help="skip walking git history for secrets (on by default)",
    )
    audit_parser.add_argument(
        "--no-check-licenses",
        dest="check_licenses",
        action="store_false",
        default=True,
        help="skip the dependency-license check (on by default)",
    )
    audit_parser.add_argument(
        "--no-map-endpoints",
        dest="map_endpoints",
        action="store_false",
        default=True,
        help="skip static API endpoint mapping (on by default)",
    )

    scan_parser = subparsers.add_parser("scan", help="run only the deterministic scan phase")
    scan_parser.add_argument("path", nargs="?", default=".")
    scan_parser.add_argument(
        "--no-check-vulnerabilities",
        dest="check_vulnerabilities",
        action="store_false",
        default=True,
        help="skip the OSV.dev dependency-vulnerability check (on by default)",
    )
    scan_parser.add_argument(
        "--no-scan-git-history",
        dest="scan_git_history",
        action="store_false",
        default=True,
        help="skip walking git history for secrets (on by default)",
    )
    scan_parser.add_argument(
        "--no-check-licenses",
        dest="check_licenses",
        action="store_false",
        default=True,
        help="skip the dependency-license check (on by default)",
    )
    scan_parser.add_argument(
        "--no-map-endpoints",
        dest="map_endpoints",
        action="store_false",
        default=True,
        help="skip static API endpoint mapping (on by default)",
    )

    query_parser = subparsers.add_parser("query", help="query an existing evidence.json")
    query_parser.add_argument("kind", choices=list(QUERY_FUNCTIONS.keys()) + ["changes"])
    query_parser.add_argument("target", nargs="?", default=None)
    query_parser.add_argument("--path", dest="repo_path", default=".")
    query_parser.add_argument(
        "--full",
        action="store_true",
        default=False,
        help="show the full raw diff instead of the curated summary (only applies to 'changes')",
    )

    diff_parser = subparsers.add_parser("diff", help="compare two evidence.json files")
    diff_parser.add_argument("old", help="path to the baseline evidence.json")
    diff_parser.add_argument("new", help="path to the comparison evidence.json")
    diff_parser.add_argument(
        "--full",
        action="store_true",
        default=False,
        help="show the full raw diff instead of the curated summary",
    )
    diff_parser.add_argument(
        "--fail-on-new-secrets",
        dest="fail_on_new_secrets",
        action="store_true",
        default=False,
        help="exit 1 if a new real (non-placeholder) secret finding appears",
    )
    diff_parser.add_argument(
        "--fail-on-new-vulnerabilities",
        dest="fail_on_new_vulnerabilities",
        action="store_true",
        default=False,
        help="exit 1 if a new dependency vulnerability finding appears",
    )
    diff_parser.add_argument(
        "--fail-on-new-layer-violations",
        dest="fail_on_new_layer_violations",
        action="store_true",
        default=False,
        help="exit 1 if a new layer-convention violation appears",
    )

    mcp_parser = subparsers.add_parser("mcp", help="run an MCP server scoped to a repository")
    mcp_parser.add_argument("path", nargs="?", default=".")

    dashboard_parser = subparsers.add_parser(
        "dashboard", help="run a live local dashboard scoped to a repository"
    )
    dashboard_parser.add_argument("path", nargs="?", default=".")
    dashboard_parser.add_argument("--port", type=int, default=8420)

    healthcheck_parser = subparsers.add_parser(
        "healthcheck", help="GET-only live health check of mapped API endpoints"
    )
    healthcheck_parser.add_argument("path", nargs="?", default=".")
    healthcheck_parser.add_argument("--base-url", required=True, dest="base_url")

    args = parser.parse_args()

    if args.command == "audit":
        return _audit(
            args.path,
            args.agent,
            args.check_vulnerabilities,
            args.scan_git_history,
            args.check_licenses,
            args.map_endpoints,
        )
    if args.command == "scan":
        exit_code, _evidence, _evidence_path = _scan(
            args.path,
            args.check_vulnerabilities,
            args.scan_git_history,
            args.check_licenses,
            args.map_endpoints,
        )
        return exit_code
    if args.command == "query":
        return _query(args.kind, args.target, args.repo_path, args.full)
    if args.command == "diff":
        return _diff(
            args.old,
            args.new,
            args.full,
            args.fail_on_new_secrets,
            args.fail_on_new_vulnerabilities,
            args.fail_on_new_layer_violations,
        )
    if args.command == "mcp":
        return _mcp(args.path)
    if args.command == "dashboard":
        return _dashboard(args.path, args.port)
    if args.command == "healthcheck":
        return _healthcheck(args.path, args.base_url)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
