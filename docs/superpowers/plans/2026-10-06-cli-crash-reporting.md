# CLI Crash Reporting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `src/aletheore/cli.py` (the local, pip-installed CLI) a global crash-reporting safety net - any unhandled exception in any command gets sent to a dedicated `aletheore-cli` Sentry project, scrubbed of anything identifying the user or machine, on by default with a discoverable opt-out.

**Architecture:** Two new, self-contained modules - `aletheore/preferences.py` (a local JSON preference store, patterned directly after the existing `aletheore/credentials.py`) and `aletheore/sentry_reporting.py` (Sentry init + event scrubbing, a CLI-specific sibling of the backend's `github-app/app_server/sentry_config.py`, not a shared import). `cli.py`'s `main()` gets three additions: an `init_cli_sentry()` call, a one-time first-run notice, and a `try/except` wrapper around the existing `app()` dispatch. A new `aletheore config crash-reporting [on|off]` command and a new line in the existing `status` command expose the toggle.

**Tech Stack:** Python, `sentry-sdk`, `typer`, `click`, `rich`, pytest, `monkeypatch`-based test conventions (matching this package's existing `tests/test_credentials.py` and `tests/test_cli.py`).

**Spec:** `docs/superpowers/specs/2026-10-06-cli-crash-reporting-design.md`

## Global Constraints

- `ALETHEORE_CRASH_REPORTING` env var: `"0"`/`"false"`/`"no"` (case-insensitive) disables; any other value, including an empty string, is treated as enabled - a merely-set env var must never silently disable reporting. The env var always wins over the preferences file when set at all.
- Crash reporting defaults to **enabled** when neither the env var nor the preferences file says otherwise, and defaults to enabled on a corrupted/unreadable preferences file (fail open, not closed).
- `_scrub_event` must strip: `request`, `server_name`, `contexts.device.name`, stack-frame `vars` (every frame of every exception in `exception.values`), and the home-directory segment of every stack-frame `filename`/`abs_path`. It must **not** strip `contexts.os` or `contexts.runtime` - that OS/Python-version signal is the entire point of this feature.
- `sentry_sdk.init()` always uses `traces_sample_rate=0` and `send_default_pii=False` - no call site gets a weaker config.
- The test suite must never activate a real Sentry client against the live `aletheore-cli` project. `ALETHEORE_CRASH_REPORTING=0` is set autouse for the whole suite (Task 2); any test exercising the real capture call explicitly overrides that and stubs `sentry_sdk.capture_exception` directly.
- `main()`'s crash wrapper must re-raise the original exception - reporting is a side effect, never a replacement for today's existing exit/traceback behavior - and a failure inside `sentry_sdk.capture_exception` itself must never replace or swallow that original exception.
- `typer.Exit`, `click.exceptions.Exit`, `SystemExit`, and `KeyboardInterrupt` must never trigger a capture or the "this was reported" message - these are normal exits, not crashes.

## Review Focus

- A corrupted/non-JSON `preferences.json` on disk - `is_crash_reporting_enabled()` must default to `True` and must not crash CLI startup (Task 1).
- `sentry_sdk.capture_exception` itself raising inside the crash wrapper (e.g. no network, a future bad DSN) - the user's original exception must still propagate, not get replaced by an unrelated Sentry SDK error (Task 4).
- Ctrl-C (`KeyboardInterrupt`) during a running command - must propagate normally with no Sentry capture and no "this was reported" message (Task 4).
- `ALETHEORE_CRASH_REPORTING` set to an empty string (e.g. exported-but-unset by some shell config) - must not silently disable reporting, since it's "set" but not a recognized off-value (Task 1).
- A brand-new install with no `~/.config/aletheore/` directory yet (nobody has ever run `login`) - the first write (first-run notice, or first explicit toggle) must create the directory, not crash (Task 1).

---

## Task 1: `preferences.py` - local preference store

**Files:**
- Create: `src/aletheore/preferences.py`
- Test: `src/tests/test_preferences.py`

**Interfaces:**
- Produces: `DEFAULT_PREFERENCES_PATH: Path`, `is_crash_reporting_enabled(preferences_path: Path = DEFAULT_PREFERENCES_PATH) -> bool`, `set_crash_reporting_enabled(enabled: bool, preferences_path: Path = DEFAULT_PREFERENCES_PATH) -> None`, `has_shown_crash_reporting_notice(preferences_path: Path = DEFAULT_PREFERENCES_PATH) -> bool`, `mark_crash_reporting_notice_shown(preferences_path: Path = DEFAULT_PREFERENCES_PATH) -> None` - consumed by Tasks 2, 3, 4, 5. Also produces the internal `_load_preferences(preferences_path: Path) -> dict` and `_save_preference(preferences_path: Path, key: str, value: bool) -> None`, which Task 2's test-isolation fixture monkeypatches directly (same shape as `credentials.py`'s `_load_saved_key`).

- [ ] **Step 1: Write the failing tests**

Create `src/tests/test_preferences.py`:

```python
from aletheore.preferences import (
    has_shown_crash_reporting_notice,
    is_crash_reporting_enabled,
    mark_crash_reporting_notice_shown,
    set_crash_reporting_enabled,
)


def test_crash_reporting_defaults_to_enabled_when_nothing_set(monkeypatch, tmp_path):
    monkeypatch.delenv("ALETHEORE_CRASH_REPORTING", raising=False)
    assert is_crash_reporting_enabled(tmp_path / "prefs.json") is True


def test_crash_reporting_env_var_zero_disables(monkeypatch, tmp_path):
    monkeypatch.setenv("ALETHEORE_CRASH_REPORTING", "0")
    assert is_crash_reporting_enabled(tmp_path / "prefs.json") is False


def test_crash_reporting_env_var_false_disables_case_insensitive(monkeypatch, tmp_path):
    monkeypatch.setenv("ALETHEORE_CRASH_REPORTING", "FALSE")
    assert is_crash_reporting_enabled(tmp_path / "prefs.json") is False


def test_crash_reporting_env_var_empty_string_does_not_disable(monkeypatch, tmp_path):
    # An env var merely being *set* (e.g. exported empty by some shell
    # config) must not silently disable reporting - only a recognized
    # "off" value should.
    monkeypatch.setenv("ALETHEORE_CRASH_REPORTING", "")
    assert is_crash_reporting_enabled(tmp_path / "prefs.json") is True


def test_crash_reporting_env_var_overrides_a_disabled_file(monkeypatch, tmp_path):
    prefs_path = tmp_path / "prefs.json"
    set_crash_reporting_enabled(False, prefs_path)
    monkeypatch.setenv("ALETHEORE_CRASH_REPORTING", "1")
    assert is_crash_reporting_enabled(prefs_path) is True


def test_set_crash_reporting_enabled_persists_across_reads(monkeypatch, tmp_path):
    monkeypatch.delenv("ALETHEORE_CRASH_REPORTING", raising=False)
    prefs_path = tmp_path / "prefs.json"
    set_crash_reporting_enabled(False, prefs_path)
    assert is_crash_reporting_enabled(prefs_path) is False


def test_corrupted_preferences_file_defaults_to_enabled(monkeypatch, tmp_path):
    # Fail open, not closed: a malformed file must not crash CLI startup,
    # and must not silently disable the very safety net meant to catch
    # bugs like this one.
    monkeypatch.delenv("ALETHEORE_CRASH_REPORTING", raising=False)
    prefs_path = tmp_path / "prefs.json"
    prefs_path.write_text("{not valid json")
    assert is_crash_reporting_enabled(prefs_path) is True


def test_set_crash_reporting_enabled_creates_missing_config_directory(tmp_path):
    prefs_path = tmp_path / "nested" / "does" / "not" / "exist" / "preferences.json"
    set_crash_reporting_enabled(False, prefs_path)
    assert prefs_path.exists()


def test_notice_not_shown_by_default(tmp_path):
    assert has_shown_crash_reporting_notice(tmp_path / "prefs.json") is False


def test_mark_notice_shown_persists(tmp_path):
    prefs_path = tmp_path / "prefs.json"
    mark_crash_reporting_notice_shown(prefs_path)
    assert has_shown_crash_reporting_notice(prefs_path) is True


def test_setting_crash_reporting_does_not_clobber_notice_shown_flag(tmp_path):
    prefs_path = tmp_path / "prefs.json"
    mark_crash_reporting_notice_shown(prefs_path)
    set_crash_reporting_enabled(False, prefs_path)
    assert has_shown_crash_reporting_notice(prefs_path) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `src/`): `pytest tests/test_preferences.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aletheore.preferences'`.

- [ ] **Step 3: Write the implementation**

Create `src/aletheore/preferences.py`:

```python
"""Local, non-secret CLI preferences - today just the crash-reporting
opt-out. Sibling to credentials.py (same ~/.config/aletheore/ directory,
same "patch the loader, not the path constant" test-isolation pattern -
see tests/conftest.py), kept in a separate file since this isn't a secret
and has a simpler read/write pattern: load the whole file, mutate one key,
write it back whole. Deliberately skips credentials.py's cross-platform
file locking (_locked_rw_credentials_file) - losing a race between two
concurrent CLI invocations on a preference toggle just means re-running
the toggle, not silently losing a saved API token, so that complexity
isn't worth carrying here.
"""
import json
import os
from pathlib import Path

DEFAULT_PREFERENCES_PATH = Path.home() / ".config" / "aletheore" / "preferences.json"

_CRASH_REPORTING_KEY = "crash_reporting"
_NOTICE_SHOWN_KEY = "crash_reporting_notice_shown"

# Recognized "disable" values for ALETHEORE_CRASH_REPORTING, matched
# case-insensitively. Any other non-empty value - including "" - is
# treated as enabled: an env var merely being *set* must not silently
# disable reporting, only an explicit, recognized "off" value should.
_DISABLE_VALUES = {"0", "false", "no"}


def is_crash_reporting_enabled(preferences_path: Path = DEFAULT_PREFERENCES_PATH) -> bool:
    env_value = os.environ.get("ALETHEORE_CRASH_REPORTING")
    if env_value is not None:
        return env_value.strip().lower() not in _DISABLE_VALUES

    data = _load_preferences(preferences_path)
    value = data.get(_CRASH_REPORTING_KEY)
    return value if isinstance(value, bool) else True


def set_crash_reporting_enabled(
    enabled: bool, preferences_path: Path = DEFAULT_PREFERENCES_PATH
) -> None:
    _save_preference(preferences_path, _CRASH_REPORTING_KEY, enabled)


def has_shown_crash_reporting_notice(preferences_path: Path = DEFAULT_PREFERENCES_PATH) -> bool:
    data = _load_preferences(preferences_path)
    return bool(data.get(_NOTICE_SHOWN_KEY, False))


def mark_crash_reporting_notice_shown(preferences_path: Path = DEFAULT_PREFERENCES_PATH) -> None:
    _save_preference(preferences_path, _NOTICE_SHOWN_KEY, True)


def _load_preferences(preferences_path: Path) -> dict:
    if not preferences_path.exists():
        return {}
    try:
        data = json.loads(preferences_path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_preference(preferences_path: Path, key: str, value: bool) -> None:
    preferences_path.parent.mkdir(parents=True, exist_ok=True)
    data = _load_preferences(preferences_path)
    data[key] = value
    preferences_path.write_text(json.dumps(data, indent=2))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_preferences.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add aletheore/preferences.py tests/test_preferences.py
git commit -m "feat: add local CLI preferences store for crash-reporting opt-out"
```

---

## Task 2: Isolate preferences in the test suite

**Files:**
- Modify: `src/tests/conftest.py`

**Interfaces:**
- Consumes: `aletheore.preferences.DEFAULT_PREFERENCES_PATH`, `_load_preferences`, `_save_preference` (Task 1).

**Why this task exists:** `tests/conftest.py` already has `_isolate_saved_credentials`, an autouse fixture solving exactly this problem for `credentials.json` - real developer-machine state (a saved token, in that case) silently changed test behavior. The same risk now exists for `preferences.json`, plus a second, CLI-specific risk the backend never had: this DSN is baked into `sentry_reporting.py` rather than read from an unset-by-default env var, so without an explicit override, any test that exercises `main()`'s crash path would configure a real Sentry client against the live `aletheore-cli` project.

- [ ] **Step 1: Add the fixture**

In `src/tests/conftest.py`, add after the existing `_isolate_saved_credentials` fixture:

```python
@pytest.fixture(autouse=True)
def _isolate_crash_reporting_preferences(tmp_path, monkeypatch):
    """Global safety net, same class of bug as _isolate_saved_credentials
    above: no test run should ever read or write this machine's real
    ~/.config/aletheore/preferences.json. Also forces crash reporting off
    for the whole suite by default (ALETHEORE_CRASH_REPORTING=0) - unlike
    the backend's Sentry setup (SENTRY_DSN simply unset in every test
    environment), this CLI's DSN is a baked-in constant, so nothing else
    stops a test that reaches main()'s crash path from configuring a real
    Sentry client against the live aletheore-cli project. Tests that
    specifically exercise the capture call override this env var
    themselves and stub sentry_sdk.capture_exception directly - never a
    real client.
    """
    monkeypatch.setenv("ALETHEORE_CRASH_REPORTING", "0")

    import aletheore.preferences as preferences

    fake = tmp_path / "preferences.json"
    real_default = preferences.DEFAULT_PREFERENCES_PATH
    real_load = preferences._load_preferences
    real_save = preferences._save_preference

    def _load_ignoring_the_real_file(preferences_path):
        if preferences_path == real_default:
            preferences_path = fake
        return real_load(preferences_path)

    def _save_ignoring_the_real_file(preferences_path, key, value):
        if preferences_path == real_default:
            preferences_path = fake
        return real_save(preferences_path, key, value)

    monkeypatch.setattr(preferences, "_load_preferences", _load_ignoring_the_real_file)
    monkeypatch.setattr(preferences, "_save_preference", _save_ignoring_the_real_file)
```

- [ ] **Step 2: Run the full test suite to confirm no regression**

Run (from `src/`): `pytest tests/ -v`
Expected: all PASS, including `tests/test_preferences.py` from Task 1 (those tests pass their own `tmp_path`-derived paths explicitly, so this fixture is a no-op for them - confirms the redirect-only-the-real-default logic doesn't interfere with tests that already isolate themselves).

- [ ] **Step 3: Commit**

```bash
git add tests/conftest.py
git commit -m "test: isolate CLI preferences and disable crash reporting by default in tests"
```

---

## Task 3: `sentry_reporting.py` - Sentry init and event scrubbing

**Files:**
- Create: `src/aletheore/sentry_reporting.py`
- Modify: `src/pyproject.toml`
- Test: `src/tests/test_sentry_reporting.py`

**Interfaces:**
- Consumes: `aletheore.preferences.is_crash_reporting_enabled()` (Task 1).
- Produces: `init_cli_sentry() -> None`, used by Task 4. `_scrub_event(event: dict, hint: dict) -> dict` and `_HOME: str` (module-private, tested directly; `_HOME` is monkeypatched in one test below).

- [ ] **Step 1: Add the dependency**

In `src/pyproject.toml`, add to the `dependencies` list (after the `"click<8.6.0"` entry and its comment, before the `watchdog` entry):

```
    "sentry-sdk>=2.18.0,<3.0",
```

Install it: `pip install -e .` (from `src/`)

- [ ] **Step 2: Write the failing tests**

Create `src/tests/test_sentry_reporting.py`:

```python
import importlib.metadata
from pathlib import Path

import pytest
import sentry_sdk

from aletheore.sentry_reporting import _scrub_event, init_cli_sentry


@pytest.fixture
def _reset_sentry_client():
    yield
    # Global SDK state (sentry_sdk.init sets a process-wide client) must
    # not leak into whichever test runs next.
    sentry_sdk.init(dsn=None)


def test_init_cli_sentry_is_a_noop_when_crash_reporting_is_disabled(
    monkeypatch, _reset_sentry_client
):
    monkeypatch.setenv("ALETHEORE_CRASH_REPORTING", "0")

    init_cli_sentry()

    assert not sentry_sdk.get_client().is_active()


def test_init_cli_sentry_activates_client_when_crash_reporting_is_enabled(
    monkeypatch, _reset_sentry_client
):
    monkeypatch.setenv("ALETHEORE_CRASH_REPORTING", "1")

    init_cli_sentry()

    assert sentry_sdk.get_client().is_active()


def test_init_cli_sentry_configures_no_performance_tracing(monkeypatch, _reset_sentry_client):
    monkeypatch.setenv("ALETHEORE_CRASH_REPORTING", "1")

    init_cli_sentry()

    assert sentry_sdk.get_client().options["traces_sample_rate"] == 0


def test_init_cli_sentry_disables_default_pii(monkeypatch, _reset_sentry_client):
    monkeypatch.setenv("ALETHEORE_CRASH_REPORTING", "1")

    init_cli_sentry()

    assert sentry_sdk.get_client().options["send_default_pii"] is False


def test_init_cli_sentry_tags_release_with_installed_version(monkeypatch, _reset_sentry_client):
    monkeypatch.setenv("ALETHEORE_CRASH_REPORTING", "1")

    init_cli_sentry()

    installed_version = importlib.metadata.version("aletheore")
    assert sentry_sdk.get_client().options["release"] == f"aletheore-cli@{installed_version}"


def test_scrub_event_strips_request_data():
    event = {"request": {"headers": {"Cookie": "secret"}}, "exception": {"values": []}}

    scrubbed = _scrub_event(event, {})

    assert "request" not in scrubbed


def test_scrub_event_strips_server_name():
    event = {"server_name": "arihants-macbook", "exception": {"values": []}}

    scrubbed = _scrub_event(event, {})

    assert "server_name" not in scrubbed


def test_scrub_event_strips_device_name_but_keeps_other_device_fields():
    event = {
        "contexts": {"device": {"name": "arihants-macbook", "arch": "arm64"}},
        "exception": {"values": []},
    }

    scrubbed = _scrub_event(event, {})

    assert "name" not in scrubbed["contexts"]["device"]
    assert scrubbed["contexts"]["device"]["arch"] == "arm64"


def test_scrub_event_keeps_os_and_runtime_context():
    event = {
        "contexts": {
            "os": {"name": "Darwin", "version": "24.6.0"},
            "runtime": {"name": "CPython", "version": "3.13.1"},
        },
        "exception": {"values": []},
    }

    scrubbed = _scrub_event(event, {})

    assert scrubbed["contexts"]["os"]["name"] == "Darwin"
    assert scrubbed["contexts"]["runtime"]["version"] == "3.13.1"


def test_scrub_event_redacts_home_directory_segment_of_stack_frame_paths(monkeypatch):
    monkeypatch.setattr("aletheore.sentry_reporting._HOME", str(Path("/Users/johnsmith")))
    event = {
        "exception": {
            "values": [
                {
                    "stacktrace": {
                        "frames": [
                            {
                                "filename": "/Users/johnsmith/project/cli.py",
                                "abs_path": "/Users/johnsmith/project/cli.py",
                            }
                        ]
                    }
                }
            ]
        }
    }

    scrubbed = _scrub_event(event, {})

    frame = scrubbed["exception"]["values"][0]["stacktrace"]["frames"][0]
    assert frame["filename"] == "~/project/cli.py"
    assert frame["abs_path"] == "~/project/cli.py"


def test_scrub_event_strips_local_variables_from_every_frame_of_every_exception():
    event = {
        "exception": {
            "values": [
                {"stacktrace": {"frames": [{"filename": "a.py", "vars": {"secret": "x"}}]}},
                {"stacktrace": {"frames": [{"filename": "b.py", "vars": {"token": "y"}}]}},
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


def test_scrub_event_does_not_crash_when_contexts_or_device_is_missing():
    event = {"exception": {"values": []}}

    scrubbed = _scrub_event(event, {})

    assert scrubbed == {"exception": {"values": []}}
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_sentry_reporting.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aletheore.sentry_reporting'`.

- [ ] **Step 4: Write the implementation**

Create `src/aletheore/sentry_reporting.py`:

```python
"""Sentry crash reporting for the local CLI (src/aletheore). Deliberately
separate from the backend's github-app/app_server/sentry_config.py, not a
shared import: different Sentry project (aletheore-cli vs aletheore),
different trust model (this DSN ships inside a public PyPI package, so it
stays a module constant here rather than reading SENTRY_DSN from the
environment the way the backend does), and a different scrub policy - see
docs/superpowers/specs/2026-10-06-cli-crash-reporting-design.md.
"""
import importlib.metadata
import logging
from pathlib import Path

import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration

from aletheore.preferences import is_crash_reporting_enabled

_CLI_SENTRY_DSN = (
    "https://2db08ff31f604202c9bccb356383f072@"
    "o4512209917444096.ingest.de.sentry.io/4512209960173648"
)

_HOME = str(Path.home())


def _scrub_event(event: dict, hint: dict) -> dict:
    """Strip anything that identifies the user or their machine, while
    keeping the OS/Python-version signal this feature exists to collect.
    """
    event.pop("request", None)
    event.pop("server_name", None)

    device = event.get("contexts", {}).get("device")
    if isinstance(device, dict):
        device.pop("name", None)

    for exc_value in event.get("exception", {}).get("values", []):
        for frame in exc_value.get("stacktrace", {}).get("frames", []):
            frame.pop("vars", None)
            for path_field in ("filename", "abs_path"):
                value = frame.get(path_field)
                if isinstance(value, str) and value.startswith(_HOME):
                    frame[path_field] = "~" + value[len(_HOME) :]

    return event


def init_cli_sentry() -> None:
    """No-op if crash reporting is disabled (preferences.py) - checked
    fresh on every call, not cached, so a mid-session
    `aletheore config crash-reporting off` takes effect on the CLI's next
    invocation with no reinstall needed.
    """
    if not is_crash_reporting_enabled():
        return

    sentry_sdk.init(
        dsn=_CLI_SENTRY_DSN,
        environment="production",
        release=f"aletheore-cli@{importlib.metadata.version('aletheore')}",
        send_default_pii=False,
        before_send=_scrub_event,
        traces_sample_rate=0,
        integrations=[
            LoggingIntegration(level=logging.INFO, event_level=logging.ERROR)
        ],
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_sentry_reporting.py -v`
Expected: all PASS

- [ ] **Step 6: Run the full test suite to check for cross-test pollution**

Run: `pytest tests/ -v`
Expected: all PASS - confirms Task 2's autouse fixture actually prevents `ALETHEORE_CRASH_REPORTING` and the global Sentry client from leaking between this file's tests and the rest of the suite.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml aletheore/sentry_reporting.py tests/test_sentry_reporting.py
git commit -m "feat: add Sentry init and event scrubbing for the CLI"
```

---

## Task 4: Wire into `cli.py`'s `main()`

**Files:**
- Modify: `src/aletheore/cli.py` (imports block, and `main()` at the end of the file)
- Test: `src/tests/test_cli.py`

**Interfaces:**
- Consumes: `aletheore.sentry_reporting.init_cli_sentry()` (Task 3), `aletheore.preferences.is_crash_reporting_enabled()`, `has_shown_crash_reporting_notice()`, `mark_crash_reporting_notice_shown()` (Task 1).

- [ ] **Step 1: Write the failing tests**

Add to `src/tests/test_cli.py` (near the existing `test_main_pins_stdout_and_stderr_to_utf8_before_running_the_cli` tests):

```python
def test_main_initializes_cli_sentry_before_running_the_cli(monkeypatch):
    from aletheore.cli import main

    calls = []
    monkeypatch.setattr("aletheore.cli.init_cli_sentry", lambda: calls.append("init"))
    monkeypatch.setattr("aletheore.cli.has_shown_crash_reporting_notice", lambda: True)
    monkeypatch.setattr("aletheore.cli.app", lambda: calls.append("app"))

    main()

    assert calls == ["init", "app"]


def test_main_prints_first_run_notice_once(monkeypatch):
    from aletheore.cli import main

    monkeypatch.setattr("aletheore.cli.init_cli_sentry", lambda: None)
    monkeypatch.setattr("aletheore.cli.has_shown_crash_reporting_notice", lambda: False)
    marked = []
    monkeypatch.setattr(
        "aletheore.cli.mark_crash_reporting_notice_shown", lambda: marked.append(True)
    )
    monkeypatch.setattr("aletheore.cli.app", lambda: None)

    main()

    assert marked == [True]


def test_main_omits_first_run_notice_when_already_shown(monkeypatch, capsys):
    from aletheore.cli import main

    monkeypatch.setattr("aletheore.cli.init_cli_sentry", lambda: None)
    monkeypatch.setattr("aletheore.cli.has_shown_crash_reporting_notice", lambda: True)
    monkeypatch.setattr("aletheore.cli.app", lambda: None)

    main()

    assert "crashes" not in capsys.readouterr().out


def test_main_reports_an_unhandled_exception_to_sentry_and_reraises(monkeypatch, capsys):
    from aletheore.cli import main

    monkeypatch.setattr("aletheore.cli.init_cli_sentry", lambda: None)
    monkeypatch.setattr("aletheore.cli.has_shown_crash_reporting_notice", lambda: True)
    monkeypatch.setattr("aletheore.cli.is_crash_reporting_enabled", lambda: True)

    def _boom():
        raise RuntimeError("boom")

    monkeypatch.setattr("aletheore.cli.app", _boom)
    captured = []
    monkeypatch.setattr(
        "aletheore.cli.sentry_sdk.capture_exception", lambda exc: captured.append(exc)
    )

    with pytest.raises(RuntimeError, match="boom"):
        main()

    assert len(captured) == 1
    assert "This error was reported" in capsys.readouterr().out


def test_main_does_not_report_when_crash_reporting_is_disabled(monkeypatch):
    from aletheore.cli import main

    monkeypatch.setattr("aletheore.cli.init_cli_sentry", lambda: None)
    monkeypatch.setattr("aletheore.cli.has_shown_crash_reporting_notice", lambda: True)
    monkeypatch.setattr("aletheore.cli.is_crash_reporting_enabled", lambda: False)
    monkeypatch.setattr(
        "aletheore.cli.app", lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    captured = []
    monkeypatch.setattr(
        "aletheore.cli.sentry_sdk.capture_exception", lambda exc: captured.append(exc)
    )

    with pytest.raises(RuntimeError):
        main()

    assert captured == []


def test_main_does_not_report_keyboard_interrupt(monkeypatch):
    from aletheore.cli import main

    monkeypatch.setattr("aletheore.cli.init_cli_sentry", lambda: None)
    monkeypatch.setattr("aletheore.cli.has_shown_crash_reporting_notice", lambda: True)
    monkeypatch.setattr(
        "aletheore.cli.app", lambda: (_ for _ in ()).throw(KeyboardInterrupt())
    )
    captured = []
    monkeypatch.setattr(
        "aletheore.cli.sentry_sdk.capture_exception", lambda exc: captured.append(exc)
    )

    with pytest.raises(KeyboardInterrupt):
        main()

    assert captured == []


def test_main_does_not_crash_when_sentry_capture_itself_raises(monkeypatch):
    from aletheore.cli import main

    monkeypatch.setattr("aletheore.cli.init_cli_sentry", lambda: None)
    monkeypatch.setattr("aletheore.cli.has_shown_crash_reporting_notice", lambda: True)
    monkeypatch.setattr("aletheore.cli.is_crash_reporting_enabled", lambda: True)
    monkeypatch.setattr(
        "aletheore.cli.app", lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    def _broken_capture(exc):
        raise OSError("sentry transport unavailable")

    monkeypatch.setattr("aletheore.cli.sentry_sdk.capture_exception", _broken_capture)

    with pytest.raises(RuntimeError, match="boom"):
        main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli.py -k "main_initializes_cli_sentry or main_prints_first_run or main_omits_first_run or main_reports_an_unhandled or main_does_not_report or main_does_not_crash" -v`
Expected: FAIL - `AttributeError: <module 'aletheore.cli'> does not have the attribute 'init_cli_sentry'` (or equivalent) for each.

- [ ] **Step 3: Add the imports**

In `src/aletheore/cli.py`, add to the stdlib/third-party import block - `click` before `httpx`, `sentry_sdk` after `httpx`:

```python
import click
import httpx
import sentry_sdk
import tomli_w
import typer
import uvicorn
```

Add to the `from aletheore.*` import block, alongside the existing `from aletheore.credentials import get_api_key` line:

```python
from aletheore.credentials import get_api_key
from aletheore.preferences import (
    has_shown_crash_reporting_notice,
    is_crash_reporting_enabled,
    mark_crash_reporting_notice_shown,
    set_crash_reporting_enabled,
)
from aletheore.sentry_reporting import init_cli_sentry
```

(`set_crash_reporting_enabled` isn't used until Task 5, but importing it alongside its siblings now avoids touching this import block twice.)

- [ ] **Step 4: Update `main()`**

Replace the end of `src/aletheore/cli.py`:

```python
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="backslashreplace")
    app()
```

with:

```python
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="backslashreplace")

    init_cli_sentry()
    if not has_shown_crash_reporting_notice():
        console.print(
            "[dim]Aletheore reports crashes to help fix bugs across "
            "environments we can't all test. Disable with "
            "`aletheore config crash-reporting off`.[/dim]"
        )
        mark_crash_reporting_notice_shown()

    try:
        app()
    except (typer.Exit, click.exceptions.Exit, SystemExit, KeyboardInterrupt):
        raise
    except Exception as exc:
        if is_crash_reporting_enabled():
            # A broken Sentry SDK environment (no network, a bad DSN after
            # a future rotation, etc.) must never replace or mask the
            # user's real crash with a second, unrelated one - reporting
            # is a side effect, not a precondition for the exception
            # continuing to propagate normally.
            try:
                sentry_sdk.capture_exception(exc)
            except Exception:
                pass
            else:
                console.print(
                    "\n[dim]This error was reported to help fix it. "
                    "Disable with `aletheore config crash-reporting off`.[/dim]"
                )
        raise
```

(leave the `if __name__ == "__main__": main()` block below it unchanged)

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_cli.py -k "main_initializes_cli_sentry or main_prints_first_run or main_omits_first_run or main_reports_an_unhandled or main_does_not_report or main_does_not_crash" -v`
Expected: all PASS

- [ ] **Step 6: Run the full test suite to check for regressions**

Run: `pytest tests/ -v`
Expected: all PASS, including the two pre-existing `test_main_pins_stdout...`/`test_main_tolerates_a_stdout_stream...` tests, which patch `aletheore.cli.app` directly and don't raise - confirming the new wrapper is transparent to the existing no-crash path.

- [ ] **Step 7: Commit**

```bash
git add aletheore/cli.py tests/test_cli.py
git commit -m "feat: catch unhandled CLI exceptions and report them to Sentry"
```

---

## Task 5: `aletheore config crash-reporting` command and `status` line

**Files:**
- Modify: `src/aletheore/cli.py` (new command, and the existing `status()` function)
- Test: `src/tests/test_cli.py`

**Interfaces:**
- Consumes: `aletheore.preferences.is_crash_reporting_enabled()`, `set_crash_reporting_enabled()` (Task 1, already imported in Task 4).

- [ ] **Step 1: Write the failing tests**

Add to `src/tests/test_cli.py`:

```python
def test_config_crash_reporting_shows_current_state_when_enabled(monkeypatch):
    monkeypatch.setenv("ALETHEORE_CRASH_REPORTING", "1")

    result = runner.invoke(app, ["config", "crash-reporting"])

    assert result.exit_code == 0
    assert "Crash reporting: on" in result.output


def test_config_crash_reporting_shows_current_state_when_disabled(monkeypatch):
    monkeypatch.setenv("ALETHEORE_CRASH_REPORTING", "0")

    result = runner.invoke(app, ["config", "crash-reporting"])

    assert result.exit_code == 0
    assert "Crash reporting: off" in result.output


def test_config_crash_reporting_off_persists_the_preference(monkeypatch):
    monkeypatch.delenv("ALETHEORE_CRASH_REPORTING", raising=False)

    result = runner.invoke(app, ["config", "crash-reporting", "off"])

    assert result.exit_code == 0
    assert "turned off" in result.output
    assert is_crash_reporting_enabled() is False


def test_config_crash_reporting_on_persists_the_preference(monkeypatch):
    monkeypatch.delenv("ALETHEORE_CRASH_REPORTING", raising=False)
    set_crash_reporting_enabled(False)

    result = runner.invoke(app, ["config", "crash-reporting", "on"])

    assert result.exit_code == 0
    assert "turned on" in result.output
    assert is_crash_reporting_enabled() is True


def test_config_crash_reporting_rejects_an_invalid_value(monkeypatch):
    monkeypatch.delenv("ALETHEORE_CRASH_REPORTING", raising=False)

    result = runner.invoke(app, ["config", "crash-reporting", "maybe"])

    assert result.exit_code == 1
    assert "expected 'on' or 'off'" in result.output


def test_status_shows_crash_reporting_on(monkeypatch):
    monkeypatch.setenv("ALETHEORE_CRASH_REPORTING", "1")
    monkeypatch.delenv("ALETHEORE_API_TOKEN", raising=False)

    result = runner.invoke(app, ["status"])

    assert "Crash reporting: on" in result.output


def test_status_shows_crash_reporting_off(monkeypatch):
    monkeypatch.setenv("ALETHEORE_CRASH_REPORTING", "0")
    monkeypatch.delenv("ALETHEORE_API_TOKEN", raising=False)

    result = runner.invoke(app, ["status"])

    assert "Crash reporting: off" in result.output
```

Add the new import at the top of `src/tests/test_cli.py`, alongside the existing `from aletheore.device_auth import DeviceFlowError` line:

```python
from aletheore.preferences import is_crash_reporting_enabled, set_crash_reporting_enabled
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli.py -k "config_crash_reporting or status_shows_crash_reporting" -v`
Expected: FAIL - `config` isn't a recognized command (non-zero exit, "No such command" in output), and `status`'s output has no "Crash reporting" line yet.

- [ ] **Step 3: Add the `config` command group**

In `src/aletheore/cli.py`, add immediately before the `login` command (before `@app.command(help="authenticate and save a managed-audit API token")`):

```python
config_app = typer.Typer(help="manage local CLI preferences")
app.add_typer(config_app, name="config")


@config_app.command(
    "crash-reporting",
    help="show or change whether unhandled CLI errors are reported to Aletheore",
)
def config_crash_reporting(
    state: Optional[str] = typer.Argument(
        None, help="'on' or 'off' - omit to show the current state"
    ),
) -> None:
    if state is None:
        current = "on" if is_crash_reporting_enabled() else "off"
        console.print(f"Crash reporting: {current}")
        raise typer.Exit(code=0)

    normalized = state.strip().lower()
    if normalized not in ("on", "off"):
        console.print(f"[bold red]error:[/bold red] expected 'on' or 'off', got '{state}'")
        raise typer.Exit(code=1)

    set_crash_reporting_enabled(normalized == "on")
    console.print(f"[bold green]Crash reporting turned {normalized}.[/bold green]")
```

- [ ] **Step 4: Add the line to `status()`**

In `src/aletheore/cli.py`'s existing `status()` function, replace:

```python
    installed_version = importlib.metadata.version("aletheore")
    version_note = _check_for_update(installed_version)
    console.print(f"Aletheore v{installed_version} ({version_note})")

    if not credentials.has_api_key(
```

with:

```python
    installed_version = importlib.metadata.version("aletheore")
    version_note = _check_for_update(installed_version)
    console.print(f"Aletheore v{installed_version} ({version_note})")

    crash_reporting_state = (
        "on"
        if is_crash_reporting_enabled()
        else "off (run 'aletheore config crash-reporting on' to enable)"
    )
    console.print(f"Crash reporting: {crash_reporting_state}")

    if not credentials.has_api_key(
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_cli.py -k "config_crash_reporting or status_shows_crash_reporting" -v`
Expected: all PASS

- [ ] **Step 6: Run the full test suite to check for regressions**

Run: `pytest tests/ -v`
Expected: all PASS, including the pre-existing `status`-adjacent tests (none assert on exact full output, only on login-state substrings, so the new line doesn't break them - confirmed by reading `status()`'s existing tests before this step).

- [ ] **Step 7: Commit**

```bash
git add aletheore/cli.py tests/test_cli.py
git commit -m "feat: add 'aletheore config crash-reporting' command and status line"
```

---

## After this plan

Not part of this plan (release, not code):
1. Version bump in `src/pyproject.toml` and publish to PyPI via the
   existing release process - this is the actual "deploy" for a CLI;
   there's no server-side rollout step the way the backend had.
