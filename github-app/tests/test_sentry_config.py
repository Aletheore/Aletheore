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
