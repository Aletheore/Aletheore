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
