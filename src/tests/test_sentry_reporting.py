import importlib.metadata
import json
import os
import platform
from pathlib import Path

import pytest
import sentry_sdk

from aletheore.sentry_reporting import _scrub_event, init_cli_sentry

# A syntactically valid but fake DSN - same pattern the backend's own
# tests/test_sentry_config.py uses. Patched into every test below that
# activates a real client, so the test suite can never configure a live
# client against the real aletheore-cli DSN (final-review finding: two
# tests previously activated the real DSN with no transport stub).
_FAKE_DSN = "https://examplePublicKey@o0.ingest.sentry.io/0"


@pytest.fixture
def _reset_sentry_client():
    yield
    # Global SDK state (sentry_sdk.init sets a process-wide client) must
    # not leak into whichever test runs next.
    sentry_sdk.init(dsn=None)


@pytest.fixture
def _fake_dsn(monkeypatch):
    monkeypatch.setattr("aletheore.sentry_reporting._CLI_SENTRY_DSN", _FAKE_DSN)


def test_init_cli_sentry_is_a_noop_when_crash_reporting_is_disabled(
    monkeypatch, _reset_sentry_client
):
    monkeypatch.setenv("ALETHEORE_CRASH_REPORTING", "0")

    init_cli_sentry()

    assert not sentry_sdk.get_client().is_active()


def test_init_cli_sentry_activates_client_when_crash_reporting_is_enabled(
    monkeypatch, _reset_sentry_client, _fake_dsn
):
    monkeypatch.setenv("ALETHEORE_CRASH_REPORTING", "1")

    init_cli_sentry()

    assert sentry_sdk.get_client().is_active()


def test_init_cli_sentry_configures_no_performance_tracing(
    monkeypatch, _reset_sentry_client, _fake_dsn
):
    monkeypatch.setenv("ALETHEORE_CRASH_REPORTING", "1")

    init_cli_sentry()

    assert sentry_sdk.get_client().options["traces_sample_rate"] == 0


def test_init_cli_sentry_disables_default_pii(monkeypatch, _reset_sentry_client, _fake_dsn):
    monkeypatch.setenv("ALETHEORE_CRASH_REPORTING", "1")

    init_cli_sentry()

    assert sentry_sdk.get_client().options["send_default_pii"] is False


def test_init_cli_sentry_tags_release_with_installed_version(
    monkeypatch, _reset_sentry_client, _fake_dsn
):
    monkeypatch.setenv("ALETHEORE_CRASH_REPORTING", "1")

    init_cli_sentry()

    installed_version = importlib.metadata.version("aletheore")
    assert sentry_sdk.get_client().options["release"] == f"aletheore-cli@{installed_version}"


def test_init_cli_sentry_sets_os_context_on_a_real_captured_event(
    monkeypatch, _reset_sentry_client, _fake_dsn
):
    # Final-review finding: the hand-built-event scrub test below proves
    # _scrub_event doesn't strip os/runtime, but never proved the SDK
    # actually puts an os context on a real event in the first place - it
    # didn't, with no application code setting it. This drives a real
    # event through init -> capture -> before_send -> a stubbed transport
    # and inspects what was actually about to be sent.
    monkeypatch.setenv("ALETHEORE_CRASH_REPORTING", "1")

    init_cli_sentry()

    captured = []
    sentry_sdk.get_client().transport.capture_envelope = captured.append
    sentry_sdk.capture_message("test-os-context")
    sentry_sdk.get_client().flush()

    assert captured, "no envelope captured"
    event = captured[0].get_event()
    assert event["contexts"]["os"]["name"] == platform.system()


def test_init_cli_sentry_strips_argv_and_redacts_home_from_a_real_captured_event(
    monkeypatch, _reset_sentry_client, _fake_dsn, tmp_path
):
    # Final-review finding: _scrub_event's hand-built-event tests never
    # exercised what the real SDK actually attaches (extra["sys.argv"],
    # via the default ArgvIntegration) or what a real OSError's message
    # looks like (it embeds the full path, not just the frame filename -
    # the home-path rewrite only touched frame filename/abs_path before).
    fake_home = str(tmp_path / "johnsmith")
    monkeypatch.setattr("aletheore.sentry_reporting._HOME", fake_home)
    monkeypatch.setattr("aletheore.sentry_reporting._HOME_PREFIX", fake_home + os.sep)
    monkeypatch.setenv("ALETHEORE_CRASH_REPORTING", "1")

    init_cli_sentry()

    captured = []
    sentry_sdk.get_client().transport.capture_envelope = captured.append

    try:
        open(fake_home + "/secret-project/missing.txt")
    except OSError as exc:
        sentry_sdk.capture_exception(exc)
    sentry_sdk.get_client().flush()

    assert captured, "no envelope captured"
    event = captured[0].get_event()
    assert "sys.argv" not in event.get("extra", {})
    assert "johnsmith" not in json.dumps(event)


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
    monkeypatch.setattr("aletheore.sentry_reporting._HOME_PREFIX", str(Path("/Users/johnsmith")) + "/")
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


def test_scrub_event_strips_argv_from_extra():
    event = {
        "extra": {"sys.argv": ["/Users/johnsmith/.local/bin/aletheore", "scan", "."]},
        "exception": {"values": []},
    }

    scrubbed = _scrub_event(event, {})

    assert "sys.argv" not in scrubbed["extra"]


