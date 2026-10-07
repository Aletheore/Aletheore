# Sentry Error Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire `sentry-sdk` into all five of Aletheore's long-running backend processes (app_server, scan_worker's three entrypoints, jina_embed) so every exception already being logged anywhere in the running code - today or in any future code - is automatically reported to Sentry, scrubbed of request/user data and local-variable values.

**Architecture:** One shared `app_server/sentry_config.init_sentry(service_name)` helper, called once per process at startup, configures `sentry_sdk` with `LoggingIntegration` (so any `logger.exception(...)`/`logger.error(..., exc_info=True)` anywhere in that process becomes a Sentry event with zero per-call-site changes) plus a `before_send` scrubber. `error_alerts.send_error_alert()` gets one added line (`sentry_sdk.capture_exception`) as a safety net for its two callers that don't log with `exc_info` first. `jina_embed/server.py` is a separately-built Docker image that never has `app_server` on its path (confirmed via `Dockerfile.jina-embed`'s `COPY github-app/jina_embed ./jina_embed` only), so it gets a small, deliberately duplicated, self-contained init block instead of importing the shared helper.

**Tech Stack:** Python, `sentry-sdk`, FastAPI, pytest, `pytest-asyncio`, existing `monkeypatch`-based test conventions.

**Spec:** `docs/superpowers/specs/2026-10-05-sentry-error-tracking-design.md`

## Global Constraints

- `SENTRY_DSN` unset ⇒ `init_sentry()` is a no-op. No test in this plan, and no existing test in the suite, may require `SENTRY_DSN` to be set - the whole suite runs today with it absent and must keep doing so.
- `send_default_pii=False`, `before_send` strips `request` data and every stack-frame's `vars`, `traces_sample_rate=0`. Every call site that initializes Sentry must set all of these - no entrypoint gets a lighter config than another.
- `jina_embed/server.py` must never import anything from `app_server.*`. It is built into a separate Docker image (`Dockerfile.jina-embed`) that only copies `github-app/jina_embed`; an `app_server` import would pass every test in this repo (same PYTHONPATH) and then fail at container startup in production, where the test suite can't see it.
- `LoggingIntegration`'s `event_level` stays at `logging.ERROR` (the SDK default), not `WARNING`. The codebase has existing, deliberate `logger.warning(...)` calls for conditions explicitly documented as not alert-worthy (e.g. `app_server/main.py`'s `ClientDisconnect` handling) - lowering this would silently turn those into tracked Sentry issues.
- Email alerting (`error_alerts.py`) is untouched in behavior - Sentry is an additional, independent channel, not a replacement. `sentry_sdk.capture_exception` must still fire even when `RESEND_API_KEY` is unconfigured.

## Review Focus

- A `before_send` event with no `exception` key at all (a plain message-level log event) - the scrubber must not crash on it (Task 2).
- A chained/multi-exception event (`raise X from Y`) - the scrubber must strip `vars` from every frame of every exception in `values`, not just the first (Task 2).
- `sentry_sdk.capture_exception` is called unconditionally inside `send_error_alert`, but the whole existing test suite runs with `SENTRY_DSN` unset - every existing `test_error_alerts.py` test must keep passing unchanged, proving the capture call is a safe no-op with no active client (Task 5).
- `jina_embed`'s new global exception handler must still return a real HTTP response (not hang, not re-raise past Starlette) when a route raises - Sentry capture is a side effect, not a replacement for a response (Task 6).
- `LoggingIntegration` must not promote `logger.warning(...)` calls to Sentry events - only `ERROR` and above (Task 2).

---

## Task 1: Config and dependency

**Files:**
- Modify: `github-app/requirements.txt`
- Modify: `github-app/requirements-jina-embed.txt`
- Modify: `github-app/app_server/config.py`
- Test: `github-app/tests/test_config.py`

**Interfaces:**
- Produces: `Settings.sentry_dsn: str` (empty string default), `Settings.sentry_environment: str` (defaults `"production"`) - consumed by Task 2.

- [ ] **Step 1: Write the failing tests**

Add to `github-app/tests/test_config.py`:

