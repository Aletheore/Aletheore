"""Anonymous CLI usage telemetry - reports only that a scan happened
and a random per-machine identifier generated once and cached locally.
No repo name, code content, or account info ever leaves the machine.
See the hosted service's migrations/023_cli_telemetry.sql for the full
privacy/scope rationale.

Respects ALETHEORE_TELEMETRY_DISABLED and DO_NOT_TRACK; any failure (no
network, endpoint down, disk unwritable) is silently swallowed - this
must never affect a real scan's outcome or add noticeable latency.
"""
import os
import uuid
from pathlib import Path

import httpx

TELEMETRY_API_BASE_URL = "https://app.aletheore.com"
TELEMETRY_DISABLED_ENV = "ALETHEORE_TELEMETRY_DISABLED"
DO_NOT_TRACK_ENV = "DO_NOT_TRACK"


def _default_telemetry_id_path() -> Path:
    return Path.home() / ".aletheore" / "telemetry_id"


def is_telemetry_disabled() -> bool:
    return bool(os.environ.get(TELEMETRY_DISABLED_ENV)) or bool(os.environ.get(DO_NOT_TRACK_ENV))


def load_or_create_anonymous_id(path: Path | None = None) -> str:
    path = path or _default_telemetry_id_path()
    if path.exists():
        existing = path.read_text().strip()
        if existing:
            return existing
    new_id = str(uuid.uuid4())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new_id)
    return new_id


def report_scan_event(
    *, path: Path | None = None, http_client: httpx.Client | None = None, timeout: float = 2.0
) -> None:
    """Fire-and-forget: never raises, and its short timeout keeps it from
    adding noticeable latency even called inline - callers wanting zero
    latency impact can still run this in a background thread."""
    if is_telemetry_disabled():
        return
    try:
        anonymous_id = load_or_create_anonymous_id(path)
        client = http_client or httpx.Client(base_url=TELEMETRY_API_BASE_URL)
        client.post("/v1/telemetry", json={"event": "scan", "anonymous_id": anonymous_id}, timeout=timeout)
    except Exception:
        pass
