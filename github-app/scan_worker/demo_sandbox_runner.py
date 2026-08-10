"""Internal-only sidecar that is the sole holder of the host Docker socket
for the public, unauthenticated website demo.

demo-scan-worker used to run `docker run --runtime=runsc ...` directly
with the socket mounted into its own container - a compromise of that
worker's process meant the full Docker API, and from there the host. A
docker-socket-proxy (HAProxy-based method/path allowlisting) was tested
directly against this repo's own production daemon as an alternative and
does NOT close that: with only CONTAINERS+POST enabled (everything else,
including VOLUMES, off), `docker run -v /:/hostfs alpine ls /hostfs`
still succeeded and listed the real host filesystem - the proxy gates
which endpoints are reachable, not what's inside a POST
/containers/create body, so POST=1 (required for `docker run` to work at
all) already allows an arbitrary bind mount.

This service closes the hole instead of trying to filter it: it is the
only container with real socket access, and it exposes exactly one
operation - "run this repo_url through the fixed, hardcoded sandbox
command" - over a narrow internal HTTP API. There is no field in that API
a caller could use to inject a bind mount, an exec, or any other Docker
API call: the actual `docker run` argv is built entirely server-side,
from nothing but a validated repo_url string.
"""

import json
import logging
import re
import subprocess
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logger = logging.getLogger(__name__)

DEMO_SANDBOX_IMAGE = "aletheore-demo-sandbox:latest"
CONTAINER_TIMEOUT_SECONDS = 90
PORT = 8090

# ThreadingHTTPServer spawns one thread per request with no cap of its own,
# and this endpoint's only real cost is `docker run --cpus=1 --memory=1g` -
# each concurrent sandbox is a full container on the host. In practice
# demo-scan-worker's single RQ worker already serializes requests to this
# service, but that's an upstream property of a different process, not
# something this one enforces - this semaphore is the actual limit,
# independent of anything upstream.
MAX_CONCURRENT_SANDBOXES = 4
_sandbox_slots = threading.Semaphore(MAX_CONCURRENT_SANDBOXES)

# Mirrors app_server.demo_scan_validation._REPO_URL_RE exactly. Re-checked
# here rather than trusted from the caller: this process, not
# demo-scan-worker, is the one thing standing between a string and a
# real docker invocation, and RQ jobs can in principle be enqueued by
# anything with access to the same Redis instance.
_REPO_URL_RE = re.compile(
    r"^https://github\.com/"
    r"(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/"
    r"(?P<repo>[A-Za-z0-9._-]{1,100}?)"
    r"(?:\.git)?/?$"
)

# Mirrors aletheore.git_intel.analyzer.GIT_ANALYSIS_RESOURCE_EXIT_CODE.
# Inlined for the same reason scan_worker.demo_scan does: this image
# deliberately excludes the aletheore package and its dependency tree.
GIT_ANALYSIS_RESOURCE_EXIT_CODE = 2


class SandboxRunError(RuntimeError):
    """reason is a stable code (see the raise sites below) - the HTTP
    layer forwards it verbatim so the caller (scan_worker.demo_scan) can
    reconstruct its own user-facing message without this service needing
    to know anything about how that message should read."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def run_sandboxed_scan(repo_url: str) -> dict:
    if not _REPO_URL_RE.match(repo_url.strip()):
        raise SandboxRunError("invalid_repo_url")

    if not _sandbox_slots.acquire(timeout=CONTAINER_TIMEOUT_SECONDS):
        raise SandboxRunError("too_many_concurrent_scans")
    try:
        return _run_sandboxed_scan_with_slot_held(repo_url)
    finally:
        _sandbox_slots.release()


def _run_sandboxed_scan_with_slot_held(repo_url: str) -> dict:
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
        raise SandboxRunError("timeout") from exc

    if result.returncode == GIT_ANALYSIS_RESOURCE_EXIT_CODE:
        logger.info("demo scan hit the memory limit for %s", repo_url)
        raise SandboxRunError("memory_limited")

    if result.returncode != 0:
        logger.warning(
            "demo scan container exited %s for %s: %s",
            result.returncode,
            repo_url,
            result.stderr[-2000:],
        )
        raise SandboxRunError("nonzero_exit")

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        logger.warning("demo scan produced non-JSON stdout for %s", repo_url)
        raise SandboxRunError("non_json_output") from exc


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, format_str, *args):
        logger.info("%s - %s", self.address_string(), format_str % args)

    def do_POST(self):
        if self.path != "/run":
            self.send_response(404)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            repo_url = body["repo_url"]
        except (json.JSONDecodeError, KeyError, UnicodeDecodeError):
            self._send_json(400, {"error": "invalid_request_body"})
            return

        try:
            evidence = run_sandboxed_scan(repo_url)
        except SandboxRunError as exc:
            self._send_json(422, {"error": exc.reason})
            return

        self._send_json(200, evidence)

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    server = ThreadingHTTPServer(("0.0.0.0", PORT), _Handler)
    logger.info("demo sandbox runner listening on :%d", PORT)
    server.serve_forever()


if __name__ == "__main__":
    main()