```python
def test_sentry_dsn_defaults_to_empty_string(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    settings = get_settings()
    assert settings.sentry_dsn == ""


def test_sentry_dsn_reads_from_env(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "https://examplePublicKey@o0.ingest.sentry.io/0")
    settings = get_settings()
    assert settings.sentry_dsn == "https://examplePublicKey@o0.ingest.sentry.io/0"


def test_sentry_environment_defaults_to_production(monkeypatch):
    monkeypatch.delenv("SENTRY_ENVIRONMENT", raising=False)
    settings = get_settings()
    assert settings.sentry_environment == "production"


def test_sentry_environment_reads_from_env(monkeypatch):
    monkeypatch.setenv("SENTRY_ENVIRONMENT", "staging")
    settings = get_settings()
    assert settings.sentry_environment == "staging"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_config.py -k sentry -v`
Expected: FAIL with `TypeError: Settings.__init__() got an unexpected keyword argument` or `AttributeError: 'Settings' object has no attribute 'sentry_dsn'`.

- [ ] **Step 3: Add the fields and dependency**

In `app_server/config.py`, add to the `Settings` dataclass (after `pushover_api_token`):

```python
    sentry_dsn: str
    sentry_environment: str
```

Add to `get_settings()`'s `return Settings(...)` call (after `pushover_api_token=...`):

```python
        # Optional, not required: empty means init_sentry() (sentry_config.py)
        # no-ops - local dev, tests, and CI need zero Sentry configuration.
        sentry_dsn=os.environ.get("SENTRY_DSN", "").strip(),
        # Only meaningfully read when sentry_dsn is also set.
        sentry_environment=os.environ.get("SENTRY_ENVIRONMENT", "production").strip()
        or "production",
```

In `requirements.txt`, add (after `semgrep>=1.99.0,<2.0`):

```
sentry-sdk>=2.18.0,<3.0
```

In `requirements-jina-embed.txt`, add:

```
sentry-sdk>=2.18.0,<3.0
```

- [ ] **Step 4: Install and run tests to verify they pass**

Run: `pip install -r requirements.txt` (from `github-app/`), then `pytest tests/test_config.py -k sentry -v`
Expected: PASS

- [ ] **Step 5: Run the full config test file to check no regression**

Run: `pytest tests/test_config.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add requirements.txt requirements-jina-embed.txt app_server/config.py tests/test_config.py
git commit -m "feat: add SENTRY_DSN/SENTRY_ENVIRONMENT config and sentry-sdk dependency"
```

---

## Task 2: Shared `init_sentry()` helper

**Files:**
- Create: `github-app/app_server/sentry_config.py`
- Test: `github-app/tests/test_sentry_config.py`

**Interfaces:**
- Consumes: `app_server.config.get_settings()` → `.sentry_dsn`, `.sentry_environment` (Task 1).
- Produces: `init_sentry(service_name: str) -> None`, used by Tasks 3-5. `_scrub_event(event: dict, hint: dict) -> dict` (module-private, tested directly).

- [ ] **Step 1: Write the failing tests**

Create `github-app/tests/test_sentry_config.py`:

```python
import logging

import pytest
import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration

from app_server.sentry_config import _scrub_event, init_sentry

_FAKE_DSN = "https://examplePublicKey@o0.ingest.sentry.io/0"


@pytest.fixture
def _reset_sentry_client():
    yield
    # Global SDK state (sentry_sdk.init sets a process-wide client) must not
    # leak into whichever test or test file runs next.
    sentry_sdk.init(dsn=None)


def test_init_sentry_is_a_noop_when_dsn_is_unset(monkeypatch, _reset_sentry_client):
    monkeypatch.delenv("SENTRY_DSN", raising=False)

    init_sentry("app_server")

    assert not sentry_sdk.get_client().is_active()


def test_init_sentry_activates_client_when_dsn_is_set(monkeypatch, _reset_sentry_client):
    monkeypatch.setenv("SENTRY_DSN", _FAKE_DSN)

    init_sentry("app_server")

    assert sentry_sdk.get_client().is_active()


def test_init_sentry_configures_no_performance_tracing(monkeypatch, _reset_sentry_client):
    monkeypatch.setenv("SENTRY_DSN", _FAKE_DSN)

    init_sentry("app_server")

    assert sentry_sdk.get_client().options["traces_sample_rate"] == 0


def test_init_sentry_disables_default_pii(monkeypatch, _reset_sentry_client):
    monkeypatch.setenv("SENTRY_DSN", _FAKE_DSN)

    init_sentry("app_server")

    assert sentry_sdk.get_client().options["send_default_pii"] is False


def test_init_sentry_only_reports_error_level_logs_and_above(monkeypatch, _reset_sentry_client):
    monkeypatch.setenv("SENTRY_DSN", _FAKE_DSN)

    init_sentry("app_server")

    integration = sentry_sdk.get_client().get_integration(LoggingIntegration)
    assert integration._handler.level == logging.ERROR


def test_scrub_event_strips_request_data():
    event = {"request": {"headers": {"Cookie": "secret"}}, "exception": {"values": []}}

    scrubbed = _scrub_event(event, {})

    assert "request" not in scrubbed


def test_scrub_event_strips_local_variables_from_every_frame_of_every_exception():
    event = {
        "exception": {
            "values": [
                {
                    "type": "ValueError",
                    "stacktrace": {
                        "frames": [{"filename": "a.py", "vars": {"secret": "x"}}]
                    },
                },
                {
                    "type": "RuntimeError",
                    "stacktrace": {
                        "frames": [{"filename": "b.py", "vars": {"token": "y"}}]
                    },
                },
            ]
        }
    }

    scrubbed = _scrub_event(event, {})

    for exc_value in scrubbed["exception"]["values"]:
        for frame in exc_value["stacktrace"]["frames"]:
            assert "vars" not in frame


def test_scrub_event_does_not_crash_on_a_message_only_event_with_no_exception():
    event = {"message": "something happened", "level": "warning"}

    scrubbed = _scrub_event(event, {})

    assert scrubbed["message"] == "something happened"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_sentry_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app_server.sentry_config'`.

