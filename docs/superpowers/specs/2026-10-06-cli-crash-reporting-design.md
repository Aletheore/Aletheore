# CLI Crash Reporting Design

## Problem

The backend Sentry work (`docs/superpowers/specs/2026-10-05-sentry-error-tracking-design.md`,
PR #961) explicitly excluded `src/aletheore` - the local CLI - as a
non-goal, since it runs client-side on end users' own machines rather than
as a long-lived service we control. That exclusion stands for the backend
PR, but leaves `cli.py`'s own crash coverage at effectively zero: today,
no command wraps its dispatch in a catch-all handler, so any exception not
already anticipated by a specific command's own `except` block prints a
raw Python traceback and exits - with nobody at Aletheore ever seeing it,
across however many OS/Python/environment combinations thousands of
installs represent. We cannot test every one of those combinations
ourselves; the CLI's own users are the only ones who will ever hit most of
them.

## Goal

An unhandled exception in any CLI command gets reported to a dedicated
Sentry project, scrubbed to exclude anything that identifies the user or
their machine, while keeping the OS/Python-version signal that's the
actual point of this feature (telling us *which* environments a bug is
specific to). On by default, with an explicit, discoverable way to turn it
off, and a one-time disclosure so "on by default" is never silent.

## Non-goals

- Performance tracing (`traces_sample_rate=0`, same as the backend).
- Reusing the backend's `aletheore` Sentry project or its
  `app_server/sentry_config.py` module. Different trust model (this DSN
  ships inside a public PyPI package; the backend's is a private env var),
  different volume/noise profile, different owner of the data (end users,
  not our own infra) - see "Architecture" below.
- A GUI settings page. The CLI is a terminal tool; "settings" means a new
  `config` command plus `status` reflecting its state - there is no other
  surface to build this into.
- Retrofitting every individual `except` block in `cli.py` with explicit
  capture calls. Same reasoning as the backend spec: the global wrapper in
  `main()` is the actual "catches everything" mechanism; per-call-site
  capture would be the ~120-except-block sweep rejected there, applied
  here to a smaller but analogous set.
- The token-savings dashboard (GitHub issue #845). Unrelated feature,
  tracked separately.

## Architecture

**New Sentry project:** `aletheore-cli`, same `aletheore` org, created via
the Sentry MCP (`create_project`). Its DSN is public by design - baked
into the published package, the same way any client-side SDK DSN works
(rate-limited per project server-side, not a secret like an API key).

**New file:** `src/aletheore/sentry_reporting.py`

```python
_CLI_SENTRY_DSN = "https://2db08ff31f604202c9bccb356383f072@o4512209917444096.ingest.de.sentry.io/4512209960173648"

def init_cli_sentry() -> None:
    """No-op if crash reporting is disabled (preferences.py) - checked
    before every command dispatch, not just once at import time, so a
    mid-session `config crash-reporting off` takes effect immediately."""
```

Calls `sentry_sdk.init()` with:
- `dsn=_CLI_SENTRY_DSN`
- `environment="production"`
- `release=f"aletheore-cli@{importlib.metadata.version('aletheore')}"` -
  groups issues by CLI version, so "this only breaks on 0.9.19" is visible
  without cross-referencing timestamps.
- `send_default_pii=False`
- `before_send=_scrub_event`
- `traces_sample_rate=0`
- `integrations=[LoggingIntegration(level=logging.INFO, event_level=logging.ERROR)]`

`_scrub_event(event, hint)`:
- Strips `request` (consistent with the backend scrubber, though the CLI
  has no HTTP request context of its own).
- Strips stack-frame `vars` from every frame of every exception in
  `exception.values` (same as the backend).
- Strips `server_name` and `contexts.device.name` - both default to the
  machine's hostname, which is frequently a real person's name (e.g.
  "arihants-macbook").
- Rewrites the home-directory segment of every file path appearing in
  stack-frame `filename`/`abs_path` fields: `str(Path.home())` replaced
  with `"~"`. This is CLI-specific - the backend has no equivalent gap,
  since its stack traces only ever contain paths inside our own
  containers, never a path that embeds someone's OS username.
- **Does not** strip `contexts.os` or `contexts.runtime` (OS type/version,
  Python version) - this is the signal the whole feature exists to
  collect. Keeping it is a deliberate, narrower scrub than the "strip all
  environment context" alternative considered and rejected during
  brainstorming.

**New file:** `src/aletheore/preferences.py`

Local preference store at `~/.config/aletheore/preferences.json` -
sibling to the existing `credentials.json` (same directory, same
dot-config convention), kept as a separate file since this isn't a
secret and has a different read/write pattern.

```python
def is_crash_reporting_enabled() -> bool:
    """ALETHEORE_CRASH_REPORTING env var wins if set (any of "0"/"false"/"no"
    disables, case-insensitive; anything else enables). Otherwise reads the
    preferences file; defaults True if the file or key doesn't exist."""

def set_crash_reporting_enabled(enabled: bool) -> None: ...

def has_shown_crash_reporting_notice() -> bool: ...
def mark_crash_reporting_notice_shown() -> None: ...
```

**Wiring into `cli.py`:**

- `main()` calls `init_cli_sentry()` before dispatching to `app()`.
- `main()` wraps the `app()` call:

```python
try:
    app()
except (typer.Exit, click.exceptions.Exit, SystemExit, KeyboardInterrupt):
    raise
except Exception as exc:
    if is_crash_reporting_enabled():
        sentry_sdk.capture_exception(exc)
        console.print(
            "\n[dim]This error was reported to help fix it. "
            "Disable with `aletheore config crash-reporting off`.[/dim]"
        )
    raise
```

  The explicit re-raise after `console.print` preserves today's existing
  behavior (traceback still reaches the user / their shell's exit code
  handling) - this wrapper adds reporting as a side effect, not a
  replacement for however `typer`/Python already surfaces an unhandled
  error.
- First-run notice: immediately after `init_cli_sentry()`, if
  `not has_shown_crash_reporting_notice()`, print a one-time line ("crash
  reports help fix bugs across environments we can't all test; disable
  with `aletheore config crash-reporting off`") and call
  `mark_crash_reporting_notice_shown()`. Shown once ever, regardless of
  which command was invoked.
- New command group: `aletheore config crash-reporting [on|off]`. Bare
  `aletheore config crash-reporting` (no argument) prints the current
  state instead of changing it.
- `status()` gains a line: `Crash reporting: on` / `Crash reporting: off
  (run 'aletheore config crash-reporting on' to enable)`.

## Config

- `ALETHEORE_CRASH_REPORTING` env var (optional) - overrides the
  preference file. Exists for CI/scripted runs where a prior interactive
  toggle isn't practical, matching how `SENTRY_DSN` itself is env-driven
  on the backend.
- `~/.config/aletheore/preferences.json` - new file, created on first
  write (first notice-shown, or first explicit toggle).
- `sentry-sdk` added to the CLI package's dependencies in `src/pyproject.toml`.

## Testing

- Unit tests for `preferences.py`: env var override wins over the file in
  both directions; defaults to enabled when neither is set; file
  round-trips correctly.
- Unit tests for `_scrub_event`: hostname/device-name stripped; home-dir
  path segments redacted to `~`; local variables stripped from every
  frame of every exception; `os`/`runtime` contexts are **not** stripped
  (a regression here would silently defeat the feature's actual purpose).
- Unit test for the `main()` crash wrapper: a real `Exception` triggers
  `capture_exception` and the disclosure line (when enabled) and is
  re-raised; `typer.Exit`/`KeyboardInterrupt` do not trigger capture.
- **Global test-suite constraint, inverted from the backend spec:** since
  this DSN is baked in (not env-gated) and defaults to enabled, the test
  suite must not activate a real Sentry client against the live
  `aletheore-cli` project. An autouse `conftest.py` fixture sets
  `ALETHEORE_CRASH_REPORTING=0` for every test by default; the capture
  tests above explicitly override it per-test via `monkeypatch` and assert
  against a stubbed `sentry_sdk.capture_exception`, never a real client.

## Rollout

1. `aletheore-cli` Sentry project created via the Sentry MCP (done during
   brainstorming - DSN above).
2. Implement per the plan (TDD, as usual).
3. Version bump + publish to PyPI (existing release process) - this is
   the actual "deploy" for a CLI; there's no server-side rollout step.
