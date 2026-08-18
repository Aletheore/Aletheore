"""RQ job for the public, unauthenticated "paste a repo" website demo.

Runs on the dedicated `demo_scan` queue, consumed only by the
demo-scan-worker compose service, kept separate from the trusted
scan_worker.jobs path, which only ever touches repos from installed
GitHub Apps. Every run is a single ephemeral gVisor-sandboxed container:
the clone and the deterministic scan happen entirely inside it, and only
a curated, public-safe summary of the results ever leaves - never the
cloned source, never raw secret matches, never the full evidence.

The actual `docker run` invocation happens in a separate process this
one talks to over HTTP (see scan_worker.demo_sandbox_runner) - this
worker itself never touches the host Docker socket, so a bug in the
public-facing code path here can never translate into Docker API access.
"""

import json
import logging
import os
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

MAX_LISTED_ITEMS = 5

DEMO_SANDBOX_RUNNER_URL = os.environ.get("DEMO_SANDBOX_RUNNER_URL", "http://demo-sandbox-runner:8090")
# A little past the runner's own 90s container timeout, so a slow
# container is what times this out, not this request racing it.
REQUEST_TIMEOUT_SECONDS = 100

_MEMORY_LIMITED_MESSAGE = (
    "this repo needs more memory to scan than the live demo allows - install the "
    "free CLI (`pip install aletheore`) and run `aletheore scan` locally, or "
    "connect via MCP, with no size limit"
)
_GENERIC_FAILURE_MESSAGE = "scan failed - the repo may be invalid, private, or unparseable"

# Maps scan_worker.demo_sandbox_runner.SandboxRunError's stable `reason`
# codes to this module's own DemoScanError messages, so callers see
# exactly the same user-facing text regardless of which process actually
# ran the container.
_REASON_MESSAGES = {
    "timeout": "scan timed out",
    # Confirmed directly: a full scan of the Linux kernel got OOM-killed
    # under the runner's 1GB container limit. The demo's own 400MB
    # repo-size cap makes this rare, but a pathologically dense repo
    # could still hit it - when it does, say so plainly.
    "memory_limited": _MEMORY_LIMITED_MESSAGE,
    "nonzero_exit": _GENERIC_FAILURE_MESSAGE,
    "non_json_output": "scan failed",
    "invalid_repo_url": _GENERIC_FAILURE_MESSAGE,
}


class DemoScanError(RuntimeError):
    pass


def _run_sandboxed_scan(repo_url: str) -> dict:
    request = urllib.request.Request(
        f"{DEMO_SANDBOX_RUNNER_URL}/run",
        data=json.dumps({"repo_url": repo_url}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        try:
            reason = json.loads(exc.read() or b"{}").get("error", "")
        except json.JSONDecodeError:
            reason = ""
        raise DemoScanError(_REASON_MESSAGES.get(reason, _GENERIC_FAILURE_MESSAGE)) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        # The runner process/network hop itself failed - distinct from
        # any of the repo-shaped failures above, which all come back as a
        # clean HTTP response from a runner that's up and running fine.
        raise DemoScanError("demo scan is temporarily unavailable - please try again shortly") from exc


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
            {"name": lang.get("name"), "lines": lang.get("loc")} for lang in languages[:MAX_LISTED_ITEMS]
        ],
        "dead_code": {
            "unreachable_module_count": len(unreachable_modules),
            "unused_dependency_count": len(unused_dependencies),
            # unreachable_modules entries are {"path": ..., "reason": ...} dicts
            # (dead_code.py) - the live-demo frontend renders each sample item as
            # a plain path string, so extract that field rather than passing the
            # dict through (it would otherwise render as "[object Object]").
            "sample": [m.get("path") for m in unreachable_modules[:MAX_LISTED_ITEMS]],
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