- [ ] **Step 3: Write the implementation**

Create `github-app/app_server/sentry_config.py`:

```python
"""Shared Sentry setup for app_server and scan_worker (the two processes
that already depend on app_server.config). jina_embed/server.py does NOT
use this module - it's built into a separate Docker image that never has
app_server on its path (see Dockerfile.jina-embed) - and keeps its own,
deliberately duplicated, self-contained init block instead.

A single init_sentry() call per process is enough for broad coverage:
sentry_sdk's LoggingIntegration hooks the logging module itself, so any
logger.exception(...)/logger.error(..., exc_info=True) call anywhere in
that process - present or future - becomes a Sentry event with no
per-call-site code change. See
docs/superpowers/specs/2026-10-05-sentry-error-tracking-design.md for why
that, rather than touching every except block, is the real mechanism here.
"""
import logging

import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration

from app_server.config import get_settings


def _scrub_event(event: dict, hint: dict) -> dict:
    """Strip request data and stack-frame local variables before an event
    leaves the process, consistent with Aletheore's existing no-telemetry
    privacy stance applied to this separate category of operational error
    data.
    """
    event.pop("request", None)
    for exc_value in event.get("exception", {}).get("values", []):
        for frame in exc_value.get("stacktrace", {}).get("frames", []):
            frame.pop("vars", None)
    return event


def init_sentry(service_name: str) -> None:
    """No-op when SENTRY_DSN is unset - local dev, tests, and CI need zero
    Sentry configuration. Call once near process startup.
    """
    settings = get_settings()
    if not settings.sentry_dsn:
        return
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.sentry_environment,
        send_default_pii=False,
        before_send=_scrub_event,
        traces_sample_rate=0,
        integrations=[
            LoggingIntegration(level=logging.INFO, event_level=logging.ERROR)
        ],
    )
    sentry_sdk.set_tag("service", service_name)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_sentry_config.py -v`
Expected: all PASS. If `test_init_sentry_only_reports_error_level_logs_and_above` fails because the installed `sentry-sdk` version names the attribute differently from `integration._handler.level`, inspect the installed version's `sentry_sdk/integrations/logging.py` for the actual attribute and adjust the assertion - the behavior being pinned (event_level stays ERROR) does not change, only how to read it back.

- [ ] **Step 5: Run the full test suite to check for cross-test pollution**

Run: `pytest tests/ -v`
Expected: all PASS, including files that ran before `test_sentry_config.py` and after it alphabetically - confirms `_reset_sentry_client` actually prevents the global client from leaking.

- [ ] **Step 6: Commit**

```bash
git add app_server/sentry_config.py tests/test_sentry_config.py
git commit -m "feat: add shared init_sentry() helper for app_server/scan_worker"
```

---

## Task 3: Wire into `app_server/main.py`

**Files:**
- Modify: `github-app/app_server/main.py`
- Test: `github-app/tests/test_main.py`

**Interfaces:**
- Consumes: `app_server.sentry_config.init_sentry(service_name: str)` (Task 2).

- [ ] **Step 1: Write the failing test**

Add to `github-app/tests/test_main.py`:

