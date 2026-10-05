# Sentry Error Tracking Design

## Problem

Aletheore's backend (`app_server`, `scan_worker`, `jina_embed`) has no
third-party error tracking. The only existing signal is
`app_server/error_alerts.py`, a homegrown, rate-limited email alerter whose
own docstring says it exists specifically *instead of* standing up a
separate error-tracking service. It's wired into exactly two choke points
(app_server's global FastAPI exception handler, and the `@log_job`
decorator wrapping scan_worker's background jobs) plus a handful of manual
call sites in `webhooks/paddle.py` and `jobs.py`'s ops monitor. Everything
else - any of the ~120 `except Exception` blocks elsewhere in the codebase,
and all of `jina_embed/server.py`, which has zero exception handling of any
kind - is invisible beyond a log line nobody is watching. Code review and
the ongoing hotspot audit catch static defects; this is for the ones that
only show up at runtime.

Sentry MCP is already connected and authenticated to this session
(`claude mcp get sentry`). The user wants a new, dedicated Sentry org
(separate from the old, shut-down Procta project currently in the only
org this session has access to - `procta-9p`) and wants coverage to be as
close to total as the tooling allows, not just the paths already wired to
email alerting.

## Goal

Every exception that is already being logged anywhere in the running
backend - today or in any future code, without anyone remembering to wire
a new call site - shows up in Sentry, scrubbed of request/user data and
local-variable values, tagged by which of the five long-running processes
produced it.

## Non-goals

- Performance tracing / transaction sampling. Errors only
  (`traces_sample_rate=0`). Revisit later if wanted.
- Replacing `error_alerts.py`'s email alerting. It stays exactly as-is;
  Sentry is a second, independent channel.
- Retrofitting every one of the ~120 `except Exception` blocks in the
  codebase with an explicit Sentry call. See "What this cannot cover"
  below for why, and what to do about the residual gap instead.
- Instrumenting `src/aletheore` (the CLI) or `benchmarks/`. Both run
  client-side/in CI, not as a long-lived service; a crash there is already
  visible to whoever invoked it.
- A new Sentry org. The user is creating that manually in the Sentry
  dashboard (MCP has no `create_organization` tool); this plan starts from
  `create_project` once that org exists and the MCP session can see it.

## How "all" actually gets covered

The honest mechanism for "all" is not finding and touching every
individual `except` block - that's ~120 call sites across 165k LOC, an
unscoped and error-prone diff for a one-time setup task. The real lever is
that the Python SDK's `LoggingIntegration` hooks the logging module itself:
once `sentry_sdk.init()` has run in a process, **any** `logger.exception(...)`
or `logger.error(..., exc_info=True)` call anywhere in that process -
written today or six months from now - becomes a Sentry event automatically,
with no per-call-site code change. A live count in this codebase today:

- `logger.exception(...)`: 3 call sites
- `logger.error(..., exc_info=True)`: 30 call sites
- `logger.warning(..., exc_info=True)`: 31 call sites

`LoggingIntegration`'s `event_level` is set to `ERROR` (the SDK default),
not lowered to `WARNING`. The 33 ERROR-level call sites above get swept in
for free. Lowering to `WARNING` was considered and rejected: a concrete
example in `app_server/main.py` logs `ClientDisconnect` at `warning` with an
explicit comment that it is deliberately *not* a bug worth alerting on - the
codebase already uses warning-level logging for expected, benign conditions
in several places, and promoting all of them to tracked Sentry issues would
reintroduce the exact alert-fatigue problem `error_alerts.py`'s cooldown
system was built to solve. The 31 `warning`-level `exc_info=True` sites are
a known, explicit gap left at this severity rather than silently dropped or
indiscriminately promoted.

Two of `send_error_alert()`'s existing callers (`webhooks/paddle.py`,
`jobs.py`'s ops monitor) call it after only a plain `logger.warning()` with
no `exc_info` - `LoggingIntegration` can't construct an exception event from
that. `send_error_alert()` itself gets an explicit `sentry_sdk.capture_exception(error)`
call as a safety net, so nothing already routed through the existing alert
system can fall through this particular gap, present or future. This does
mean two call sites (`log_job`'s except block, `main.py`'s global handler)
report the same exception twice - once via `LoggingIntegration` picking up
their `logger.exception(...)` call, once via `send_error_alert`'s explicit
capture. Sentry groups events into one Issue by stack-trace fingerprint
regardless of how many paths reported it, so this shows up as one Issue
with a slightly inflated event count, not a duplicate bug report - judged
worth it for the guarantee that the alert system's own call sites are never
silently unwired.

### What this cannot cover

A small number of `except Exception: ...` blocks neither re-raise nor log
anything at all - true silent swallows. No error-tracking tool can see an
exception that never produces a log record or a re-raise; this is a static
property of the code, not a runtime-observability gap. This is exactly the
kind of finding the ongoing hotspot code-health audit
(`docs/audits/overnight_review_2026_10_05.md`) is suited to catch - one
pass already flagged a "fail silently" pattern near `redis_client.py`. If
the user wants this residual category closed, the right move is adding
"silent exception swallowing" as an explicit checklist item to that audit's
next pass, not expanding this spec's scope to a blind, codebase-wide
except-block sweep.

## Architecture

**New file:** `github-app/app_server/sentry_config.py`

```python
def init_sentry(service_name: str) -> None:
    """No-op if SENTRY_DSN is unset - local dev, tests, and CI need zero config."""
```

Calls `sentry_sdk.init()` with:
- `dsn=settings.sentry_dsn` (empty default)
- `environment=settings.sentry_environment` (default `"production"`)
- `send_default_pii=False`
- `before_send`: strips request body/headers and stack-frame local-variable
  values, keeping exception type/message and `file:line` only
- `traces_sample_rate=0`
- `integrations=[LoggingIntegration(level=logging.INFO, event_level=logging.ERROR)]`
  (`level=INFO` only affects breadcrumbs attached to an event, not whether
  one is created)
- `tags={"service": service_name}`

**Call sites** (one line added near each process's existing
`configure_json_logging()` call):

| Process | File | Today's coverage |
|---|---|---|
| `app_server` | `app_server/main.py` | global exception handler → `send_error_alert` |
| scan_worker (scans) | `scan_worker/worker.py` | `@log_job` on all 29 job functions |
| scan_worker (scheduler) | `scan_worker/scheduler.py` | none - bare loop, no try/except |
| scan_worker (health) | `scan_worker/health_worker.py` | none |
| jina_embed | `jina_embed/server.py` | **none at all today** - real gap closed by this change |

Sentry's default global-exception hook (enabled automatically by `init_sentry()`)
covers process-crashing exceptions in the three bare-loop/worker entrypoints
with no application code changes needed.

**`error_alerts.py`:** `send_error_alert()` gains one line,
`sentry_sdk.capture_exception(error)`, alongside its existing email send.

**`jina_embed/server.py`:** gains a minimal
`@app.exception_handler(Exception)` mirroring `app_server/main.py`'s
pattern (log + capture, no email - lower criticality, and plumbing it into
`error_alerts.py` would add an app_server dependency to a service that
doesn't otherwise have one) - this is the one new capture point that isn't
"add init_sentry() and let the existing logging handle the rest."

## Config

- `SENTRY_DSN` (optional, `app_server/config.py`) - empty by default.
- `SENTRY_ENVIRONMENT` (optional, default `"production"`).
- `sentry-sdk` added to `requirements.txt` (app_server + scan_worker share
  it, confirmed via `scan_worker/worker.py` already importing
  `app_server.config`/`app_server.logging_config`) and
  `requirements-jina-embed.txt`, plus their `.lock.txt` counterparts.
- DSN and org slug are set once the user has created the Aletheore org and
  I've run `create_project` via the Sentry MCP to provision it.

## Testing

- Unit test: `init_sentry()` is a no-op (doesn't raise, doesn't configure a
  client) when `SENTRY_DSN` is unset - the existing test suite must keep
  passing with zero Sentry config.
- Unit test: `before_send` scrubbing strips local variables / request body
  from a constructed event dict.
- Manual verification after deploy: trigger one real exception in each of
  the 5 processes (or use Sentry's own test-event mechanism) and confirm an
  event lands in the new project, tagged with the right `service`.

## Rollout

1. User creates the new Sentry org in the dashboard.
2. I create the `aletheore` project in it via the Sentry MCP
   (`create_project`, provisions the DSN).
3. Implement per the plan (TDD, as usual).
4. DSN set as an env var on the prod host
   (`ssh root@187.127.169.89`), not committed to the repo.
5. One PR, reviewed and merged like the rest of this session's work.
