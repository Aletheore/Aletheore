"""RQ job for the public, unauthenticated "paste a repo" website demo.

Runs on the dedicated `demo_scan` queue, consumed only by the
demo-scan-worker compose service - the one component with access to the
host's Docker socket, deliberately kept separate from the trusted
scan_worker.jobs path, which only ever touches repos from installed
GitHub Apps. Every run is a single ephemeral gVisor-sandboxed container:
the clone and the deterministic scan happen entirely inside it, and only
a curated, public-safe summary of the results ever leaves - never the
cloned source, never raw secret matches, never the full evidence.
"""

import json
import logging
import subprocess
import uuid

logger = logging.getLogger(__name__)

DEMO_SANDBOX_IMAGE = "aletheore-demo-sandbox:latest"
CONTAINER_TIMEOUT_SECONDS = 90
MAX_LISTED_ITEMS = 5


class DemoScanError(RuntimeError):
    pass


def _run_sandboxed_scan(repo_url: str) -> dict:
    container_name = f"demo-scan-{uuid.uuid4().hex}"
    cmd = [
        "docker",
        "run",
        "--name",
        container_name,
        "--rm",
        "--runtime=runsc",
        "--cpus=1",
        "--memory=1g",
        "--pids-limit=256",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        DEMO_SANDBOX_IMAGE,
        repo_url,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=CONTAINER_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        # Killing the `docker run` CLI process (what subprocess's own
        # timeout does) does not guarantee the container it started stops -
        # the container is managed by the daemon, not the CLI. Force it
        # explicitly so a slow/stuck clone can never linger past the
        # timeout using resources or holding the untrusted repo on disk.
        subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)
        raise DemoScanError("scan timed out") from exc

    if result.returncode != 0:
        logger.warning(
            "demo scan container exited %s for %s: %s",
            result.returncode,
            repo_url,
            result.stderr[-2000:],
        )
        raise DemoScanError("scan failed - the repo may be invalid, private, or unparseable")

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        logger.warning("demo scan produced non-JSON stdout for %s", repo_url)
        raise DemoScanError("scan failed") from exc


def _summarize_for_public_display(evidence: dict) -> dict:
    repository = evidence.get("repository", {})
    security = evidence.get("security", {})
    architecture = evidence.get("architecture", {})

    languages = repository.get("languages", [])
    dead_code = repository.get("dead_code", {})
    unreachable_modules = dead_code.get("unreachable_modules", [])
    unused_dependencies = dead_code.get("unused_dependencies", [])
    secrets_findings = security.get("secrets", {}).get("findings", [])
    license_findings = security.get("dependency_licenses", {}).get("findings", [])
    endpoints = repository.get("api_endpoints", {}).get("endpoints", [])
    clusters = architecture.get("clusters", [])

    return {
        "languages": [
            {"name": lang.get("name"), "lines": lang.get("lines")} for lang in languages[:MAX_LISTED_ITEMS]
        ],
        "dead_code": {
            "unreachable_module_count": len(unreachable_modules),
            "unused_dependency_count": len(unused_dependencies),
            "sample": unreachable_modules[:MAX_LISTED_ITEMS],
        },
        "secrets": {
            "finding_count": len(secrets_findings),
            # Never the matched value or its preview - path, line, and
            # which pattern matched is enough to prove the scanner found
            # something real without republishing anyone's leaked secret
            # on a public web page.
            "sample": [
                {"path": f.get("path"), "line": f.get("line"), "pattern": f.get("pattern")}
                for f in secrets_findings[:MAX_LISTED_ITEMS]
            ],
        },
        "dependency_licenses": {
            "issue_count": len(license_findings),
        },
        "api_endpoints": {
            "count": len(endpoints),
        },
        "architecture": {
            "cluster_count": len(clusters),
        },
        "held_back": {
            "vulnerabilities": "dependency vulnerability check (OSV.dev) skipped in the live demo",
            "message": (
                "This is a preview of what Aletheore finds - install the CLI and run "
                "`aletheore scan` locally, or connect via MCP, for the full report "
                "including OSV.dev vulnerability checks and every finding."
            ),
        },
    }


def run_demo_scan_job(repo_url: str) -> dict:
    evidence = _run_sandboxed_scan(repo_url)
    return _summarize_for_public_display(evidence)