def test_scrub_event_redacts_home_directory_from_exception_message(monkeypatch):
    monkeypatch.setattr("aletheore.sentry_reporting._HOME", "/Users/johnsmith")
    monkeypatch.setattr("aletheore.sentry_reporting._HOME_PREFIX", "/Users/johnsmith/")
    event = {
        "exception": {
            "values": [
                {
                    "type": "FileNotFoundError",
                    "value": (
                        "[Errno 2] No such file or directory: "
                        "'/Users/johnsmith/secret-project/missing.txt'"
                    ),
                    "stacktrace": {"frames": []},
                }
            ]
        }
    }

    scrubbed = _scrub_event(event, {})

    value = scrubbed["exception"]["values"][0]["value"]
    assert "johnsmith" not in value
    assert "~/secret-project/missing.txt" in value


def test_scrub_event_redacts_home_directory_from_top_level_message(monkeypatch):
    monkeypatch.setattr("aletheore.sentry_reporting._HOME", "/Users/johnsmith")
    monkeypatch.setattr("aletheore.sentry_reporting._HOME_PREFIX", "/Users/johnsmith/")
    event = {"message": "failed to read /Users/johnsmith/notes.txt", "exception": {"values": []}}

    scrubbed = _scrub_event(event, {})

    assert "johnsmith" not in scrubbed["message"]


def test_scrub_event_redacts_home_directory_from_logentry(monkeypatch):
    monkeypatch.setattr("aletheore.sentry_reporting._HOME", "/Users/johnsmith")
    monkeypatch.setattr("aletheore.sentry_reporting._HOME_PREFIX", "/Users/johnsmith/")
    event = {
        "logentry": {
            "formatted": "scanning /Users/johnsmith/project",
            "message": "scanning %s",
        },
        "exception": {"values": []},
    }

    scrubbed = _scrub_event(event, {})

    assert "johnsmith" not in scrubbed["logentry"]["formatted"]


def test_scrub_event_redacts_home_directory_from_breadcrumbs(monkeypatch):
    monkeypatch.setattr("aletheore.sentry_reporting._HOME", "/Users/johnsmith")
    monkeypatch.setattr("aletheore.sentry_reporting._HOME_PREFIX", "/Users/johnsmith/")
    event = {
        "breadcrumbs": {"values": [{"message": "opened /Users/johnsmith/config.json"}]},
        "exception": {"values": []},
    }

    scrubbed = _scrub_event(event, {})

    assert "johnsmith" not in scrubbed["breadcrumbs"]["values"][0]["message"]


def test_scrub_event_does_not_false_positive_on_a_sibling_directory(monkeypatch):
    # Final-review finding: a naive startswith/replace on the bare home
    # path would wrongly match /Users/ari inside /Users/arijit/... - must
    # anchor on a real path separator boundary.
    monkeypatch.setattr("aletheore.sentry_reporting._HOME", "/Users/ari")
    monkeypatch.setattr("aletheore.sentry_reporting._HOME_PREFIX", "/Users/ari/")
    event = {
        "exception": {
            "values": [
                {
                    "value": "boom",
                    "stacktrace": {"frames": [{"filename": "/Users/arijit/project/cli.py"}]},
                }
            ]
        }
    }

    scrubbed = _scrub_event(event, {})

    frame = scrubbed["exception"]["values"][0]["stacktrace"]["frames"][0]
    assert frame["filename"] == "/Users/arijit/project/cli.py"


def test_scrub_event_redacts_home_directory_case_insensitively_on_windows(monkeypatch):
    # Windows filesystems are case-preserving but case-insensitive, so a
    # traceback frame's casing (from how the module happened to be
    # imported) can differ from Path.home()'s casing. os.path.normcase
    # lowercases on Windows and is a no-op on POSIX - simulate the
    # Windows behavior here so this is pinned regardless of which OS the
    # suite actually runs on.
    monkeypatch.setattr("aletheore.sentry_reporting.os.path.normcase", str.lower)
    monkeypatch.setattr("aletheore.sentry_reporting._HOME", "C:\\Users\\JohnSmith")
    monkeypatch.setattr("aletheore.sentry_reporting._HOME_PREFIX", "C:\\Users\\JohnSmith\\")
    event = {
        "exception": {
            "values": [
                {
                    "value": "boom",
                    "stacktrace": {
                        "frames": [{"filename": "c:\\users\\johnsmith\\project\\cli.py"}]
                    },
                }
            ]
        }
    }

    scrubbed = _scrub_event(event, {})

    filename = scrubbed["exception"]["values"][0]["stacktrace"]["frames"][0]["filename"]
    assert "johnsmith" not in filename.lower()


def test_scrub_event_redacts_an_exact_home_directory_match_with_no_trailing_separator(
    monkeypatch,
):
    monkeypatch.setattr("aletheore.sentry_reporting._HOME", "/Users/johnsmith")
    monkeypatch.setattr("aletheore.sentry_reporting._HOME_PREFIX", "/Users/johnsmith/")
    event = {
        "exception": {
            "values": [{"value": "boom", "stacktrace": {"frames": [{"filename": "/Users/johnsmith"}]}}]
        }
    }

    scrubbed = _scrub_event(event, {})

    frame = scrubbed["exception"]["values"][0]["stacktrace"]["frames"][0]
    assert frame["filename"] == "~"
