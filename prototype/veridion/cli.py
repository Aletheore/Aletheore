import argparse
import sys
from pathlib import Path

from veridion.adapters.claude_code import AdapterInvocationError, ClaudeCodeAdapter
from veridion.evidence import scan_repository, write_evidence
from veridion.report import (
    AmbiguousAdapterError,
    NoAdapterAvailableError,
    run_reasoning_phase,
    select_adapter,
)

KNOWN_ADAPTERS = [ClaudeCodeAdapter()]

MANUAL_DIR = str(Path(__file__).resolve().parent.parent / "manual")


def _audit(repo_path: str, forced_agent: str | None, check_vulnerabilities: bool) -> int:
    repo = Path(repo_path).resolve()

    print(f"Scanning {repo}...")
    evidence = scan_repository(repo, check_vulnerabilities=check_vulnerabilities)
    evidence_path = write_evidence(evidence, repo)
    print(f"Evidence written to {evidence_path}")

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

    args = parser.parse_args()

    if args.command == "audit":
        return _audit(args.path, args.agent, args.check_vulnerabilities)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
