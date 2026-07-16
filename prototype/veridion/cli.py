import argparse
import json
import sys
import webbrowser
from pathlib import Path

import uvicorn

from veridion.adapters.claude_code import AdapterInvocationError, ClaudeCodeAdapter
from veridion.dashboard import build_app
from veridion.evidence import scan_repository, write_evidence
from veridion.history import compute_diff, list_snapshots, save_snapshot
from veridion.mcp_server import build_server
from veridion.query import (
    BranchNotFoundInEvidenceError,
    ModuleNotFoundInEvidenceError,
    QUERY_FUNCTIONS,
)
from veridion.report import (
    AmbiguousAdapterError,
    NoAdapterAvailableError,
    run_reasoning_phase,
    select_adapter,
)

KNOWN_ADAPTERS = [ClaudeCodeAdapter()]

MANUAL_DIR = str(Path(__file__).resolve().parent.parent / "manual")

SPONSOR_NOTE = """
┌────────────────────────────────────────────────────────┐
│  Veridion is 100% open-source, local, and free.         │
│  No accounts, no tracking — nothing leaves this machine.│
│                                                          │
│  If it saved you time, consider supporting development: │
│  https://github.com/sponsors/ArihantK15                 │
└────────────────────────────────────────────────────────┘
"""


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
        print("no prior snapshot to compare against - run 'veridion scan' again later to compare")
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
    evidence_path = repo / ".veridion" / "evidence.json"
    if not evidence_path.exists():
        print(f"error: no evidence found at {evidence_path}")
        print(f"Run 'veridion scan {repo}' first.")
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


def _mcp(repo_path: str) -> int:
    repo = Path(repo_path).resolve()
    server = build_server(repo)
    server.run(transport="stdio")
    return 0


def _dashboard(repo_path: str, port: int) -> int:
    repo = Path(repo_path).resolve()
    app = build_app(repo)
    url = f"http://127.0.0.1:{port}"
    print(f"Dashboard running at {url}")
    webbrowser.open(url)
    uvicorn.run(app, host="127.0.0.1", port=port)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="veridion")
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

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