```python
import importlib

import sentry_sdk


def test_main_module_initializes_sentry_when_dsn_is_configured(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "https://examplePublicKey@o0.ingest.sentry.io/0")
    from app_server.config import get_settings

    get_settings.cache_clear()
    import app_server.main as main_module

    try:
        importlib.reload(main_module)
        assert sentry_sdk.get_client().is_active()
    finally:
        sentry_sdk.init(dsn=None)
        monkeypatch.delenv("SENTRY_DSN", raising=False)
        get_settings.cache_clear()
        importlib.reload(main_module)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_main.py -k initializes_sentry -v`
Expected: FAIL with `assert not True` becoming `assert False` (client never activated - `init_sentry` isn't called yet) or equivalent assertion failure.

- [ ] **Step 3: Add the call**

In `app_server/main.py`, add the import alongside the existing `app_server.*` imports:

```python
from app_server.sentry_config import init_sentry
```

Immediately after `configure_json_logging()` (before `access_logger = logging.getLogger(...)`):

```python
configure_json_logging()
init_sentry("app_server")
access_logger = logging.getLogger("app_server.access")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_main.py -k initializes_sentry -v`
Expected: PASS

- [ ] **Step 5: Run the full test_main.py and full suite to check for regressions**

Run: `pytest tests/test_main.py -v` then `pytest tests/ -v`
Expected: all PASS - the final `importlib.reload(main_module)` in the test's `finally` block restores the module other test files already hold their own `app`/`settings` references to, with Sentry back in its no-op state.

- [ ] **Step 6: Commit**

```bash
git add app_server/main.py tests/test_main.py
git commit -m "feat: initialize Sentry in app_server's main entrypoint"
```

---

## Task 4: Wire into scan_worker's three process entrypoints

**Files:**
- Modify: `github-app/scan_worker/worker.py`
- Modify: `github-app/scan_worker/scheduler.py`
- Modify: `github-app/scan_worker/health_worker.py`

**Interfaces:**
- Consumes: `app_server.sentry_config.init_sentry(service_name: str)` (Task 2).

These three calls live inside each file's `if __name__ == "__main__":` guard, which only runs when the script is executed directly (`python -m scan_worker.worker`, etc.), never on import. This repo's existing convention already leaves that guard untested - `scan_worker/scheduler.py`'s own `configure_json_logging()` call in the same block has no test either; only the extracted `run_forever()` function is unit-tested (`tests/test_scheduler.py`). `init_sentry()`'s own behavior (no-op vs. active, scrubbing, event level) is already fully pinned by Task 2's tests, so this task adds no new decision logic to test - only wiring, verified by the full suite still passing (these entrypoint files are imported, though never executed, by nothing in the test suite, so there is no import-time regression surface here beyond a straightforward syntax/reference check).

- [ ] **Step 1: Add to `scan_worker/worker.py`**

```python
from app_server.config import get_settings
from app_server.heartbeat import start_heartbeat_thread
from app_server.logging_config import configure_json_logging
from app_server.sentry_config import init_sentry


if __name__ == "__main__":
    configure_json_logging()
    init_sentry("scan_worker")
    settings = get_settings()
```

(keep the rest of the block unchanged below `settings = get_settings()`)

- [ ] **Step 2: Add to `scan_worker/scheduler.py`**

In the imports:

```python
from app_server.logging_config import configure_json_logging
from app_server.sentry_config import init_sentry
```

In the `if __name__ == "__main__":` block:

```python
if __name__ == "__main__":
    configure_json_logging()
    init_sentry("scan_worker-scheduler")
    run_forever()
```

- [ ] **Step 3: Add to `scan_worker/health_worker.py`**

In the imports:

```python
from app_server.heartbeat import start_heartbeat_thread
from app_server.logging_config import configure_json_logging
from app_server.sentry_config import init_sentry
```

Immediately after `configure_json_logging()`:

```python
if __name__ == "__main__":
    configure_json_logging()
    init_sentry("scan_worker-health")
    settings = get_settings()
```

(keep the rest of the block unchanged)

- [ ] **Step 4: Run the full test suite to confirm no regression**

Run: `pytest tests/ -v`
Expected: all PASS (these three files aren't imported by any test module, so this step only confirms nothing elsewhere broke).

- [ ] **Step 5: Commit**

```bash
git add scan_worker/worker.py scan_worker/scheduler.py scan_worker/health_worker.py
git commit -m "feat: initialize Sentry in scan_worker's worker/scheduler/health entrypoints"
```

---

## Task 5: `send_error_alert()` safety net

**Files:**
- Modify: `github-app/app_server/error_alerts.py`
- Test: `github-app/tests/test_error_alerts.py`

**Interfaces:**
- Consumes: `sentry_sdk.capture_exception` (stdlib SDK call, no local interface).

**Why this task exists:** `LoggingIntegration` (Task 2) already covers every call site that logs with `exc_info` before alerting. Two of `send_error_alert`'s own callers (in `webhooks/paddle.py` and `jobs.py`'s ops monitor) call it after only a plain `logger.warning()` with no `exc_info` - `LoggingIntegration` can't construct an exception event from that log call alone. This task makes `send_error_alert` itself report to Sentry directly, so nothing routed through the existing alert system - present or future callers - can fall through that gap.

- [ ] **Step 1: Write the failing test**

Add to `github-app/tests/test_error_alerts.py`:

```python
import sentry_sdk


def test_captures_exception_in_sentry_even_when_resend_api_key_is_not_configured(monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    _clear_cooldown("app_server:KeyError")
    captured = []
    monkeypatch.setattr(sentry_sdk, "capture_exception", lambda exc: captured.append(exc))

    error = KeyError("missing")
    send_error_alert("app_server", error)

    assert captured == [error]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_error_alerts.py -k captures_exception_in_sentry -v`
Expected: FAIL with `assert [] == [KeyError('missing')]`.

- [ ] **Step 3: Add the capture call**

In `app_server/error_alerts.py`, add the import at the top:

```python
import sentry_sdk
```

In `send_error_alert`, add the call right after the cooldown gate and before the Resend check:

```python
def send_error_alert(source: str, error: BaseException, context: str = "") -> None:
    key = f"{source}:{type(error).__name__}"
    if not _should_alert(key):
        return

    # Independent of email - still fires even when RESEND_API_KEY is unset,
    # since this is a second, separate alert channel, not a fallback for it.
    sentry_sdk.capture_exception(error)

    settings = get_settings()
    if not settings.resend_api_key:
        return
    ...
```

(keep the rest of the function body unchanged below the `settings = get_settings()` line)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_error_alerts.py -k captures_exception_in_sentry -v`
Expected: PASS

- [ ] **Step 5: Run the full error_alerts test file to check for regressions**

Run: `pytest tests/test_error_alerts.py -v`
Expected: all PASS - confirms `sentry_sdk.capture_exception` is a safe no-op (no active client, `SENTRY_DSN` unset in the test env) for every existing test, including the cooldown/dedup ones.

- [ ] **Step 6: Commit**

```bash
git add app_server/error_alerts.py tests/test_error_alerts.py
git commit -m "feat: report exceptions to Sentry from send_error_alert as an independent channel"
```

---

## Task 6: `jina_embed/server.py` - close the zero-coverage gap

**Files:**
- Modify: `github-app/jina_embed/server.py`
- Test: `github-app/tests/test_jina_embed.py`

**Interfaces:**
- Produces: nothing consumed elsewhere - self-contained, deliberately not importing `app_server.sentry_config` (see Global Constraints).

**Why self-contained:** `Dockerfile.jina-embed` only `COPY`s `github-app/jina_embed` into its image - `app_server` is never present there. An `import app_server...` here would pass the full test suite (same PYTHONPATH as everywhere else in this monorepo) and then fail at container startup in production with `ModuleNotFoundError`, invisible to any test in this repo. The ~15 lines of init logic are duplicated from `sentry_config.py` rather than shared.

- [ ] **Step 1: Write the failing test**

Add to `github-app/tests/test_jina_embed.py`:

```python
from fastapi.testclient import TestClient


def test_unhandled_exception_returns_500_and_reports_to_sentry(monkeypatch):
    server, _ = _import_server(monkeypatch)

    class ExplodingLlama:
        def __init__(self, *args, **kwargs):
            pass

        def create_embedding(self, input):
            raise RuntimeError("boom")

    server._instances = [server._Instance(ExplodingLlama())]
    captured = []
    monkeypatch.setattr(server.sentry_sdk, "capture_exception", lambda exc: captured.append(exc))

    client = TestClient(server.app, raise_server_exceptions=False)
    response = client.post("/embed", json={"text": "hello"})

    assert response.status_code == 500
    assert len(captured) == 1
    assert isinstance(captured[0], RuntimeError)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_jina_embed.py -k unhandled_exception -v`
Expected: FAIL - either a 500 with `captured == []` (no handler registered yet to call `capture_exception`) or an `AttributeError: module 'jina_embed.server' has no attribute 'sentry_sdk'`.

- [ ] **Step 3: Add the self-contained init block and exception handler**

In `jina_embed/server.py`, add to the imports:

```python
import sentry_sdk
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from llama_cpp import Llama
from pydantic import BaseModel
from sentry_sdk.integrations.logging import LoggingIntegration
```

After `logger = logging.getLogger(__name__)`, before the `_THREADS = ...` block:

```python
def _scrub_event(event: dict, hint: dict) -> dict:
    """Strip request data and stack-frame local variables - same policy as
    app_server/sentry_config.py's _scrub_event, duplicated here rather than
    imported since this module is built into a separate Docker image that
    never has app_server on its path (see Dockerfile.jina-embed).
    """
    event.pop("request", None)
    for exc_value in event.get("exception", {}).get("values", []):
        for frame in exc_value.get("stacktrace", {}).get("frames", []):
            frame.pop("vars", None)
    return event


_SENTRY_DSN = os.environ.get("SENTRY_DSN", "").strip()
if _SENTRY_DSN:
    sentry_sdk.init(
        dsn=_SENTRY_DSN,
        environment=os.environ.get("SENTRY_ENVIRONMENT", "production").strip() or "production",
        send_default_pii=False,
        before_send=_scrub_event,
        traces_sample_rate=0,
        integrations=[LoggingIntegration(level=logging.INFO, event_level=logging.ERROR)],
    )
    sentry_sdk.set_tag("service", "jina_embed")
```

Right after `app = FastAPI()`:

```python
app = FastAPI()


@app.exception_handler(Exception)
async def handle_unexpected_exception(request: Request, exc: Exception) -> JSONResponse:
    # No alerting at all before this - a crash here was invisible beyond
    # stdout logs no one was watching (see the design spec's "What this
    # closes" section).
    logger.exception("unhandled exception in request", extra={"path": request.url.path})
    sentry_sdk.capture_exception(exc)
    return JSONResponse(status_code=500, content={"detail": "internal error"})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_jina_embed.py -k unhandled_exception -v`
Expected: PASS

- [ ] **Step 5: Run the full jina_embed test file to check for regressions**

Run: `pytest tests/test_jina_embed.py -v`
Expected: all PASS - every other test in this file uses the same `_import_server` helper and must still import and behave identically with `SENTRY_DSN` unset.

- [ ] **Step 6: Commit**

```bash
git add jina_embed/server.py tests/test_jina_embed.py
git commit -m "feat: add Sentry reporting and exception handling to jina_embed (previously had none)"
```

---

## Task 7: Regenerate lock files

**Files:**
- Modify: `github-app/requirements.lock.txt`
- Modify: `github-app/requirements-jina-embed.lock.txt`

**Interfaces:** none - mechanical, no code.

- [ ] **Step 1: Regenerate the app_server/scan_worker lock file**

Run (from `github-app/`): `pip-compile --allow-unsafe --generate-hashes --no-index --output-file=requirements.lock.txt requirements.txt`

If `pip-compile` or `--no-index`'s required local package source isn't available in this environment, note that explicitly rather than hand-editing the lock file, and flag it in the task report - this step may need to run wherever this repo's existing lock-regeneration tooling actually lives (check for a `chore/regen-github-app-lockfiles`-style script or CI job before assuming it must run here).

- [ ] **Step 2: Regenerate the jina_embed lock file**

Run (from `github-app/`): `pip-compile --allow-unsafe --generate-hashes --no-index --output-file=requirements-jina-embed.lock.txt requirements-jina-embed.txt`

- [ ] **Step 3: Confirm `sentry-sdk` appears in both regenerated lock files**

Run: `grep -i "^sentry-sdk==" requirements.lock.txt requirements-jina-embed.lock.txt`
Expected: one match per file.

- [ ] **Step 4: Run the full test suite one final time**

Run: `pytest tests/ -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add requirements.lock.txt requirements-jina-embed.lock.txt
git commit -m "chore: regenerate lock files with sentry-sdk"
```

---

## After this plan

Not part of this plan (operational, not code):
1. User creates the new Sentry org in the Sentry dashboard.
2. Claude creates the `aletheore` project in it via the already-connected Sentry MCP (`create_project`), which provisions the DSN.
3. `SENTRY_DSN` (and `SENTRY_ENVIRONMENT` if not `"production"`) gets set as an env var on the prod host (`ssh root@187.127.169.89`), not committed to the repo.
4. One PR from this branch, reviewed and merged.
