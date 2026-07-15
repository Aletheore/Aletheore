import argparse
import json
import sys
from pathlib import Path

from veridion.adapters.claude_code import AdapterInvocationError, ClaudeCodeAdapter
from veridion.evidence import scan_repository, write_evidence
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


def _scan(repo_path: str, check_vulnerabilities: bool) -> tuple[int, dict, Path]:
    repo = Path(repo_path).resolve()
    print(f"Scanning {repo}...")
    evidence = scan_repository(repo, check_vulnerabilities=check_vulnerabilities)
    evidence_path = write_evidence(evidence, repo)
    print(f"Evidence written to {evidence_path}")
    return 0, evidence, evidence_path


def _audit(repo_path: str, forced_agent: str | None, check_vulnerabilities: bool) -> int:
    _exit_code, _evidence, evidence_path = _scan(repo_path, check_vulnerabilities)
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
    return 0


def _query(kind: str, target: str | None, repo_path: str) -> int:
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

    scan_parser = subparsers.add_parser("scan", help="run only the deterministic scan phase")
    scan_parser.add_argument("path", nargs="?", default=".")
    scan_parser.add_argument(
        "--no-check-vulnerabilities",
        dest="check_vulnerabilities",
        action="store_false",
        default=True,
        help="skip the OSV.dev dependency-vulnerability check (on by default)",
    )

    query_parser = subparsers.add_parser("query", help="query an existing evidence.json")
    query_parser.add_argument("kind", choices=list(QUERY_FUNCTIONS.keys()))
    query_parser.add_argument("target", nargs="?", default=None)
    query_parser.add_argument("--path", dest="repo_path", default=".")

    args = parser.parse_args()

    if args.command == "audit":
        return _audit(args.path, args.agent, args.check_vulnerabilities)
    if args.command == "scan":
        exit_code, _evidence, _evidence_path = _scan(args.path, args.check_vulnerabilities)
        return exit_code
    if args.command == "query":
        return _query(args.kind, args.target, args.repo_path)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
